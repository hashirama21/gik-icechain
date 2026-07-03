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
        (br.offset - merged_start, br.offset - merged_start + br.length) for br in members
    )
    return CoalescedRange(
        uri=uri,
        offset=merged_start,
        length=merged_end - merged_start,
        slices=slices,
        original_ranges=tuple(members),
    )


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    stripped = uri[len("s3://") :]
    bucket, _, key = stripped.partition("/")
    return bucket, key


def _demux_buffers(
    crs: list[CoalescedRange],
    buffers: list[Any],
) -> list[tuple[tuple, bytes]]:
    """Slice each fetched buffer back into its original per-chunk pieces."""
    pieces: list[tuple[tuple, bytes]] = []
    for cr, buf in zip(crs, buffers, strict=True):
        raw = bytes(buf)
        for sl, orig in zip(cr.slices, cr.original_ranges, strict=True):
            chunk_key = (
                orig.metadata.get("member_idx"),
                orig.metadata.get("step_idx"),
                orig.metadata.get("variable"),
            )
            pieces.append((chunk_key, raw[sl[0] : sl[1]]))
    return pieces


def fetch_coalesced_ranges(
    coalesced: list[CoalescedRange],
    *,
    max_workers: int = 8,
    s3_region: str = "eu-central-1",
    anon: bool = True,
) -> dict[tuple, bytes]:
    """Fetch coalesced ranges from S3 in parallel using obstore.

    Ranges are grouped by file (URI) and retrieved with a single
    ``obstore.get_ranges`` multi-range request per file, which batches and
    coalesces them at the HTTP layer. Because each ECMWF GRIB2 step-file holds
    all 51 ensemble members, this collapses ~1500 per-chunk ``get_range`` calls
    into ~30 per-file requests - the request reduction the adjacency-only
    coalescing never delivered (members are not byte-adjacent).

    Uses ``obstore.store.S3Store`` with ``skip_signature=True`` (anonymous,
    the same mechanism IceChunk uses internally) and an *explicit* AWS
    endpoint, so the ``AWS_ENDPOINT_URL`` environment variable (set to MinIO
    for the IceChunk store) is never inherited for ECMWF byte-range reads.

    A file whose fetch fails (after obstore's own retries) is logged and
    skipped rather than aborting the whole day; its chunks stay absent and
    become NaN downstream, guarded by the ``min_members`` check in the caller.

    Returns:
        Mapping from ``(member_idx, step_idx, variable)`` to raw GRIB2 bytes.
    """
    from datetime import timedelta

    import obstore as obs
    from obstore.store import S3Store

    # Explicit AWS regional endpoint
    aws_endpoint = f"https://s3.{s3_region}.amazonaws.com"

    _client_options = {"timeout": "120s", "connect_timeout": "30s"}
    _retry_config = {"max_retries": 10, "retry_timeout": timedelta(seconds=300)}

    _stores: dict[str, Any] = {}

    def _get_store(bucket: str) -> Any:
        if bucket not in _stores:
            _stores[bucket] = S3Store(
                bucket,
                region=s3_region,
                skip_signature=True,
                endpoint=aws_endpoint,
                client_options=_client_options,  # type: ignore[arg-type]
                retry_config=_retry_config,  # type: ignore[arg-type]
            )
        return _stores[bucket]

    # Group coalesced ranges by file - one multi-range request per file.
    by_uri: dict[str, list[CoalescedRange]] = {}
    for cr in coalesced:
        by_uri.setdefault(cr.uri, []).append(cr)

    n_original = sum(len(cr.original_ranges) for cr in coalesced)

    def _fetch_file(item: tuple[str, list[CoalescedRange]]) -> list[tuple[tuple, bytes]]:
        uri, crs = item
        bucket, key = _parse_s3_uri(uri)
        store = _get_store(bucket)
        try:
            buffers = obs.get_ranges(
                store,
                key,
                starts=[cr.offset for cr in crs],
                ends=[cr.offset + cr.length for cr in crs],
            )
        except Exception as exc:
            log.warning(
                "fetch_file_failed",
                uri=uri,
                n_ranges=len(crs),
                n_chunks=sum(len(cr.original_ranges) for cr in crs),
                error=str(exc)[:160],
            )
            return []
        return _demux_buffers(crs, list(buffers))

    result: dict[tuple, bytes] = {}
    items = list(by_uri.items())
    effective_workers = min(max_workers, len(items)) if items else 1
    n_failed_files = 0
    with ThreadPoolExecutor(max_workers=effective_workers) as pool:
        for pieces in pool.map(_fetch_file, items):
            if not pieces:
                n_failed_files += 1
                continue
            for key, data in pieces:
                result[key] = data

    total_bytes = sum(cr.length for cr in coalesced)
    log.info(
        "fetch_coalesced_complete",
        n_files=len(items),
        n_failed_files=n_failed_files,
        n_coalesced=len(coalesced),
        n_original=n_original,
        n_fetched=len(result),
        total_mb=round(total_bytes / 1_048_576, 2),
        request_reduction=round(n_original / max(len(items), 1), 2),
    )

    return result


def _fetch_with_retry(fs: Any, uri: str, offset: int, length: int) -> bytes:
    """Read a byte range from S3 with retry on transient errors (legacy fsspec path)."""
    from gik_icechain.shared.storage import _s3_retry

    @_s3_retry
    def _do_fetch() -> bytes:
        with fs.open(uri, "rb") as f:
            f.seek(offset)
            return f.read(length)

    return _do_fetch()
