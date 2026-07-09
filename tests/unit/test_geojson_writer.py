"""Unit tests for the lightweight risk-score / boundaries / EAHW writers."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from gik_icechain.risk.geojson_writer import (
    build_score,
    export_eahw_format,
    write_boundaries,
    write_risk_scores,
)


def _result(state: int = 2, label: str = "Orange") -> dict:
    return {
        "risk_state": state,
        "risk_label": label,
        "p_green": 0.10001,
        "p_yellow": 0.2,
        "p_orange": 0.55,
        "p_red": 0.14999,
    }


class TestBuildScore:
    def test_basic_fields(self, make_evidence):
        unit = pd.Series({"admin1_pcode": "AA1"})
        ev = make_evidence(exceedance_prob_24h=0.42, api_mm=33.333)
        pcode, score = build_score(unit, _result(), ev)
        assert pcode == "AA1"
        assert score["risk_state"] == 2
        assert score["risk_label"] == "Orange"
        assert score["p_green"] == pytest.approx(0.1)
        assert score["exceedance_24h"] == pytest.approx(0.42)
        assert score["api_mm"] == pytest.approx(33.33)
        assert score["emdat_flood_match"] is False

    def test_emdat_flag_and_risk_by_rp(self, make_evidence):
        unit = pd.Series({"admin1_pcode": "AA1"})
        _, score = build_score(
            unit,
            _result(),
            make_evidence(),
            emdat_flood_match=True,
            risk_by_rp={"5": _result(1, "Yellow")},
        )
        assert score["emdat_flood_match"] is True
        assert score["risk_by_rp"]["5"]["risk_label"] == "Yellow"
        assert score["risk_by_rp"]["5"]["p_green"] == pytest.approx(0.1)


class TestWriteRiskScores:
    def test_roundtrip_with_meta(self, tmp_path):
        day = date(2024, 11, 15)
        scores = {"AA1": {"risk_state": 0, "risk_label": "Green"}}
        meta = {"pipeline_version": "test", "rp_signal": 5}
        out = write_risk_scores(day, scores, str(tmp_path), meta=meta)
        payload = json.loads(Path(out).read_text())
        assert payload["date"] == "2024-11-15"
        assert payload["units"] == scores
        assert payload["meta"] == meta

    def test_no_meta_key_when_absent(self, tmp_path):
        out = write_risk_scores(date(2024, 11, 15), {}, str(tmp_path))
        assert "meta" not in json.loads(Path(out).read_text())


class TestWriteBoundaries:
    def test_write_and_idempotent(self, tmp_path, square_admin_gdf):
        out = write_boundaries(square_admin_gdf, str(tmp_path))
        fc = json.loads(Path(out).read_text())
        assert fc["type"] == "FeatureCollection"
        pcodes = {f["properties"]["admin1_pcode"] for f in fc["features"]}
        assert pcodes == {"AA1", "BB2"}
        # Second call must not rewrite the existing file.
        Path(out).write_text("sentinel")
        assert write_boundaries(square_admin_gdf, str(tmp_path)) == out
        assert Path(out).read_text() == "sentinel"


class TestExportEahwFormat:
    def test_end_to_end(self, tmp_path, square_admin_gdf, make_evidence):
        day = date(2024, 11, 15)
        boundaries = Path(write_boundaries(square_admin_gdf, str(tmp_path)))
        _, score = build_score(
            pd.Series({"admin1_pcode": "AA1"}), _result(), make_evidence()
        )
        scores_path = Path(
            write_risk_scores(day, {"AA1": score}, str(tmp_path), meta={"v": "1"})
        )
        out = tmp_path / "eahw" / "eahw.geojson"
        export_eahw_format(scores_path, boundaries, out)

        fc = json.loads(out.read_text())
        assert fc["meta"] == {"v": "1"}
        by_pcode = {f["properties"]["admin1_pcode"]: f["properties"] for f in fc["features"]}
        # AA1 scored Orange (risk_state 2): EAHW level 3, probability = p_orange %.
        assert by_pcode["AA1"]["risk_level"] == 3
        assert by_pcode["AA1"]["risk_label"] == "Orange"
        assert by_pcode["AA1"]["probability"] == 55
        assert by_pcode["AA1"]["valid_date"] == "2024-11-15"
        # BB2 has no score: defaults to Green / level 1.
        assert by_pcode["BB2"]["risk_level"] == 1
        assert by_pcode["BB2"]["risk_label"] == "Green"
