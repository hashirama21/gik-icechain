"""Load pre-computed CMORPH return-period thresholds from HuggingFace.

The dataset ``E4DRR/virtualizarr-stores`` (HuggingFace) exposes CMORPH v1.0
climatological thresholds at 3h–7d accumulation windows and 2–100 year return
periods as a virtual IceChunk Zarr store.  These static thresholds serve as
the baseline for evaluating GIK adaptive GEV thresholds (AUC-ROC comparison).

Schema of the remote store:
    variable:    threshold_mm
    dimensions:  (latitude, longitude)
    coords:      window_h, return_period (encoded in the group path)
    group path:  ``{window_h}h/{return_period}y``
"""

from __future__ import annotations

from pathlib import Path

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_HF_DATASET = "E4DRR/virtualizarr-stores"
_HF_THRESHOLD_FILE = "cmorph_thresholds_{window_h}h_{return_period}y.nc"
_LOCAL_CACHE_DIR = Path("data/cmorph_baseline_cache")


def load_cmorph_baseline(
    window_h: int,
    return_period: int,
    hf_dataset: str = _HF_DATASET,
    cache_dir: Path | None = None,
) -> xr.DataArray:
    """Return CMORPH climatological threshold as a (latitude, longitude) DataArray.

    Downloads from HuggingFace on first call and caches locally.  Subsequent
    calls read from the local cache — no network required.

    Args:
        window_h:      Accumulation window in hours (3, 6, 12, 24, 48, 72, 168).
        return_period: Return period in years (2, 5, 10, 20, 40, 100).
        hf_dataset:    HuggingFace dataset ID.
        cache_dir:     Local directory for cached NetCDF files.

    Returns:
        DataArray of threshold values in mm (latitude × longitude).

    Raises:
        ValueError: If the requested window or return period is unavailable.
        ImportError: If ``huggingface_hub`` is not installed.
    """
    effective_cache = cache_dir or _LOCAL_CACHE_DIR
    cache_path = effective_cache / f"cmorph_{window_h}h_{return_period}y.nc"

    if cache_path.exists():
        return _open_cached(cache_path, window_h, return_period)

    return _download_and_cache(hf_dataset, window_h, return_period, cache_path)


def _open_cached(path: Path, window_h: int, return_period: int) -> xr.DataArray:
    ds = xr.open_dataset(path)
    var = _find_threshold_var(ds)
    da = ds[var].squeeze()
    da.attrs.setdefault("window_h", window_h)
    da.attrs.setdefault("return_period", return_period)
    da.attrs.setdefault("source", "CMORPH v1.0 static climatology")
    log.debug("cmorph_baseline_loaded_from_cache", path=str(path))
    return da


def _download_and_cache(
    hf_dataset: str,
    window_h: int,
    return_period: int,
    cache_path: Path,
) -> xr.DataArray:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise ImportError("huggingface_hub is required: pip install huggingface-hub") from None

    filename = _HF_THRESHOLD_FILE.format(window_h=window_h, return_period=return_period)
    log.info("cmorph_baseline_downloading", dataset=hf_dataset, file=filename)

    try:
        local = hf_hub_download(
            repo_id=hf_dataset,
            filename=filename,
            repo_type="dataset",
        )
    except Exception as exc:
        raise ValueError(
            f"CMORPH threshold file not found on HuggingFace: {filename}. "
            f"Available windows: 3,6,12,24,48,72,168h; return periods: 2,5,10,20,40,100y. "
            f"Original error: {exc}"
        ) from exc

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy(local, cache_path)
    log.info("cmorph_baseline_cached", path=str(cache_path))
    return _open_cached(cache_path, window_h, return_period)


def _find_threshold_var(ds: xr.Dataset) -> str:
    """Find the threshold variable by name or by elimination."""
    for candidate in ("threshold_mm", "threshold", "precip_threshold", "tp_threshold"):
        if candidate in ds:
            return candidate
    data_vars = [v for v in ds.data_vars if "lat" not in v and "lon" not in v]
    if data_vars:
        return data_vars[0]
    raise KeyError(f"No threshold variable found in dataset. Variables: {list(ds.data_vars)}")


def compare_adaptive_vs_cmorph(
    adaptive_threshold: xr.DataArray,
    window_h: int,
    return_period: int,
    cache_dir: Path | None = None,
) -> xr.Dataset:
    """Compute pointwise ratio adaptive_GEV / CMORPH_static for a given (window, RP).

    A ratio > 1 means the adaptive threshold is more conservative (fewer false alarms
    in wet ENSO regimes); ratio < 1 means more sensitive (better detection in dry
    regimes).  Used in the benchmarking report.

    Args:
        adaptive_threshold: DataArray (lat × lon) from AdaptiveGEVThresholds.get().
        window_h:           Accumulation window in hours.
        return_period:      Return period in years.
        cache_dir:          Local cache directory for CMORPH files.

    Returns:
        Dataset with variables ``adaptive_mm``, ``cmorph_mm``, and ``ratio``.
    """
    cmorph = load_cmorph_baseline(window_h, return_period, cache_dir=cache_dir)

    cmorph_aligned = cmorph.interp_like(adaptive_threshold, method="linear")

    ratio = adaptive_threshold / cmorph_aligned.where(cmorph_aligned > 0)

    return xr.Dataset(
        {
            "adaptive_mm": adaptive_threshold,
            "cmorph_mm": cmorph_aligned,
            "ratio": ratio,
        },
        attrs={
            "window_h": window_h,
            "return_period": return_period,
            "description": "Adaptive GEV vs CMORPH static threshold comparison",
        },
    )
