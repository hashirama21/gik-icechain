"""Byte-range coalescence for efficient S3 fetches.

Groups adjacent byte ranges by URI, merges those within a configurable gap
threshold, fetches the merged ranges in parallel, and demultiplexes the
results back to individual chunks.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, NamedTuple

import structlog

log = structlog.get_logger(__name__)


class ByteRange(NamedTuple):
    """A single virtual chunk reference with provenance metadata."""

    uri: str
    offset: int
    length: int
    metadata: dict[str, Any]  # {member_idx, step_idx, variable}


@dataclass(frozen=True)
class CoalescedRange:
    """A merged byte range covering one or more adjacent ByteRanges."""

    uri: str
    offset: int
    length: int
    slices: tuple[tuple[int, int], ...]  # (start, end) within the fetched buffer
    original_ranges: tuple[ByteRange, ...]


_DEFAULT_MAX_GAP = 65_536  # 64 KB
_DEFAULT_MAX_MERGED = 5_242_880  # 5 MB


def coalesce_byte_ranges(
    ranges: list[ByteRange],
    *,
    max_gap_bytes: int = _DEFAULT_MAX_GAP,
    max_merged_bytes: int = _DEFAULT_MAX_MERGED,
) -> list[CoalescedRange]:
    """Merge adjacent byte ranges that share the same URI.

    Args:
        ranges: Individual byte ranges to coalesce.
        max_gap_bytes: Maximum gap (in bytes) between two ranges to merge them.
        max_merged_bytes: Maximum total length of a merged range.

    Returns:
        List of :class:`CoalescedRange`, each covering one or more originals.
    """
    if not ranges:
        return []

    # Group by URI
    by_uri: dict[str, list[ByteRange]] = {}
    for br in ranges:
        by_uri.setdefault(br.uri, []).append(br)

    result: list[CoalescedRange] = []
    for uri, group in by_uri.items():
        sorted_group = sorted(group, key=lambda r: r.offset)
        _coalesce_sorted(sorted_group, uri, max_gap_bytes, max_merged_bytes, result)

    return result


def _coalesce_sorted(
    sorted_ranges: list[ByteRange],
    uri: str,
    max_gap: int,
    max_merged: int,
    out: list[CoalescedRange],
) -> None:
    """Merge a sorted list of ranges for a single URI into *out*."""
    current_start = sorted_ranges[0].offset
    current_end = current_start + sorted_ranges[0].length
    current_members: list[ByteRange] = [sorted_ranges[0]]

    for br in sorted_ranges[1:]:
        gap = br.offset - current_end
        new_end = br.offset + br.length
        new_length = new_end - current_start

        if gap <= max_gap and new_length <= max_merged:
            current_end = max(current_end, new_end)
            current_members.append(br)
        else:
            out.append(_build_coalesced(uri, current_start, current_end, current_members))
            current_start = br.offset
            current_end = br.offset + br.length
            current_members = [br]

    out.append(_build_coalesced(uri, current_start, current_end, current_members))


def _build_coalesced(
    uri: str,
    merged_start: int,
    merged_end: int,
    members: list[ByteRange],
) -> CoalescedRange:
    slices = tuple(
        (br.offset - merged_start, br.offset - merged_start + br.length)
        for br in members
    )
    return CoalescedRange(
        uri=uri,
        offset=merged_start,
        length=merged_end - merged_start,
        slices=slices,
        original_ranges=tuple(members),
    )


def fetch_coalesced_ranges(
    coalesced: list[CoalescedRange],
    *,
    max_workers: int = 8,
    s3_region: str = "eu-central-1",
    anon: bool = True,
) -> dict[tuple, bytes]:
    """Fetch coalesced ranges from S3 in parallel and demultiplex.

    Returns:
        Mapping from ``(member_idx, step_idx, variable)`` to raw GRIB2 bytes.
    """
    import fsspec

    fs = fsspec.filesystem(
        "s3",
        anon=anon,
        client_kwargs={"region_name": s3_region},
        config_kwargs={"retries": {"max_attempts": 5}},
    )

    n_original = sum(len(cr.original_ranges) for cr in coalesced)

    def _fetch_one(cr: CoalescedRange) -> list[tuple[tuple, bytes]]:
        buf = _fetch_with_retry(fs, cr.uri, cr.offset, cr.length)
        pieces: list[tuple[tuple, bytes]] = []
        for sl, orig in zip(cr.slices, cr.original_ranges, strict=True):
            key = (
                orig.metadata.get("member_idx"),
                orig.metadata.get("step_idx"),
                orig.metadata.get("variable"),
            )
            pieces.append((key, buf[sl[0]:sl[1]]))
        return pieces

    result: dict[tuple, bytes] = {}
    effective_workers = min(max_workers, len(coalesced)) if coalesced else 1
    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        for pieces in pool.map(_fetch_one, coalesced):
            for key, data in pieces:
                result[key] = data

    total_bytes = sum(cr.length for cr in coalesced)
    log.info(
        "fetch_coalesced_complete",
        n_requests=len(coalesced),
        n_original=n_original,
        total_mb=round(total_bytes / 1_048_576, 2),
        coalescing_ratio=round(n_original / max(len(coalesced), 1), 2),
    )

    return result


def _fetch_with_retry(fs: Any, uri: str, offset: int, length: int) -> bytes:
    """Read a byte range from S3 with retry on transient errors."""
    from gik_icechain.shared.storage import _s3_retry

    @_s3_retry
    def _do_fetch() -> bytes:
        with fs.open(uri, "rb") as f:
            f.seek(offset)
            return f.read(length)

    return _do_fetch()
