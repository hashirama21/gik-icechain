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

import itertools
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import xarray as xr
from scipy.optimize import minimize
from scipy.stats import genextreme

log = structlog.get_logger(__name__)

RETURN_PERIODS = [2, 5, 10, 20, 40, 100]
ACCUMULATION_WINDOWS_H = [3, 6, 12, 24, 48, 72, 168]


class Season(StrEnum):
    """East Africa rainfall seasons."""

    MAM = "MAM"  # March–April–May (long rains)
    OND = "OND"  # October–November–December (short rains)
    JJAS = "JJAS"  # June–July–August–September
    DJF = "DJF"  # December–January–February


class ENSOPhase(StrEnum):
    EL_NINO = "el_nino"
    NEUTRAL = "neutral"
    LA_NINA = "la_nina"


class IODPhase(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


@dataclass(frozen=True)
class ClimateMode:
    """Current climate mode used to select the appropriate GEV threshold."""

    season: Season
    enso_phase: ENSOPhase
    iod_phase: IODPhase

    @property
    def key(self) -> str:
        return f"{self.season.value}_{self.enso_phase.value}_{self.iod_phase.value}"


_SEASON_MONTHS: dict[Season, list[int]] = {
    Season.MAM: [3, 4, 5],
    Season.OND: [10, 11],  # December transitions to DJF
    Season.JJAS: [6, 7, 8, 9],
    Season.DJF: [12, 1, 2],
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
        self._call_count: int = 0
        self._fallback_count: int = 0

    @property
    def fallback_rate(self) -> float:
        """Fraction of get() calls that fell back to the neutral-phase threshold."""
        return self._fallback_count / self._call_count if self._call_count else 0.0

    def reset_stats(self) -> None:
        self._call_count = 0
        self._fallback_count = 0

    @classmethod
    def from_cmorph(
        cls,
        cmorph_ds: xr.Dataset,
        enso_iod_index: pd.DataFrame,
        windows_h: list[int] = ACCUMULATION_WINDOWS_H,
        return_periods: list[int] = RETURN_PERIODS,
    ) -> AdaptiveGEVThresholds:
        """Fit GEV distributions to CMORPH climatology, stratified by climate mode.

        All per-cell GEV fits are submitted as a single Dask compute graph so
        that the Dask scheduler can maximise CPU utilisation across all modes
        and windows in one pass.

        Args:
            cmorph_ds:      xr.Dataset with CMORPH 30-min precipitation
                            (dimensions: time, lat, lon).
            enso_iod_index: DataFrame with columns [date, nino34, dmi].
            windows_h:      Accumulation windows in hours.
            return_periods: Return periods in years.
        """
        import dask

        instance = cls()
        log.info("fitting_gev_thresholds", n_windows=len(windows_h), n_rps=len(return_periods))

        enso_iod_daily = enso_iod_index.set_index("date")

        # Collect lazy DataArrays — no Dask compute triggered yet
        pending: list[tuple[str, int, xr.DataArray]] = []
        for season, enso_phase, iod_phase in itertools.product(Season, ENSOPhase, IODPhase):
            mode = ClimateMode(season, enso_phase, iod_phase)
            time_mask = cls._build_time_mask(
                cmorph_ds.time, season, enso_phase, iod_phase, enso_iod_daily
            )
            n_samples = int(time_mask.sum())
            if n_samples < 30:
                log.debug("insufficient_samples", mode=mode.key, n=n_samples)
                continue

            for window_h in windows_h:
                n_steps = window_h * 2  # CMORPH is 30-min resolution
                accumulated = (
                    cmorph_ds["precip"]
                    .rolling(time=n_steps, min_periods=n_steps)
                    .sum()
                    .sel(time=time_mask)
                )
                pending.append((mode.key, window_h, cls._fit_gev_lazy(accumulated, return_periods)))

        if not pending:
            log.warning("no_valid_climate_modes", msg="All modes had insufficient samples (<30)")
            return instance

        log.info("computing_gev_thresholds", n_tasks=len(pending))
        # Single Dask scheduler call — all grid-cell GEV fits run in parallel
        computed: tuple[xr.DataArray, ...] = dask.compute(*[da for _, _, da in pending])

        for (mode_key, window_h, _), all_thresholds in zip(pending, computed, strict=True):
            nan_fraction = float(np.isnan(all_thresholds.isel(return_period=0).values).mean())
            log.info(
                "gev_fit_complete",
                mode_key=mode_key,
                window_h=window_h,
                nan_fraction_pct=round(nan_fraction * 100, 1),
            )
            if nan_fraction > 0.20:
                log.warning(
                    "high_gev_nan_rate",
                    mode_key=mode_key,
                    window_h=window_h,
                    nan_fraction_pct=round(nan_fraction * 100, 1),
                    action="check_cmorph_data_coverage",
                )

            rp_dict: dict[int, xr.DataArray] = {}
            for i, rp in enumerate(return_periods):
                da = all_thresholds.isel(return_period=i).rename(f"threshold_rp{rp}y_{window_h}h")
                da.attrs = {
                    "units": "mm",
                    "window_h": window_h,
                    "return_period": rp,
                    "mode_key": mode_key,
                    "distribution": "GEV (free xi, L-BFGS-B; Gumbel fallback)",
                    "source": "CMORPH v1.0 climatology",
                }
                rp_dict[rp] = da
            instance._thresholds.setdefault(mode_key, {})[window_h] = rp_dict

        log.info("gev_thresholds_fitted", n_modes=len(instance._thresholds))
        return instance

    @classmethod
    def load(cls, directory: Path) -> AdaptiveGEVThresholds:
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
                        "mode_key": mode_key,
                        "window_h": window_h,
                        "units": "mm",
                        "source": "CMORPH v1.0 + GEV fit",
                        "description": (
                            f"GEV return-period thresholds for {mode_key}, {window_h}h accumulation"
                        ),
                    },
                )
                ds.to_netcdf(directory / f"thresholds_{mode_key}_{window_h}h.nc")

        log.info("thresholds_saved", directory=str(directory))

    def save_zarr(self, path: str | Path) -> None:
        """Save all thresholds to a single consolidated Zarr store.

        Replaces 252 per-mode NetCDF files with a single
        (mode_key, window_h, return_period, lat, lon) Zarr array.
        """
        path = str(path)

        mode_keys = sorted(self._thresholds)
        all_windows: list[int] = sorted(
            {w for mk in mode_keys for w in self._thresholds[mk]}
        )
        all_rps: list[int] = sorted(
            {
                rp
                for mk in mode_keys
                for w in self._thresholds[mk]
                for rp in self._thresholds[mk][w]
            }
        )

        sample_da: xr.DataArray | None = None
        for mk in mode_keys:
            for w in self._thresholds[mk]:
                for _rp, da in self._thresholds[mk][w].items():
                    sample_da = da
                    break
                if sample_da is not None:
                    break
            if sample_da is not None:
                break

        if sample_da is None:
            raise ValueError("No thresholds to save")

        lat_name = next((c for c in sample_da.coords if c in ("lat", "latitude")), "lat")
        lon_name = next((c for c in sample_da.coords if c in ("lon", "longitude")), "lon")
        lat_vals = sample_da[lat_name].values
        lon_vals = sample_da[lon_name].values

        data = np.full(
            (len(mode_keys), len(all_windows), len(all_rps), len(lat_vals), len(lon_vals)),
            np.nan,
            dtype=np.float32,
        )
        for i, mk in enumerate(mode_keys):
            for j, wh in enumerate(all_windows):
                for k, rp in enumerate(all_rps):
                    da = self._thresholds.get(mk, {}).get(wh, {}).get(rp)
                    if da is not None:
                        data[i, j, k] = da.values.astype(np.float32)

        ds = xr.Dataset(
            {
                "thresholds": (
                    ["mode_key", "window_h", "return_period", lat_name, lon_name],
                    data,
                )
            },
            coords={
                "mode_key": mode_keys,
                "window_h": np.array(all_windows, dtype=np.int16),
                "return_period": np.array(all_rps, dtype=np.int16),
                lat_name: lat_vals,
                lon_name: lon_vals,
            },
            attrs={
                "title": "GIK-IceChain adaptive GEV thresholds",
                "source": "CMORPH v1.0 + GEV fit",
                "conventions": "CF-1.8",
            },
        )
        ds.to_zarr(path, mode="w", consolidated=True)
        log.info("thresholds_saved_zarr", path=path, n_modes=len(mode_keys))

    @classmethod
    def load_zarr(cls, path: str | Path) -> AdaptiveGEVThresholds:
        """Load pre-computed thresholds from a consolidated Zarr store."""
        instance = cls()
        ds = xr.open_zarr(str(path), consolidated=True)

        mode_keys: list[str] = ds["mode_key"].values.tolist()
        window_sizes: list[int] = ds["window_h"].values.tolist()
        return_periods_list: list[int] = ds["return_period"].values.tolist()

        for i, mk in enumerate(mode_keys):
            rp_dicts: dict[int, dict[int, xr.DataArray]] = {}
            for j, wh in enumerate(window_sizes):
                rp_dict: dict[int, xr.DataArray] = {}
                for k, rp in enumerate(return_periods_list):
                    da = ds["thresholds"].isel(
                        mode_key=i, window_h=j, return_period=k
                    ).drop_vars(["mode_key", "window_h", "return_period"], errors="ignore")
                    if not np.all(np.isnan(da.values)):
                        rp_dict[int(rp)] = da
                if rp_dict:
                    rp_dicts[int(wh)] = rp_dict
            if rp_dicts:
                instance._thresholds[mk] = rp_dicts

        log.info("thresholds_loaded_zarr", path=str(path), n_modes=len(instance._thresholds))
        return instance

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

        self._call_count += 1

        result = _lookup(mode.key)
        if result is not None:
            return result

        self._fallback_count += 1
        fallback = ClimateMode(mode.season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        result = _lookup(fallback.key)
        if result is not None:
            log.warning(
                "threshold_fallback_to_neutral",
                requested=mode.key,
                using=fallback.key,
                fallback_rate=f"{self.fallback_rate:.1%}",
            )
            return result

        raise KeyError(
            f"No threshold for mode={mode.key}, window={window_h}h, return_period={return_period}y."
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
    def _fit_gev_lazy(
        accumulated: xr.DataArray,
        return_periods: list[int],
    ) -> xr.DataArray:
        """Return a lazy Dask-backed DataArray of GEV quantiles per grid cell.

        Shape: (lat, lon, n_return_periods). Does not trigger Dask compute.
        """
        exceedance_probs = [1.0 - 1.0 / rp for rp in return_periods]

        def fit_cell(data: np.ndarray) -> np.ndarray:
            valid = data[np.isfinite(data) & (data > 0)]
            if len(valid) < 20:
                return np.full(len(return_periods), np.nan)

            # Attempt 1: GEV with free ξ, L2 regularisation to prevent divergence
            # on short records. Bounded ξ ∈ [−0.5, 0.5] covers all practical
            # precipitation extreme value shapes (Coles, 2001).
            try:

                def _neg_ll(params: np.ndarray) -> float:
                    c, loc, scale = params
                    if scale <= 0:
                        return 1e10
                    ll = float(genextreme.logpdf(valid, c, loc=loc, scale=scale).sum())
                    return -(ll - 0.1 * c**2)  # L2 penalty on ξ

                opt = minimize(
                    _neg_ll,
                    x0=[0.1, float(np.mean(valid)), float(np.std(valid))],
                    method="L-BFGS-B",
                    bounds=[(-0.5, 0.5), (None, None), (1e-6, None)],
                )
                if opt.success:
                    c, loc, scale = opt.x
                    thresholds = genextreme.ppf(exceedance_probs, c, loc=loc, scale=scale)
                    if np.all(np.diff(thresholds) > 0) and np.all(thresholds > 0):
                        return thresholds
            except Exception:
                pass

            # Fallback: Gumbel (ξ = 0) — more robust on short records
            try:
                c, loc, scale = genextreme.fit(valid, f0=0)
                return genextreme.ppf(exceedance_probs, c, loc=loc, scale=scale)
            except Exception:
                return np.full(len(return_periods), np.nan)

        return xr.apply_ufunc(
            fit_cell,
            accumulated,
            input_core_dims=[["time"]],
            output_core_dims=[["return_period"]],
            output_dtypes=[float],
            vectorize=True,
            dask="parallelized",
            dask_gufunc_kwargs={"output_sizes": {"return_period": len(return_periods)}},
        )
