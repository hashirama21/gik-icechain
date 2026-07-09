"""Unit tests for the batch risk engine: pure helpers, checkpointing, and a
synthetic end-to-end batch run (no network, no credentials)."""

import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from gik_icechain.risk.crma_model import EastAfricaCluster
from gik_icechain.risk.dynamic_bn import DynamicBNState, init_state
from gik_icechain.risk.risk_engine import (
    _build_pcode_cluster_map,
    _config_fingerprint,
    _load_checkpoint,
    _pipeline_version,
    _save_checkpoint,
    _state_from_dict,
    run_risk_batch,
)


class TestHelpers:
    def test_config_fingerprint_default_without_file(self):
        assert _config_fingerprint(None) == "default"

    def test_config_fingerprint_tracks_content(self, tmp_path):
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("a: 1")
        fp1 = _config_fingerprint(cfg)
        assert fp1.startswith("sha256:")
        assert _config_fingerprint(cfg) == fp1
        cfg.write_text("a: 2")
        assert _config_fingerprint(cfg) != fp1

    def test_pipeline_version_is_string(self):
        assert isinstance(_pipeline_version(), str)

    def test_build_pcode_cluster_map(self, square_admin_gdf):
        clusters = _build_pcode_cluster_map(square_admin_gdf)
        assert clusters["AA1"] == EastAfricaCluster.EQUATORIAL_EAST  # KEN
        assert clusters["BB2"] == EastAfricaCluster.HORN_ARID  # SOM

    def test_unknown_iso3_falls_back_to_equatorial(self, square_admin_gdf):
        gdf = square_admin_gdf.copy()
        gdf["shapeGroup"] = ["XXX", "XXX"]
        clusters = _build_pcode_cluster_map(gdf)
        assert all(c == EastAfricaCluster.EQUATORIAL_EAST for c in clusters.values())

    def test_state_from_dict_pre_v3_without_gpm_history(self):
        s = _state_from_dict(
            {
                "api_mm": 12.5,
                "consecutive_days": 2,
                "sat_consecutive_days": 1,
                "last_risk_state": 3,
            }
        )
        assert s == DynamicBNState(
            api_mm=12.5,
            consecutive_days=2,
            sat_consecutive_days=1,
            last_risk_state=3,
            gpm_history=(),
        )


class TestCheckpoint:
    def _states(self) -> dict[str, dict[int, DynamicBNState]]:
        return {
            "AA1": {5: init_state(33.0), 20: init_state(11.0)},
            "BB2": {5: init_state(20.0), 20: init_state(20.0)},
        }

    def test_roundtrip(self, tmp_path):
        uri = str(tmp_path / "_checkpoint.json")
        states = self._states()
        _save_checkpoint(uri, states, next_date=date(2024, 11, 10))

        defaults = {
            p: {rp: init_state() for rp in (5, 20)} for p in ("AA1", "BB2")
        }
        loaded, resume = _load_checkpoint(
            uri, defaults, date(2024, 11, 1), rp_options=[5, 20]
        )
        assert resume == date(2024, 11, 10)
        assert loaded["AA1"][5].api_mm == 33.0
        assert loaded["AA1"][20].api_mm == 11.0

    def test_stale_checkpoint_ignored(self, tmp_path):
        """A checkpoint at or before the requested start is a no-op."""
        uri = str(tmp_path / "_checkpoint.json")
        _save_checkpoint(uri, self._states(), next_date=date(2024, 11, 1))
        defaults = {"AA1": {5: init_state(99.0)}}
        loaded, resume = _load_checkpoint(
            uri, defaults, date(2024, 11, 1), rp_options=[5]
        )
        assert resume == date(2024, 11, 1)
        assert loaded["AA1"][5].api_mm == 99.0

    def test_missing_file_returns_defaults(self, tmp_path):
        defaults = {"AA1": {5: init_state(99.0)}}
        loaded, resume = _load_checkpoint(
            str(tmp_path / "absent.json"), defaults, date(2024, 11, 1), rp_options=[5]
        )
        assert resume == date(2024, 11, 1)
        assert loaded is defaults

    def test_corrupt_checkpoint_returns_defaults(self, tmp_path):
        uri = tmp_path / "_checkpoint.json"
        uri.write_text("{not json")
        defaults = {"AA1": {5: init_state(99.0)}}
        loaded, resume = _load_checkpoint(
            str(uri), defaults, date(2024, 11, 1), rp_options=[5]
        )
        assert resume == date(2024, 11, 1)
        assert loaded is defaults

    def test_v1_flat_state_seeded_into_every_rp(self, tmp_path):
        """Pre-per-RP checkpoints (no version key) migrate by seeding each RP."""
        uri = tmp_path / "_checkpoint.json"
        uri.write_text(
            json.dumps(
                {
                    "next_date": "2024-11-10",
                    "bn_states": {"AA1": asdict(init_state(42.0))},
                }
            )
        )
        defaults = {"AA1": {rp: init_state() for rp in (5, 20)}}
        loaded, resume = _load_checkpoint(
            str(uri), defaults, date(2024, 11, 1), rp_options=[5, 20]
        )
        assert resume == date(2024, 11, 10)
        assert loaded["AA1"][5].api_mm == 42.0
        assert loaded["AA1"][20].api_mm == 42.0

    def test_units_missing_from_checkpoint_get_defaults(self, tmp_path):
        """New admin units added since the checkpoint keep their fresh state."""
        uri = str(tmp_path / "_checkpoint.json")
        _save_checkpoint(uri, {"AA1": {5: init_state(33.0)}}, date(2024, 11, 10))
        defaults = {
            "AA1": {5: init_state()},
            "NEW9": {5: init_state(77.0)},
        }
        loaded, _ = _load_checkpoint(uri, defaults, date(2024, 11, 1), rp_options=[5])
        assert loaded["AA1"][5].api_mm == 33.0
        assert loaded["NEW9"][5].api_mm == 77.0

    def test_checkpoint_file_is_valid_json_with_version(self, tmp_path):
        uri = tmp_path / "_checkpoint.json"
        _save_checkpoint(str(uri), self._states(), date(2024, 11, 10))
        payload = json.loads(Path(uri).read_text())
        assert payload["version"] >= 3
        assert payload["next_date"] == "2024-11-10"
        assert set(payload["bn_states"]) == {"AA1", "BB2"}


_BATCH_DATE = date(2024, 10, 15)
_WINDOWS_H = (24, 72, 168)
_RPS = (5, 20)


@pytest.fixture(scope="module")
def crma_models():
    """One built CRMAModel per cluster used by square_admin_gdf (KEN + SOM)."""
    pytest.importorskip("pgmpy", reason="pgmpy not installed")
    from gik_icechain.risk.crma_model import CRMAModel

    models = {}
    for cluster in (EastAfricaCluster.EQUATORIAL_EAST, EastAfricaCluster.HORN_ARID):
        m = CRMAModel(cluster=cluster)
        m.build()
        models[cluster] = m
    return models


def _make_batch_store(tmp_path, name: str = "exceedance.zarr", conf_value: int = 2) -> str:
    """Synthetic exceedance Zarr on the square_admin_gdf grid, with the
    storyline variables (tail/median) and ensemble confidence."""
    lat = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    lon = np.array([10.0, 11.0, 12.0, 13.0], dtype=np.float32)
    dims = ("date", "latitude", "longitude", "window", "return_period")
    shape = (1, lat.size, lon.size, len(_WINDOWS_H), len(_RPS))
    coords = {
        "date": [np.datetime64(_BATCH_DATE, "ns")],
        "latitude": lat,
        "longitude": lon,
        "window": np.array(_WINDOWS_H, dtype=np.int16),
        "return_period": np.array(_RPS, dtype=np.int16),
    }
    ds = xr.Dataset(
        {
            "exceedance_prob": (dims, np.full(shape, 0.6, dtype=np.float32)),
            "tail_ratio": (dims, np.full(shape, 1.5, dtype=np.float32)),
            "median_ratio": (dims, np.full(shape, 0.3, dtype=np.float32)),
            "ensemble_confidence": (
                dims[:3],
                np.full(shape[:3], conf_value, dtype=np.int8),
            ),
        },
        coords=coords,
    )
    out = str(tmp_path / name)
    ds.to_zarr(out, mode="w", consolidated=False)
    return out


@pytest.fixture
def batch_store(tmp_path):
    return _make_batch_store(tmp_path)


@pytest.fixture
def admin_path(tmp_path, square_admin_gdf):
    path = tmp_path / "admin1.gpkg"
    square_admin_gdf.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def gpm_dir(tmp_path):
    from tests.unit.test_gpm_loader import _write_gpm_file

    gpm = tmp_path / "gpm"
    gpm.mkdir()
    name = f"3B-DAY.MS.MRG.3IMERG.{_BATCH_DATE.strftime('%Y%m%d')}.V07B.nc4"
    _write_gpm_file(gpm, name)
    return gpm


class TestRunRiskBatch:
    """Synthetic end-to-end batch run: 2 units x 2 RPs x 2 days (the second
    day has no exceedance and exercises the GPM-only API spin-up path)."""

    def test_batch_end_to_end(
        self, crma_models, batch_store, admin_path, gpm_dir, tmp_path
    ):
        out_dir = tmp_path / "out"
        written = run_risk_batch(
            exceedance_store_uri=batch_store,
            gpm_dir=gpm_dir,
            admin_boundaries_path=admin_path,
            crma_models=crma_models,
            output_dir=out_dir,
            start=_BATCH_DATE,
            end=_BATCH_DATE + timedelta(days=1),
            rp_signal=5,
            rp_signal_options=[5, 20],
            checkpoint_interval=1,
            riverine_feed=({"BB2": ["AA1"]}, 1.0, "max"),
        )

        # Only the day with exceedance produces a scores file.
        assert len(written) == 1
        payload = json.loads(Path(written[0]).read_text())
        assert payload["date"] == _BATCH_DATE.isoformat()
        assert payload["meta"]["rp_signal"] == 5
        units = payload["units"]
        assert set(units) == {"AA1", "BB2"}

        for u in units.values():
            assert u["risk_label"] in {"Green", "Yellow", "Orange", "Red", "No_Data"}
            assert set(u["risk_by_rp"]) == {"5", "20"}

        aa1 = units["AA1"]
        # Tail-aware + storyline fields emitted with the tail/median variables.
        assert aa1["forecast_tail_ratio"] == pytest.approx(1.5)
        assert "storyline_median_state" in aa1
        assert aa1["storyline_spread"] >= 0
        # Riverine feed pools AA1's tail ratio into downstream BB2.
        assert units["BB2"]["riverine_ratio"] == pytest.approx(1.5)

        # Boundaries written once; checkpoint cleaned up after completion.
        assert (out_dir / "admin1_boundaries.geojson").exists()
        assert not (out_dir / "_checkpoint.json").exists()

    def test_sentinel_confidence_equals_default(
        self, crma_models, admin_path, gpm_dir, tmp_path
    ):
        """A store padded with the int8 missing-data sentinel (-1) must score
        exactly like one carrying the engine's default confidence (2=High) -
        the sentinel must not be clipped into Low confidence and damp the BN."""

        def _run(conf_value: int, tag: str) -> dict:
            store = _make_batch_store(tmp_path, f"exc_{tag}.zarr", conf_value)
            written = run_risk_batch(
                exceedance_store_uri=store,
                gpm_dir=gpm_dir,
                admin_boundaries_path=admin_path,
                crma_models=crma_models,
                output_dir=tmp_path / f"out_{tag}",
                start=_BATCH_DATE,
                end=_BATCH_DATE,
                rp_signal=5,
            )
            return json.loads(Path(written[0]).read_text())["units"]

        assert _run(-1, "sentinel") == _run(2, "default")
