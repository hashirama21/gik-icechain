"""Emit the GitHub Actions matrices for the parallel backfill workflow.

Splits [start, end] into C2 chunks (shard jobs) and C3 chunks (risk jobs,
with a spin-up window for the dynamic BN soil-memory state). No chunk ever
crosses the 2024-02-29 ECMWF era boundary: the two eras have different
grids and live in separate exceedance stores.

Prints a single JSON object: {"c2": [...], "c3": [...]} where each C2 entry
is {id, start, end} and each C3 entry adds {spinup_start, era} with era in
{"0p25", "0p4"}.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

ERA_0P25_FIRST_DAY = date(2024, 2, 29)
ARCHIVE_FIRST_DAY = date(2023, 1, 18)
C3_SPINUP_DAYS = 14


def era_of(day: date) -> str:
    return "0p25" if day >= ERA_0P25_FIRST_DAY else "0p4"


def era_start(day: date) -> date:
    return ERA_0P25_FIRST_DAY if day >= ERA_0P25_FIRST_DAY else ARCHIVE_FIRST_DAY


def split_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        if era_of(cur) != era_of(chunk_end):
            chunk_end = ERA_0P25_FIRST_DAY - timedelta(days=1)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--c2-chunk-days", type=int, default=25)
    parser.add_argument("--c3-chunk-days", type=int, default=120)
    args = parser.parse_args()

    if args.start < ARCHIVE_FIRST_DAY:
        raise SystemExit(f"start {args.start} predates the archive ({ARCHIVE_FIRST_DAY})")
    if args.end < args.start:
        raise SystemExit(f"end {args.end} before start {args.start}")

    c2 = [
        {"id": f"c2-{i:03d}", "start": s.isoformat(), "end": e.isoformat()}
        for i, (s, e) in enumerate(split_chunks(args.start, args.end, args.c2_chunk_days))
    ]
    c3 = [
        {
            "id": f"c3-{i:03d}",
            "start": s.isoformat(),
            "end": e.isoformat(),
            "spinup_start": max(s - timedelta(days=C3_SPINUP_DAYS), era_start(s)).isoformat(),
            "era": era_of(s),
        }
        for i, (s, e) in enumerate(split_chunks(args.start, args.end, args.c3_chunk_days))
    ]
    print(json.dumps({"c2": c2, "c3": c3}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
