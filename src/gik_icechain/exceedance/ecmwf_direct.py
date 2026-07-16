"""Real-time C2 source: byte ranges straight from the ECMWF open-data bucket.

Every GRIB2 step file on ``s3://ecmwf-forecasts`` ships a sibling ``.index``
file (one JSON object per line: param, member number, step, ``_offset``,
``_length``). Reading the day's ~85 small index files yields the same
(uri, offset, length) references the IceChunk manifests carry - no store, no
catalog, no publication lag. The rest of the pipeline (coalesce, parallel
fetch, eccodes decode, assemble) is shared with the manifest-aware path.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import numpy as np
import structlog

if TYPE_CHECKING:
    import xarray as xr

from gik_icechain.exceedance.manifest_store import _assemble_dataset, _decode_grib_message
from gik_icechain.shared.byte_range import (
    ByteRange,
    coalesce_byte_ranges,
    fetch_coalesced_ranges,
)
from gik_icechain.shared.grid import grid_deg, shape_for_uri

log = structlog.get_logger(__name__)

# IFS ENS forecast horizon: 3-hourly to 144 h, then 6-hourly to 360 h.
INDEX_STEP_HOURS = [*range(0, 145, 3), *range(150, 361, 6)]


def _step_file_uri(date_str: str, run_hour: int, step_h: int, suffix: str) -> str:
    ymd = date_str.replace("-", "")
    return (
        f"s3://ecmwf-forecasts/{ymd}/{run_hour:02d}z/ifs/0p25/enfo/"
        f"{ymd}000000-{step_h}h-enfo-ef.{suffix}"
    )


def parse_index_lines(
    text: str,
    grib_uri: str,
    step_idx: int,
    variables: list[str],
) -> list[ByteRange]:
    """Build ByteRanges from one ``.index`` file's content.

    Keeps surface-level (``levtype: sfc``) entries of the requested params.
    The control member (``type: cf``, no ``number``) maps to member 0;
    perturbed members keep their 1-based ``number``.
    """
    wanted = set(variables)
    refs: list[ByteRange] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("levtype") != "sfc" or entry.get("param") not in wanted:
            continue
        length = int(entry.get("_length", 0))
        if length == 0:
            continue
        refs.append(
            ByteRange(
                uri=grib_uri,
                offset=int(entry.get("_offset", 0)),
                length=length,
                metadata={
                    "member_idx": int(entry["number"]) if entry.get("number") else 0,
                    "step_idx": step_idx,
                    "variable": entry["param"],
                },
            )
        )
    return refs


def _fetch_index_refs(
    date_str: str,
    run_hour: int,
    step_hours: list[int],
    variables: list[str],
    s3_region: str,
    max_workers: int = 8,
) -> list[ByteRange]:
    import fsspec

    fs = fsspec.filesystem("s3", anon=True, client_kwargs={"region_name": s3_region})

    def _one(pos: int, step_h: int) -> list[ByteRange]:
        index_uri = _step_file_uri(date_str, run_hour, step_h, "index")
        grib_uri = _step_file_uri(date_str, run_hour, step_h, "grib2")
        try:
            text = fs.cat(index_uri).decode("utf-8")
        except FileNotFoundError:
            log.warning("index_file_missing", uri=index_uri)
            return []
        return parse_index_lines(text, grib_uri, pos, variables)

    refs: list[ByteRange] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, pos, h) for pos, h in enumerate(step_hours)]
        for future in as_completed(futures):
            refs.extend(future.result())

    log.info(
        "ecmwf_index_refs_fetched",
        date=date_str,
        n_refs=len(refs),
        n_steps=len(step_hours),
        variables=variables,
    )
    return refs


def load_day_ecmwf_direct(
    date_str: str,
    variables: list[str],
    max_step_h: int,
    step_resolution_h: int,
    step_buffer: int,
    bbox: tuple[float, float, float, float] | None,
    *,
    run_hour: int = 0,
    max_gap_bytes: int = 65_536,
    max_merged_bytes: int = 5_242_880,
    fetch_workers: int = 8,
    min_members: int = 10,
    s3_region: str = "eu-central-1",
) -> xr.Dataset:
    """Load one forecast day straight from the ECMWF open-data bucket.

    Same contract as ``load_day_manifest_aware``: concrete
    ``xr.Dataset(member, step, latitude, longitude)`` for *date_str*, with
    references resolved from the day's ``.index`` files instead of a store.

    Raises:
        ValueError: If no references are found (date not yet published) or
            fewer than *min_members* members decode.
    """
    max_steps = (max_step_h // step_resolution_h) + step_buffer + 1
    step_hours = INDEX_STEP_HOURS[:max_steps]

    byte_ranges = _fetch_index_refs(date_str, run_hour, step_hours, variables, s3_region)
    if not byte_ranges:
        raise ValueError(
            f"No ECMWF index references for {date_str} {run_hour:02d}z - "
            "the forecast may not be published yet"
        )

    coalesced = coalesce_byte_ranges(
        byte_ranges,
        max_gap_bytes=max_gap_bytes,
        max_merged_bytes=max_merged_bytes,
    )

    raw_data = fetch_coalesced_ranges(
        coalesced,
        max_workers=fetch_workers,
        s3_region=s3_region,
    )

    decoded: dict[tuple, np.ndarray] = {}
    for key in list(raw_data.keys()):
        member_idx, step_idx, var = key
        data = raw_data.pop(key)
        grid = _decode_grib_message(
            data,
            uri=f"{date_str}/{var}",
            offset=0,
            member=member_idx if member_idx is not None else -1,
            step=step_idx if step_idx is not None else -1,
            bbox=bbox,
        )
        if grid is not None:
            decoded[key] = grid

    if not decoded:
        raise ValueError(f"All GRIB2 decodes failed for {date_str}")

    unique_members = sorted({k[0] for k in decoded})
    if len(unique_members) < min_members:
        raise ValueError(
            f"Only {len(unique_members)} members decoded for {date_str}, "
            f"minimum required: {min_members}"
        )

    native_shape = shape_for_uri(byte_ranges[0].uri)
    log.info(
        "ecmwf_direct_load_complete",
        date=date_str,
        n_members=len(unique_members),
        n_decoded=len(decoded),
        n_coalesced_requests=len(coalesced),
        source_grid_deg=grid_deg(native_shape[0]) if native_shape else None,
    )

    ds = _assemble_dataset(
        decoded,
        variables,
        unique_members,
        len(step_hours),
        np.asarray(step_hours, dtype=np.int32),
        bbox,
    )
    if native_shape is not None:
        ds.attrs["source_grid_deg"] = grid_deg(native_shape[0])
    return ds
