"""Unit tests for the dashboard's per-unit zonal exceedance extraction."""

import json

import numpy as np
import pytest
import xarray as xr
from dashboard.data_pipeline.pipeline import _zonal, dependency

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


def _write_store_with_lead(tmp_path, windows_h, return_periods, leads, horizon_val, lead_vals):
    """Store with a constant max-horizon field and a per-lead field (constant per lead)."""
    lats = np.array([4.5, 3.5, 2.5, 1.5], dtype="float64")
    lons = np.array([33.5, 34.5, 35.5, 36.5], dtype="float64")
    nw, nr, nl = len(windows_h), len(return_periods), len(leads)
    by_lead = np.stack(
        [np.full((4, 4, nw, nr), v, dtype="float32") for v in lead_vals], axis=0
    )
    ds = xr.Dataset(
        {
            "exceedance_prob": (
                ["latitude", "longitude", "window", "return_period"],
                np.full((4, 4, nw, nr), horizon_val, dtype="float32"),
            ),
            "exceedance_prob_by_lead": (
                ["lead", "latitude", "longitude", "window", "return_period"],
                by_lead,
            ),
        },
        coords={
            "latitude": lats, "longitude": lons,
            "window": windows_h, "return_period": return_periods, "lead": leads,
        },
    )
    store = str(tmp_path / "exceedance_lead.zarr")
    ds.to_zarr(store, mode="w")
    return store


class TestZonalLead:
    def test_selects_requested_lead(self, tmp_path):
        store = _write_store_with_lead(
            tmp_path, [24], [5], leads=[0, 1, 2], horizon_val=0.1, lead_vals=[0.2, 0.9, 0.3]
        )
        boundaries = tmp_path / "b.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        r1 = _zonal(boundaries, "2024-11-04", store, None, lead=1)
        assert r1["KEN_Turkana"]["gev"]["24h"]["5"] == 0.9
        r0 = _zonal(boundaries, "2024-11-04", store, None, lead=0)
        assert r0["KEN_Turkana"]["gev"]["24h"]["5"] == 0.2

    def test_default_is_max_horizon(self, tmp_path):
        store = _write_store_with_lead(
            tmp_path, [24], [5], leads=[0, 1], horizon_val=0.1, lead_vals=[0.2, 0.9]
        )
        boundaries = tmp_path / "b.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        r = _zonal(boundaries, "2024-11-04", store, None)
        assert r["KEN_Turkana"]["gev"]["24h"]["5"] == pytest.approx(0.1)

    def test_absent_lead_falls_back_to_horizon(self, tmp_path):
        store = _write_store_with_lead(
            tmp_path, [24], [5], leads=[0, 1], horizon_val=0.1, lead_vals=[0.2, 0.9]
        )
        boundaries = tmp_path / "b.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        r = _zonal(boundaries, "2024-11-04", store, None, lead=9)
        assert r["KEN_Turkana"]["gev"]["24h"]["5"] == pytest.approx(0.1)

    def test_with_leads_emits_all_leads(self, tmp_path):
        store = _write_store_with_lead(
            tmp_path, [24], [5], leads=[0, 1, 2], horizon_val=0.1, lead_vals=[0.2, 0.9, 0.3]
        )
        boundaries = tmp_path / "b.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        r = _zonal(boundaries, "2024-11-04", store, None, with_leads=True)
        gbl = r["KEN_Turkana"]["gev_by_lead"]
        assert set(gbl) == {"0", "1", "2"}
        assert gbl["1"]["24h"]["5"] == 0.9
        # The max-horizon gev is still the default view.
        assert r["KEN_Turkana"]["gev"]["24h"]["5"] == pytest.approx(0.1)

    def test_with_leads_absent_when_store_lacks_variable(self, tmp_path):
        store = _write_store(tmp_path, [24], [5])
        boundaries = tmp_path / "b.geojson"
        boundaries.write_text(json.dumps(_BOUNDARIES))

        r = _zonal(boundaries, "2024-11-04", store, None, with_leads=True)
        assert "gev_by_lead" not in r["KEN_Turkana"]


class TestDependencyContract:
    """dependency.json is what the web DependencyPanel lead selector consumes."""

    def _boundaries(self, tmp_path):
        b = tmp_path / "b.geojson"
        b.write_text(json.dumps(_BOUNDARIES))
        return b

    def test_gev_by_lead_lands_in_contract(self, tmp_path):
        store = _write_store_with_lead(
            tmp_path, [24], [5], leads=[0, 1, 2], horizon_val=0.1, lead_vals=[0.2, 0.9, 0.3]
        )
        boundaries = self._boundaries(tmp_path)
        scores = {"units": {"KEN_Turkana": {"risk_state": 2, "rp_years": 5}}}
        data_dir = tmp_path / "data"

        dependency(scores, boundaries, data_dir, "2024-11-04", store, None)

        dep = json.loads((data_dir / "2024-11-04" / "dependency.json").read_text())
        gbl = dep["KEN_Turkana"]["gev_by_lead"]
        assert set(gbl) == {"0", "1", "2"}
        assert gbl["1"]["24h"]["5"] == 0.9

    def test_no_gev_by_lead_when_store_lacks_variable(self, tmp_path):
        store = _write_store(tmp_path, [24], [5])
        boundaries = self._boundaries(tmp_path)
        scores = {"units": {"KEN_Turkana": {"risk_state": 1, "rp_years": 5}}}
        data_dir = tmp_path / "data"

        dependency(scores, boundaries, data_dir, "2024-11-04", store, None)

        dep = json.loads((data_dir / "2024-11-04" / "dependency.json").read_text())
        assert "gev_by_lead" not in dep["KEN_Turkana"]


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
