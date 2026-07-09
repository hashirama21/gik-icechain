"""Unit tests for spatial aggregation to admin-1 units (risk/aggregator.py)."""

import numpy as np
import pytest

from gik_icechain.risk.aggregator import aggregate_to_admin1, coverage_fraction


class TestAggregateToAdmin1:
    def test_mean(self, square_grid_da, square_admin_gdf):
        s = aggregate_to_admin1(square_grid_da, square_admin_gdf, stat="mean")
        assert s["AA1"] == pytest.approx(2.5)
        assert s["BB2"] == pytest.approx(25.0)

    def test_max(self, square_grid_da, square_admin_gdf):
        s = aggregate_to_admin1(square_grid_da, square_admin_gdf, stat="max")
        assert s["AA1"] == pytest.approx(4.0)
        assert s["BB2"] == pytest.approx(40.0)

    def test_percentile(self, square_grid_da, square_admin_gdf):
        """p50 of [1, 2, 3, 4] is 2.5 (linear interpolation)."""
        s = aggregate_to_admin1(square_grid_da, square_admin_gdf, stat="p50")
        assert s["AA1"] == pytest.approx(2.5)
        assert s["BB2"] == pytest.approx(25.0)

    def test_area_weighted_near_equator_matches_mean(
        self, square_grid_da, square_admin_gdf
    ):
        """Cosine-latitude weights at 0-3 degrees are ~1, so the weighted
        mean stays within 1% of the arithmetic mean."""
        s = aggregate_to_admin1(square_grid_da, square_admin_gdf, stat="area_weighted")
        assert s["AA1"] == pytest.approx(2.5, rel=0.01)
        assert s["BB2"] == pytest.approx(25.0, rel=0.01)

    def test_unknown_stat_raises(self, square_grid_da, square_admin_gdf):
        with pytest.raises(ValueError, match="Unknown stat"):
            aggregate_to_admin1(square_grid_da, square_admin_gdf, stat="median")

    def test_min_coverage_yields_nan(self, square_grid_da, square_admin_gdf):
        """A unit with 1/4 valid cells (< min_coverage 0.5) gets NaN, not a
        misleading statistic; the intact unit is unaffected."""
        da = square_grid_da.copy()
        da[0, 0] = np.nan
        da[0, 1] = np.nan
        da[1, 0] = np.nan
        s = aggregate_to_admin1(da, square_admin_gdf, stat="mean", min_coverage=0.5)
        assert np.isnan(s["AA1"])
        assert s["BB2"] == pytest.approx(25.0)

    def test_min_coverage_nan_applies_to_area_weighted(
        self, square_grid_da, square_admin_gdf
    ):
        da = square_grid_da.copy()
        da[0, 0] = np.nan
        da[0, 1] = np.nan
        da[1, 0] = np.nan
        s = aggregate_to_admin1(
            da, square_admin_gdf, stat="area_weighted", min_coverage=0.5
        )
        assert np.isnan(s["AA1"])
        assert s["BB2"] == pytest.approx(25.0, rel=0.01)


class TestCoverageFraction:
    def test_all_or_nothing(self, square_grid_da, square_admin_gdf):
        """Threshold 5 sits between AA1's max (4) and BB2's min (10)."""
        s = coverage_fraction(square_grid_da, square_admin_gdf, threshold=5.0)
        assert s["AA1"] == pytest.approx(0.0)
        assert s["BB2"] == pytest.approx(1.0)

    def test_partial_fraction(self, square_grid_da, square_admin_gdf):
        """Threshold 15 keeps 3 of BB2's 4 cells (20, 30, 40)."""
        s = coverage_fraction(square_grid_da, square_admin_gdf, threshold=15.0)
        assert s["BB2"] == pytest.approx(0.75)
