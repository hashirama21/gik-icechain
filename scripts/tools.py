#!/usr/bin/env python3
"""GIK-IceChain developer tools.

CLI for benchmarking, store validation, data download,
gap-filling, EAHW export, and EM-DAT validation.

Usage:
    python scripts/tools.py --help
    python scripts/tools.py benchmark --gik-store s3://...
    python scripts/tools.py validate-store --store-uri s3://...
    python scripts/tools.py download --component all
    python scripts/tools.py gap-fill --start 2023-05-01 --end 2024-02-29
    python scripts/tools.py export-eahw --risk-dir results/admin1_risk/ --output results/eahw/
    python scripts/tools.py validate-emdat --risk-dir results/admin1_risk/
    python scripts/tools.py download-emdat --from-year 2024 --to-year 2024
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

REPO_ROOT = Path(__file__).resolve().parent.parent

app = typer.Typer(
    name="gik-tools",
    help="GIK-IceChain developer and operations tools.",
    no_args_is_help=True,
)


def _urlopen(url: str, timeout: int):
    """urlopen with the certifi CA bundle (macOS Python lacks system roots)."""
    import ssl
    import urllib.request

    ctx = None
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    return urllib.request.urlopen(url, timeout=timeout, context=ctx)


@app.command()
def benchmark(
    gik_store: Annotated[str, typer.Option("--gik-store", help="URI of the GIK IceChunk store.")],
    dynamical_store: Annotated[
        str | None,
        typer.Option(
            "--dynamical-store",
            help="URI of conventional Zarr store to compare.",
        ),
    ] = None,
    domain: Annotated[str, typer.Option(help="Domain label for output CSV.")] = "east_africa",
    n_days: Annotated[int, typer.Option(help="Forecast day-groups to benchmark.")] = 30,
    workers: Annotated[int, typer.Option(help="Dask workers for full-scan measurement.")] = 4,
    output_dir: Annotated[Path, typer.Option(help="Directory for benchmark CSV output.")] = Path(
        "results/benchmarks/"
    ),
) -> None:
    """Benchmark storage efficiency and access speed of GIK+IceChunk."""
    from gik_icechain.conversion.benchmark import run_benchmark

    results = run_benchmark(
        gik_store_uri=gik_store,
        dynamical_store_uri=dynamical_store,
        domain=domain,
        n_days=n_days,
        n_workers=workers,
        output_dir=str(output_dir),
    )

    if not results:
        typer.echo("No benchmark results produced. Check that the store URI is accessible.")
        raise typer.Exit(1)

    header = (
        f"{'Approach':<20} {'Store (GB)':>12} {'TTFB (s)':>10} {'Scan (s)':>10} {'Egress $':>10}"
    )
    typer.echo(f"\n{header}")
    typer.echo("-" * 66)
    for r in results.values():
        typer.echo(
            f"{r.approach:<20} {r.store_size_gb:>12,.0f} "
            f"{r.time_to_first_byte_s:>10.3f} "
            f"{r.full_scan_elapsed_s:>10.1f} "
            f"{r.estimated_egress_usd:>10.4f}"
        )

    if "GIK+IceChunk" in results and "dynamical.org" in results:
        gik = results["GIK+IceChunk"]
        dyn = results["dynamical.org"]
        ratio = dyn.store_size_gb / gik.store_size_gb if gik.store_size_gb else 0
        typer.echo(f"\nStorage compression ratio: {ratio:,.0f}x")

    typer.echo(f"\nResults saved to {output_dir}")


#  validate-store


@app.command("validate-store")
def validate_store(
    store_uri: Annotated[
        str,
        typer.Option("--store-uri", help="IceChunk store URI (s3:// or local path)."),
    ],
    output_json: Annotated[bool, typer.Option("--json", help="Output results as JSON.")] = False,
) -> None:
    """Validate IceChunk store integrity: committed days, gaps, variables."""
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    store = IceChainStore(store_uri)
    store.create_or_open()
    report = store.validate()

    if output_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        typer.echo(f"Store URI       : {store_uri}")
        typer.echo(f"Committed days  : {report['committed_days']}")
        typer.echo(f"Date range      : {report['date_range']}")
        typer.echo(f"Total snapshots : {report['total_snapshots']}")
        typer.echo(f"Gaps detected   : {report['gaps_detected']}")
        if report["gap_details"]:
            typer.echo("Gap details:")
            for g in report["gap_details"]:
                typer.echo(f"  {g}")
        typer.echo(f"Variables       : {report['variables_present']}")

    if report["gaps_detected"] > 0:
        typer.echo(
            f"\nWARNING: {report['gaps_detected']} gap(s) detected",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("\nStore is valid.")


def _download_admin_boundaries(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "east_africa_admin1.geojson"
    if dest.exists():
        typer.echo(f"  Already exists: {dest}")
        return

    typer.echo("  Downloading admin-1 boundaries from geoBoundaries ...")

    # In sync with shared.regions.EAST_AFRICA_COUNTRIES_ISO3.
    _ea_countries = [
        "KEN",
        "ETH",
        "UGA",
        "TZA",
        "SOM",
        "RWA",
        "BDI",
        "SSD",
        "ERI",
        "DJI",
        "MDG",
        "SDN",
        "COM",
        "SYC",
        "MWI",
        "ZMB",
    ]
    all_features: list[dict] = []
    for iso in _ea_countries:
        api_url = f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM1/"
        try:
            with _urlopen(api_url, timeout=15) as r:
                meta = json.loads(r.read())
            dl_url = meta.get("gjDownloadURL", "")
            if not dl_url:
                typer.echo(f"    {iso}: no download URL", err=True)
                continue
            with _urlopen(dl_url, timeout=30) as r:
                fc = json.loads(r.read())
            for feat in fc.get("features", []):
                feat["properties"]["admin1_pcode"] = (
                    iso + "_" + str(feat["properties"].get("shapeName", ""))[:20]
                )
                all_features.append(feat)
            typer.echo(f"    {iso}: {len(fc.get('features', []))} units")
        except Exception as exc:
            typer.echo(f"    {iso}: skipped ({exc})", err=True)

    if not all_features:
        raise RuntimeError("No admin-1 features downloaded from geoBoundaries")

    dest.write_text(json.dumps({"type": "FeatureCollection", "features": all_features}))
    typer.echo(f"  Saved {len(all_features)} admin-1 units: {dest}")


def _download_cmorph_thresholds(output_dir: Path) -> None:
    """Download CMORPH East Africa return-period thresholds from HuggingFace."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "cmorph_ea_return_periods.nc"
    if dest.exists():
        typer.echo(f"  Already exists: {dest}")
        return

    typer.echo("  Downloading CMORPH return-period thresholds from HuggingFace ...")
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        repo_id="E4DRR/virtualizarr-stores",
        filename="cmorph_ea_return_periods.nc",
        repo_type="dataset",
        local_dir=str(output_dir),
    )
    typer.echo(f"  Saved: {path}")


def _download_gpm_nasa(output_dir: Path, start: date, end: date) -> None:
    """Download GPM IMERG V07B (EA subset) from NASA GES DISC via the fixed loader.

    Delegates to ``gpm_seasonal.download_gpm_ea`` (correct YYYY/MM path, .V07B.nc4
    filename, Earthdata OAuth-redirect auth, OPeNDAP EA subset). The previous
    inline implementation used the wrong day-of-year path + .1440.HDF5 name and a
    basic-auth opener that never followed the GES DISC redirect (returned HTML).
    """
    from gik_icechain.thresholds.gpm_seasonal import download_gpm_ea

    got = download_gpm_ea(start, end, output_dir)
    typer.echo(f"  NASA GPM (EA subset): {len(got)} files in {output_dir}")


def _download_chirps(output_dir: Path, start: date, end: date) -> None:
    """Download CHIRPS v2.0 daily Africa rainfall — no authentication required.

    CHIRPS (Climate Hazards Group InfraRed Precipitation with Station data)
    offers 0.05 degree daily rainfall for Africa.  Files are converted to
    GPM-compatible nc4 format so gpm_loader.py reads them transparently.

    Source: https://www.chc.ucsb.edu/data/chirps
    """
    import gzip
    import tempfile

    import numpy as np
    import xarray as xr

    base = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/africa_daily/tifs/p05"

    output_dir.mkdir(parents=True, exist_ok=True)
    current, n_downloaded, n_skipped, n_failed = start, 0, 0, 0

    while current <= end:
        date_str = current.strftime("%Y%m%d")
        out_path = output_dir / f"3B-DAY.MS.MRG.3IMERG.{date_str}.V07B.nc4"

        if out_path.exists():
            n_skipped += 1
            current += timedelta(days=1)
            continue

        tif_name = f"chirps-v2.0.{current.year}.{current.month:02d}.{current.day:02d}.tif.gz"
        url = f"{base}/{current.year}/{tif_name}"

        tmp_gz_path = Path(tempfile.mktemp(suffix=".tif.gz"))
        # Remove ".gz" to get ".tif" — avoid double-extension on Windows
        tmp_tif = tmp_gz_path.parent / tmp_gz_path.name[:-3]
        try:
            with _urlopen(url, timeout=60) as resp:
                tmp_gz_path.write_bytes(resp.read())

            with gzip.open(tmp_gz_path, "rb") as gz_in:
                tmp_tif.write_bytes(gz_in.read())
            tmp_gz_path.unlink(missing_ok=True)

            # Use context manager so rasterio releases the file handle on exit
            with xr.open_dataset(tmp_tif, engine="rasterio") as ds_raw:
                precip = ds_raw["band_data"].isel(band=0).drop_vars("band", errors="ignore")

                # rasterio uses 'y'/'x'; CHIRPS uses 'lat'/'lon' — normalise both
                lat_name = next((c for c in precip.dims if "lat" in c.lower() or c == "y"), None)
                lon_name = next((c for c in precip.dims if "lon" in c.lower() or c == "x"), None)
                rename = {}
                if lat_name and lat_name != "lat":
                    rename[lat_name] = "lat"
                if lon_name and lon_name != "lon":
                    rename[lon_name] = "lon"
                if rename:
                    precip = precip.rename(rename)

                nodata = float(ds_raw["band_data"].attrs.get("_FillValue", -9999.0))
                precip = precip.where(precip != nodata, other=np.nan).clip(min=0.0)

                xr.Dataset(
                    {"precipitationCal": precip.astype(np.float32)},
                    attrs={"source": "CHIRPS v2.0", "units": "mm/day"},
                ).to_netcdf(out_path)

            tmp_tif.unlink(missing_ok=True)
            typer.echo(f"  {date_str}: {out_path.stat().st_size // 1024} KB")
            n_downloaded += 1

        except Exception as exc:
            typer.echo(f"  {date_str}: FAILED ({exc})", err=True)
            n_failed += 1
            tmp_gz_path.unlink(missing_ok=True)
            tmp_tif.unlink(missing_ok=True)

        current += timedelta(days=1)

    typer.echo(
        f"  CHIRPS: {n_downloaded} downloaded, {n_skipped} skipped"
        + (f", {n_failed} failed" if n_failed else "")
    )


def _download_enso_iod(output_dir: Path) -> None:
    """Download ENSO/IOD indices: Niño 3.4 (NOAA CPC) + DMI (NOAA PSL)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "enso_iod_index.csv"
    if dest.exists():
        typer.echo(f"  Already exists: {dest}")
        return

    nino34_url = "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"
    typer.echo(f"  Downloading Niño 3.4 anomaly from {nino34_url} ...")
    with _urlopen(nino34_url, timeout=30) as r:
        nino34_raw = r.read().decode("utf-8")

    nino34: dict[tuple[int, int], float] = {}
    for line in nino34_raw.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[0].lstrip("-").isdigit():
            try:
                year, month = int(parts[0]), int(parts[1])
                nino34[(year, month)] = float(parts[9])
            except (ValueError, IndexError):
                continue
    typer.echo(f"    {len(nino34)} monthly Niño 3.4 records")

    dmi_url = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
    typer.echo(f"  Downloading DMI from {dmi_url} ...")
    with _urlopen(dmi_url, timeout=30) as r:
        dmi_raw = r.read().decode("utf-8")

    dmi: dict[tuple[int, int], float] = {}
    _missing = {-99.99, -99.9, -999.0, -999.9}
    for line in dmi_raw.splitlines():
        parts = line.split()
        if len(parts) == 13 and parts[0].lstrip("-").isdigit():
            try:
                year = int(parts[0])
                for m in range(1, 13):
                    val = float(parts[m])
                    if val not in _missing and abs(val) < 90:
                        dmi[(year, m)] = val
            except (ValueError, IndexError):
                continue
    typer.echo(f"    {len(dmi)} monthly DMI records")

    common = sorted(set(nino34) & set(dmi))
    if not common:
        raise RuntimeError(
            "No overlapping dates between Niño 3.4 and DMI datasets. Check source URLs."
        )

    lines = ["date,nino34_anom,dmi"]
    for year, month in common:
        lines.append(f"{year}-{month:02d}-01,{nino34[(year, month)]:.2f},{dmi[(year, month)]:.3f}")

    dest.write_text("\n".join(lines) + "\n")
    typer.echo(f"  Saved {len(common)} merged monthly records: {dest}")


@app.command("download-gpm")
def download_gpm(
    start: Annotated[str, typer.Option("--start", help="First date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Last date (YYYY-MM-DD).")],
    output: Annotated[
        Path, typer.Option(help="Output directory for precipitation files.")
    ] = REPO_ROOT / "data" / "gpm_imerg",
    source: Annotated[
        str,
        typer.Option(
            help="Data source: 'nasa' (GPM IMERG, requires Earthdata account) "
            "or 'chirps' (CHIRPS v2.0, no auth required)."
        ),
    ] = "chirps",
) -> None:
    """Download daily precipitation data for C3 GPM input.

    Two sources are available:

    chirps (default, no authentication):
      CHIRPS v2.0 — 0.05 deg daily Africa rainfall, freely available.
      Files saved as GPM-compatible nc4 so gpm_loader.py reads them.

    nasa (requires free Earthdata account):
      GPM IMERG V07B Final Run — 0.1 deg global, official NASA product.
      Register at https://urs.earthdata.nasa.gov/home then set:
        export EARTHDATA_USER=<user>
        export EARTHDATA_PASSWORD=<pass>

    Example — OND 2024 wet season:
      python scripts/tools.py download-gpm --start 2024-10-01 --end 2024-10-07
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if s > e:
        typer.echo("--start must be <= --end", err=True)
        raise typer.Exit(1)
    if source not in ("nasa", "chirps"):
        typer.echo(f"Unknown source '{source}'. Use 'nasa' or 'chirps'.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Downloading precipitation ({source}): {s} -> {e}  =>  {output}")
    if source == "nasa":
        _download_gpm_nasa(output, s, e)
    else:
        _download_chirps(output, s, e)
    typer.echo("Done.")


@app.command()
def download(
    component: Annotated[
        str,
        typer.Option(help="Component to download: all, admin, thresholds, enso_iod."),
    ] = "all",
    output: Annotated[Path, typer.Option(help="Base output directory.")] = REPO_ROOT / "data",
) -> None:
    """Download reference data (admin boundaries, thresholds, ENSO/IOD).

    For GPM IMERG daily rainfall, use the dedicated ``download-gpm`` command.
    """
    typer.echo(f"Downloading: {component}  ->  {output}")

    errors: list[str] = []

    if component in ("all", "admin"):
        try:
            _download_admin_boundaries(output / "admin_boundaries")
        except Exception as exc:
            typer.echo(f"  admin download failed: {exc}", err=True)
            errors.append("admin")

    if component in ("all", "thresholds"):
        try:
            _download_cmorph_thresholds(output / "cmorph_thresholds")
        except Exception as exc:
            typer.echo(f"  thresholds download failed: {exc}", err=True)
            errors.append("thresholds")

    if component in ("all", "enso_iod"):
        try:
            _download_enso_iod(output)
        except Exception as exc:
            typer.echo(f"  enso_iod download failed: {exc}", err=True)
            errors.append("enso_iod")

    if errors:
        typer.echo(f"Done (with errors in: {', '.join(errors)}).", err=True)
        raise typer.Exit(1)
    typer.echo("Done.")


_HF_REPO = "E4DRR/virtualizarr-stores"
_HF_THRESHOLDS_ARCHIVE = "cmorph_gev_thresholds.tar.gz"


@app.command("upload-thresholds")
def upload_thresholds(
    source: Annotated[
        Path, typer.Option(help="Directory containing fitted thresholds_*.nc files.")
    ] = REPO_ROOT / "data" / "cmorph_thresholds",
    repo_id: Annotated[str, typer.Option(help="HuggingFace dataset repo ID.")] = _HF_REPO,
) -> None:
    """Pack and upload pre-fitted GEV thresholds to HuggingFace (one-time).

    Reads all ``thresholds_*.nc`` files from *source*, packs them into a
    tarball, and uploads to the HuggingFace dataset repo so that
    ``download-thresholds`` can retrieve them on any machine.

    Requires a valid ``HF_TOKEN`` environment variable with write access.
    """
    import tarfile
    import tempfile

    from huggingface_hub import HfApi

    files = sorted(source.glob("thresholds_*.nc"))
    if not files:
        typer.echo(f"No thresholds_*.nc files found in {source}", err=True)
        raise typer.Exit(1)

    archive = Path(tempfile.mktemp(suffix=".tar.gz"))
    try:
        typer.echo(f"Packing {len(files)} threshold files ...")
        with tarfile.open(archive, "w:gz") as tar:
            for f in files:
                tar.add(str(f), arcname=f.name)
        typer.echo(f"  Archive size: {archive.stat().st_size / 1024 / 1024:.1f} MB")

        typer.echo(f"Uploading to {repo_id} as {_HF_THRESHOLDS_ARCHIVE} ...")
        api = HfApi()
        api.upload_file(
            path_or_fileobj=str(archive),
            path_in_repo=_HF_THRESHOLDS_ARCHIVE,
            repo_id=repo_id,
            repo_type="dataset",
        )
        typer.echo(f"Done. {len(files)} thresholds uploaded.")
    finally:
        archive.unlink(missing_ok=True)


_DURATION_TO_HOURS: dict[str, int] = {
    "3hr": 3,
    "6hr": 6,
    "12hr": 12,
    "24hr": 24,
    "48hr": 48,
    "72hr": 72,
    "7day": 168,
}
_TARGET_RPS = [2, 5, 10, 20, 40, 100]
_SEASONS = ["MAM", "OND", "JJAS", "DJF"]
_ENSO_PHASES = ["el_nino", "neutral", "la_nina"]
_IOD_PHASES = ["positive", "neutral", "negative"]


def _classify_years_enso_iod(years: list[int], index_path: Path) -> dict[int, tuple[str, str]]:
    """Classify each year by its OND-season (Oct-Dec) ENSO & IOD phase.

    Uses the Oct-Dec mean of the Niño-3.4 anomaly and DMI for each year.
    Returns ``{year: (enso_phase, iod_phase)}`` with strings matching
    ``_ENSO_PHASES`` / ``_IOD_PHASES``; ``{}`` if the index is unavailable.
    """
    import pandas as pd

    from gik_icechain.exceedance.thresholds import classify_enso, classify_iod

    if not index_path.exists():
        return {}
    df = pd.read_csv(index_path, parse_dates=["date"])
    df["year"] = df["date"].dt.year
    ond = df[df["date"].dt.month.isin([10, 11, 12])]
    out: dict[int, tuple[str, str]] = {}
    for y in years:
        sub = ond[ond["year"] == int(y)]
        if sub.empty:
            continue
        enso = classify_enso(float(sub["nino34_anom"].mean())).value
        iod = classify_iod(float(sub["dmi"].mean())).value
        out[int(y)] = (enso, iod)
    return out


@app.command("download-thresholds")
def download_thresholds(
    output: Annotated[Path, typer.Option(help="Output directory for threshold files.")] = REPO_ROOT
    / "data"
    / "cmorph_thresholds",
) -> None:
    """Generate threshold files from cmorph_ea_return_periods.nc.

    Reads the CMORPH return-period NetCDF (downloaded by ``download
    --component thresholds``), regrids to 1-degree East Africa, and
    produces the individual ``thresholds_*.nc`` files that
    ``AdaptiveGEVThresholds.load()`` expects.

    Idempotent — skips if threshold files already exist.
    """
    import numpy as np
    import xarray as xr

    output.mkdir(parents=True, exist_ok=True)
    existing = sorted(output.glob("thresholds_*.nc"))
    if existing:
        typer.echo(f"Already exists: {len(existing)} threshold files in {output}")
        return

    src_path = output / "cmorph_ea_return_periods.nc"
    if not src_path.exists():
        typer.echo(f"Source not found: {src_path}", err=True)
        typer.echo("Run 'python scripts/tools.py download --component thresholds' first.", err=True)
        raise typer.Exit(1)

    typer.echo(f"  Opening {src_path.name} ...")
    ds = xr.open_dataset(src_path)

    # ENSO/IOD-stratified Method-of-Moments Gumbel fit on the per-year annual
    # maxima (`annual_maxima`: duration × year × lat × lon). Season is NOT
    # stratified — annual maxima conflate seasons (one max per year) — so the
    # four season files for a given (enso, iod) are identical by construction.
    # This delivers genuinely phase-varying thresholds (was 36 identical copies
    # before — Innovation 2 was cosmetic). See ISSUE-20.
    target_lat = np.arange(-14.0, 25.0, 1.0)
    target_lon = np.arange(20.0, 54.0, 1.0)
    euler = 0.5772156649015329  # Euler-Mascheroni (Gumbel mean offset)
    min_years = 6  # below this, fall back to the all-years (unstratified) fit

    years = [int(y) for y in ds["year"].values]
    year_phase = _classify_years_enso_iod(years, REPO_ROOT / "data" / "enso_iod_index.csv")
    if not year_phase:
        typer.echo(
            "  WARNING: ENSO/IOD index missing -> thresholds NOT stratified (single climatology).",
            err=True,
        )
    available = {str(d) for d in ds["duration"].values}

    n_files = 0
    for dur_label, window_h in _DURATION_TO_HOURS.items():
        if dur_label not in available:
            continue
        am = ds["annual_maxima"].sel(duration=dur_label)  # (year, lat, lon)

        for enso in _ENSO_PHASES:
            for iod in _IOD_PHASES:
                yrs = [y for y in years if year_phase.get(y) == (enso, iod)]
                if len(yrs) >= min_years:
                    sub, n_used, stratified = am.sel(year=yrs), len(yrs), 1
                else:
                    sub, n_used, stratified = am, len(years), 0  # fallback

                # Method-of-moments Gumbel: scale = std·√6/π, loc = mean − γ·scale
                scale = sub.std("year") * (np.sqrt(6.0) / np.pi)
                loc = sub.mean("year") - euler * scale

                rp_arrays: dict[str, xr.DataArray] = {}
                for rp in _TARGET_RPS:
                    y_rp = -np.log(-np.log(1.0 - 1.0 / rp))  # Gumbel reduced variate
                    rp_arrays[f"rp_{rp}y"] = (loc + scale * y_rp).interp(
                        lat=target_lat, lon=target_lon, method="linear"
                    )

                for season in _SEASONS:
                    mode_key = f"{season}_{enso}_{iod}"
                    xr.Dataset(
                        rp_arrays,
                        attrs={
                            "mode_key": mode_key,
                            "window_h": window_h,
                            "units": "mm",
                            "source": "CMORPH v1.0 annual maxima",
                            "method": (
                                "Method-of-moments Gumbel; ENSO/IOD-stratified "
                                "(OND-season phase); season NOT stratified "
                                "(annual maxima conflate seasons)"
                            ),
                            "n_years": n_used,
                            "enso_iod_stratified": stratified,
                            "description": (
                                f"Gumbel return-period thresholds for {mode_key}, "
                                f"{window_h}h accumulation"
                            ),
                        },
                    ).to_netcdf(output / f"thresholds_{mode_key}_{window_h}h.nc")
                    n_files += 1

    ds.close()
    typer.echo(f"  Generated {n_files} threshold files in {output}")


@app.command("build-thresholds-gpm")
def build_thresholds_gpm(
    start: Annotated[str, typer.Option("--start", help="First date YYYY-MM-DD.")],
    end: Annotated[str, typer.Option("--end", help="Last date YYYY-MM-DD.")],
    gpm_dir: Annotated[
        Path, typer.Option(help="Directory with GPM IMERG V07B HDF5 files.")
    ] = REPO_ROOT / "data" / "gpm_imerg",
    output: Annotated[
        Path, typer.Option(help="Output directory for threshold NetCDFs.")
    ] = REPO_ROOT / "data" / "cmorph_thresholds",
    enso_iod_csv: Annotated[Path, typer.Option(help="ENSO/IOD index CSV.")] = REPO_ROOT
    / "data"
    / "enso_iod_index.csv",
    min_years: Annotated[int, typer.Option(help="Min years per bin before fallback.")] = 6,
    seasons: Annotated[str, typer.Option(help="Comma-separated seasons.")] = "MAM,OND,JJAS,DJF",
    download: Annotated[
        bool, typer.Option(help="Download missing GPM files first (needs Earthdata).")
    ] = False,
    workers: Annotated[int, typer.Option(help="Parallel download workers.")] = 12,
) -> None:
    """Build season x ENSO x IOD Gumbel thresholds from GPM IMERG daily data.

    Sub-daily windows (3/6/12 h) are skipped (daily input). With --download,
    missing HDF5 files are fetched first (EARTHDATA_USER/PASSWORD or ~/.netrc).

    Example:
      python scripts/tools.py build-thresholds-gpm --start 2001-01-01 --end 2023-12-31
    """
    from gik_icechain.thresholds.gpm_seasonal import (
        build_seasonal_thresholds,
        download_gpm_ea,
        load_gpm_daily_ea,
    )

    s, e = date.fromisoformat(start), date.fromisoformat(end)

    if download:
        typer.echo(f"Downloading GPM IMERG {s} -> {e} ({workers} workers) ...")
        download_gpm_ea(s, e, gpm_dir, workers=workers)

    all_files = sorted(gpm_dir.glob("3B-DAY.MS.MRG.3IMERG.*.V07B.nc4"))
    files_in_range = [
        f for f in all_files if s <= date.fromisoformat(f.name.split(".")[4][:8]) <= e
    ]
    if not files_in_range:
        typer.echo(
            f"No GPM files in {gpm_dir} for {s} -> {e}.\n"
            f"Run with --download, or: python scripts/tools.py download-gpm "
            f"--source nasa --start {start} --end {end}",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Loading {len(files_in_range)} GPM files ({s} -> {e}) ...")
    daily_da = load_gpm_daily_ea(files_in_range)
    season_list = [x.strip() for x in seasons.split(",")]
    typer.echo(f"Fitting thresholds: seasons={season_list}, min_years={min_years} ...")
    written = build_seasonal_thresholds(
        daily_da=daily_da,
        enso_iod_csv=enso_iod_csv,
        output_dir=output,
        min_years=min_years,
        seasons=season_list,
    )
    typer.echo(f"Wrote {len(written)} threshold files to {output}")


_GAP_START = date(2023, 5, 1)
_GAP_END = date(2024, 2, 29)


def _already_committed(store_uri: str, config_path: Path) -> set[str]:
    """Return forecast dates already committed to the IceChunk store."""
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.shared.config import load_config

    cfg = load_config(config_path if config_path.exists() else None)
    uri = store_uri or cfg.outputs.icechunk_store_uri
    store = IceChainStore(uri)
    try:
        store.create_or_open()
        return {s["forecast_date"] for s in store.list_snapshots() if s["forecast_date"]}
    except Exception:
        return set()


@app.command("gap-fill")
def gap_fill(
    start: Annotated[
        str, typer.Option("--start", help="First date (YYYY-MM-DD).")
    ] = _GAP_START.isoformat(),
    end: Annotated[
        str, typer.Option("--end", help="Last date (YYYY-MM-DD).")
    ] = _GAP_END.isoformat(),
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path(
        "configs/default.yaml"
    ),
    store: Annotated[
        str | None,
        typer.Option("--store", help="Override IceChunk store URI."),
    ] = None,
    batch_size: Annotated[int, typer.Option(help="Days per batch (resume-safe).")] = 7,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print dates without ingesting.")
    ] = False,
) -> None:
    """Back-fill the GIK archive gap into the IceChunk store."""
    from gik_icechain.cli import _bootstrap, _run_convert

    s = date.fromisoformat(start)
    e = date.fromisoformat(end)

    if s > e:
        typer.echo("--start must be before --end", err=True)
        raise typer.Exit(1)

    committed = _already_committed(store or "", config)
    missing = []
    current = s
    while current <= e:
        if current.isoformat() not in committed:
            missing.append(current)
        current += timedelta(days=1)

    if not missing:
        typer.echo(f"No missing dates in [{s}, {e}]. Store is up to date.")
        return

    typer.echo(f"Missing dates: {len(missing)} (of {(e - s).days + 1} total)")
    typer.echo(f"Already committed: {len(committed)} dates")

    if dry_run:
        for d in missing[:20]:
            typer.echo(f"  {d.isoformat()}")
        if len(missing) > 20:
            typer.echo(f"  ... and {len(missing) - 20} more")
        return

    cfg = _bootstrap(config)
    if store:
        cfg.outputs.icechunk_store_uri = store

    processed = 0
    idx = 0
    while idx < len(missing):
        batch = missing[idx : idx + batch_size]
        batch_s, batch_e = batch[0], batch[-1]
        typer.echo(f"Ingesting {batch_s} -> {batch_e} ({len(batch)} days) ...")
        try:
            _run_convert(cfg, batch_s, batch_e)
            processed += len(batch)
        except Exception as exc:
            typer.echo(f"Batch failed ({batch_s} -> {batch_e}): {exc}", err=True)
            typer.echo("Resuming from next batch ...")
        idx += batch_size

    typer.echo(f"\nGap-fill complete. Processed {processed} / {len(missing)} dates.")


@app.command("export-eahw")
def export_eahw(
    risk_dir: Annotated[
        Path,
        typer.Option(
            help="Directory containing risk_scores.json files and admin1_boundaries.geojson."
        ),
    ],
    output: Annotated[Path, typer.Option(help="Output directory for EAHW GeoJSON files.")],
) -> None:
    """Export daily risk scores to East Africa Hazard Watch Portal format.

    Combines the shared ``admin1_boundaries.geojson`` with each
    ``{date}_risk_scores.json`` file to produce one EAHW GeoJSON per day.
    """
    from gik_icechain.risk.geojson_writer import export_eahw_format

    boundaries_path = risk_dir / "admin1_boundaries.geojson"
    if not boundaries_path.exists():
        typer.echo(f"admin1_boundaries.geojson not found in: {risk_dir}", err=True)
        raise typer.Exit(1)

    scores_files = sorted(risk_dir.glob("*_risk_scores.json"))
    if not scores_files:
        typer.echo(f"No *_risk_scores.json files found in: {risk_dir}", err=True)
        raise typer.Exit(1)

    output.mkdir(parents=True, exist_ok=True)
    exported = 0
    for f in scores_files:
        date_str = f.stem[:10]
        out_path = output / f"eahw_{date_str}.geojson"
        export_eahw_format(f, boundaries_path, out_path)
        exported += 1

    typer.echo(f"Exported {exported} files to {output}")


#  validate-emdat


def _in_range(date_str: str, start: str | None, end: str | None) -> bool:
    return not ((start and date_str < start) or (end and date_str > end))


def _load_risk_results(risk_dir: Path, start: str | None = None, end: str | None = None):
    """Load per-day risk score files (optionally date-filtered) into a DataFrame."""
    import pandas as pd

    rows: list[dict] = []
    for scores_path in sorted(risk_dir.glob("*_risk_scores.json")):
        data = json.loads(scores_path.read_text())
        date_str = data.get("date", scores_path.stem[:10])
        if not _in_range(date_str, start, end):
            continue
        for pcode, score in data.get("units", {}).items():
            rows.append(
                {
                    "date": date_str,
                    "admin1_pcode": pcode,
                    "risk_state": int(score.get("risk_state", 0)),
                    "p_red": float(score.get("p_red", 0.0)),
                }
            )
    if not rows:
        raise ValueError(f"No *_risk_scores.json files found in {risk_dir}")
    return pd.DataFrame(rows)


def _contiguous_runs(days: list[str], flag: list[bool]) -> list[tuple[int, int]]:
    """Index spans of maximal True runs over calendar-adjacent days."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(days)
    while i < n:
        if not flag[i]:
            i += 1
            continue
        j = i
        while (
            j + 1 < n
            and flag[j + 1]
            and (date.fromisoformat(days[j + 1]) - date.fromisoformat(days[j])).days == 1
        ):
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def _event_level_metrics(
    risk_dir: Path, lead_days: int, start: str | None = None, end: str | None = None
) -> dict[str, dict]:
    """Event-level early detection: collapse contiguous emdat_flood_match runs
    per unit into ONE event, detected if the model fires >=threshold on any day
    in [onset - lead_days, end]. Uses the pre-joined emdat_flood_match label
    (avoids the EM-DAT-pcode-namespace mismatch). FAR at model-fired-run level.
    """
    levels = {"Yellow": 1, "Orange": 2, "Red": 3}
    by_pc: dict[str, list[dict]] = {}
    for scores_path in sorted(risk_dir.glob("*_risk_scores.json")):
        data = json.loads(scores_path.read_text())
        date_str = data.get("date", scores_path.stem[:10])
        if not _in_range(date_str, start, end):
            continue
        for pcode, score in data.get("units", {}).items():
            if score.get("risk_label") == "No_Data" or int(score.get("risk_state", -1)) < 0:
                continue

            by_pc.setdefault(pcode, []).append(
                {
                    "date": date_str,
                    "state": int(score.get("risk_state", 0)),
                    "label": 1 if score.get("emdat_flood_match") else 0,
                }
            )
    out: dict[str, dict] = {}
    for name, thr in levels.items():
        n_events = detected = n_fired = fp = 0
        for rs in by_pc.values():
            rs.sort(key=lambda r: r["date"])
            days = [r["date"] for r in rs]
            ev_runs = _contiguous_runs(days, [r["label"] == 1 for r in rs])
            fired_runs = _contiguous_runs(days, [r["state"] >= thr for r in rs])
            windows = [
                (
                    date.fromisoformat(days[a]) - timedelta(days=lead_days),
                    date.fromisoformat(days[b]),
                )
                for a, b in ev_runs
            ]
            n_events += len(ev_runs)
            for w0, w1 in windows:
                detected += int(
                    any(w0 <= date.fromisoformat(r["date"]) <= w1 and r["state"] >= thr for r in rs)
                )
            n_fired += len(fired_runs)
            for a, b in fired_runs:
                fr0, fr1 = date.fromisoformat(days[a]), date.fromisoformat(days[b])
                if not any(not (fr1 < w0 or fr0 > w1) for w0, w1 in windows):
                    fp += 1
        out[name] = {
            "n_events": n_events,
            "detected": detected,
            "recall": round(detected / n_events, 4) if n_events else 0.0,
            "false_alarm_runs": fp,
            "precision": round((n_fired - fp) / n_fired, 4) if n_fired else 0.0,
        }
    return out


@app.command("validate-emdat")
def validate_emdat(
    risk_dir: Annotated[
        Path, typer.Option(help="Directory with per-day GeoJSON risk files.")
    ] = Path("results/admin1_risk/"),
    emdat_csv: Annotated[Path, typer.Option(help="EM-DAT flood CSV (from emdat.be).")] = Path(
        "data/emdat/east_africa_floods.csv"
    ),
    output: Annotated[Path, typer.Option(help="Output CSV for per-event hit/miss table.")] = Path(
        "results/validation/emdat_validation.csv"
    ),
    risk_threshold: Annotated[
        int,
        typer.Option(help="Min risk_state for prediction (default 2=Orange)."),
    ] = 2,
    event_level: Annotated[
        bool,
        typer.Option("--event-level", help="Also score early-warning detection per EM-DAT event."),
    ] = False,
    lead_days: Annotated[
        int,
        typer.Option(help="Credit a hit up to N days before event onset (event-level only)."),
    ] = 1,
    start: Annotated[str | None, typer.Option(help="First date YYYY-MM-DD (inclusive).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date YYYY-MM-DD (inclusive).")] = None,
) -> None:
    """Validate CRMA risk outputs against EM-DAT historical flood events."""
    from gik_icechain.risk.cpt_refinement import (
        load_emdat_east_africa,
        run_validation,
    )

    if not emdat_csv.exists():
        typer.echo(f"EM-DAT CSV not found: {emdat_csv}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading EM-DAT records from {emdat_csv} ...")
    emdat_records = load_emdat_east_africa(emdat_csv)
    typer.echo(f"  {len(emdat_records)} flood events loaded.")

    typer.echo(f"Loading CRMA risk results from {risk_dir} ...")
    risk_df = _load_risk_results(risk_dir, start, end)
    typer.echo(f"  {len(risk_df)} day x unit records loaded.")

    typer.echo("Running validation ...")
    metrics = run_validation(
        risk_results_df=risk_df,
        emdat_records=emdat_records,
        output_path=output,
        risk_threshold=risk_threshold,
    )

    typer.echo(f"\n{'Metric':<22} {'Value':>8}")
    typer.echo("-" * 32)
    for k, v in metrics.items():
        typer.echo(f"  {k:<20} {v:>8.4f}")

    typer.echo(f"\nPer-event table saved to {output}")

    if event_level:
        ev = _event_level_metrics(risk_dir, lead_days, start, end)
        typer.echo(
            f"\nEvent-level early detection (lead={lead_days}d) — "
            "contiguous EM-DAT runs per unit collapsed to one event:"
        )
        typer.echo(
            f"{'level':<8} {'recall':>8} {'precision':>10} {'detected':>10} {'falseAlarm':>11}"
        )
        for name, m in ev.items():
            typer.echo(
                f"{name:<8} {m['recall']:>8.3f} {m['precision']:>10.3f} "
                f"{m['detected']:>6}/{m['n_events']:<3} {m['false_alarm_runs']:>11}"
            )


@app.command("calibrate-cost-loss")
def calibrate_cost_loss_cmd(
    risk_dir: Annotated[
        Path, typer.Option(help="Directory with per-day risk_scores.json files.")
    ] = Path("results/admin1_risk/"),
    emdat_csv: Annotated[Path, typer.Option(help="EM-DAT flood CSV (from emdat.be).")] = Path(
        "data/emdat/east_africa_floods.csv"
    ),
    output: Annotated[
        Path | None,
        typer.Option(help="Optional JSON path for the calibrated CostLossConfig + report."),
    ] = None,
    alpha_yellow: Annotated[float, typer.Option(help="Cost-loss ratio C/L for Yellow.")] = 0.10,
    alpha_orange: Annotated[float, typer.Option(help="Cost-loss ratio C/L for Orange.")] = 0.20,
    alpha_red: Annotated[float, typer.Option(help="Cost-loss ratio C/L for Red.")] = 0.30,
    start: Annotated[str | None, typer.Option(help="First date YYYY-MM-DD (inclusive).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date YYYY-MM-DD (inclusive).")] = None,
) -> None:
    """Calibrate cost-loss thresholds (tau) by maximising Relative Economic Value.

    Learns tau_yellow/orange/red from EM-DAT so the cost-loss trigger captures the
    most economic value at each tier's anticipatory-action cost-loss ratio. The
    result is advisory: review it, then set component3.crma_model.cost_loss in
    config (enabled=true) to put it live.
    """
    from gik_icechain.risk.cost_loss_calibration import calibrate_from_risk_dir
    from gik_icechain.risk.cpt_refinement import load_emdat_east_africa

    if not emdat_csv.exists():
        typer.echo(f"EM-DAT CSV not found: {emdat_csv}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading EM-DAT records from {emdat_csv} ...")
    emdat_records = load_emdat_east_africa(emdat_csv)
    typer.echo(f"  {len(emdat_records)} flood events loaded.")

    alphas = {"yellow": alpha_yellow, "orange": alpha_orange, "red": alpha_red}
    cfg, report = calibrate_from_risk_dir(risk_dir, emdat_records, start, end, alphas)

    typer.echo(
        f"\nCalibrated on {report['n_unit_days']} unit-days "
        f"({report['n_positives']} flood-positive, base rate {report['base_rate']:.4f})."
    )
    typer.echo(f"\n{'tier':<8} {'alpha':>6} {'tau':>6} {'REV':>8} {'hit':>6} {'falseAlarm':>11}")
    typer.echo("-" * 48)
    for tier in ("yellow", "orange", "red"):
        t = report["tiers"][tier]
        typer.echo(
            f"{tier:<8} {t['alpha']:>6.2f} {t['tau']:>6.2f} {t['rev']:>8.3f} "
            f"{t['hit_rate']:>6.2f} {t['false_alarm_rate']:>11.3f}"
        )
    if report["ordering_clamped"]:
        typer.echo("  (taus clamped to be non-decreasing for the ordering constraint)")
    typer.echo(
        f"\nConfig: cost_loss.enabled=true tau_yellow={cfg.tau_yellow:.2f} "
        f"tau_orange={cfg.tau_orange:.2f} tau_red={cfg.tau_red:.2f}"
    )

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"cost_loss": cfg.model_dump(), "report": report}, indent=2))
        typer.echo(f"\nWritten to {output}")


@app.command("skill-vs-lead")
def skill_vs_lead(
    risk_dir: Annotated[
        Path, typer.Option(help="Directory with per-day risk_scores.json files.")
    ] = Path("results/admin1_risk/"),
    emdat_csv: Annotated[Path, typer.Option(help="EM-DAT flood CSV (from emdat.be).")] = Path(
        "data/emdat/east_africa_floods.csv"
    ),
    max_lead: Annotated[int, typer.Option(help="Maximum lead time in days.")] = 7,
    output: Annotated[
        Path | None, typer.Option(help="Optional JSON path for the lead-time curve.")
    ] = None,
    start: Annotated[str | None, typer.Option(help="First date YYYY-MM-DD (inclusive).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date YYYY-MM-DD (inclusive).")] = None,
) -> None:
    """As-of-date early-warning skill vs forecast lead time, against EM-DAT.

    For each EM-DAT flood onset, reads the risk signal from the forecast issued L
    days earlier and reports recall@tier and mean trigger probability per lead —
    "how many days ahead could we have acted?". Honest because every daily batch
    is an IceChunk snapshot (no future leakage).
    """
    from gik_icechain.risk.cpt_refinement import load_emdat_east_africa
    from gik_icechain.risk.lead_time_skill import lead_time_skill_from_risk_dir

    if not emdat_csv.exists():
        typer.echo(f"EM-DAT CSV not found: {emdat_csv}", err=True)
        raise typer.Exit(1)

    emdat_records = load_emdat_east_africa(emdat_csv)
    typer.echo(f"Loaded {len(emdat_records)} EM-DAT flood events.")
    curve = lead_time_skill_from_risk_dir(
        risk_dir, emdat_records, max_lead=max_lead, start=start, end=end
    )

    typer.echo(
        f"\n{'lead':>4} {'n':>4} {'rec_Y':>7} {'rec_O':>7} {'rec_R':>7} "
        f"{'p>=Y':>6} {'p>=O':>6} {'p>=R':>6}"
    )
    typer.echo("-" * 56)
    for lead in sorted(curve):
        e = curve[lead]
        typer.echo(
            f"{lead:>4} {int(e['n']):>4} "
            f"{e['recall_yellow']:>7.3f} {e['recall_orange']:>7.3f} {e['recall_red']:>7.3f} "
            f"{e['mean_p_yellow']:>6.3f} {e['mean_p_orange']:>6.3f} {e['mean_p_red']:>6.3f}"
        )

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(curve, indent=2))
        typer.echo(f"\nWritten to {output}")


# --------------------------------------------------------------------------- #
# EM-DAT download (ground truth)                                              #
# --------------------------------------------------------------------------- #

# EM-DAT new-classification keys for the flood family (doc.emdat.be).
_EMDAT_FLOOD_CLASSIF = [
    "nat-hyd-flo-flo",  # Flood (general)
    "nat-hyd-flo-fla",  # Flash flood
    "nat-hyd-flo-riv",  # Riverine flood
    "nat-hyd-flo-coa",  # Coastal flood
]

_EMDAT_API_URL = "https://api.emdat.be/v1"


def _load_dotenv(repo_root: Path) -> None:
    """Populate os.environ from repo .env (KEY=VALUE, no export), if present."""
    import os

    env = repo_root / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _emdat_graphql(query: str, api_key: str, timeout: int = 60) -> dict:
    """POST a GraphQL query to the EM-DAT API with the Authorization key."""
    import ssl
    import urllib.error
    import urllib.request

    ctx = None
    try:
        import certifi

        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass

    body = json.dumps({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        _EMDAT_API_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": api_key},
        method="POST",
    )
    try:
        raw = urllib.request.urlopen(request, timeout=timeout, context=ctx).read()
    except urllib.error.HTTPError as exc:
        # EM-DAT returns 500 with a JSON {"errors":[...]} body for a bad/expired
        # key; surface that message instead of a raw HTTPError traceback.
        detail = exc.read().decode("utf-8", "replace")
        try:
            errs = json.loads(detail).get("errors", [])
            msgs = "; ".join(e.get("message", str(e)) for e in errs) or detail
        except json.JSONDecodeError:
            msgs = detail
        raise RuntimeError(f"EM-DAT API HTTP {exc.code}: {msgs}") from None

    payload = json.loads(raw)
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RuntimeError(f"EM-DAT API error: {msgs}")
    return payload["data"]


def _emdat_admin1_rows(rec: dict) -> list[dict]:
    """Flatten one EM-DAT record into per-admin1 CSV rows (pipeline schema).

    ``admin_units`` is a JSON list of {adm1_code, adm1_name} and/or
    {adm2_code, adm2_name} dicts. We key on the pipeline's unit format
    ``ISO_<adm1 name>`` so rows join directly to the risk output. Records with
    no admin1 detail yield a single country-level row (empty Admin1).
    """
    iso = rec.get("iso") or ""
    country = rec.get("country") or ""

    def _num(v: object) -> int | None:
        # Tolerate floats/NaN/strings coming from either the API or an xlsx.
        try:
            if v is None or v != v:  # NaN
                return None
            return int(float(v))
        except (TypeError, ValueError):
            return None

    sy, sm, sd = (
        _num(rec.get("start_year")),
        _num(rec.get("start_month")),
        _num(rec.get("start_day")),
    )
    ey, em, ed = _num(rec.get("end_year")), _num(rec.get("end_month")), _num(rec.get("end_day"))
    # EM-DAT leaves month/day blank when unknown. Bound the event with sane
    # defaults so the validator's date-overlap stays monotonic (end >= start).
    start_iso = f"{sy:04d}-{sm or 1:02d}-{sd or 1:02d}" if sy else ""
    ey = ey or sy
    end_iso = f"{ey:04d}-{(em or sm or 12):02d}-{(ed or 28):02d}" if ey else start_iso

    base = {
        "DisNo.": rec.get("disno") or "",
        "Disaster Type": "Flood",
        "ISO": iso,
        "Country": country,
        "Start Date": start_iso,
        "End Date": end_iso,
        "Total Deaths": _num(rec.get("total_deaths"))
        if _num(rec.get("total_deaths")) is not None
        else "",
        "No. Affected": _num(rec.get("no_affected"))
        if _num(rec.get("no_affected")) is not None
        else "",
    }

    units = rec.get("admin_units")
    if isinstance(units, str):
        try:
            units = json.loads(units)
        except (json.JSONDecodeError, TypeError):
            units = None

    adm1_names: list[str] = []
    if isinstance(units, list):
        for u in units:
            if isinstance(u, dict) and u.get("adm1_name"):
                name = str(u["adm1_name"]).strip()
                if name and name not in adm1_names:
                    adm1_names.append(name)

    if not adm1_names:
        return [{**base, "Admin1": "", "Admin1 Code": ""}]
    return [{**base, "Admin1": n, "Admin1 Code": f"{iso}_{n}"} for n in adm1_names]


@app.command("download-emdat")
def download_emdat(
    from_year: Annotated[int, typer.Option("--from-year", help="First disaster year.")] = 2015,
    to_year: Annotated[int, typer.Option("--to-year", help="Last disaster year.")] = 2024,
    output: Annotated[Path, typer.Option(help="Output CSV (pipeline schema).")] = Path(
        "data/emdat/east_africa_floods.csv"
    ),
    merge: Annotated[
        bool,
        typer.Option(
            "--merge/--overwrite",
            help="Merge with existing CSV (dedupe by DisNo.+Admin1 Code) vs replace it.",
        ),
    ] = True,
) -> None:
    """Download EM-DAT flood ground truth for East Africa via the GraphQL API.

    Needs an EM-DAT API key (Account > API key on public.emdat.be), read from
    the EMDAT_API_KEY environment variable or the repo .env. The portal
    email/password is NOT the API key.
    """
    import csv
    import os

    from gik_icechain.shared.regions import EAST_AFRICA_COUNTRIES_ISO3

    _load_dotenv(REPO_ROOT)
    api_key = os.environ.get("EMDAT_API_KEY", "").strip()
    if not api_key:
        typer.echo(
            "ERROR: EMDAT_API_KEY not set. Get it from public.emdat.be "
            "(Account > API key) and add EMDAT_API_KEY=<key> to .env.",
            err=True,
        )
        raise typer.Exit(1)

    iso_list = sorted(EAST_AFRICA_COUNTRIES_ISO3)
    iso_json = json.dumps(iso_list)
    classif_json = json.dumps(_EMDAT_FLOOD_CLASSIF)

    cols = [
        "DisNo.",
        "Disaster Type",
        "ISO",
        "Country",
        "Start Date",
        "End Date",
        "Total Deaths",
        "No. Affected",
        "Admin1",
        "Admin1 Code",
    ]
    fetched: dict[tuple[str, str], dict] = {}
    cursor: str | None = None
    n_records = 0

    typer.echo(
        f"Downloading EM-DAT floods {from_year}-{to_year} for {len(iso_list)} EA countries ..."
    )
    while True:
        cur_lit = "null" if cursor is None else json.dumps(cursor)
        query = f"""
        query {{
          public_emdat(
            cursor: {cur_lit}, limit: 100,
            filters: {{from: {from_year}, to: {to_year}, classif: {classif_json}, iso: {iso_json}}}
          ) {{
            total_available
            info {{ cursor }}
            data {{
              disno type iso country
              start_year start_month start_day
              end_year end_month end_day
              total_deaths no_affected admin_units
            }}
          }}
        }}"""
        try:
            block = _emdat_graphql(query, api_key)["public_emdat"]
        except RuntimeError as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(1) from None
        data = block.get("data") or []
        for rec in data:
            n_records += 1
            for row in _emdat_admin1_rows(rec):
                fetched[(row["DisNo."], row["Admin1 Code"])] = row
        cursor = (block.get("info") or {}).get("cursor")
        typer.echo(f"  fetched {n_records}/{block.get('total_available')} events ...")
        if not cursor or not data:
            break

    rows = dict(fetched)
    if merge and output.exists():
        with output.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                key = (r.get("DisNo.", ""), r.get("Admin1 Code", ""))
                rows.setdefault(key, {c: r.get(c, "") for c in cols})

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for _, r in sorted(rows.items()):
            w.writerow({c: r.get(c, "") for c in cols})

    typer.echo(f"\n{n_records} EM-DAT events -> {len(fetched)} admin1 rows.")
    typer.echo(f"Wrote {len(rows)} total rows to {output} (merge={merge}).")


@app.command("clean-emdat-xlsx")
def clean_emdat_xlsx(
    xlsx: Annotated[Path, typer.Argument(help="EM-DAT public portal .xlsx export.")],
    output: Annotated[Path, typer.Option(help="Output CSV (pipeline schema).")] = Path(
        "data/emdat/east_africa_floods.csv"
    ),
    sheet: Annotated[str, typer.Option(help="Worksheet holding the records.")] = "EM-DAT Data",
    from_year: Annotated[
        int, typer.Option("--from-year", help="Keep events from this year on.")
    ] = 0,
    merge: Annotated[
        bool,
        typer.Option(
            "--merge/--overwrite",
            help="Merge with existing CSV (dedupe by DisNo.+Admin1 Code) vs replace it.",
        ),
    ] = False,
) -> None:
    """Clean a manual EM-DAT xlsx export into the pipeline's flood CSV.

    Same output schema and admin1 flattening as ``download-emdat`` (which the
    free portal can't reach without an API account). Keeps Disaster Type=Flood
    over the East-Africa ISO set and flattens ``Admin Units`` to per-admin1 rows.
    """
    import csv

    import pandas as pd

    from gik_icechain.shared.regions import EAST_AFRICA_COUNTRIES_ISO3

    if not xlsx.exists():
        typer.echo(f"xlsx not found: {xlsx}", err=True)
        raise typer.Exit(1)

    df = pd.read_excel(xlsx, sheet_name=sheet)
    ea = sorted(EAST_AFRICA_COUNTRIES_ISO3)
    mask = (df["Disaster Type"] == "Flood") & (df["ISO"].isin(ea))
    if from_year:
        mask &= df["Start Year"] >= from_year
    fl = df[mask]
    typer.echo(f"{len(fl)} EA flood events in {xlsx.name} (from {from_year or 'all'}).")

    cols = [
        "DisNo.",
        "Disaster Type",
        "ISO",
        "Country",
        "Start Date",
        "End Date",
        "Total Deaths",
        "No. Affected",
        "Admin1",
        "Admin1 Code",
    ]
    rows: dict[tuple[str, str], dict] = {}
    for _, r in fl.iterrows():
        rec = {
            "disno": r["DisNo."],
            "iso": r["ISO"],
            "country": r["Country"],
            "start_year": r["Start Year"],
            "start_month": r["Start Month"],
            "start_day": r["Start Day"],
            "end_year": r["End Year"],
            "end_month": r["End Month"],
            "end_day": r["End Day"],
            "total_deaths": r["Total Deaths"],
            "no_affected": r["No. Affected"],
            "admin_units": None if pd.isna(r["Admin Units"]) else r["Admin Units"],
        }
        for row in _emdat_admin1_rows(rec):
            rows[(row["DisNo."], row["Admin1 Code"])] = row

    n_admin1 = sum(1 for k in rows if k[1])
    if merge and output.exists():
        with output.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                key = (r.get("DisNo.", ""), r.get("Admin1 Code", ""))
                rows.setdefault(key, {c: r.get(c, "") for c in cols})

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for _, r in sorted(rows.items()):
            w.writerow({c: r.get(c, "") for c in cols})

    typer.echo(f"Wrote {len(rows)} rows ({n_admin1} with admin1) to {output} (merge={merge}).")


if __name__ == "__main__":
    app()
