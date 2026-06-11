"""Dashboard data builder — turns pipeline ``results/`` + the C2 exceedance Zarr
into the static contract (web/public/data) and COG/STAC assets (web/public).

Commands:
    contract   geojson/{code}.json, {date}/region_risks.json, dependency.json, index.json
    cogs       exceedance_{date}_{w}_{rp}.tif + risk_{date}.tif
    gpm        gpm_{date}.tif
    emdat      {date}/emdat.geojson
    stac       stac/catalog.json (collections risk + exceedance)
    all        every stage (each degrades gracefully)

Example:
    python -m dashboard.data_pipeline.pipeline all \
        --results results/oneday_20251119/admin1_risk \
        --exceedance-store s3://gik-icechain/exceedance-zarr \
        --endpoint-url http://MINIO:9000 --out dashboard/web/public --date 2025-11-19
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import structlog
import typer

log = structlog.get_logger(__name__)
app = typer.Typer(add_completion=False, help=__doc__)

RISK_LABELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red", -1: "No_Data"}
WINDOWS_H = [3, 6, 12, 24, 48, 72, 168]
WINDOW_LABELS = {3: "3h", 6: "6h", 12: "12h", 24: "24h", 48: "48h", 72: "72h", 168: "7d"}
RETURN_PERIODS = [2, 5, 10, 20, 40, 100]
N_MEMBERS = 51
EA_BBOX = [22.0, -14.5, 54.0, 25.0]  # west, south, east, north
_COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
_DEFAULT_COG_BASE = "https://gik-icechain-cogs.s3.eu-west-1.amazonaws.com/cogs"
_GPM_DIR = Path("data/gpm_imerg")
_EMDAT_CSV = Path("data/emdat/east_africa_floods.csv")


def _data(out: Path) -> Path:
    return out / "data"


def _cogs(out: Path) -> Path:
    return out / "cogs"


def _pcode(p: dict) -> tuple[str, str, str]:
    """Return (pcode, name, country) from either source or C3 output schema."""
    country = (p.get("country") or p.get("shapeGroup") or "").upper()
    name = p.get("admin1_name") or p.get("shapeName") or ""
    return p.get("admin1_pcode") or f"{country}_{name}", name, country


#  static contract
def split_geojson(boundaries: Path, data_dir: Path) -> dict[str, int]:
    fc = json.loads(boundaries.read_text())
    by_country: dict[str, list[dict]] = {}
    for feat in fc.get("features", []):
        pcode, name, country = _pcode(feat.get("properties", {}))
        if not country:
            continue
        by_country.setdefault(country, []).append({
            "type": "Feature", "geometry": feat["geometry"],
            "properties": {"name": name, "pcode": pcode, "country": country},
        })
    gj = data_dir / "geojson"
    gj.mkdir(parents=True, exist_ok=True)
    counts = {}
    for country, feats in by_country.items():
        (gj / f"{country.lower()}.json").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        counts[country.lower()] = len(feats)
    return counts


def region_risks(scores: dict, boundaries: Path, data_dir: Path, day: str) -> int:
    meta = {}
    for feat in json.loads(boundaries.read_text()).get("features", []):
        pcode, name, country = _pcode(feat.get("properties", {}))
        meta[pcode] = (name, country)
    out = {}
    for pcode, u in scores.get("units", {}).items():
        name, country = meta.get(pcode, (pcode.split("_", 1)[-1], pcode.split("_", 1)[0]))
        out[pcode] = {
            "pcode": pcode, "name": name, "country": country,
            "risk_state": int(u.get("risk_state", -1)),
            "risk_label": u.get("risk_label", "No_Data"),
            "p_green": round(float(u.get("p_green", 0.0)), 4),
            "p_yellow": round(float(u.get("p_yellow", 0.0)), 4),
            "p_orange": round(float(u.get("p_orange", 0.0)), 4),
            "p_red": round(float(u.get("p_red", 0.0)), 4),
        }
    d = data_dir / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "region_risks.json").write_text(json.dumps(out, separators=(",", ":")))
    return len(out)


def dependency(scores: dict, boundaries: Path, data_dir: Path, day: str,
               store: str | None, endpoint: str | None) -> None:
    zonal = None
    if store:
        try:
            zonal = _zonal(boundaries, day, store, endpoint)
        except Exception as exc:
            log.warning("dependency_store_fallback", error=str(exc)[:120])
    out = {}
    for pcode, u in scores.get("units", {}).items():
        if zonal and pcode in zonal:
            gev, conf_m = zonal[pcode]["gev"], zonal[pcode]["conf_m"]
        else:
            gev = {lbl: {} for lbl in WINDOW_LABELS.values()}
            gev["24h"]["5"] = round(float(u.get("exceedance_24h_5y", 0.0)), 4)
            gev["72h"]["5"] = round(float(u.get("exceedance_72h_5y", 0.0)), 4)
            conf_m = round(float(u.get("spatial_coverage", 0.0)) * N_MEMBERS)
        win = {lbl: _sev(max((gev.get(lbl, {}) or {}).values(), default=0.0))
               for lbl in WINDOW_LABELS.values()}
        out[pcode] = {"win": win, "gev": gev,
                      "confidence": {"m": conf_m, "label": _conf(conf_m)}}
    (data_dir / day).mkdir(parents=True, exist_ok=True)
    (data_dir / day / "dependency.json").write_text(json.dumps(out, separators=(",", ":")))


def _zonal(boundaries: Path, day: str, store: str, endpoint: str | None) -> dict[str, dict]:
    """Max exceedance per admin-1 polygon, per (window, RP), from the Zarr store."""
    import numpy as np
    import shapely
    import xarray as xr
    from shapely.geometry import shape

    so = {"endpoint_url": endpoint} if endpoint else None
    ds = xr.open_zarr(store, consolidated=False, storage_options=so)
    if "date" in ds.dims:
        ds = ds.sel(date=day)
    exc = ds["exceedance_prob"].transpose("latitude", "longitude", "window", "return_period").load()
    conf = ds.get("ensemble_confidence")
    conf = conf.transpose("latitude", "longitude").load() if conf is not None else None
    lon2d, lat2d = np.meshgrid(exc["longitude"].values, exc["latitude"].values)

    result = {}
    for feat in json.loads(boundaries.read_text()).get("features", []):
        pcode, _, _ = _pcode(feat.get("properties", {}))
        geom = shape(feat["geometry"])
        minx, miny, maxx, maxy = geom.bounds
        box = (lon2d >= minx) & (lon2d <= maxx) & (lat2d >= miny) & (lat2d <= maxy)
        if not box.any():
            continue
        jj, ii = np.where(box)
        inside = shapely.contains_xy(geom, lon2d[jj, ii], lat2d[jj, ii])
        if not inside.any():
            inside = np.zeros(jj.shape, dtype=bool)
            inside[len(jj) // 2] = True
        sj, si = jj[inside], ii[inside]
        gev = {}
        for wi, wh in enumerate(WINDOWS_H):
            gev[WINDOW_LABELS[wh]] = {}
            for ri, rp in enumerate(RETURN_PERIODS):
                vmax = float(np.nanmax(exc.values[sj, si, wi, ri]))
                if not np.isnan(vmax):
                    gev[WINDOW_LABELS[wh]][str(rp)] = round(vmax, 4)
        conf_m = 0
        if conf is not None:
            conf_m = round(float(np.nanmax(conf.values[sj, si])) / 2.0 * N_MEMBERS)
        result[pcode] = {"gev": gev, "conf_m": conf_m}
    return result


def _sev(p: float) -> int:
    return 3 if p >= 0.5 else 2 if p >= 0.3 else 1 if p >= 0.15 else 0


def _conf(m: int) -> str:
    f = m / N_MEMBERS
    return "High" if f >= 0.66 else "Medium" if f >= 0.33 else "Low"


def update_index(data_dir: Path, day: str, scores: dict) -> None:
    p = data_dir / "index.json"
    idx = json.loads(p.read_text()) if p.exists() else {}
    units = scores.get("units", {})
    worst = max((int(u.get("risk_state", -1)) for u in units.values()), default=-1)
    idx[day] = {"worst_risk": worst, "risk_label": RISK_LABELS.get(worst, "No_Data"),
                "n_units": len(units)}
    data_dir.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(dict(sorted(idx.items())), indent=1))


#  rasters (COG)
def exceedance_cogs(store: str, endpoint: str | None, day: str, out: Path,
                    windows: list[int], rps: list[int]) -> int:
    import rioxarray  # noqa: F401
    import xarray as xr

    so = {"endpoint_url": endpoint} if endpoint else None
    ds = xr.open_zarr(store, consolidated=False, storage_options=so)
    if "date" in ds.dims:
        ds = ds.sel(date=day)
    exc = ds["exceedance_prob"]
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for wh in windows:
        for rp in rps:
            da = exc.sel(window=wh, return_period=rp)
            da = da.rio.set_spatial_dims(x_dim="longitude", y_dim="latitude")
            da = da.rio.write_crs("EPSG:4326")
            da.rio.to_raster(out / f"exceedance_{day}_{wh}_{rp}.tif",
                             driver="COG", compress="deflate")
            n += 1
    return n


def risk_cog(scores_path: Path, boundaries: Path, day: str, out: Path, res: float = 0.1) -> Path:
    import numpy as np
    import rasterio
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds
    from shapely.geometry import shape

    scores = json.loads(scores_path.read_text()).get("units", {})
    w, s, e, n = EA_BBOX
    width, height = int((e - w) / res), int((n - s) / res)
    transform = from_bounds(w, s, e, n, width, height)
    shapes = []
    for feat in json.loads(boundaries.read_text()).get("features", []):
        pcode, _, _ = _pcode(feat.get("properties", {}))
        state = int(scores.get(pcode, {}).get("risk_state", -1))
        shapes.append((shape(feat["geometry"]), state))
    raster = rasterize(shapes, out_shape=(height, width), transform=transform,
                       fill=-1, dtype="int16")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"risk_{day}.tif"
    with rasterio.open(path, "w", driver="COG", height=height, width=width, count=1,
                       dtype="int16", crs="EPSG:4326", transform=transform, nodata=-1,
                       compress="deflate") as dst:
        dst.write(raster.astype(np.int16), 1)
    return path


def gpm_cog(gpm_dir: Path, day: str, out: Path) -> Path | None:
    import rioxarray  # noqa: F401

    from gik_icechain.thresholds.gpm_seasonal import load_gpm_daily_ea

    matches = sorted(gpm_dir.glob(f"*{date_cls.fromisoformat(day).strftime('%Y%m%d')}*.nc4"))
    if not matches:
        log.warning("gpm_no_file", date=day, dir=str(gpm_dir))
        return None
    da = load_gpm_daily_ea(matches).isel(time=0, drop=True)
    da = da.rio.set_spatial_dims(x_dim="lon", y_dim="lat").rio.write_crs("EPSG:4326")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"gpm_{day}.tif"
    da.rio.to_raster(path, driver="COG", compress="deflate")
    return path


# EM-DAT overlay
def emdat_geojson(emdat_csv: Path, boundaries: Path, data_dir: Path, day: str,
                  window_days: int) -> int:
    import pandas as pd
    from shapely.geometry import shape

    if not emdat_csv.exists():
        log.warning("emdat_not_found", path=str(emdat_csv))
        return 0
    df = pd.read_csv(emdat_csv)
    centroids = {}
    for feat in json.loads(boundaries.read_text()).get("features", []):
        pcode, _, _ = _pcode(feat.get("properties", {}))
        c = shape(feat["geometry"]).centroid
        centroids[pcode] = (c.x, c.y)
    d = date_cls.fromisoformat(day)
    lo, hi = d - timedelta(days=window_days), d + timedelta(days=window_days)
    date_col = next((c for c in ("date", "start_date", "event_date") if c in df.columns), None)
    pcode_col = next((c for c in ("admin1_pcode", "pcode") if c in df.columns), None)
    feats = []
    for _, row in df.iterrows():
        if date_col:
            try:
                if not (lo <= pd.to_datetime(row[date_col]).date() <= hi):
                    continue
            except Exception:
                pass
        xy = centroids.get(str(row[pcode_col])) if pcode_col else None
        if not xy:
            continue
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(xy)},
                      "properties": {k: (None if pd.isna(v) else v) for k, v in row.items()}})
    out = data_dir / day
    out.mkdir(parents=True, exist_ok=True)
    (out / "emdat.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return len(feats)


#  STAC
def _stac_item(coll: str, day: str, key: str, cog_base: str, extra: dict | None = None) -> dict:
    w, s, e, n = EA_BBOX
    return {
        "type": "Feature", "stac_version": "1.0.0", "id": key.replace(".tif", ""),
        "collection": coll,
        "geometry": {"type": "Polygon",
                     "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
        "bbox": EA_BBOX,
        "properties": {"datetime": f"{day}T00:00:00Z", "collection": coll, **(extra or {})},
        "assets": {"data": {"href": f"{cog_base}/{key}", "type": _COG_TYPE, "roles": ["data"]}},
        "links": [],
    }


def stac_catalog(out: Path, cog_base: str) -> tuple[int, int]:
    risk, exc = [], []
    for tif in sorted(_cogs(out).glob("*.tif")):
        mr = re.match(r"risk_(\d{4}-\d{2}-\d{2})\.tif", tif.name)
        me = re.match(r"exceedance_(\d{4}-\d{2}-\d{2})_(\d+)_(\d+)\.tif", tif.name)
        if mr:
            risk.append(_stac_item("gik-icechain-risk", mr.group(1), tif.name, cog_base))
        elif me:
            day, wh, rp = me.groups()
            exc.append(_stac_item("gik-icechain-exceedance", day, tif.name, cog_base,
                                  {"window_h": int(wh), "return_period": int(rp)}))
    sd = out / "stac"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "catalog.json").write_text(json.dumps({
        "type": "Catalog", "stac_version": "1.0.0", "id": "gik-icechain",
        "description": "GIK-IceChain East Africa flood-risk COG catalog",
        "collections": {"gik-icechain-risk": {"items": risk},
                        "gik-icechain-exceedance": {"items": exc}},
        "links": [],
    }, indent=1))
    return len(risk), len(exc)


# CLI
def _scores(results: Path, day: str) -> tuple[dict, Path]:
    p = results / f"{day}_risk_scores.json"
    if not p.exists():
        raise typer.BadParameter(f"risk_scores not found: {p}")
    return json.loads(p.read_text()), results / "admin1_boundaries.geojson"


Results = Annotated[Path, typer.Option(help="admin1_risk dir")]
Out = Annotated[Path, typer.Option(help="web/public root")]
Date = Annotated[str, typer.Option(help="forecast date YYYY-MM-DD")]
Store = Annotated[str | None, typer.Option("--exceedance-store")]
Endpoint = Annotated[str | None, typer.Option("--endpoint-url")]


def _do_contract(results: Path, out: Path, day: str,
                 store: str | None, endpoint: str | None) -> None:
    scores, boundaries = _scores(results, day)
    data_dir = _data(out)
    gj = split_geojson(boundaries, data_dir)
    region_risks(scores, boundaries, data_dir, day)
    dependency(scores, boundaries, data_dir, day, store, endpoint)
    update_index(data_dir, day, scores)
    log.info("contract_done", date=day, units=len(scores.get("units", {})), countries=len(gj))


@app.command()
def contract(results: Results, out: Out, date: Date,
             exceedance_store: Store = None, endpoint_url: Endpoint = None) -> None:
    """Static JSON/GeoJSON contract (geojson, region_risks, dependency, index)."""
    _do_contract(results, out, date, exceedance_store, endpoint_url)


@app.command()
def cogs(results: Results, out: Out, date: Date,
         exceedance_store: Store = None, endpoint_url: Endpoint = None,
         windows: Annotated[list[int] | None, typer.Option()] = None,
         rps: Annotated[list[int] | None, typer.Option()] = None) -> None:
    """Exceedance + risk Cloud-Optimised GeoTIFFs for TiTiler."""
    _, boundaries = _scores(results, date)
    n = exceedance_cogs(exceedance_store, endpoint_url, date, _cogs(out),
                        windows or [24, 72], rps or [5]) if exceedance_store else 0
    risk_cog(results / f"{date}_risk_scores.json", boundaries, date, _cogs(out))
    log.info("cogs_done", date=date, exceedance=n, risk=1)


@app.command()
def gpm(results: Results, out: Out, date: Date,
        gpm_dir: Annotated[Path, typer.Option()] = _GPM_DIR) -> None:
    """GPM IMERG observed-rainfall COG."""
    path = gpm_cog(gpm_dir, date, _cogs(out))
    log.info("gpm_done", date=date, path=str(path) if path else None)


@app.command()
def emdat(results: Results, out: Out, date: Date,
          emdat_csv: Annotated[Path, typer.Option("--emdat")] = _EMDAT_CSV,
          window_days: Annotated[int, typer.Option()] = 30) -> None:
    """EM-DAT flood-event overlay GeoJSON."""
    _, boundaries = _scores(results, date)
    n = emdat_geojson(emdat_csv, boundaries, _data(out), date, window_days)
    log.info("emdat_done", date=date, events=n)


@app.command()
def stac(out: Out, cog_base: Annotated[str, typer.Option()] = _DEFAULT_COG_BASE) -> None:
    """STAC catalog over the generated COGs (collections risk + exceedance)."""
    r, e = stac_catalog(out, cog_base)
    log.info("stac_done", risk=r, exceedance=e)


@app.command(name="all")
def build_all(results: Results, out: Out, date: Date,
              exceedance_store: Store = None, endpoint_url: Endpoint = None,
              gpm_dir: Annotated[Path, typer.Option()] = _GPM_DIR,
              emdat_csv: Annotated[Path, typer.Option("--emdat")] = _EMDAT_CSV,
              cog_base: Annotated[str, typer.Option()] = _DEFAULT_COG_BASE) -> None:
    """Run every stage (each degrades gracefully on missing inputs)."""
    _do_contract(results, out, date, exceedance_store, endpoint_url)
    _, boundaries = _scores(results, date)
    for name, fn in (
        ("cogs", lambda: (exceedance_store and exceedance_cogs(
            exceedance_store, endpoint_url, date, _cogs(out), [24, 72], [5]),
            risk_cog(results / f"{date}_risk_scores.json", boundaries, date, _cogs(out)))),
        ("gpm", lambda: gpm_cog(gpm_dir, date, _cogs(out))),
        ("emdat", lambda: emdat_geojson(emdat_csv, boundaries, _data(out), date, 30)),
        ("stac", lambda: stac_catalog(out, cog_base)),
    ):
        try:
            fn()
            log.info("stage_done", stage=name, date=date)
        except Exception as exc:
            log.warning("stage_skipped", stage=name, error=str(exc)[:120])


if __name__ == "__main__":
    app()
