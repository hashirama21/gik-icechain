"""Season x ENSO x IOD stratified Gumbel return-period thresholds from GPM IMERG.

Computes return-period thresholds from GPM IMERG V07B Final Run daily data
(NASA GES DISC), fitting a method-of-moments Gumbel per grid cell on the
per-(season, year) block maxima, stratified by ENSO and IOD phase. This fixes
ISSUE-20: ``annual_maxima`` conflate MAM and OND (one max per year), which
biases OND thresholds low and MAM high. Seasonal block maxima keep them apart.

Output files are compatible with ``AdaptiveGEVThresholds.load()`` (one NetCDF
per ``thresholds_{mode}_{window}h.nc`` with ``rp_{rp}y`` variables and the
``mode_key`` / ``window_h`` attributes the loader reads).

Usage (CLI):
    python scripts/tools.py build-thresholds-gpm \
        --start 2001-01-01 --end 2023-12-31 --output data/cmorph_thresholds/

Usage (Python):
    from gik_icechain.thresholds.gpm_seasonal import (
        load_gpm_daily_ea, build_seasonal_thresholds,
    )
"""

from __future__ import annotations

import netrc
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import xarray as xr

from gik_icechain.exceedance.thresholds import SEASON_MONTHS

log = structlog.get_logger(__name__)

# East Africa bounding box
EA_LAT_MIN, EA_LAT_MAX = -12.0, 23.0
EA_LON_MIN, EA_LON_MAX = 22.0, 52.0

RETURN_PERIODS = [2, 5, 10, 20, 40, 100]
WINDOWS_H = [3, 6, 12, 24, 48, 72, 168]

# Min years per (season, phase) bin before falling back to the all-years fit.
MIN_YEARS_PER_BIN = 6

_EULER = 0.5772156649015329
GPM_BASE = "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGDF.07"
GPM_OPENDAP = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07"
_PRECIP_VARS = ("precipitation", "precipitationCal")

# IMERG 0.1deg grid origin: lat[j] = -89.95 + 0.1*j, lon[i] = -179.95 + 0.1*i.
_GRID_LAT0, _GRID_LON0, _GRID_STEP = -89.95, -179.95, 0.1


def _ea_index_ranges() -> tuple[int, int, int, int]:
    """(lon0, lon1, lat0, lat1) index bounds of the EA bbox on the IMERG grid."""
    la0 = round((EA_LAT_MIN - _GRID_LAT0) / _GRID_STEP)
    la1 = round((EA_LAT_MAX - _GRID_LAT0) / _GRID_STEP)
    lo0 = round((EA_LON_MIN - _GRID_LON0) / _GRID_STEP)
    lo1 = round((EA_LON_MAX - _GRID_LON0) / _GRID_STEP)
    return lo0, lo1, la0, la1


def _creds_from_file() -> tuple[str | None, str | None]:
    """Read Earthdata creds from a gitignored Python file.

    Looks at ``$EARTHDATA_CREDENTIALS_FILE`` then ``data/earthdata_credentials.py``
    (``data/`` is gitignored) then ``earthdata_credentials.py``. The file must
    define ``EARTHDATA_USER`` and ``EARTHDATA_PASSWORD`` string variables.
    """
    import importlib.util

    candidates = [
        os.environ.get("EARTHDATA_CREDENTIALS_FILE"),
        "data/earthdata_credentials.py",
        "earthdata_credentials.py",
    ]
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if not p.exists():
            continue
        spec = importlib.util.spec_from_file_location("_earthdata_creds", p)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        u = getattr(mod, "EARTHDATA_USER", None)
        pw = getattr(mod, "EARTHDATA_PASSWORD", None)
        if u and pw and not str(u).startswith("YOUR_"):
            return str(u), str(pw)
    return None, None


def _get_credentials(user: str | None, password: str | None) -> tuple[str, str]:
    """Resolve Earthdata credentials from args, a creds file, env, or ~/.netrc."""
    user = user or os.environ.get("EARTHDATA_USER")
    password = password or os.environ.get("EARTHDATA_PASSWORD")
    if not (user and password):
        user, password = _creds_from_file()
    if not (user and password):
        try:
            auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
            if auth:
                user, _, password = auth
        except (FileNotFoundError, netrc.NetrcParseError):
            pass
    if not (user and password):
        raise ValueError(
            "NASA Earthdata credentials not found. Provide them via any of:\n"
            "  - data/earthdata_credentials.py  (EARTHDATA_USER / EARTHDATA_PASSWORD)\n"
            "  - EARTHDATA_USER / EARTHDATA_PASSWORD env vars\n"
            "  - ~/.netrc  (machine urs.earthdata.nasa.gov login <user> password <pass>)\n"
            "Register free at: https://urs.earthdata.nasa.gov/home"
        )
    return user, password


def _earthdata_session(user: str, password: str):
    """requests.Session that keeps the Authorization header across the URS redirect.

    GES DISC returns a 302 to ``urs.earthdata.nasa.gov`` (OAuth) rather than a
    401 challenge, so plain basic-auth never fires. The official NASA recipe
    re-attaches credentials on the URS host and strips them on other hosts.
    """
    import requests

    auth_host = "urs.earthdata.nasa.gov"

    class _Session(requests.Session):
        def rebuild_auth(self, prepared_request, response):
            headers = prepared_request.headers
            if "Authorization" in headers:
                orig = requests.utils.urlparse(response.request.url).hostname
                redir = requests.utils.urlparse(prepared_request.url).hostname
                if orig != redir and redir != auth_host and orig != auth_host:
                    del headers["Authorization"]

    s = _Session()
    s.auth = (user, password)
    return s


_HDF5_MAGIC = b"\x89HDF"


def _gpm_url(day: date, fname: str, subset_ea: bool, ranges: tuple[int, int, int, int]):
    """Return (url, params) for a single GPM daily file (OPeNDAP subset or full)."""
    if subset_ea:
        lo0, lo1, la0, la1 = ranges
        url = f"{GPM_OPENDAP}/{day.year}/{day.month:02d}/{fname}.dap.nc4"
        params = {
            "dap4.ce": (
                f"/precipitation[0][{lo0}:{lo1}][{la0}:{la1}];"
                f"/lat[{la0}:{la1}];/lon[{lo0}:{lo1}];/time[0]"
            )
        }
        return url, params
    return f"{GPM_BASE}/{day.year}/{day.month:02d}/{fname}", None


def download_gpm_ea(
    start: date,
    end: date,
    output_dir: Path,
    earthdata_user: str | None = None,
    earthdata_password: str | None = None,
    force: bool = False,
    subset_ea: bool = True,
    workers: int = 12,
) -> list[Path]:
    """Download GPM IMERG V07B daily ``.nc4`` files for [start, end] in parallel.

    With *subset_ea* (default), fetches only the East Africa window via OPeNDAP
    DAP4 (~150 KB/file vs ~30 MB global). Runs *workers* concurrent requests
    (OPeNDAP is I/O-bound). Saved as
    ``3B-DAY.MS.MRG.3IMERG.YYYYMMDD-S000000-E235959.V07B.nc4``; skips existing
    files unless *force* (so the download is resumable). Validates HDF5 magic
    bytes - a returned HTML page means the Earthdata "NASA GESDISC DATA ARCHIVE"
    app is not authorised (Earthdata profile -> Applications -> Authorized Apps).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import local

    user, pwd = _get_credentials(earthdata_user, earthdata_password)
    output_dir.mkdir(parents=True, exist_ok=True)
    ranges = _ea_index_ranges()

    # Enumerate the dates still to fetch (resumable: skip existing).
    todo: list[tuple[date, Path]] = []
    done: list[Path] = []
    current = start
    while current <= end:
        fname = f"3B-DAY.MS.MRG.3IMERG.{current.strftime('%Y%m%d')}-S000000-E235959.V07B.nc4"
        out = output_dir / fname
        if out.exists() and not force:
            done.append(out)
        else:
            todo.append((current, out))
        current += timedelta(days=1)

    # One requests.Session per worker thread (cookies/redirect auth are per-session).
    _tls = local()

    def _get_session():
        s = getattr(_tls, "session", None)
        if s is None:
            s = _earthdata_session(user, pwd)
            _tls.session = s
        return s

    counts = {"dl": 0, "fail": 0, "html": 0}

    def _fetch(item: tuple[date, Path]) -> Path | None:
        import time

        day, out = item
        url, params = _gpm_url(day, out.name, subset_ea, ranges)
        last = ""
        # GES DISC throttles concurrent OPeNDAP requests -> retry with backoff.
        for attempt in range(4):
            try:
                resp = _get_session().get(url, params=params, timeout=180)
                resp.raise_for_status()
                content = resp.content
                if content[:4] != _HDF5_MAGIC:
                    counts["html"] += 1
                    raise ValueError("response is not HDF5 (auth/HTML page)")
                out.write_bytes(content)
                counts["dl"] += 1
                if counts["dl"] % 200 == 0:
                    log.info("gpm_download_progress", downloaded=counts["dl"], remaining=len(todo))
                return out
            except Exception as exc:
                last = str(exc)[:100]
                time.sleep(2.0 * (attempt + 1))
        counts["fail"] += 1
        log.warning("gpm_download_failed", date=day.isoformat(), error=last)
        return None

    log.info("gpm_download_start", to_fetch=len(todo), already=len(done), workers=workers)
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed(pool.submit(_fetch, it) for it in todo):
                got = fut.result()
                if got is not None:
                    done.append(got)

    log.info("gpm_download_done", downloaded=counts["dl"], failed=counts["fail"], total=len(done))
    if counts["dl"] == 0 and counts["html"] > 0:
        raise RuntimeError(
            "GES DISC returned HTML (not HDF5). Authorise the 'NASA GESDISC DATA "
            "ARCHIVE' app at https://urs.earthdata.nasa.gov/profile -> Applications "
            "-> Authorized Apps, then retry."
        )
    return done

def load_gpm_daily_ea(paths: list[Path]) -> xr.DataArray:
    """Load GPM IMERG V07B daily ``.nc4`` files, subset to East Africa (mm/day).

    3IMERGDF V07 stores variables at the ROOT group: ``precipitation``
    (time, lon, lat) plus 1-D ``lat``/``lon``.

    UNIT: verified against real files, ``precipitation`` units = ``mm/day``
    (NOT a rate) - magnitudes max ~338, p99 ~31 mm/day. The conversion is
    units-aware: only an ``mm/hr`` source is scaled by 24. (An earlier x24
    assumption would have inflated thresholds ~24x - same class as the tp
    m/mm bug, ISSUE-14.)
    """
    arrays: list[xr.DataArray] = []
    for path in sorted(paths):
        try:
            with xr.open_dataset(path, engine="netcdf4") as ds:
                var = next((v for v in _PRECIP_VARS if v in ds.data_vars), None)
                if var is None:
                    log.warning("gpm_no_precip_var", path=str(path), vars=list(ds.data_vars)[:4])
                    continue
                da = ds[var].load()

            units = str(da.attrs.get("units", "mm/day")).lower()
            scale = 24.0 if units in ("mm/hr", "mm/h", "mm hr-1") else 1.0
            if "time" in da.dims:
                da = da.isel(time=0, drop=True)
            da = da.transpose("lat", "lon")
            da = da.where(da >= 0) * scale
            ts = pd.Timestamp(path.name.split(".")[4][:8])
            da = (
                da.assign_coords(time=ts)
                .expand_dims("time")
                .sel(lat=slice(EA_LAT_MIN, EA_LAT_MAX), lon=slice(EA_LON_MIN, EA_LON_MAX))
                .astype(np.float32)
            )
            arrays.append(da)
        except Exception as exc:
            log.warning("gpm_load_failed", path=str(path), error=str(exc)[:120])

    if not arrays:
        raise ValueError("No GPM files loaded successfully.")

    da_all = xr.concat(arrays, dim="time").sortby("time")
    log.info("gpm_loaded", n_files=len(arrays), shape=str(tuple(da_all.shape)))
    return da_all

def compute_seasonal_maxima(
    daily_da: xr.DataArray,
    accumulation_h: int,
    season: str,
    pool_seasons: bool = False,
) -> xr.DataArray:
    """Per-(season, year) block maxima of the *accumulation_h* rolling sum.

    Daily input -> windows must be multiples of 24 h; sub-daily windows need
    hourly data and yield an empty result (caller skips them).
    Returns dims (year, lat, lon).

    With ``pool_seasons``, every calendar month feeds the block maxima whatever
    *season* is - i.e. annual maxima. Used to build the unstratified baseline
    the adaptive thresholds are ablated against (see build_seasonal_thresholds).
    """
    win_days = accumulation_h // 24
    empty = xr.DataArray(
        np.full((0, daily_da.sizes["lat"], daily_da.sizes["lon"]), np.nan, dtype=np.float32),
        dims=["year", "lat", "lon"],
        coords={"lat": daily_da["lat"], "lon": daily_da["lon"]},
    )
    if win_days < 1:
        log.warning("sub_daily_window_skipped", window_h=accumulation_h)
        return empty

    acc = daily_da.rolling(time=win_days, min_periods=win_days).sum()
    months = list(range(1, 13)) if pool_seasons else SEASON_MONTHS[season]
    mask = np.isin(pd.DatetimeIndex(acc["time"].values).month, months)
    acc_season = acc.isel(time=mask)
    if acc_season.sizes["time"] == 0:
        log.warning("season_empty", season=season, window_h=accumulation_h)
        return empty

    season_years = pd.DatetimeIndex(acc_season["time"].values).year
    return acc_season.assign_coords(year=("time", season_years)).groupby("year").max("time")


def classify_enso_iod(enso_iod_csv: Path) -> dict[str, dict[str, np.ndarray]]:
    """Return {axis: {phase: years}} from the ENSO/IOD index CSV.

    Accepts either a per-year phase CSV (columns ``year, enso_phase,
    iod_phase``) or the monthly anomaly CSV (columns ``date, nino34_anom, dmi``),
    in which case each year is classified from its OND-season (Oct-Dec) means.
    """
    from gik_icechain.exceedance.thresholds import classify_enso, classify_iod

    df = pd.read_csv(enso_iod_csv)
    if "enso_phase" not in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        nino_col = "nino34" if "nino34" in df.columns else "nino34_anom"
        ond = df[df["date"].dt.month.isin([10, 11, 12])].copy()
        ond["year"] = ond["date"].dt.year
        rows = []
        for yr, g in ond.groupby("year"):
            rows.append(
                {
                    "year": int(yr),
                    "enso_phase": classify_enso(float(g[nino_col].mean())).value,
                    "iod_phase": classify_iod(float(g["dmi"].mean())).value,
                }
            )
        df = pd.DataFrame(rows)

    df["year"] = df["year"].astype(int)
    enso_years = {p: df.loc[df["enso_phase"] == p, "year"].to_numpy() for p in
                  ("el_nino", "neutral", "la_nina")}
    iod_years = {p: df.loc[df["iod_phase"] == p, "year"].to_numpy() for p in
                 ("positive", "neutral", "negative")}
    return {"enso": enso_years, "iod": iod_years}



@dataclass
class _FitResult:
    loc: np.ndarray
    scale: np.ndarray
    n_years: int
    stratified: bool


def fit_gumbel_gridded(
    seasonal_max: xr.DataArray,
    year_subset: np.ndarray | None,
    min_years: int = MIN_YEARS_PER_BIN,
) -> _FitResult:
    """Vectorised method-of-moments Gumbel fit per grid cell.

    Fits on *year_subset* when it has >= *min_years* matching years, else falls
    back to all years. Returns per-cell (loc, scale).
    """
    data = seasonal_max.values  # (n_years, nlat, nlon)
    year_coord = seasonal_max["year"].values.astype(int)

    if year_subset is not None:
        sub_idx = np.where(np.isin(year_coord, year_subset))[0]
    else:
        sub_idx = np.array([], dtype=int)

    use_subset = len(sub_idx) >= min_years
    fit_idx = sub_idx if use_subset else np.arange(len(year_coord))
    block = data[fit_idx]  # (n, nlat, nlon)

    import warnings

    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN cells
        mu = np.nanmean(block, axis=0)
        sigma = np.nanstd(block, axis=0, ddof=1) if block.shape[0] > 1 else np.zeros(mu.shape)
    scale = sigma * (np.sqrt(6.0) / np.pi)
    loc = mu - _EULER * scale
    return _FitResult(
        loc=loc.astype(np.float32),
        scale=scale.astype(np.float32),
        n_years=len(fit_idx),
        stratified=bool(use_subset),
    )


def _gumbel_quantile(loc: np.ndarray, scale: np.ndarray, rp: int) -> np.ndarray:
    """Gumbel return level for return period *rp* (years)."""
    return loc - scale * np.log(-np.log(1.0 - 1.0 / rp))


def build_seasonal_thresholds(
    daily_da: xr.DataArray,
    enso_iod_csv: Path,
    output_dir: Path,
    return_periods: list[int] = RETURN_PERIODS,
    windows_h: list[int] = WINDOWS_H,
    min_years: int = MIN_YEARS_PER_BIN,
    seasons: list[str] | None = None,
    pool_seasons: bool = False,
) -> dict[str, Path]:
    """Write season x ENSO x IOD Gumbel threshold NetCDFs from daily precip.

    One file per (season, enso, iod, window): ``thresholds_{mode}_{w}h.nc`` with
    ``rp_{rp}y`` variables, loader-compatible (``mode_key`` / ``window_h`` attrs).
    Returns {f"{mode}_{w}h": path}.

    Ablation baselines (Innovation 2 - proposal §7.2 promises an adaptive-vs-static
    AUC-ROC comparison). All three arms write the same filenames, so the pipeline
    loads any of them unchanged - point ``component2.thresholds`` at the chosen dir:

    ==================  ==============================  ====================================
    arm                 call                            what varies per grid cell
    ==================  ==============================  ====================================
    adaptive (shipped)  defaults                        season x ENSO x IOD
    season-only         ``min_years=999``               season (ENSO/IOD bins all fall back)
    static (baseline)   ``min_years=999,                nothing - one annual-maxima fit
                        pool_seasons=True``             replicated across every mode_key
    ==================  ==============================  ====================================
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # str() so a Season enum (SEASON_MONTHS is enum-keyed) serialises cleanly to netCDF attrs.
    seasons = [str(s) for s in (seasons or SEASON_MONTHS)]
    phases = classify_enso_iod(enso_iod_csv)
    lat = daily_da["lat"].values
    lon = daily_da["lon"].values

    written: dict[str, Path] = {}
    for season in seasons:
        for window_h in windows_h:
            s_max = compute_seasonal_maxima(daily_da, window_h, season, pool_seasons=pool_seasons)
            if s_max.sizes.get("year", 0) == 0:
                continue
            year_coord = s_max["year"].values.astype(int)

            for enso in ("el_nino", "neutral", "la_nina"):
                for iod in ("positive", "neutral", "negative"):
                    mode_key = f"{season}_{enso}_{iod}"
                    subset = np.intersect1d(
                        np.intersect1d(phases["enso"][enso], phases["iod"][iod]), year_coord
                    )
                    fit = fit_gumbel_gridded(
                        s_max,
                        year_subset=subset if len(subset) >= min_years else None,
                        min_years=min_years,
                    )

                    rp_arrays = {
                        f"rp_{rp}y": xr.DataArray(
                            np.clip(_gumbel_quantile(fit.loc, fit.scale, rp), 0, None),
                            dims=["lat", "lon"],
                            coords={"lat": lat, "lon": lon},
                            attrs={"units": "mm", "return_period": rp},
                        )
                        for rp in return_periods
                    }
                    out = output_dir / f"thresholds_{mode_key}_{window_h}h.nc"
                    xr.Dataset(
                        rp_arrays,
                        attrs={
                            "mode_key": mode_key,
                            "window_h": window_h,
                            "units": "mm",
                            "source": "GPM IMERG V07B Final Run daily",
                            "method": "Gumbel (method-of-moments) on seasonal block maxima",
                            "season": season,
                            "enso_phase": enso,
                            "iod_phase": iod,
                            "n_years_fit": fit.n_years,
                            "enso_iod_stratified": int(fit.stratified),
                            "season_stratified": int(not pool_seasons),
                            "ea_bbox": f"{EA_LAT_MIN},{EA_LAT_MAX},{EA_LON_MIN},{EA_LON_MAX}",
                        },
                    ).to_netcdf(out)
                    written[f"{mode_key}_{window_h}h"] = out
                    log.info(
                        "threshold_written",
                        mode=mode_key,
                        window_h=window_h,
                        stratified=fit.stratified,
                        n_years=fit.n_years,
                    )

    log.info("thresholds_complete", n_files=len(written))
    return written
