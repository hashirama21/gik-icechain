"""Benchmark: GIK+IceChunk virtual store vs dynamical.org full Zarr copy.

Measures storage footprint, time-to-first-byte, full-scan throughput, and
estimated S3 egress cost for the East Africa domain over a configurable
number of forecast days.

GIK+IceChunk stores only chunk manifests (~18.5 GB for 737 days);
dynamical.org maintains a full-copy Zarr (~242 TB for the same archive).
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_EAST_AFRICA_LAT = slice(-12.0, 23.0)
_EAST_AFRICA_LON = slice(22.0, 52.0)
_S3_EGRESS_USD_PER_GB = 0.09
_GIK_STORE_METADATA_GB = 18.5
_DYNAMICAL_STORE_FULL_GB = 242_000.0


@dataclass
class BenchmarkResult:
    approach: str
    n_days: int
    domain: str
    store_size_gb: float
    time_to_first_byte_s: float
    full_scan_elapsed_s: float
    data_read_gb: float
    estimated_egress_usd: float
    n_workers: int


def run_benchmark(
    gik_store_uri: str,
    dynamical_store_uri: str | None = None,
    domain: str = "east_africa",
    n_days: int = 30,
    n_workers: int = 4,
    output_dir: str = "results/benchmarks/",
) -> dict[str, BenchmarkResult]:
    """Run full benchmark and save results to CSV.

    Measures both the GIK+IceChunk approach (virtual chunks) and, optionally,
    a conventional full-copy Zarr store (e.g. dynamical.org).

    Args:
        gik_store_uri:        URI of the GIK+IceChunk virtual store.
        dynamical_store_uri:  URI of a conventional Zarr store to compare.
                              Skipped when None.
        domain:               Domain label used in the output filename.
        n_days:               Number of forecast day-groups to include.
        n_workers:            Dask workers for the full-scan measurement.
        output_dir:           Directory for CSV output.

    Returns:
        Dict mapping approach name → BenchmarkResult.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: dict[str, BenchmarkResult] = {}

    gik_result = _benchmark_gik(gik_store_uri, n_days, n_workers, domain)
    if gik_result is not None:
        results["GIK+IceChunk"] = gik_result

    if dynamical_store_uri:
        conv_result = _benchmark_conventional(
            dynamical_store_uri, n_days, n_workers, domain, _DYNAMICAL_STORE_FULL_GB
        )
        if conv_result is not None:
            results["dynamical.org"] = conv_result

    _save_csv(results, out / f"benchmark_{domain}_{n_days}days.csv")
    return results


def _benchmark_gik(
    store_uri: str,
    n_days: int,
    n_workers: int,
    domain: str,
) -> BenchmarkResult | None:
    """Benchmark the GIK+IceChunk virtual store using its date-group schema."""
    try:

        from gik_icechain.conversion.icechunk_writer import IceChainStore

        store = IceChainStore(store_uri)
        store.create_or_open()
        snapshots = store.list_snapshots()
        date_groups = sorted({s["forecast_date"] for s in snapshots if s["forecast_date"]})
        if not date_groups:
            log.warning("benchmark_gik_no_snapshots", uri=store_uri)
            return None

        session = store._repo.readonly_session(branch=store.branch)  # type: ignore[union-attr]

        ttfb = _measure_ttfb_icechunk(session, date_groups[0])
        elapsed, data_gb = _measure_full_scan_icechunk(
            session, date_groups[:n_days], n_workers
        )
        store_size_gb = _measure_store_size_gb(store_uri)

    except Exception as exc:
        log.warning("benchmark_gik_failed", error=str(exc)[:120])
        return None

    result = BenchmarkResult(
        approach="GIK+IceChunk",
        n_days=min(n_days, len(date_groups)),
        domain=domain,
        store_size_gb=store_size_gb,
        time_to_first_byte_s=ttfb,
        full_scan_elapsed_s=elapsed,
        data_read_gb=data_gb,
        estimated_egress_usd=data_gb * _S3_EGRESS_USD_PER_GB,
        n_workers=n_workers,
    )
    log.info(
        "benchmark_gik_done",
        ttfb_s=f"{ttfb:.3f}",
        scan_s=f"{elapsed:.1f}",
        data_gb=f"{data_gb:.2f}",
    )
    return result


def _benchmark_conventional(
    store_uri: str,
    n_days: int,
    n_workers: int,
    domain: str,
    store_size_gb: float,
) -> BenchmarkResult | None:
    """Benchmark a conventional full-copy Zarr store with a time dimension."""
    try:
        import xarray as xr

        ds = xr.open_zarr(store_uri, consolidated=False)
        ttfb = _measure_ttfb_conventional(ds)
        elapsed, data_gb = _measure_full_scan_conventional(ds, n_days, n_workers)
    except Exception as exc:
        log.warning("benchmark_conventional_failed", uri=store_uri, error=str(exc)[:120])
        return None

    result = BenchmarkResult(
        approach="dynamical.org",
        n_days=n_days,
        domain=domain,
        store_size_gb=store_size_gb,
        time_to_first_byte_s=ttfb,
        full_scan_elapsed_s=elapsed,
        data_read_gb=data_gb,
        estimated_egress_usd=data_gb * _S3_EGRESS_USD_PER_GB,
        n_workers=n_workers,
    )
    log.info(
        "benchmark_conventional_done",
        ttfb_s=f"{ttfb:.3f}",
        scan_s=f"{elapsed:.1f}",
        data_gb=f"{data_gb:.2f}",
    )
    return result


def _measure_ttfb_icechunk(session: Any, date_group: str) -> float:
    """Wall-clock time to read one scalar from the first IceChunk date group."""
    import xarray as xr

    t0 = time.perf_counter()
    ds = xr.open_zarr(session.store, group=date_group, consolidated=False)
    var = next(iter(ds.data_vars), None)
    if var is None:
        return time.perf_counter() - t0
    scalar_sel: dict[str, Any] = {d: 0 for d in ds[var].dims}
    _ = float(ds[var].isel(**scalar_sel).values)
    return time.perf_counter() - t0


def _measure_ttfb_conventional(ds: Any) -> float:
    """Wall-clock time to read one scalar from a time-indexed Zarr store."""
    t0 = time.perf_counter()
    var = next(iter(ds.data_vars), None)
    if var is None:
        return time.perf_counter() - t0
    scalar_sel: dict[str, Any] = {d: 0 for d in ds[var].dims}
    _ = float(ds[var].isel(**scalar_sel).values)
    return time.perf_counter() - t0


def _measure_store_size_gb(store_uri: str) -> float:
    """Measure the real on-disk size of the IceChunk store (GB).

    Sums object byte-sizes under the store prefix via s3fs (picks up
    AWS_* / AWS_ENDPOINT_URL from the environment, so it works against both
    AWS S3 and a MinIO mirror). Falls back to the documented metadata estimate
    if the bucket can't be listed. See ISSUE-18.
    """
    try:
        import s3fs

        fs = s3fs.S3FileSystem()
        path = store_uri.replace("s3://", "")
        total_bytes = fs.du(path, total=True)
        if total_bytes and total_bytes > 0:
            return total_bytes / 1e9
    except Exception as exc:
        log.warning("store_size_measure_failed", uri=store_uri, error=str(exc)[:120])
    return _GIK_STORE_METADATA_GB


def _bbox_subset(da: Any, lat_bounds: slice, lon_bounds: slice) -> Any:
    """Select a lat/lon bbox regardless of coordinate sort order.

    ``DataArray.sel(latitude=slice(lo, hi))`` returns an EMPTY selection when
    the coordinate is descending (IFS latitude runs 90 -> -90), which silently
    made the benchmark scan read 0 bytes. Order each slice to match the actual
    coordinate direction so a real EA-domain read is forced.
    """
    lo_lat, hi_lat = lat_bounds.start, lat_bounds.stop
    if "latitude" in da.coords and float(da["latitude"][0]) > float(da["latitude"][-1]):
        lat_sel = slice(hi_lat, lo_lat)
    else:
        lat_sel = slice(lo_lat, hi_lat)
    lo_lon, hi_lon = lon_bounds.start, lon_bounds.stop
    if "longitude" in da.coords and float(da["longitude"][0]) > float(da["longitude"][-1]):
        lon_sel = slice(hi_lon, lo_lon)
    else:
        lon_sel = slice(lo_lon, hi_lon)
    return da.sel(latitude=lat_sel, longitude=lon_sel)


def _measure_full_scan_icechunk(
    session: Any,
    date_groups: list[str],
    n_workers: int,
) -> tuple[float, float]:
    """Spatial mean of ``tp`` over East Africa for each date group.

    Returns (elapsed_seconds, total_data_read_gb).
    """
    import xarray as xr

    client = _maybe_start_dask(n_workers)
    total_bytes = 0
    t0 = time.perf_counter()

    for group in date_groups:
        try:
            ds = xr.open_zarr(session.store, group=group, consolidated=False)
            if "tp" not in ds:
                continue
            subset = _bbox_subset(ds["tp"], _EAST_AFRICA_LAT, _EAST_AFRICA_LON)
            total_bytes += getattr(subset, "nbytes", 0)
            _ = float(subset.mean().compute())
        except Exception:
            continue

    elapsed = time.perf_counter() - t0
    if client is not None:
        client.close()
    return elapsed, total_bytes / 1e9


def _measure_full_scan_conventional(
    ds: Any,
    n_days: int,
    n_workers: int,
) -> tuple[float, float]:
    """Spatial mean over East Africa for the first n_days time steps."""
    client = _maybe_start_dask(n_workers)

    time_dim = next((d for d in ("time", "forecast_time", "valid_time") if d in ds.dims), None)
    var = next(iter(ds.data_vars), None)
    if var is None or time_dim is None:
        return 0.0, 0.0

    subset = _bbox_subset(ds[var], _EAST_AFRICA_LAT, _EAST_AFRICA_LON).isel(
        {time_dim: slice(0, n_days)}
    )
    data_gb = getattr(subset, "nbytes", 0) / 1e9

    t0 = time.perf_counter()
    _ = float(subset.mean().compute()) if hasattr(subset, "compute") else float(subset.mean())
    elapsed = time.perf_counter() - t0

    if client is not None:
        client.close()
    return elapsed, data_gb


def _maybe_start_dask(n_workers: int) -> Any | None:
    if n_workers <= 1:
        return None
    try:
        from dask.distributed import Client

        return Client(n_workers=n_workers, threads_per_worker=2, silence_logs=True)
    except ImportError:
        return None


def _save_csv(results: dict[str, BenchmarkResult], path: Path) -> None:
    if not results:
        return
    rows = [asdict(r) for r in results.values()]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log.info("benchmark_csv_saved", path=str(path))
