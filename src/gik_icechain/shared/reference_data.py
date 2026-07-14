"""Reference data the pipeline needs before C2/C3 can run.

Admin-1 boundaries, GEV return-period thresholds and the ENSO/IOD index are
fetched from public sources and cached under ``data/``. Every ``ensure_*``
function is idempotent: present -> no-op, missing -> download. ``run-all`` calls
:func:`ensure_reference_data` at startup so a fresh checkout runs end to end with
no manual prep; ``scripts/tools.py`` exposes the same functions as commands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

import structlog

if TYPE_CHECKING:
    from gik_icechain.shared.config import GIKConfig

log = structlog.get_logger(__name__)

# In sync with shared.regions.EAST_AFRICA_COUNTRIES_ISO3.
_EA_COUNTRIES = [
    "KEN", "ETH", "UGA", "TZA", "SOM", "RWA", "BDI", "SSD",
    "ERI", "DJI", "MDG", "SDN", "COM", "SYC", "MWI", "ZMB",
]

_CMORPH_HF_REPO = "E4DRR/virtualizarr-stores"
_CMORPH_RAW_FILE = "cmorph_ea_return_periods.nc"
_NINO34_URL = "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"
_DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"

_DURATION_TO_HOURS: dict[str, int] = {
    "3hr": 3, "6hr": 6, "12hr": 12, "24hr": 24, "48hr": 48, "72hr": 72, "7day": 168,
}
_TARGET_RPS = [2, 5, 10, 20, 40, 100]
_SEASONS = ["MAM", "OND", "JJAS", "DJF"]
_ENSO_PHASES = ["el_nino", "neutral", "la_nina"]
_IOD_PHASES = ["positive", "neutral", "negative"]
_EULER = 0.5772156649015329  # Euler-Mascheroni, Gumbel mean offset
_MIN_YEARS_PER_BIN = 6


def _urlopen(url: str, timeout: int):
    return urlopen(Request(url, headers={"User-Agent": "gik-icechain/2.0"}), timeout=timeout)


def ensure_admin_boundaries(path: Path) -> Path:
    """Admin-1 boundary GeoJSON (geoBoundaries gbOpen). Returns the file path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    log.info("admin_boundaries_download_start", n_countries=len(_EA_COUNTRIES))
    features: list[dict] = []
    for iso in _EA_COUNTRIES:
        try:
            with _urlopen(f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/ADM1/", 15) as r:
                meta = json.loads(r.read())
            dl_url = meta.get("gjDownloadURL", "")
            if not dl_url:
                log.warning("admin_no_download_url", iso=iso)
                continue
            with _urlopen(dl_url, 30) as r:
                fc = json.loads(r.read())
            for feat in fc.get("features", []):
                feat["properties"]["admin1_pcode"] = (
                    iso + "_" + str(feat["properties"].get("shapeName", ""))[:20]
                )
                features.append(feat)
        except Exception as exc:
            log.warning("admin_country_skipped", iso=iso, error=str(exc)[:120])

    if not features:
        raise RuntimeError("No admin-1 features downloaded from geoBoundaries")
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    log.info("admin_boundaries_saved", n_units=len(features), path=str(path))
    return path


def ensure_enso_iod(path: Path) -> Path:
    """ENSO/IOD monthly index CSV (Niño 3.4 from NOAA CPC + DMI from NOAA PSL)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    log.info("enso_iod_download_start")
    with _urlopen(_NINO34_URL, 30) as r:
        nino34_raw = r.read().decode("utf-8")
    nino34: dict[tuple[int, int], float] = {}
    for line in nino34_raw.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[0].lstrip("-").isdigit():
            try:
                nino34[(int(parts[0]), int(parts[1]))] = float(parts[9])
            except (ValueError, IndexError):
                continue

    with _urlopen(_DMI_URL, 30) as r:
        dmi_raw = r.read().decode("utf-8")
    dmi: dict[tuple[int, int], float] = {}
    missing = {-99.99, -99.9, -999.0, -999.9}
    for line in dmi_raw.splitlines():
        parts = line.split()
        if len(parts) == 13 and parts[0].lstrip("-").isdigit():
            try:
                year = int(parts[0])
                for m in range(1, 13):
                    val = float(parts[m])
                    if val not in missing and abs(val) < 90:
                        dmi[(year, m)] = val
            except (ValueError, IndexError):
                continue

    common = sorted(set(nino34) & set(dmi))
    if not common:
        raise RuntimeError("No overlapping dates between Niño 3.4 and DMI datasets")
    lines = ["date,nino34_anom,dmi"]
    for year, month in common:
        lines.append(f"{year}-{month:02d}-01,{nino34[(year, month)]:.2f},{dmi[(year, month)]:.3f}")
    path.write_text("\n".join(lines) + "\n")
    log.info("enso_iod_saved", n_records=len(common), path=str(path))
    return path


def _classify_years_enso_iod(years: list[int], index_path: Path) -> dict[int, tuple[str, str]]:
    """Map each year to its OND-season (Oct-Dec) ENSO & IOD phase; {} if no index."""
    if not index_path.exists():
        return {}
    import pandas as pd

    from gik_icechain.exceedance.thresholds import (
        SEASON_MONTHS,
        Season,
        classify_enso,
        classify_iod,
    )

    df = pd.read_csv(index_path, parse_dates=["date"])
    df["year"] = df["date"].dt.year
    ond = df[df["date"].dt.month.isin(SEASON_MONTHS[Season.OND])]
    out: dict[int, tuple[str, str]] = {}
    for y in years:
        sub = ond[ond["year"] == int(y)]
        if sub.empty:
            continue
        enso = classify_enso(float(sub["nino34_anom"].mean())).value
        iod = classify_iod(float(sub["dmi"].mean())).value
        out[int(y)] = (enso, iod)
    return out


def ensure_gev_thresholds(directory: Path, enso_iod_csv: Path) -> Path:
    """Per-mode GEV threshold NetCDFs the C2 loader reads.

    Downloads the raw CMORPH return-period NetCDF if absent, then regrids it to
    1-degree East Africa as ``thresholds_{mode}_{window}h.nc`` (ENSO/IOD-stratified
    Method-of-moments Gumbel). Returns the directory.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if sorted(directory.glob("thresholds_*.nc")):
        return directory

    import numpy as np
    import xarray as xr

    raw = directory / _CMORPH_RAW_FILE
    if not raw.exists():
        log.info("cmorph_raw_download_start", repo=_CMORPH_HF_REPO)
        from huggingface_hub import hf_hub_download

        hf_hub_download(
            repo_id=_CMORPH_HF_REPO, filename=_CMORPH_RAW_FILE,
            repo_type="dataset", local_dir=str(directory),
        )

    log.info("gev_thresholds_regrid_start", source=raw.name)
    ds = xr.open_dataset(raw)
    target_lat = np.arange(-14.0, 25.0, 1.0)
    target_lon = np.arange(20.0, 54.0, 1.0)
    years = [int(y) for y in ds["year"].values]
    year_phase = _classify_years_enso_iod(years, enso_iod_csv)
    if not year_phase:
        log.warning("gev_thresholds_unstratified", reason="enso_iod index missing")
    available = {str(d) for d in ds["duration"].values}

    n_files = 0
    for dur_label, window_h in _DURATION_TO_HOURS.items():
        if dur_label not in available:
            continue
        am = ds["annual_maxima"].sel(duration=dur_label)  # (year, lat, lon)
        for enso in _ENSO_PHASES:
            for iod in _IOD_PHASES:
                yrs = [y for y in years if year_phase.get(y) == (enso, iod)]
                if len(yrs) >= _MIN_YEARS_PER_BIN:
                    sub, n_used, stratified = am.sel(year=yrs), len(yrs), 1
                else:
                    sub, n_used, stratified = am, len(years), 0
                scale = sub.std("year") * (np.sqrt(6.0) / np.pi)
                loc = sub.mean("year") - _EULER * scale
                rp_arrays = {
                    f"rp_{rp}y": (loc + scale * (-np.log(-np.log(1.0 - 1.0 / rp)))).interp(
                        lat=target_lat, lon=target_lon, method="linear"
                    )
                    for rp in _TARGET_RPS
                }
                for season in _SEASONS:
                    mode_key = f"{season}_{enso}_{iod}"
                    xr.Dataset(
                        rp_arrays,
                        attrs={
                            "mode_key": mode_key, "window_h": window_h, "units": "mm",
                            "source": "CMORPH v1.0 annual maxima",
                            "n_years": n_used, "enso_iod_stratified": stratified,
                        },
                    ).to_netcdf(directory / f"thresholds_{mode_key}_{window_h}h.nc")
                    n_files += 1
    ds.close()
    log.info("gev_thresholds_saved", n_files=n_files, directory=str(directory))
    return directory


def ensure_reference_data(cfg: GIKConfig) -> None:
    """Fetch admin boundaries, ENSO/IOD index and GEV thresholds if any are missing."""
    enso_csv = Path(cfg.component2.thresholds.enso_iod_index_path)
    ensure_admin_boundaries(Path(cfg.sources.admin_boundaries_path))
    ensure_enso_iod(enso_csv)
    ensure_gev_thresholds(Path(cfg.component2.thresholds.cmorph_path), enso_csv)
