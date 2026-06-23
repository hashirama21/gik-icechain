"""Unit tests for as-of-date lead-time skill curves."""

from __future__ import annotations

import pandas as pd
import pytest

from gik_icechain.risk.cpt_refinement import EMDATFloodRecord
from gik_icechain.risk.lead_time_skill import event_onsets, lead_time_skill


def _event(pcode: str, onset: str, end: str | None = None) -> EMDATFloodRecord:
    return EMDATFloodRecord(
        event_id=f"{pcode}-{onset}",
        country="KE",
        admin1_name=pcode,
        admin1_pcode=pcode,
        start_date=pd.Timestamp(onset),
        end_date=pd.Timestamp(end or onset),
        deaths=None,
        affected=None,
    )


def _row(date_str: str, pcode: str, state: int, p_yellow=0.0, p_orange=0.0, p_red=0.0):
    return {
        "date": date_str,
        "admin1_pcode": pcode,
        "risk_state": state,
        "p_yellow": p_yellow,
        "p_orange": p_orange,
        "p_red": p_red,
    }


class TestEventOnsets:
    def test_one_onset_per_event_with_pcode(self):
        recs = [_event("KE001", "2024-04-10"), _event("", "2024-04-11")]
        assert event_onsets(recs) == [("KE001", "2024-04-10")]


class TestLeadTimeSkill:
    def test_recall_decreases_with_lead(self):
        # One event at onset; the signal weakens as the forecast is issued earlier.
        recs = [_event("KE001", "2024-04-10")]
        df = pd.DataFrame(
            [
                _row("2024-04-10", "KE001", 3, p_red=0.6),  # lead 0 → Red
                _row("2024-04-09", "KE001", 2, p_orange=0.5),  # lead 1 → Orange
                _row("2024-04-08", "KE001", 0),  # lead 2 → Green
            ]
        )
        curve = lead_time_skill(df, recs, max_lead=2)
        assert curve[0]["recall_red"] == 1.0
        assert curve[1]["recall_red"] == 0.0
        assert curve[1]["recall_orange"] == 1.0
        assert curve[2]["recall_orange"] == 0.0
        # cumulative posterior P(>=orange) at lead 1 = p_orange + p_red = 0.5
        assert curve[1]["mean_p_orange"] == pytest.approx(0.5)

    def test_missing_forecast_excluded_from_denominator(self):
        recs = [_event("KE001", "2024-04-10"), _event("KE002", "2024-04-10")]
        # Only KE001 has a lead-1 forecast; KE002's lead-1 row is absent.
        df = pd.DataFrame(
            [
                _row("2024-04-09", "KE001", 2, p_orange=0.4),
            ]
        )
        curve = lead_time_skill(df, recs, max_lead=1)
        assert curve[1]["n"] == 1.0  # only KE001 counted
        assert curve[1]["recall_orange"] == 1.0
        assert curve[0]["n"] == 0.0  # no onset-day forecasts present

    def test_multi_event_recall_fraction(self):
        recs = [_event("KE001", "2024-04-10"), _event("KE002", "2024-04-12")]
        df = pd.DataFrame(
            [
                _row("2024-04-10", "KE001", 2, p_orange=0.5),  # onset hit (orange)
                _row("2024-04-12", "KE002", 0),  # onset miss
            ]
        )
        curve = lead_time_skill(df, recs, max_lead=0)
        assert curve[0]["n"] == 2.0
        assert curve[0]["recall_orange"] == pytest.approx(0.5)  # 1 of 2
        assert curve[0]["recall_yellow"] == pytest.approx(0.5)

    def test_curve_covers_all_leads(self):
        recs = [_event("KE001", "2024-04-10")]
        df = pd.DataFrame([_row("2024-04-10", "KE001", 1, p_yellow=0.3)])
        curve = lead_time_skill(df, recs, max_lead=5)
        assert set(curve) == set(range(6))
        for entry in curve.values():
            for name in ("yellow", "orange", "red"):
                assert f"recall_{name}" in entry and f"mean_p_{name}" in entry
