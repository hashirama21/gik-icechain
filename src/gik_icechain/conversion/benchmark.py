"""Benchmark: GIK+IceChunk virtual store vs dynamical.org full Zarr copy.

Measures storage, time-to-first-byte, full-scan throughput, and S3 egress
cost for the East Africa domain over a configurable number of forecast days.

GIK+IceChunk stores only chunk manifests (byte-range metadata, ~18.5 GB);
dynamical.org maintains a full-copy Zarr store (~242 TB for the same archive).
This benchmark quantifies the practical performance trade-offs.
"""

from __future__ import annotations

import csv
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_EAST_AFRICA_LAT = slice(-12.0, 23.0)
_EAST_AFRICA_LON = slice(22.0, 52.0)
_S3_EGRESS_USD_PER_GB = 0.09
_GIK_STORE_METADATA_GB = 18.5
_DYNAMICAL_STORE_FULL_GB = 242_000.0


@dataclass
class BenchmarkResult:
    approach:             str
    n_days:               int
    domain:               str
    store_size_gb:        float
    time_to_first_byte_s: float
    full_scan_elapsed_s:  float
    data_read_gb:         float
    estimated_egress_usd: float
    n_workers:            int


def run_benchmark(
    gik_store_uri: str,
    dynamical_store_uri: str,
    domain: str = "east_africa",
    n_days: int = 30,
    n_workers: int = 4,
    output_dir: str = "results/benchmarks/",
) -> dict[str, BenchmarkResult]:
    """Run full benchmark and save results to CSV.

    Args:
        gik_store_uri:        URI of the GIK+IceChunk virtual store.
        dynamical_store_uri:  URI of the dynamical.org full-copy Zarr store.
        domain:               Domain label for output filenames.
        n_days:               Number of forecast days to include in the scan.
        n_workers:            Dask workers for the full-scan test.
        output_dir:           Directory for CSV output.

    Returns:
        Dict mapping approach name → BenchmarkResult.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    results: dict[str, BenchmarkResult] = {}
    store_sizes = {
        "GIK+IceChunk": _GIK_STORE_METADATA_GB,
        "dynamical.org": _DYNAMICAL_STORE_FULL_GB,
    }

    for approach, uri in (
        ("GIK+IceChunk", gik_store_uri),
        ("dynamical.org", dynamical_store_uri),
    ):
        log.info("benchmark_approach_start", approach=approach, n_days=n_days)
        try:
            ds = xr.open_zarr(uri, consolidated=False)
            ttfb = _measure_ttfb(ds)
            elapsed, data_gb = _measure_full_scan(ds, n_days, n_workers)
        except Exception as exc:
            log.warning("benchmark_approach_failed", approach=approach, error=str(exc))
            continue

        results[approach] = BenchmarkResult(
            approach=approach,
            n_days=n_days,
            domain=domain,
            store_size_gb=store_sizes[approach],
            time_to_first_byte_s=ttfb,
            full_scan_elapsed_s=elapsed,
            data_read_gb=data_gb,
            estimated_egress_usd=data_gb * _S3_EGRESS_USD_PER_GB,
            n_workers=n_workers,
        )
        log.info(
            "benchmark_approach_done",
            approach=approach,
            ttfb_s=f"{ttfb:.3f}",
            scan_s=f"{elapsed:.1f}",
            data_gb=f"{data_gb:.2f}",
        )

    _save_csv(results, out / f"benchmark_{domain}_{n_days}days.csv")
    return results


def _measure_ttfb(ds: xr.Dataset) -> float:
    """Wall-clock time to load the first scalar value (cold read)."""
    t0 = time.perf_counter()
    _ = float(ds["tp"].isel(time=0, latitude=0, longitude=0).values)
    return time.perf_counter() - t0


def _measure_full_scan(
    ds: xr.Dataset,
    n_days: int,
    n_workers: int,
) -> tuple[float, float]:
    """Timed spatial mean over the East Africa domain for n_days.

    Returns (elapsed_seconds, data_read_gb).
    """
    try:
        from dask.distributed import Client

        client: object | None = Client(
            n_workers=n_workers, threads_per_worker=2, silence_logs=True
        )
    except ImportError:
        client = None

    subset = (
        ds["tp"]
        .sel(latitude=_EAST_AFRICA_LAT, longitude=_EAST_AFRICA_LON)
        .isel(time=slice(0, n_days))
    )
    data_gb = getattr(subset, "nbytes", 0) / 1e9

    t0 = time.perf_counter()
    _ = subset.mean().compute() if hasattr(subset, "compute") else subset.mean()
    elapsed = time.perf_counter() - t0

    if client is not None:
        client.close()

    return elapsed, data_gb


def _save_csv(results: dict[str, BenchmarkResult], path: Path) -> None:
    if not results:
        return
    rows = [asdict(r) for r in results.values()]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    log.info("benchmark_csv_saved", path=str(path))
