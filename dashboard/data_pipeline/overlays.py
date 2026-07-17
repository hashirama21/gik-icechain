"""Static raster overlays for the storymaps.

Renders the exceedance field as small transparent PNGs (one per date x
window x return period) served with the data contract. At the store's
native resolution (159x137 / 100x86 cells) a pre-rendered image is
visually identical to on-the-fly tiling, with no tile server to run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import structlog

log = structlog.get_logger(__name__)

_ALPHA_FLOOR = 0.02
_SCALE = 6

# YlOrRd-style ramp anchors (value, r, g, b), alpha ramps 90 -> 230.
_RAMP = np.array(
    [
        [0.00, 255, 255, 178],
        [0.25, 254, 204, 92],
        [0.50, 253, 141, 60],
        [0.75, 240, 59, 32],
        [1.00, 189, 0, 38],
    ]
)


def _colorize(values: np.ndarray) -> np.ndarray:
    """Map a (ny, nx) float field in [0, 1] to an RGBA uint8 image."""
    v = np.nan_to_num(values, nan=0.0).clip(0.0, 1.0)
    rgba = np.zeros((*v.shape, 4), dtype=np.uint8)
    for c in range(3):
        rgba[..., c] = np.interp(v, _RAMP[:, 0], _RAMP[:, c + 1]).astype(np.uint8)
    alpha = np.where(v < _ALPHA_FLOOR, 0.0, 90 + 140 * v)
    rgba[..., 3] = alpha.astype(np.uint8)
    return rgba


def render_overlay_png(da, out_path: Path) -> list[list[float]]:
    """Render one 2-D exceedance slice to a transparent PNG.

    *da* is an xr.DataArray with descending-latitude ``latitude`` /
    ``longitude`` coords (the store's native order, which matches PNG
    row order top to bottom).

    Returns Leaflet bounds ``[[south, west], [north, east]]``.
    """
    from PIL import Image

    lats = da["latitude"].values
    lons = da["longitude"].values
    values = np.asarray(da.values, dtype="float64")
    if lats[0] < lats[-1]:
        lats = lats[::-1]
        values = values[::-1]

    rgba = _colorize(values)
    img = Image.fromarray(rgba, mode="RGBA")
    img = img.resize((img.width * _SCALE, img.height * _SCALE), Image.NEAREST)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, optimize=True)

    half_lat = abs(float(lats[0] - lats[1])) / 2 if len(lats) > 1 else 0.0
    half_lon = abs(float(lons[1] - lons[0])) / 2 if len(lons) > 1 else 0.0
    return [
        [float(lats[-1]) - half_lat, float(lons[0]) - half_lon],
        [float(lats[0]) + half_lat, float(lons[-1]) + half_lon],
    ]


def render_date_overlays(
    ds,
    day: str,
    data_dir: Path,
    windows: list[int],
    rps: list[int],
) -> int:
    """Render every (window, rp) overlay for one date; write overlays.json.

    *ds* is the exceedance dataset already selected to *day*.
    Returns the number of PNGs written.
    """
    out_dir = data_dir / day / "overlays"
    manifest: dict[str, dict] = {}
    n = 0
    for wh in windows:
        for rp in rps:
            try:
                da = ds["exceedance_prob"].sel(window=wh, return_period=rp).load()
            except KeyError:
                continue
            name = f"exceedance_{wh}h_{rp}y.png"
            bounds = render_overlay_png(da, out_dir / name)
            manifest[name] = {"bounds": bounds, "window": wh, "rp": rp}
            n += 1
    if manifest:
        (out_dir / "overlays.json").write_text(json.dumps(manifest, separators=(",", ":")))
    log.info("overlays_rendered", date=day, n_pngs=n)
    return n
