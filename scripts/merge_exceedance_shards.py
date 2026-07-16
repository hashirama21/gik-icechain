"""Merge backfill exceedance shards into the per-era production stores.

Each backfill chunk job writes an independent shard zarr (no write
contention). This script appends them chronologically into the final
stores, routing by the shard's native grid: 0.25 deg shards go to the
production store, 0.4 deg shards to the pre-2024-02-29 era store. Dates
already present in a target are skipped, so reruns are idempotent.

Usage:
    python scripts/merge_exceedance_shards.py \
      --shards s3://gik-icechain/backfill-shards/ \
      --out-0p25 s3://gik-icechain/exceedance-zarr \
      --out-0p4  s3://gik-icechain/exceedance-zarr-0p4
"""

from __future__ import annotations

import argparse
from datetime import date

import structlog
import xarray as xr

from gik_icechain.exceedance.writer import write_exceedance_store

log = structlog.get_logger(__name__)

_OPTIONAL_VARS = {
    "ensemble_confidence": "confidence_dict",
    "tail_ratio": "tail_dict",
    "median_ratio": "median_dict",
}


def list_shards(prefix: str) -> list[str]:
    import s3fs

    fs = s3fs.S3FileSystem()
    bucket_prefix = prefix.removeprefix("s3://").rstrip("/")
    shards = sorted(
        f"s3://{p}" for p in fs.ls(bucket_prefix) if fs.exists(f"{p}/zarr.json")
    )
    log.info("shards_listed", prefix=prefix, n_shards=len(shards))
    return shards


def existing_dates(store_uri: str) -> set[date]:
    try:
        ds = xr.open_zarr(store_uri, consolidated=False)
        return {d.astype("datetime64[D]").astype(date) for d in ds["date"].values}
    except Exception:
        return set()


def merge_shard(shard_uri: str, out_0p25: str, out_0p4: str, skip: dict[str, set[date]]) -> int:
    ds = xr.open_zarr(shard_uri, consolidated=False)
    grid_deg = float(ds["source_grid_deg"].values[0]) if "source_grid_deg" in ds else 0.25
    target = out_0p4 if grid_deg > 0.3 else out_0p25

    days = [d.astype("datetime64[D]").astype(date) for d in ds["date"].values]
    fresh = [d for d in days if d not in skip[target]]
    if not fresh:
        log.info("shard_all_duplicates", shard=shard_uri, n_days=len(days))
        return 0

    results: dict[date, xr.DataArray] = {}
    optional: dict[str, dict[date, xr.DataArray]] = {k: {} for k in _OPTIONAL_VARS.values()}
    grids: dict[date, float] = {}
    for d in fresh:
        sel = ds.sel(date=str(d))
        results[d] = sel["exceedance_prob"].load()
        for var, key in _OPTIONAL_VARS.items():
            if var in sel:
                optional[key][d] = sel[var].load()
        if "source_grid_deg" in sel:
            grids[d] = float(sel["source_grid_deg"].values)

    write_exceedance_store(
        results,
        target,
        append=True,
        confidence_dict=optional["confidence_dict"] or None,
        tail_dict=optional["tail_dict"] or None,
        median_dict=optional["median_dict"] or None,
        source_grid_deg=grids or None,
    )
    skip[target].update(fresh)
    log.info(
        "shard_merged",
        shard=shard_uri,
        target=target,
        grid_deg=grid_deg,
        n_written=len(fresh),
        n_skipped=len(days) - len(fresh),
    )
    return len(fresh)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", required=True, help="S3 prefix holding the shard zarrs")
    parser.add_argument("--out-0p25", required=True, help="Target store for 0.25 deg dates")
    parser.add_argument("--out-0p4", required=True, help="Target store for 0.4 deg dates")
    args = parser.parse_args()

    shards = list_shards(args.shards)
    if not shards:
        log.error("no_shards_found", prefix=args.shards)
        return 1

    skip = {
        args.out_0p25: existing_dates(args.out_0p25),
        args.out_0p4: existing_dates(args.out_0p4),
    }
    log.info(
        "merge_starting",
        n_shards=len(shards),
        existing_0p25=len(skip[args.out_0p25]),
        existing_0p4=len(skip[args.out_0p4]),
    )

    total = 0
    failures = 0
    for shard in shards:
        try:
            total += merge_shard(shard, args.out_0p25, args.out_0p4, skip)
        except Exception:
            failures += 1
            log.error("shard_merge_failed", shard=shard, exc_info=True)

    log.info("merge_complete", n_dates_written=total, n_shard_failures=failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
