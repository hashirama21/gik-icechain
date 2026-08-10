"""Unit tests for the dashboard lead-time skill contract builder."""

import json

from dashboard.data_pipeline.pipeline import lead_curve_json


def _scores_file(results, day, pcode, state, p_yellow=0.0, p_orange=0.0, p_red=0.0):
    (results / f"{day}_risk_scores.json").write_text(
        json.dumps(
            {
                "date": day,
                "units": {
                    pcode: {
                        "risk_state": state,
                        "p_yellow": p_yellow,
                        "p_orange": p_orange,
                        "p_red": p_red,
                    }
                },
            }
        )
    )


_EMDAT_HEADER = (
    "DisNo.,Disaster Type,ISO,Country,Admin1,Admin1 Code,"
    "Start Date,End Date,Total Deaths,No. Affected\n"
)


def test_lead_curve_json_written(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    # KE001 flagged Orange 2 days ahead, Red on onset day.
    _scores_file(results, "2024-04-08", "KE001", 2, p_orange=0.5)
    _scores_file(results, "2024-04-09", "KE001", 2, p_orange=0.5)
    _scores_file(results, "2024-04-10", "KE001", 3, p_red=0.6)

    emdat = tmp_path / "emdat.csv"
    emdat.write_text(
        _EMDAT_HEADER
        + "2024-0001,Flood,KEN,Kenya,Turkana,KE001,2024-04-10,2024-04-11,1,100\n"
    )

    out = tmp_path / "web"
    n = lead_curve_json(results, out, emdat, max_lead=5)
    assert n == 1

    payload = json.loads((out / "data" / "lead_time_skill.json").read_text())
    assert payload["n_events"] == 1
    assert payload["max_lead"] == 5
    assert payload["curve"]["0"]["recall_red"] == 1.0
    event = payload["events"][0]
    assert event["admin1_pcode"] == "KE001"
    assert event["first_detection_lead"]["orange"] == 2
    assert event["first_detection_lead"]["red"] == 0


def test_lead_curve_missing_emdat_returns_zero(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _scores_file(results, "2024-04-10", "KE001", 3, p_red=0.6)
    n = lead_curve_json(results, tmp_path / "web", tmp_path / "nope.csv", max_lead=3)
    assert n == 0
    assert not (tmp_path / "web" / "data" / "lead_time_skill.json").exists()
