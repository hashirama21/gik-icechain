"""AIFS ENS parallel exceedance track (Innovation 3).

Runs the same C2 exceedance pipeline on ECMWF AIFS ensemble output and
computes skill metrics (BSS, FSS) against the IFS ENS baseline.

AIFS (Artificial Intelligence/Integrated Forecasting System) is ECMWF's
ML-based global model; running it in parallel with IFS ENS lets the project
benchmark AI-NWP forecast quality for East Africa flood events.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

if TYPE_CHECKING:
    import xarray as xr

from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds, ClimateMode
from gik_icechain.exceedance.accumulations import compute_rolling_accumulations, WINDOWS_H
from gik_icechain.exceedance.exceedance import compute_exceedance_probabilities
from gik_icechain.exceedance.writer import write_exceedance_store, build_exceedance_dataset
from gik_icechain.exceedance.loader import open_aifs_store

log = structlog.get_logger(__name__)

_RETURN_PERIODS: list[int] = [2, 5, 10, 20, 40, 100]


def run_aifs_exceedance(
    aifs_store_uri: str,
    thresholds: AdaptiveGEVThresholds,
    output_uri: str,
    start: date,
    end: date,
    climate_mode_fn: Callable[[date], ClimateMode],
    n_workers: int = 16,
    return_periods: list[int] = _RETURN_PERIODS,
    windows_h: list[int] = WINDOWS_H,
) -> None:
    """Compute exceedance probabilities from AIFS ENS and write to a Zarr store.

    Mirrors the IFS exceedance pipeline so outputs are directly comparable
    via :func:`compare_ifs_vs_aifs`.

    Args:
        aifs_store_uri:   URI of the AIFS ENS IceChunk or Zarr store.
        thresholds:       Fitted :class:`AdaptiveGEVThresholds` instance.
        output_uri:       URI for the output AIFS exceedance Zarr store.
        start:            First forecast date (inclusive).
        end:              Last forecast date (inclusive).
        climate_mode_fn:  Callable that maps a date to a :class:`ClimateMode`.
        n_workers:        Dask workers for parallel computation.
        return_periods:   Return periods in years.
        windows_h:        Accumulation windows in hours.
    """
    from datetime import timedelta

    try:
        from dask.distributed import Client
        client: object | None = Client(n_workers=n_workers, threads_per_worker=2, silence_logs=True)
    except ImportError:
        client = None

    ds = open_aifs_store(aifs_store_uri)
    acc_ds = compute_rolling_accumulations(ds, windows_h=windows_h)

    results: dict[date, "xr.DataArray"] = {}
    current = start
    while current <= end:
        mode = climate_mode_fn(current)
        day_results: dict[tuple[int, int], "xr.DataArray"] = {}

        for w in windows_h:
            for rp in return_periods:
                try:
                    thr_ds = _make_threshold_dataset(thresholds, w, rp, mode)
                    exc = compute_exceedance_probabilities(
                        acc_ds.sel(time=pd.Timestamp(current)),
                        thr_ds,
                        window_h=w,
                        return_period=rp,
                    )
                    day_results[(w, rp)] = exc
                except Exception as exc_err:
                    log.warning(
                        "aifs_exceedance_failed",
                        date=current, window=w, rp=rp, error=str(exc_err),
                    )

        if day_results:
            results[current] = build_exceedance_dataset(day_results, current)
        current += timedelta(days=1)

    write_exceedance_store(results, output_uri, append=True)
    log.info("aifs_exceedance_complete", n_dates=len(results), uri=output_uri)

    if client is not None:
        client.close()  # type: ignore[union-attr]


def compare_ifs_vs_aifs(
    ifs_store_uri: str,
    aifs_store_uri: str,
    region_slice: dict,
    output_dir: Path,
    return_period: int = 5,
    window_h: int = 24,
) -> pd.DataFrame:
    """Compute BSS and FSS comparing IFS ENS vs AIFS ENS exceedance probabilities.

    Args:
        ifs_store_uri:   URI of the IFS exceedance Zarr store.
        aifs_store_uri:  URI of the AIFS exceedance Zarr store.
        region_slice:    Dict with ``latitude`` and ``longitude`` slices for
                         the domain of interest (e.g., East Africa).
        output_dir:      Directory to write the comparison CSV.
        return_period:   Return period (years) to compare.
        window_h:        Accumulation window (hours) to compare.

    Returns:
        DataFrame with columns ``[date, bss_aifs, fss_aifs]``.
    """
    import xarray as xr

    ifs_ds  = xr.open_zarr(ifs_store_uri,  consolidated=False)
    aifs_ds = xr.open_zarr(aifs_store_uri, consolidated=False)

    ifs_exc  = ifs_ds["exceedance_prob"].sel(
        window=window_h, return_period=return_period, **region_slice
    )
    aifs_exc = aifs_ds["exceedance_prob"].sel(
        window=window_h, return_period=return_period, **region_slice
    )

    common_dates = sorted(
        set(str(d)[:10] for d in ifs_exc["date"].values)
        & set(str(d)[:10] for d in aifs_exc["date"].values)
    )

    rows: list[dict] = []
    for d_str in common_dates:
        ifs_day  = ifs_exc.sel(date=pd.Timestamp(d_str)).values.ravel()
        aifs_day = aifs_exc.sel(date=pd.Timestamp(d_str)).values.ravel()

        mask = np.isfinite(ifs_day) & np.isfinite(aifs_day)
        if mask.sum() == 0:
            continue

        bss  = _brier_skill_score(ifs_day[mask], aifs_day[mask])
        fss  = _fractions_skill_score(ifs_day[mask], aifs_day[mask])
        rows.append({"date": d_str, "bss_aifs": bss, "fss_aifs": fss})

    df = pd.DataFrame(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"ifs_vs_aifs_rp{return_period}y_{window_h}h.csv"
    df.to_csv(out_path, index=False)
    log.info("ifs_aifs_comparison_saved", path=str(out_path), n_dates=len(df))
    return df


def _make_threshold_dataset(
    thresholds: AdaptiveGEVThresholds,
    window_h: int,
    return_period: int,
    mode: ClimateMode,
) -> "xr.Dataset":
    """Wrap a single threshold DataArray in a Dataset for exceedance.py API."""
    import xarray as xr

    da = thresholds.get(window_h, return_period, mode)
    return xr.Dataset({f"rp_{return_period}y": da})


def _brier_skill_score(reference: np.ndarray, forecast: np.ndarray) -> float:
    """BSS of *forecast* relative to *reference* (climatological mean as reference)."""
    clim = reference.mean()
    bs_ref = float(np.mean((reference - clim) ** 2))
    bs_fcs = float(np.mean((forecast - reference) ** 2))
    if bs_ref == 0.0:
        return 0.0
    return float(1.0 - bs_fcs / bs_ref)


def _fractions_skill_score(
    reference: np.ndarray,
    forecast: np.ndarray,
    threshold: float = 0.15,
) -> float:
    """Fraction Skill Score: fraction of cells where |forecast - reference| < threshold."""
    if len(reference) == 0:
        return 0.0
    close = np.abs(forecast - reference) < threshold
    return float(close.mean())
