"""Unit tests for the alert-extent fields of the calendar index."""

import json

from dashboard.data_pipeline.pipeline import update_index


def _scores(states):
    return {"units": {f"U{i}": {"risk_state": s} for i, s in enumerate(states)}}


class TestUpdateIndexExtent:
    def test_counts_orange_and_red(self, tmp_path):
        update_index(tmp_path, "2024-11-04", _scores([0, 0, 1, 2, 2, 3]))

        idx = json.loads((tmp_path / "index.json").read_text())
        entry = idx["2024-11-04"]
        assert entry["worst_risk"] == 3
        assert entry["risk_label"] == "Red"
        assert entry["n_units"] == 6
        assert entry["n_orange"] == 2
        assert entry["n_red"] == 1

    def test_merges_with_existing_index(self, tmp_path):
        update_index(tmp_path, "2024-11-04", _scores([3]))
        update_index(tmp_path, "2024-11-05", _scores([0, 2]))

        idx = json.loads((tmp_path / "index.json").read_text())
        assert set(idx) == {"2024-11-04", "2024-11-05"}
        assert idx["2024-11-05"]["n_orange"] == 1
        assert idx["2024-11-05"]["n_red"] == 0

    def test_empty_units(self, tmp_path):
        update_index(tmp_path, "2024-11-04", {"units": {}})

        entry = json.loads((tmp_path / "index.json").read_text())["2024-11-04"]
        assert entry["worst_risk"] == -1
        assert entry["n_orange"] == 0
        assert entry["n_red"] == 0
