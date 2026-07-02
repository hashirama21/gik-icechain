#!/usr/bin/env python3
"""Admin-1 SATELLITE validation of Component-3 flood risk (Nov-2024 case).

EM-DAT has not yet catalogued the Nov-2024 East-Africa floods (reporting lag),
so this script validates C3 against *satellite-observed* flood extent from two
public, no-auth sources on HDX:

  * FAO EVE Global Flood Monitoring (NOAA VIIRS) - per-country CSVs giving
    flooded area (km2) and population exposed per admin unit per 15-day period.
    Covers 8 East-African countries incl. Somalia and South Sudan, and - unlike
    activation products - reports *every* admin unit, so it yields real negatives
    (dry units), enabling an AUC / precision / recall panel.
  * UNOSAT "satellite-detected water extents" (Sentinel-1) - per-window
    admin-1 population-exposure tables; used here for South Sudan (all 10 states).

Ground truth is written to data/emdat/ in the pipeline CSV schema and the panel
metrics are printed. See README ("Live dashboard & validation").

Usage:
    python scripts/satellite_validation.py --risk-dir results/admin1_risk
"""

from __future__ import annotations

import io
import json
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

REPO_ROOT = Path(__file__).resolve().parent.parent
app = typer.Typer(add_completion=False)

# FAO EVE covers these East-African ISO3s on HDX (no ETH/KEN/UGA).
_FAO_EA = ["som", "ssd", "sdn", "tza", "bdi", "mdg", "mwi", "zmb"]
_HDX = (
    "https://data.humdata.org/api/3/action/package_show?id=fao-eve-global-flood-monitoring-system"
)
_UNOSAT_SSD_XLSX = (
    "https://unosat.org/static/unosat_filesystem/4040/"
    "UNOSAT_Population_Exposure_FL20220424SSD_23Nov_27Nov2024_SouthSudan_Week29.xlsx"
)
# FAO adm1 spellings that differ from the pipeline's geoBoundaries names.
_ALIAS = {
    ("SOM", "Middle Shabelle"): "SOM_Middle Shebelle",
    ("SOM", "Lower Shabelle"): "SOM_Lower Shebelle",
    ("SOM", "Hiraan"): "SOM_Hiiraan",
}
_FLOOD_KM2 = 50.0  # binary "flooded" threshold on VIIRS flooded area


def _ctx() -> ssl.SSLContext | None:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


def _get(url: str, timeout: int = 90) -> bytes:
    return urllib.request.urlopen(url, timeout=timeout, context=_ctx()).read()


def _pipeline_units(risk_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """{ISO3: [(admin1_name, unit_key)]} from any daily risk file."""
    sample = sorted(risk_dir.glob("*_risk_scores.json"))[
        len(list(risk_dir.glob("*_risk_scores.json"))) // 2
    ]
    units = json.loads(sample.read_text())["units"]
    by_iso: dict[str, list[tuple[str, str]]] = {}
    for key in units:
        iso, name = key.split("_", 1)
        by_iso.setdefault(iso, []).append((name, key))
    return by_iso


def _matcher(by_iso: dict[str, list[tuple[str, str]]]):
    import difflib

    def norm(s: object) -> str:
        return re.sub(r"[^a-z]", "", str(s).lower())

    def match(iso: str, name: str) -> str | None:
        if (iso, name) in _ALIAS:
            return _ALIAS[(iso, name)]
        n = norm(name)
        cands = by_iso.get(iso, [])
        for nm, key in cands:  # exact, then prefix (pipeline names truncated to 20 chars)
            if norm(nm) == n:
                return key
        for nm, key in cands:
            nn = norm(nm)
            if nn and (n.startswith(nn) or nn.startswith(n)):
                return key
        names = [norm(nm) for nm, _ in cands]
        hit = difflib.get_close_matches(n, names, n=1, cutoff=0.82)
        return cands[names.index(hit[0])][1] if hit else None

    return match


def _fao_nov_adm1() -> pd.DataFrame:
    """Nov-2024 flooded-area per admin-1 across the FAO-covered EA countries."""
    pkg = json.loads(_get(_HDX, timeout=30))["result"]
    urls = {
        r["name"].split("-")[0]: r["url"]
        for r in pkg["resources"]
        if r["name"].split("-")[0] in _FAO_EA
    }
    frames = []
    for iso, url in urls.items():
        df = pd.read_csv(io.BytesIO(_get(url)))
        frames.append(df)
        typer.echo(f"  FAO {iso}: {len(df)} rows")
    alld = pd.concat(frames, ignore_index=True)
    alld["start_date"] = pd.to_datetime(alld["start_date"])
    alld["end_date"] = pd.to_datetime(alld["end_date"])
    w0, w1 = pd.Timestamp(2024, 10, 31), pd.Timestamp(2024, 11, 30)
    nov = alld[~((alld["end_date"] < w0) | (alld["start_date"] > w1))]
    return (
        nov.groupby(["adm0_iso3", "adm1_name"])
        .agg(flood_km2=("total_area_flooded_sq_km", "sum"), pop=("pop_exposed", "sum"))
        .reset_index()
    )


def _unosat_ssd_states() -> set[str]:
    """South Sudan provinces with UNOSAT flood exposure (23-27 & 03-07 Nov 2024)."""
    raw_bytes = _get(_UNOSAT_SSD_XLSX, timeout=60)
    states: set[str] = set()
    for sheet in ("Admin_1_23-27Nov2024", "Admin_1_03-07Nov2024"):
        raw = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=sheet, header=None)
        hdr = raw.index[
            raw.apply(lambda r: r.astype(str).str.contains("Province", case=False).any(), axis=1)
        ][0]
        df = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=sheet, header=hdr)
        df.columns = [str(c) for c in df.columns]
        pc = next(c for c in df.columns if "Province" in c)
        ec = next(c for c in df.columns if "exposed" in c.lower())
        for _, r in df.iterrows():
            prov = str(r[pc]).strip()
            try:
                expo = float(r[ec])
            except (TypeError, ValueError):
                expo = 0.0
            if prov and prov.lower() not in ("province", "total", "grand total") and expo > 0:
                states.add(prov)
    return states


_COLS = [
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


@app.command()
def main(
    risk_dir: Annotated[Path, typer.Option(help="Daily C3 risk JSON dir.")] = Path(
        "results/admin1_risk"
    ),
) -> None:
    from sklearn.metrics import roc_auc_score

    by_iso = _pipeline_units(risk_dir)
    match = _matcher(by_iso)

    # ---- FAO/VIIRS panel (8 countries, real negatives) ----------------------
    fao = _fao_nov_adm1()
    fao["unit_key"] = [match(r.adm0_iso3, r.adm1_name) for r in fao.itertuples()]
    fao = fao[fao.unit_key.notna()].drop_duplicates("unit_key")

    days = [f"2024-11-{d:02d}" for d in range(1, 31)]
    daily = {d: json.loads((risk_dir / f"{d}_risk_scores.json").read_text())["units"] for d in days}
    maxpred, alertdays, maxstate = {}, {}, {}
    for key in fao.unit_key:
        pr = [daily[d][key]["p_red"] for d in days if key in daily[d]]
        maxpred[key] = max(pr) if pr else float("nan")
        alertdays[key] = sum(daily[d][key]["risk_state"] >= 2 for d in days if key in daily[d])
        maxstate[key] = max(
            (daily[d][key]["risk_state"] for d in days if key in daily[d]), default=0
        )
    fao["c3_maxpred"] = fao.unit_key.map(maxpred)
    fao["c3_alertdays"] = fao.unit_key.map(alertdays)
    fao = fao[fao.c3_maxpred.notna()].copy()
    fao["viirs_flood"] = (fao.flood_km2 >= _FLOOD_KM2).astype(int)

    y = fao.viirs_flood.values
    alert = (fao.c3_alertdays > 0).astype(int).values
    tp = int(((alert == 1) & (y == 1)).sum())
    fp = int(((alert == 1) & (y == 0)).sum())
    fn = int(((alert == 0) & (y == 1)).sum())
    auc = roc_auc_score(y, fao.c3_maxpred.values) if 0 < y.sum() < len(y) else float("nan")

    typer.echo(f"\n=== FAO/VIIRS satellite panel: {len(fao)} admin-1 units, 8 countries ===")
    typer.echo(f"  flooded (>= {_FLOOD_KM2:.0f} km2): {int(y.sum())} | dry: {int((1 - y).sum())}")
    typer.echo(f"  AUC (C3 max p_red vs VIIRS flood): {auc:.3f}")
    typer.echo(f"  Recall @ Orange+   : {tp / (tp + fn):.2f} ({tp}/{tp + fn})")
    typer.echo(f"  Precision @ Orange+: {tp / (tp + fp):.2f} ({tp}/{tp + fp})")

    som = fao[fao.adm0_iso3 == "SOM"].sort_values("flood_km2", ascending=False)
    typer.echo("\n  Somalia - real flood (VIIRS km2) vs C3 Orange+ alert-days:")
    for r in som.itertuples():
        typer.echo(
            f"    {r.adm1_name:20s} {r.flood_km2:8.0f} km2  "
            f"pop {int(r.pop):>7d}  C3 {r.c3_alertdays:2d}d"
        )

    out_fao = REPO_ROOT / "data/emdat/nov2024_satellite_fao_viirs.csv"
    rows = [
        {
            **dict.fromkeys(_COLS, ""),
            "DisNo.": f"FAO-VIIRS-{r.adm0_iso3}",
            "Disaster Type": "Flood",
            "ISO": r.adm0_iso3,
            "Country": r.adm0_iso3,
            "Start Date": "2024-11-16",
            "End Date": "2024-11-30",
            "No. Affected": int(r.pop),
            "Admin1": r.adm1_name,
            "Admin1 Code": r.unit_key,
        }
        for r in fao[fao.viirs_flood == 1].itertuples()
    ]
    pd.DataFrame(rows, columns=_COLS).to_csv(out_fao, index=False, encoding="utf-8-sig")
    typer.echo(f"\nWrote {len(rows)} FAO/VIIRS flood-positive units -> {out_fao}")

    # UNOSAT South Sudan (Sentinel-1) -> pipeline keys
    ssd_states = _unosat_ssd_states()
    ssd_rows = []
    for prov in sorted(ssd_states):
        key = match("SSD", prov)
        if not key:
            continue
        for day in [f"2024-11-{d:02d}" for d in (*range(3, 8), *range(23, 28))]:
            ssd_rows.append(
                {
                    **dict.fromkeys(_COLS, ""),
                    "DisNo.": "UNOSAT-SSD",
                    "Disaster Type": "Flood",
                    "ISO": "SSD",
                    "Country": "South Sudan",
                    "Start Date": day,
                    "End Date": day,
                    "Admin1": prov,
                    "Admin1 Code": key,
                }
            )
    out_ssd = REPO_ROOT / "data/emdat/nov2024_satellite_ssd_unosat.csv"
    pd.DataFrame(ssd_rows, columns=_COLS).to_csv(out_ssd, index=False, encoding="utf-8-sig")
    typer.echo(f"Wrote {len(ssd_rows)} UNOSAT SSD rows -> {out_ssd}")


if __name__ == "__main__":
    app()
