"""Unit tests for EM-DAT CPT refinement dataset builder."""

from __future__ import annotations

import pandas as pd

from gik_icechain.risk.cpt_refinement import (
    _EVIDENCE_COLS,
    _STATE_NAMES,
    EMDATFloodRecord,
    build_training_dataset,
)


def _make_emdat_record(start_date="2024-10-15", pcode="KE001") -> EMDATFloodRecord:
    return EMDATFloodRecord(
        event_id="2024-0001",
        country="Kenya",
        admin1_name="Nairobi",
        admin1_pcode=pcode,
        start_date=pd.Timestamp(start_date),
        end_date=pd.Timestamp(start_date),
        deaths=5,
        affected=500,
    )


def _make_dataframes(pcode="KE001", event_date="2024-10-15"):
    dates = pd.date_range("2024-10-01", periods=30).date.tolist()
    exc_rows = [
        {
            "date": d,
            "admin1_pcode": pcode,
            "exceedance_prob_24h": 0.4 if str(d) == event_date else 0.05,
            "exceedance_prob_72h": 0.3,
            "exceedance_prob_7d": 0.2,
            "spatial_coverage_fraction": 0.5,
            "consecutive_signal_days": 2,
        }
        for d in dates
    ]
    gpm_rows = [
        {"date": d, "admin1_pcode": pcode, "gpm_obs_24h": 30.0 if str(d) == event_date else 2.0}
        for d in dates
    ]
    api_rows = [{"date": d, "admin1_pcode": pcode, "api_mm": 80.0, "sat_consecutive_days": 0}
                for d in dates]
    return (pd.DataFrame(exc_rows), pd.DataFrame(gpm_rows), pd.DataFrame(api_rows))


class TestEvidenceCols:
    def test_soil_memory_in_evidence_cols(self):
        assert "Soil_Memory" in _EVIDENCE_COLS

    def test_risk_state_last(self):
        assert _EVIDENCE_COLS[-1] == "Risk_State"

    def test_soil_memory_in_state_names(self):
        assert "Soil_Memory" in _STATE_NAMES
        assert _STATE_NAMES["Soil_Memory"] == [0, 1]


class TestBuildTrainingDataset:
    def test_positive_samples_have_risk_state_3(self):
        record = _make_emdat_record()
        exc_df, gpm_df, api_df = _make_dataframes()
        df = build_training_dataset([record], exc_df, gpm_df, api_df, negative_sample_ratio=0.0)
        positives = df[df["source"] == "emdat_positive"]
        assert len(positives) >= 1
        assert (positives["Risk_State"] == 3).all()

    def test_negative_samples_present(self):
        record = _make_emdat_record()
        exc_df, gpm_df, api_df = _make_dataframes()
        df = build_training_dataset([record], exc_df, gpm_df, api_df, negative_sample_ratio=2.0)
        negs = df[df["source"] == "negative_sample"]
        assert len(negs) > 0

    def test_soil_memory_column_present(self):
        record = _make_emdat_record()
        exc_df, gpm_df, api_df = _make_dataframes()
        df = build_training_dataset([record], exc_df, gpm_df, api_df)
        assert "Soil_Memory" in df.columns
        assert df["Soil_Memory"].isin([0, 1]).all()

    def test_all_evidence_cols_present(self):
        record = _make_emdat_record()
        exc_df, gpm_df, api_df = _make_dataframes()
        df = build_training_dataset([record], exc_df, gpm_df, api_df)
        for col in _EVIDENCE_COLS:
            assert col in df.columns, f"Missing column: {col}"

    def test_empty_emdat_returns_empty_df(self):
        exc_df, gpm_df, api_df = _make_dataframes()
        df = build_training_dataset([], exc_df, gpm_df, api_df)
        assert len(df) == 0
