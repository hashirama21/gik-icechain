"""Manifest-aware data loader for C2.

Reads VirtualChunkRefs directly from an IceChunk store (produced by C1),
coalesces byte ranges, fetches GRIB2 data from S3 in parallel, decodes
via eccodes, and assembles a concrete xr.Dataset — without any HuggingFace
access and without loading the full global grid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:
    import xarray as xr

from gik_icechain.shared.byte_range import (
    ByteRange,
    coalesce_byte_ranges,
    fetch_coalesced_ranges,
)

log = structlog.get_logger(__name__)

try:
    import eccodes

    _HAS_ECCODES = True
except ImportError:
    eccodes = None  # type: ignore[assignment]
    _HAS_ECCODES = False

_GRID_RES = 0.25
_NLAT_GLOBAL = 721  # 90N to 90S inclusive
_NLON_GLOBAL = 1440  # 0E to 359.75E


def _bbox_to_slices(
    bbox: tuple[float, float, float, float],
) -> tuple[slice, slice]:
    """Convert a geographic bounding box to numpy index slices.

    ECMWF 0.25-deg grid: lat[0]=90N (descending), lon[0]=0E.

    Args:
        bbox: (lat_min, lat_max, lon_min, lon_max) in degrees.

    Returns:
        (lat_slice, lon_slice) for indexing a (721, 1440) global grid.
    """
    lat_min, lat_max, lon_min, lon_max = bbox
    lat_idx_top = round((90.0 - lat_max) / _GRID_RES)
    lat_idx_bot = round((90.0 - lat_min) / _GRID_RES)
    lon_idx_left = round(lon_min / _GRID_RES) % _NLON_GLOBAL
    lon_idx_right = round(lon_max / _GRID_RES) % _NLON_GLOBAL

    # Clamp to valid range
    lat_idx_top = max(0, min(lat_idx_top, _NLAT_GLOBAL - 1))
    lat_idx_bot = max(0, min(lat_idx_bot, _NLAT_GLOBAL - 1))

    return (
        slice(lat_idx_top, lat_idx_bot + 1),
        slice(lon_idx_left, lon_idx_right + 1),
    )


def _decode_grib_message(
    data: bytes,
    bbox_slices: tuple[slice, slice] | None = None,
    *,
    uri: str = "",
    offset: int = 0,
    member: int = -1,
    step: int = -1,
) -> np.ndarray | None:
    """Decode a single GRIB2 message to a float32 ndarray.

    Uses eccodes directly (not GRIBCodec) for control over error handling
    and bbox sub-setting at decode time.

    Returns:
        ndarray of shape ``(nlat, nlon)`` (global) or ``(nlat_bbox, nlon_bbox)``
        if *bbox_slices* is provided.  ``None`` on decode failure.
    """
    if not _HAS_ECCODES:
        log.error("eccodes_not_installed", msg="pip install eccodes-python")
        return None

    try:
        msg_id = eccodes.codes_new_from_message(bytes(data))
        try:
            values = eccodes.codes_get_values(msg_id).astype(np.float32)
            nlat = eccodes.codes_get(msg_id, "Nj")
            nlon = eccodes.codes_get(msg_id, "Ni")
        finally:
            eccodes.codes_release(msg_id)

        grid = values.reshape(nlat, nlon)
        if bbox_slices is not None:
            grid = grid[bbox_slices[0], bbox_slices[1]]
        return grid

    except Exception:
        log.warning(
            "grib_decode_failed",
            uri=uri,
            offset=offset,
            member=member,
            step=step,
            exc_info=True,
        )
        return None


_VIRTUAL_CHUNK_TYPE = 2  # IceChunk 2.x ChunkType.virtual


def _extract_virtual_chunk_refs(
    session: Any,
    date_str: str,
    variables: list[str],
    max_steps: int,
) -> list[ByteRange]:
    """Extract byte-range references from an IceChunk store session.

    Uses ``store.array_chunk_iterator()`` (IceChunk 2.x) which yields batches
    of (coords, types, uris, offsets, lengths, extra) per array.  This gives
    (url, offset, length) per virtual chunk without triggering any S3 fetch.

    Args:
        session: An IceChunk readonly session.
        date_str: Forecast date group key (e.g. ``"2024-01-15"``).
        variables: List of variable names to extract (e.g. ``["tp"]``).
        max_steps: Maximum number of forecast steps to include.

    Returns:
        List of :class:`ByteRange` referencing GRIB2 byte ranges on S3.
    """
    import asyncio

    ic_store = session.store
    refs: list[ByteRange] = []

    async def _collect() -> None:
        for var in variables:
            array_path = f"{date_str}/{var}"
            try:
                it = ic_store.array_chunk_iterator(array_path)
                async for batch in it:
                    # batch = (coords, types, uris, offsets, lengths, extra)
                    coords_arr, types_arr, uris, offsets, lengths = (
                        batch[0], batch[1], batch[2], batch[3], batch[4]
                    )
                    for i in range(len(coords_arr)):
                        if int(types_arr[i]) != _VIRTUAL_CHUNK_TYPE:
                            continue
                        member_idx = int(coords_arr[i, 0])
                        step_idx   = int(coords_arr[i, 1])
                        if step_idx >= max_steps:
                            continue
                        uri    = uris[i]
                        offset = int(offsets[i])
                        length = int(lengths[i])
                        if not uri or length == 0:
                            continue
                        refs.append(ByteRange(
                            uri=uri,
                            offset=offset,
                            length=length,
                            metadata={
                                "member_idx": member_idx,
                                "step_idx":   step_idx,
                                "variable":   var,
                            },
                        ))
            except Exception as exc:
                log.warning(
                    "array_chunk_iterator_failed",
                    array_path=array_path,
                    error=str(exc)[:120],
                )

    try:
        asyncio.run(_collect())
    except RuntimeError:
        # already inside an event loop (shouldn't happen in subprocess workers)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_collect())
        finally:
            loop.close()

    log.info(
        "virtual_chunk_refs_extracted",
        date=date_str,
        n_refs=len(refs),
        variables=variables,
        max_steps=max_steps,
    )
    return refs


def _parse_chunk_location(location: Any) -> tuple[str | None, int, int]:
    """Extract (url, offset, length) from an IceChunk chunk location.

    The location object varies by IceChunk version:
    - dict with "url"/"uri", "offset", "length" keys
    - named tuple / dataclass with .url/.offset/.length attributes
    - 3-tuple (url, offset, length)

    Returns:
        ``(url, offset, length)`` or ``(None, 0, 0)`` on failure.
    """
    # dict-like
    if isinstance(location, dict):
        url = location.get("url") or location.get("uri")
        return (url, location.get("offset", 0), location.get("length", 0))

    # tuple / list
    if isinstance(location, (tuple, list)) and len(location) >= 3:
        return (str(location[0]), int(location[1]), int(location[2]))

    # object with attributes
    url = getattr(location, "url", None) or getattr(location, "uri", None)
    if url is not None:
        return (
            str(url),
            getattr(location, "offset", 0),
            getattr(location, "length", 0),
        )

    return (None, 0, 0)


def _assemble_dataset(
    decoded: dict[tuple, np.ndarray],
    variables: list[str],
    member_indices: list[int],
    max_steps: int,
    step_resolution_h: int,
    bbox: tuple[float, float, float, float] | None,
) -> xr.Dataset:
    """Assemble decoded grids into a concrete xr.Dataset.

    Args:
        decoded: Mapping ``(member_idx, step_idx, variable) -> ndarray(nlat, nlon)``.
        variables: Variable names.
        member_indices: Sorted list of unique member indices.
        max_steps: Number of forecast steps.
        step_resolution_h: Hours between steps.
        bbox: Geographic bounding box (lat_min, lat_max, lon_min, lon_max) or None.

    Returns:
        xr.Dataset with dims ``(member, step, latitude, longitude)``.
    """
    import xarray as xr

    if not decoded:
        raise ValueError("No decoded grids to assemble")

    # Determine spatial shape from the first decoded grid
    sample = next(iter(decoded.values()))
    nlat, nlon = sample.shape

    # Build member position map
    member_pos = {m: i for i, m in enumerate(member_indices)}
    n_members = len(member_indices)

    # Pre-allocate arrays per variable
    data_arrays: dict[str, np.ndarray] = {}
    for var in variables:
        arr = np.full(
            (n_members, max_steps, nlat, nlon), np.nan, dtype=np.float32
        )
        data_arrays[var] = arr

    # Place decoded grids
    for (member_idx, step_idx, var), grid in decoded.items():
        if var not in data_arrays:
            continue
        m_pos = member_pos.get(member_idx)
        if m_pos is None or step_idx >= max_steps:
            continue
        data_arrays[var][m_pos, step_idx] = grid

    # Build coordinates from decoded grid shape (not from bbox indices)
    # to guarantee coordinate arrays match actual data dimensions.
    steps = np.arange(max_steps, dtype=np.int32) * step_resolution_h
    if bbox is not None:
        lat_min, lat_max, lon_min, lon_max = bbox
        lats = np.linspace(lat_max, lat_min, nlat, dtype=np.float32)
        lons = np.linspace(lon_min, lon_max, nlon, dtype=np.float32)
    else:
        lats = (90.0 - np.arange(nlat) * _GRID_RES).astype(np.float32)
        lons = (np.arange(nlon) * _GRID_RES).astype(np.float32)

    coords = {
        "member": np.array(member_indices, dtype=np.int32),
        "step": steps,
        "latitude": lats,
        "longitude": lons,
    }

    ds_vars = {}
    for var, arr in data_arrays.items():
        ds_vars[var] = (["member", "step", "latitude", "longitude"], arr)

    return xr.Dataset(ds_vars, coords=coords)


def load_day_manifest_aware(
    session: Any,
    date_str: str,
    variables: list[str],
    max_step_h: int,
    step_resolution_h: int,
    step_buffer: int,
    bbox: tuple[float, float, float, float] | None,
    *,
    max_gap_bytes: int = 65_536,
    max_merged_bytes: int = 5_242_880,
    fetch_workers: int = 8,
    min_members: int = 10,
    s3_region: str = "eu-central-1",
) -> xr.Dataset:
    """Load one forecast day via the manifest-aware path.

    1. Extract VirtualChunkRefs from IceChunk (metadata only).
    2. Coalesce byte ranges for efficient S3 fetching.
    3. Fetch coalesced ranges in parallel.
    4. Decode GRIB2 messages and apply bbox subsetting.
    5. Validate member count.
    6. Assemble a concrete xr.Dataset.

    Args:
        session: IceChunk readonly session.
        date_str: Forecast date key (e.g. ``"2024-01-15"``).
        variables: Variables to load (e.g. ``["tp"]``).
        max_step_h: Maximum forecast horizon in hours.
        step_resolution_h: Hours between steps.
        step_buffer: Extra steps beyond max_step_h.
        bbox: Geographic bounding box ``(lat_min, lat_max, lon_min, lon_max)``.
        max_gap_bytes: Coalescing gap threshold.
        max_merged_bytes: Maximum merged range size.
        fetch_workers: Number of parallel S3 fetch threads.
        min_members: Minimum required ensemble members.
        s3_region: AWS region for S3 access.

    Returns:
        Concrete xr.Dataset with dims ``(member, step, latitude, longitude)``.

    Raises:
        ValueError: If fewer than *min_members* members are decoded.
    """
    max_steps = (max_step_h // step_resolution_h) + step_buffer + 1

    # Step 1: Extract virtual chunk refs from IceChunk metadata
    byte_ranges = _extract_virtual_chunk_refs(
        session, date_str, variables, max_steps
    )
    if not byte_ranges:
        raise ValueError(f"No virtual chunk refs found for {date_str}")

    # Step 2: Coalesce adjacent byte ranges
    coalesced = coalesce_byte_ranges(
        byte_ranges,
        max_gap_bytes=max_gap_bytes,
        max_merged_bytes=max_merged_bytes,
    )

    # Step 3: Fetch coalesced ranges from S3
    raw_data = fetch_coalesced_ranges(
        coalesced,
        max_workers=fetch_workers,
        s3_region=s3_region,
    )

    # Step 4: Decode GRIB2 messages
    bbox_slices = _bbox_to_slices(bbox) if bbox is not None else None
    decoded: dict[tuple, np.ndarray] = {}

    for (member_idx, step_idx, var), data in raw_data.items():
        grid = _decode_grib_message(
            data,
            bbox_slices,
            uri=f"{date_str}/{var}",
            offset=0,
            member=member_idx if member_idx is not None else -1,
            step=step_idx if step_idx is not None else -1,
        )
        if grid is not None:
            decoded[(member_idx, step_idx, var)] = grid

    if not decoded:
        raise ValueError(f"All GRIB2 decodes failed for {date_str}")

    # Step 5: Validate member count
    unique_members = sorted({k[0] for k in decoded})
    if len(unique_members) < min_members:
        raise ValueError(
            f"Only {len(unique_members)} members decoded for {date_str}, "
            f"minimum required: {min_members}"
        )

    # Step 6: Assemble dataset
    log.info(
        "manifest_aware_load_complete",
        date=date_str,
        n_members=len(unique_members),
        n_decoded=len(decoded),
        n_coalesced_requests=len(coalesced),
    )

    return _assemble_dataset(
        decoded,
        variables,
        unique_members,
        max_steps,
        step_resolution_h,
        bbox,
    )
