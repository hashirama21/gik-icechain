"""Adaptive GEV return-period thresholds for East Africa precipitation extremes.

Fits separate GEV distributions per grid cell, stratified by:
  (a) Rainfall season  (MAM long rains | OND short rains | JJAS | DJF)
  (b) ENSO phase       (El Niño | Neutral | La Niña)
  (c) IOD phase        (Positive | Neutral | Negative)

At inference time the threshold matching the current phase is selected,
reducing false-alarm rates in dry regimes and improving detection in wet
regimes (Finney et al., 2020; White et al., 2021; Nana et al., 2025).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import structlog
import xarray as xr
from scipy.stats import genextreme

log = structlog.get_logger(__name__)

RETURN_PERIODS = [2, 5, 10, 20, 40, 100]
ACCUMULATION_WINDOWS_H = [3, 6, 12, 24, 48, 72, 168]


class Season(str, Enum):
    """East Africa rainfall seasons."""

    MAM  = "MAM"   # March–April–May (long rains)
    OND  = "OND"   # October–November–December (short rains)
    JJAS = "JJAS"  # June–July–August–September
    DJF  = "DJF"   # December–January–February


class ENSOPhase(str, Enum):
    EL_NINO = "el_nino"
    NEUTRAL = "neutral"
    LA_NINA = "la_nina"


class IODPhase(str, Enum):
    POSITIVE = "positive"
    NEUTRAL  = "neutral"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class ClimateMode:
    """Current climate mode used to select the appropriate GEV threshold."""

    season:     Season
    enso_phase: ENSOPhase
    iod_phase:  IODPhase

    @property
    def key(self) -> str:
        return f"{self.season.value}_{self.enso_phase.value}_{self.iod_phase.value}"


_SEASON_MONTHS: dict[Season, list[int]] = {
    Season.MAM:  [3, 4, 5],
    Season.OND:  [10, 11, 12],
    Season.JJAS: [6, 7, 8, 9],
    Season.DJF:  [12, 1, 2],
}


def get_season(month: int) -> Season:
    """Map a calendar month (1–12) to an East Africa rainfall season."""
    for season, months in _SEASON_MONTHS.items():
        if month in months:
            return season
    raise ValueError(f"Invalid month: {month}")


def classify_enso(nino34_anomaly: float, threshold: float = 0.5) -> ENSOPhase:
    """Classify ENSO phase from Niño 3.4 SST anomaly (°C), WMO ±0.5°C default."""
    if nino34_anomaly >= threshold:
        return ENSOPhase.EL_NINO
    if nino34_anomaly <= -threshold:
        return ENSOPhase.LA_NINA
    return ENSOPhase.NEUTRAL


def classify_iod(dmi_anomaly: float, threshold: float = 0.4) -> IODPhase:
    """Classify IOD phase from DMI anomaly (°C), NOAA ±0.4°C default."""
    if dmi_anomaly >= threshold:
        return IODPhase.POSITIVE
    if dmi_anomaly <= -threshold:
        return IODPhase.NEGATIVE
    return IODPhase.NEUTRAL


class AdaptiveGEVThresholds:
    """GEV return-period thresholds stratified by season and climate mode.

    Usage::

        thresholds = AdaptiveGEVThresholds.from_cmorph(cmorph_ds, enso_iod_index)
        thresholds.save("data/cmorph_thresholds/")

        mode = ClimateMode(Season.MAM, ENSOPhase.EL_NINO, IODPhase.POSITIVE)
        thresh = thresholds.get(window_h=24, return_period=5, mode=mode)
    """

    def __init__(self) -> None:
        self._thresholds: dict[str, dict[int, dict[int, xr.DataArray]]] = {}

    @classmethod
    def from_cmorph(
        cls,
        cmorph_ds: xr.Dataset,
        enso_iod_index: pd.DataFrame,
        windows_h: list[int] = ACCUMULATION_WINDOWS_H,
        return_periods: list[int] = RETURN_PERIODS,
    ) -> "AdaptiveGEVThresholds":
        """Fit GEV distributions to CMORPH climatology, stratified by climate mode.

        Args:
            cmorph_ds:      xr.Dataset with CMORPH 30-min precipitation
                            (dimensions: time, lat, lon).
            enso_iod_index: DataFrame with columns [date, nino34, dmi].
            windows_h:      Accumulation windows in hours.
            return_periods: Return periods in years.
        """
        instance = cls()
        log.info("fitting_gev_thresholds", n_windows=len(windows_h), n_rps=len(return_periods))

        enso_iod_daily = enso_iod_index.set_index("date")

        for season in Season:
            for enso_phase in ENSOPhase:
                for iod_phase in IODPhase:
                    mode = ClimateMode(season, enso_phase, iod_phase)
                    time_mask = cls._build_time_mask(
                        cmorph_ds.time, season, enso_phase, iod_phase, enso_iod_daily
                    )
                    n_samples = int(time_mask.sum())
                    if n_samples < 30:
                        log.debug("insufficient_samples", mode=mode.key, n=n_samples)
                        continue

                    instance._thresholds[mode.key] = {}
                    for window_h in windows_h:
                        n_steps = window_h * 2  # CMORPH is 30-min resolution
                        accumulated = (
                            cmorph_ds["precip"]
                            .rolling(time=n_steps, min_periods=n_steps)
                            .sum()
                            .sel(time=time_mask)
                        )
                        instance._thresholds[mode.key][window_h] = cls._fit_gev_gridded(
                            accumulated, return_periods, mode.key, window_h
                        )

        log.info("gev_thresholds_fitted", n_modes=len(instance._thresholds))
        return instance

    @classmethod
    def load(cls, directory: Path) -> "AdaptiveGEVThresholds":
        """Load pre-computed thresholds from a directory of NetCDF files."""
        instance = cls()
        threshold_files = sorted(directory.glob("thresholds_*.nc"))
        if not threshold_files:
            raise FileNotFoundError(f"No threshold files found in {directory}")

        for f in threshold_files:
            ds = xr.open_dataset(f)
            mode_key = ds.attrs["mode_key"]
            window_h = int(ds.attrs["window_h"])
            instance._thresholds.setdefault(mode_key, {})[window_h] = {
                rp: ds[f"rp_{rp}y"] for rp in RETURN_PERIODS if f"rp_{rp}y" in ds
            }

        log.info("thresholds_loaded", directory=str(directory), n_files=len(threshold_files))
        return instance

    def save(self, directory: Path) -> None:
        """Save fitted thresholds to NetCDF files (one per mode × window)."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        for mode_key, windows in self._thresholds.items():
            for window_h, rp_dict in windows.items():
                ds = xr.Dataset(
                    {f"rp_{rp}y": da for rp, da in rp_dict.items()},
                    attrs={
                        "mode_key":    mode_key,
                        "window_h":    window_h,
                        "units":       "mm",
                        "source":      "CMORPH v1.0 + GEV fit",
                        "description": (
                            f"GEV return-period thresholds for {mode_key}, "
                            f"{window_h}h accumulation"
                        ),
                    },
                )
                ds.to_netcdf(directory / f"thresholds_{mode_key}_{window_h}h.nc")

        log.info("thresholds_saved", directory=str(directory))

    def get(
        self,
        window_h: int,
        return_period: int,
        mode: ClimateMode,
    ) -> xr.DataArray:
        """Return the (lat × lon) threshold DataArray for a given mode.

        Falls back to the neutral ENSO + neutral IOD mode for the same season
        if the exact mode has insufficient historical samples.

        Args:
            window_h:      Accumulation window in hours.
            return_period: Return period in years.
            mode:          Current climate mode.

        Returns:
            xr.DataArray (lat × lon) of threshold values in mm.
        """
        def _lookup(key: str) -> xr.DataArray | None:
            windows = self._thresholds.get(key, {})
            rp_dict = windows.get(window_h, {})
            return rp_dict.get(return_period)

        result = _lookup(mode.key)
        if result is not None:
            return result

        fallback = ClimateMode(mode.season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        result = _lookup(fallback.key)
        if result is not None:
            log.debug("threshold_fallback", requested=mode.key, using=fallback.key)
            return result

        raise KeyError(
            f"No threshold for mode={mode.key}, window={window_h}h, "
            f"return_period={return_period}y."
        )

    @staticmethod
    def _build_time_mask(
        time_index: xr.DataArray,
        season: Season,
        enso_phase: ENSOPhase,
        iod_phase: IODPhase,
        enso_iod_daily: pd.DataFrame,
    ) -> xr.DataArray:
        time_pd = pd.DatetimeIndex(time_index.values)
        month_mask = np.zeros(len(time_pd), dtype=bool)
        for m in _SEASON_MONTHS[season]:
            month_mask |= time_pd.month == m

        climate_mask = np.zeros(len(time_pd), dtype=bool)
        for i, t in enumerate(time_pd):
            try:
                row = enso_iod_daily.loc[t.date()]
                climate_mask[i] = (
                    classify_enso(float(row["nino34"])) == enso_phase
                    and classify_iod(float(row["dmi"])) == iod_phase
                )
            except (KeyError, IndexError):
                pass

        return xr.DataArray(month_mask & climate_mask, coords={"time": time_index}, dims="time")

    @staticmethod
    def _fit_gev_gridded(
        accumulated: xr.DataArray,
        return_periods: list[int],
        mode_key: str,
        window_h: int,
    ) -> dict[int, xr.DataArray]:
        """Fit GEV per grid cell and return a dict of RP → threshold DataArray."""

        def fit_cell(data: np.ndarray) -> np.ndarray:
            valid = data[np.isfinite(data) & (data > 0)]
            if len(valid) < 20:
                return np.full(len(return_periods), np.nan)
            try:
                c, loc, scale = genextreme.fit(valid, f0=0)  # f0=0 constrains to Gumbel
                # P(X > x) = 1/T  →  x = Q(1 − 1/T)
                return genextreme.ppf(
                    [1.0 - 1.0 / rp for rp in return_periods], c, loc, scale
                )
            except Exception:
                return np.full(len(return_periods), np.nan)

        all_thresholds = xr.apply_ufunc(
            fit_cell,
            accumulated,
            input_core_dims=[["time"]],
            output_core_dims=[["return_period"]],
            output_dtypes=[float],
            vectorize=True,
            dask="parallelized",
            dask_gufunc_kwargs={"output_sizes": {"return_period": len(return_periods)}},
        )

        result: dict[int, xr.DataArray] = {}
        for i, rp in enumerate(return_periods):
            da = all_thresholds.isel(return_period=i).rename(f"threshold_rp{rp}y_{window_h}h")
            da.attrs = {
                "units":         "mm",
                "window_h":      window_h,
                "return_period": rp,
                "mode_key":      mode_key,
                "distribution":  "GEV (Gumbel, c=0)",
                "source":        "CMORPH v1.0 climatology",
            }
            result[rp] = da

        return result
