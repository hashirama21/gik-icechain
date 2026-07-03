"""Unit tests for season x ENSO x IOD Gumbel thresholds (GPM seasonal builder).

Uses synthetic daily precipitation (no GPM download) to verify that seasonal
block-maxima stratification produces distinct thresholds per season - the core
ISSUE-20 fix (OND short rains must not be averaged with MAM long rains).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds
from gik_icechain.thresholds.gpm_seasonal import (
    build_seasonal_thresholds,
    classify_enso_iod,
    compute_seasonal_maxima,
)


def _synthetic_daily(ond_scale: float = 30.0, mam_scale: float = 10.0) -> xr.DataArray:
    """Daily precip over 2000-2022 with OND systematically wetter than MAM."""
    rng = np.random.default_rng(42)
    times: list[pd.Timestamp] = []
    vals: list[np.ndarray] = []
    for year in range(2000, 2023):
        for months, scale in (([3, 4, 5], mam_scale), ([10, 11], ond_scale)):
            for m in months:
                days = pd.date_range(f"{year}-{m:02d}-01", periods=28, freq="D")
                for d in days:
                    times.append(d)
                    vals.append(rng.exponential(scale, size=(2, 2)).astype(np.float32))
    arr = np.stack(vals, axis=0)
    return xr.DataArray(
        arr,
        dims=["time", "lat", "lon"],
        coords={"time": pd.DatetimeIndex(times), "lat": [0.0, 1.0], "lon": [0.0, 1.0]},
    )


def _neutral_index_csv(tmp_path) -> object:
    rows = [{"year": y, "enso_phase": "neutral", "iod_phase": "neutral"} for y in range(2000, 2023)]
    p = tmp_path / "enso_iod_phase.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_seasonal_maxima_shape_and_count():
    da = _synthetic_daily()
    sm = compute_seasonal_maxima(da, accumulation_h=24, season="OND")
    assert "year" in sm.dims
    assert sm.sizes["year"] == 23  # 2000..2022
    assert sm.sizes["lat"] == 2 and sm.sizes["lon"] == 2


def test_subdaily_window_skipped():
    da = _synthetic_daily()
    sm = compute_seasonal_maxima(da, accumulation_h=6, season="OND")
    assert sm.sizes.get("year", 0) == 0  # sub-daily on daily input -> empty


def test_classify_enso_iod_per_year_csv(tmp_path):
    p = _neutral_index_csv(tmp_path)
    phases = classify_enso_iod(p)
    assert len(phases["enso"]["neutral"]) == 23
    assert len(phases["enso"]["el_nino"]) == 0


def test_ond_threshold_exceeds_mam(tmp_path):
    """OND (wet) thresholds must be higher than MAM (dry) - seasonal stratification."""
    da = _synthetic_daily(ond_scale=30.0, mam_scale=10.0)
    idx = _neutral_index_csv(tmp_path)
    written = build_seasonal_thresholds(
        da, idx, tmp_path, return_periods=[5, 100], windows_h=[24], seasons=["MAM", "OND"]
    )
    assert len(written) == 2 * 3 * 3  # 2 seasons x 3 enso x 3 iod

    ond = xr.open_dataset(tmp_path / "thresholds_OND_neutral_neutral_24h.nc")
    mam = xr.open_dataset(tmp_path / "thresholds_MAM_neutral_neutral_24h.nc")
    assert float(ond["rp_5y"].mean()) > float(mam["rp_5y"].mean())
    # neutral bin has all 23 years -> genuinely stratified
    assert ond.attrs["enso_iod_stratified"] == 1
    assert ond.attrs["mode_key"] == "OND_neutral_neutral"


def test_loader_reads_gpm_thresholds(tmp_path):
    """Files must be loadable by the production AdaptiveGEVThresholds.load()."""
    from gik_icechain.exceedance.thresholds import ClimateMode, ENSOPhase, IODPhase, Season

    da = _synthetic_daily()
    idx = _neutral_index_csv(tmp_path)
    build_seasonal_thresholds(
        da, idx, tmp_path, return_periods=[5], windows_h=[24], seasons=["OND"]
    )
    thr = AdaptiveGEVThresholds.load(tmp_path)
    mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
    out = thr.get(24, 5, mode)
    assert float(out.mean()) > 0
