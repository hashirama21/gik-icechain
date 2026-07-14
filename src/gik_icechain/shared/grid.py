"""ECMWF global lat/lon grid geometry.

``s3://ecmwf-forecasts`` is not homogeneous: dates before ~2024-02 are served at
0.4 deg under ``0p4-beta/``, later ones at 0.25 deg under ``0p25/``. Every module
that needs the grid shape or its step resolves it here, so the two eras cannot
drift apart again.
"""

from __future__ import annotations

# S3 path token -> (nlat, nlon) of the global grid.
GRID_SHAPES: dict[str, tuple[int, int]] = {
    "0p25": (721, 1440),
    "0p4-beta": (451, 900),
    "1p0": (181, 360),
    "1p": (181, 360),
}

DEFAULT_SHAPE: tuple[int, int] = GRID_SHAPES["0p25"]


def shape_for_uri(uri: str) -> tuple[int, int] | None:
    """(nlat, nlon) inferred from an ECMWF S3 URI, or None if unrecognised."""
    for token, shape in GRID_SHAPES.items():
        if f"/{token}/" in uri:
            return shape
    return None


def lat_lon_res(nlat: int, nlon: int) -> tuple[float, float]:
    """Degrees per cell of a global grid, derived from its shape."""
    return 180.0 / (nlat - 1), 360.0 / nlon


def grid_deg(nlat: int) -> float:
    """Latitude step in degrees - the label used for a grid's resolution."""
    return 180.0 / (nlat - 1)
