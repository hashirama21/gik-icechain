"""Unit tests for the dashboard's per-unit zonal exceedance extraction."""

import json

import numpy as np
import xarray as xr
from dashboard.data_pipeline.pipeline import _zonal

_BOUNDARIES = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "admin1_pcode": "KEN_Turkana",
                "shapeName": "Turkana",
                "shapeGroup": "KEN",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[34.0, 2.0], [36.0, 2.0], [36.0, 4.0], [34.0, 4.0], [34.0, 2.0]]],
            },
        }
    ],
}


def _write_store(tmp_path, windows_h, return_periods):
    lats = np.array([4.5, 3.5, 2.5, 1.5], dtype="float64")
    lons = np.array([33.5, 34.5, 35.5, 36.5], dtype="float64")
    rng = np.random.default_rng(0)
    ds = xr.Dataset(
        {
            "exceedance_prob": (
                ["latitude", "longitude", "window", "return_period"],
                rng.random((4, 4, len(windows_h), len(return_periods))).astype("float32"),
            ),
            "ensemble_confidence": (
                ["latitude", "longitude"],
                rng.random((4, 4)).astype("float32") * 2,
            ),
        },
        coords={
            "latitude": lats,
            "longitude": lons,
            "window": windows_h,
            "return_period": return_periods,
        },
    )
    store = str(tmp_path / "exceedance.zarr")
    ds.to_zarr(store, mode="w")
    return store


class TestZonal:
    def test_extracts_gev_for_every_configured_window(self, tmp_path):
        store = _write_store(tmp_path, [3, 6, 12, 24, 48, 72, 168], [2, 5, 10, 20, 40, 100])
        boundaries = tmp_path / "boundaries.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        result = _zonal(boundaries, "2024-11-04", store, None)

        assert "KEN_Turkana" in result
        gev = result["KEN_Turkana"]["gev"]
        assert set(gev["24h"]) == {"2", "5", "10", "20", "40", "100"}
        assert 0.0 <= result["KEN_Turkana"]["conf_m"] <= 51

    def test_window_absent_from_store_is_skipped_not_misaligned(self, tmp_path):
        # Store predates the 10-day window: only 7 windows exist. WINDOWS_H in
        # pipeline.py now lists 8 (240h included) - the 8th must be skipped
        # cleanly, not read from a neighboring window's data by position.
        store = _write_store(tmp_path, [3, 6, 12, 24, 48, 72, 168], [2, 5, 10, 20, 40, 100])
        boundaries = tmp_path / "boundaries.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        result = _zonal(boundaries, "2024-11-04", store, None)

        assert result["KEN_Turkana"]["gev"]["10d"] == {}
        assert result["KEN_Turkana"]["gev"]["7d"] != {}

    def test_extended_store_with_240h_is_read_correctly(self, tmp_path):
        store = _write_store(tmp_path, [3, 6, 12, 24, 48, 72, 168, 240], [2, 5, 10, 20, 40, 100])
        boundaries = tmp_path / "boundaries.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        result = _zonal(boundaries, "2024-11-04", store, None)

        assert set(result["KEN_Turkana"]["gev"]["10d"]) == {"2", "5", "10", "20", "40", "100"}

    def test_unit_outside_grid_is_omitted(self, tmp_path):
        store = _write_store(tmp_path, [24], [5])
        boundaries = tmp_path / "boundaries.geojson"
        far_away = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"admin1_pcode": "XXX_Nowhere"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[100, 40], [101, 40], [101, 41], [100, 41], [100, 40]]],
                    },
                }
            ],
        }
        boundaries.write_text(json.dumps(far_away))

        result = _zonal(boundaries, "2024-11-04", store, None)

        assert result == {}
