"""Unit tests for EM-DAT CPT refinement dataset builder."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gik_icechain.risk.cpt_refinement import (
    _COMPOUND_PARENTS,
    _EVIDENCE_COLS,
    _STATE_NAMES,
    EMDATFloodRecord,
    build_training_dataset,
    dirichlet_partial_pool,
    refine_cpts_hierarchical,
)
from gik_icechain.risk.crma_model import NODE_CARDS, CRMAModel, EastAfricaCluster


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


class TestDirichletPartialPool:
    @staticmethod
    def _global() -> np.ndarray:
        # 4 child states (Risk_State) × 4 parent states (Compound_Risk), col-normalized.
        g = np.array(
            [
                [0.7, 0.4, 0.2, 0.1],
                [0.2, 0.4, 0.3, 0.2],
                [0.07, 0.15, 0.3, 0.3],
                [0.03, 0.05, 0.2, 0.4],
            ]
        )
        return g / g.sum(axis=0, keepdims=True)

    def test_no_data_recovers_global_exactly(self):
        g = self._global()
        counts = np.zeros_like(g)
        out = dirichlet_partial_pool(g, counts, pool_strength=8.0)
        np.testing.assert_allclose(out, g, atol=1e-12)

    def test_columns_sum_to_one(self):
        g = self._global()
        counts = np.array(
            [
                [3, 0, 0, 0],
                [1, 4, 1, 0],
                [0, 2, 5, 2],
                [0, 0, 3, 9],
            ],
            dtype=float,
        )
        out = dirichlet_partial_pool(g, counts, pool_strength=4.0)
        np.testing.assert_allclose(out.sum(axis=0), np.ones(g.shape[1]), atol=1e-12)

    def test_heavy_data_dominates_prior(self):
        g = self._global()
        # One column with overwhelming evidence concentrated on child state 3.
        counts = np.zeros_like(g)
        counts[3, 0] = 10_000
        out = dirichlet_partial_pool(g, counts, pool_strength=8.0)
        assert out[3, 0] > 0.99

    def test_weak_pool_strength_follows_data_more(self):
        g = self._global()
        counts = np.zeros_like(g)
        counts[0, 1] = 20  # column 1 evidence on child 0
        weak = dirichlet_partial_pool(g, counts, pool_strength=1.0)
        strong = dirichlet_partial_pool(g, counts, pool_strength=50.0)
        # Weaker prior shrinks less → closer to the empirical (here, child 0 → 1.0).
        assert weak[0, 1] > strong[0, 1]

    def test_zero_total_column_is_safe(self):
        # A global column that is all-zero with no counts must not produce NaNs.
        g = np.zeros((4, 1))
        counts = np.zeros((4, 1))
        out = dirichlet_partial_pool(g, counts, pool_strength=8.0)
        assert np.isfinite(out).all()


def _hier_training_df() -> pd.DataFrame:
    """Minimal labelled evidence frame spanning two pcodes/clusters."""
    rows = []
    parent_zero = {p: 0 for p in _COMPOUND_PARENTS}
    # Cluster AA: strong-signal positives (all parents maxed → Compound high → Red).
    parent_high = {p: NODE_CARDS[p] - 1 for p in _COMPOUND_PARENTS}
    for _ in range(8):
        rows.append({**parent_high, "Risk_State": 3, "admin1_pcode": "AA"})
    for _ in range(4):
        rows.append({**parent_zero, "Risk_State": 0, "admin1_pcode": "AA"})
    # Cluster BB: only negatives.
    for _ in range(5):
        rows.append({**parent_zero, "Risk_State": 0, "admin1_pcode": "BB"})
    return pd.DataFrame(rows)


class TestRefineCptsHierarchical:
    def _models(self):
        pytest.importorskip("pgmpy", reason="pgmpy not installed")
        models = {}
        for cluster in (EastAfricaCluster.EQUATORIAL_EAST, EastAfricaCluster.HORN_ARID):
            m = CRMAModel(cluster=cluster)
            m.build()
            models[cluster] = m
        return models

    def test_report_counts_and_valid_cpts(self):
        models = self._models()
        clusters = list(models)
        df = _hier_training_df()
        pcode_to_cluster = {"AA": clusters[0], "BB": clusters[1]}

        report = refine_cpts_hierarchical(models, df, pcode_to_cluster, pool_strength=8.0)

        # All rows accounted for across clusters.
        assert sum(report["n_by_cluster"].values()) == len(df)
        # Global CPT columns are a valid distribution.
        global_cpd = np.array(report["global_cpd"])
        np.testing.assert_allclose(global_cpd.sum(axis=0), np.ones(global_cpd.shape[1]), atol=1e-9)
        # Each refined model still validates and has normalized Risk_State columns.
        for m in models.values():
            risk = m.get_pgmpy_model().get_cpds("Risk_State").get_values()
            np.testing.assert_allclose(risk.sum(axis=0), np.ones(risk.shape[1]), atol=1e-9)
            assert m.get_pgmpy_model().check_model()

    def test_data_poor_cluster_shrinks_toward_global(self):
        models = self._models()
        clusters = list(models)
        df = _hier_training_df()
        pcode_to_cluster = {"AA": clusters[0], "BB": clusters[1]}

        report = refine_cpts_hierarchical(models, df, pcode_to_cluster, pool_strength=8.0)
        global_cpd = np.array(report["global_cpd"])
        # BB only saw negatives → with strong pooling its CPT stays close to global.
        bb_cpt = np.array(models[clusters[1]].get_pgmpy_model().get_cpds("Risk_State").get_values())
        # Closer to global than a degenerate identity would be.
        assert np.abs(bb_cpt - global_cpd).max() < 0.5
