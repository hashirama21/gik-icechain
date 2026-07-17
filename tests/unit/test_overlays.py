"""Unit tests for the static exceedance PNG overlays."""

import json

import numpy as np
import pytest
import xarray as xr
from dashboard.data_pipeline.overlays import (
    _colorize,
    render_date_overlays,
    render_overlay_png,
)


def _da(values, lats, lons):
    return xr.DataArray(
        values.astype("float32"),
        dims=["latitude", "longitude"],
        coords={"latitude": lats, "longitude": lons},
    )


class TestColorize:
    def test_transparent_below_floor_and_for_nan(self):
        rgba = _colorize(np.array([[0.0, 0.01, np.nan], [0.5, 1.0, 0.02]]))
        assert rgba[0, 0, 3] == 0
        assert rgba[0, 1, 3] == 0
        assert rgba[0, 2, 3] == 0
        assert rgba[1, 0, 3] > 100
        assert rgba[1, 1, 3] == 230

    def test_ramp_monotonic_toward_red(self):
        rgba = _colorize(np.array([[0.1, 0.9]]))
        assert rgba[0, 1, 0] > 150
        assert rgba[0, 1, 1] < rgba[0, 0, 1]


class TestRenderOverlayPng:
    def test_writes_png_and_returns_half_cell_padded_bounds(self, tmp_path):
        da = _da(np.full((5, 4), 0.6), np.linspace(10.0, 2.0, 5), np.linspace(30.0, 36.0, 4))
        out = tmp_path / "o.png"

        bounds = render_overlay_png(da, out)

        assert out.exists() and out.read_bytes()[:4] == b"\x89PNG"
        (south, west), (north, east) = bounds
        assert south == pytest.approx(2.0 - 1.0)
        assert north == pytest.approx(10.0 + 1.0)
        assert west == pytest.approx(30.0 - 1.0)
        assert east == pytest.approx(36.0 + 1.0)

    def test_ascending_latitude_is_flipped(self, tmp_path):
        values = np.zeros((3, 3))
        values[0] = 1.0
        asc = _da(values, np.array([0.0, 5.0, 10.0]), np.array([30.0, 35.0, 40.0]))
        desc = _da(values[::-1], np.array([10.0, 5.0, 0.0]), np.array([30.0, 35.0, 40.0]))

        b_asc = render_overlay_png(asc, tmp_path / "asc.png")
        b_desc = render_overlay_png(desc, tmp_path / "desc.png")

        assert b_asc == b_desc
        assert (tmp_path / "asc.png").read_bytes() == (tmp_path / "desc.png").read_bytes()


class TestRenderDateOverlays:
    def _ds(self):
        rng = np.random.default_rng(7)
        return xr.Dataset(
            {
                "exceedance_prob": (
                    ["window", "return_period", "latitude", "longitude"],
                    rng.random((2, 2, 6, 5)).astype("float32"),
                )
            },
            coords={
                "window": [24, 72],
                "return_period": [5, 10],
                "latitude": np.linspace(10.0, 0.0, 6),
                "longitude": np.linspace(30.0, 38.0, 5),
            },
        )

    def test_writes_pngs_and_manifest(self, tmp_path):
        n = render_date_overlays(self._ds(), "2024-11-04", tmp_path, [24, 72], [5])

        assert n == 2
        out = tmp_path / "2024-11-04" / "overlays"
        manifest = json.loads((out / "overlays.json").read_text())
        assert set(manifest) == {"exceedance_24h_5y.png", "exceedance_72h_5y.png"}
        for name, meta in manifest.items():
            assert (out / name).exists()
            assert meta["rp"] == 5
            assert len(meta["bounds"]) == 2

    def test_missing_selector_is_skipped(self, tmp_path):
        n = render_date_overlays(self._ds(), "2024-11-04", tmp_path, [168], [100])

        assert n == 0
        assert not (tmp_path / "2024-11-04" / "overlays" / "overlays.json").exists()
