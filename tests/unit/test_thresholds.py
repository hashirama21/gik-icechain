"""
tests/unit/test_thresholds.py
Comprehensive unit tests for the adaptive GEV threshold module.
"""

import dataclasses

import numpy as np
import pytest
import xarray as xr

from gik_icechain.exceedance.thresholds import (
    AdaptiveGEVThresholds,
    ClimateMode,
    ENSOPhase,
    IODPhase,
    Season,
    _SEASON_MONTHS as _SEASON_MONTHS_FOR_TEST,
    classify_enso,
    classify_iod,
    get_season,
)


class TestGetSeason:
    def test_mam_months(self):
        for month in (3, 4, 5):
            assert get_season(month) == Season.MAM

    def test_ond_months(self):
        for month in (10, 11):
            assert get_season(month) == Season.OND

    def test_jjas_months(self):
        for month in (6, 7, 8, 9):
            assert get_season(month) == Season.JJAS

    def test_djf_months(self):
        for month in (12, 1, 2):
            assert get_season(month) == Season.DJF

    def test_december_is_djf_not_ond(self):
        # December was erroneously listed in both OND and DJF; it must be DJF only.
        assert get_season(12) == Season.DJF
        assert get_season(12) != Season.OND

    def test_all_months_covered(self):
        for month in range(1, 13):
            result = get_season(month)
            assert isinstance(result, Season)

    def test_no_month_maps_to_two_seasons(self):
        """Each calendar month must map to exactly one season."""
        seen: dict[int, Season] = {}
        for season, months in _SEASON_MONTHS_FOR_TEST.items():
            for m in months:
                assert m not in seen, (
                    f"Month {m} appears in both {seen[m]} and {season}"
                )
                seen[m] = season


class TestClassifyENSO:
    def test_el_nino(self):
        assert classify_enso(0.5) == ENSOPhase.EL_NINO
        assert classify_enso(2.0) == ENSOPhase.EL_NINO
        assert classify_enso(0.51) == ENSOPhase.EL_NINO

    def test_la_nina(self):
        assert classify_enso(-0.5) == ENSOPhase.LA_NINA
        assert classify_enso(-1.5) == ENSOPhase.LA_NINA

    def test_neutral(self):
        assert classify_enso(0.0) == ENSOPhase.NEUTRAL
        assert classify_enso(0.4) == ENSOPhase.NEUTRAL
        assert classify_enso(-0.4) == ENSOPhase.NEUTRAL

    def test_custom_threshold(self):
        assert classify_enso(0.8, threshold=1.0) == ENSOPhase.NEUTRAL
        assert classify_enso(1.0, threshold=1.0) == ENSOPhase.EL_NINO



class TestClassifyIOD:
    def test_positive_iod(self):
        assert classify_iod(0.4) == IODPhase.POSITIVE
        assert classify_iod(1.2) == IODPhase.POSITIVE

    def test_negative_iod(self):
        assert classify_iod(-0.4) == IODPhase.NEGATIVE
        assert classify_iod(-0.9) == IODPhase.NEGATIVE

    def test_neutral_iod(self):
        assert classify_iod(0.0) == IODPhase.NEUTRAL
        assert classify_iod(0.3) == IODPhase.NEUTRAL
        assert classify_iod(-0.3) == IODPhase.NEUTRAL



class TestClimateMode:
    def test_key_format(self):
        mode = ClimateMode(Season.MAM, ENSOPhase.EL_NINO, IODPhase.POSITIVE)
        assert mode.key == "MAM_el_nino_positive"

    def test_immutability(self):
        mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        with pytest.raises(dataclasses.FrozenInstanceError):
            mode.season = Season.MAM  # type: ignore[misc]

    def test_equality(self):
        a = ClimateMode(Season.MAM, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        b = ClimateMode(Season.MAM, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        assert a == b


@pytest.fixture
def minimal_thresholds():
    """Minimal AdaptiveGEVThresholds instance with synthetic data."""
    instance = AdaptiveGEVThresholds()

    # Inject synthetic thresholds directly (bypass from_cmorph)
    lat = np.arange(-10, 15, 1.0)
    lon = np.arange(30, 55, 1.0)
    template = xr.DataArray(
        np.random.uniform(20, 100, (len(lat), len(lon))),
        dims=["lat", "lon"],
        coords={"lat": lat, "lon": lon},
    )

    for season in Season:
        mode = ClimateMode(season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        instance._thresholds[mode.key] = {}
        for window_h in [24, 72]:
            instance._thresholds[mode.key][window_h] = {}
            for rp in [5, 10]:
                instance._thresholds[mode.key][window_h][rp] = (
                    template * (rp / 5.0)  # scale by RP
                )

    return instance


class TestAdaptiveGEVThresholds:
    def test_get_existing_mode(self, minimal_thresholds):
        mode = ClimateMode(Season.MAM, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        result = minimal_thresholds.get(window_h=24, return_period=5, mode=mode)
        assert isinstance(result, xr.DataArray)
        assert result.dims == ("lat", "lon")

    def test_get_fallback_to_neutral(self, minimal_thresholds):
        # El Niño + Positive IOD mode not in thresholds → should fall back to neutral
        mode = ClimateMode(Season.MAM, ENSOPhase.EL_NINO, IODPhase.POSITIVE)
        result = minimal_thresholds.get(window_h=24, return_period=5, mode=mode)
        assert isinstance(result, xr.DataArray)

    def test_get_missing_raises(self, minimal_thresholds):
        mode = ClimateMode(Season.MAM, ENSOPhase.EL_NINO, IODPhase.POSITIVE)
        with pytest.raises(KeyError):
            # Remove the fallback to force KeyError
            del minimal_thresholds._thresholds[
                ClimateMode(Season.MAM, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL).key
            ]
            minimal_thresholds.get(window_h=24, return_period=5, mode=mode)

    def test_higher_rp_gives_higher_threshold(self, minimal_thresholds):
        mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        t5 = minimal_thresholds.get(window_h=24, return_period=5, mode=mode)
        t10 = minimal_thresholds.get(window_h=24, return_period=10, mode=mode)
        assert float(t10.mean()) > float(t5.mean())

    def test_save_and_load(self, minimal_thresholds, tmp_path):
        minimal_thresholds.save(tmp_path)
        loaded = AdaptiveGEVThresholds.load(tmp_path)

        mode = ClimateMode(Season.MAM, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        original = minimal_thresholds.get(24, 5, mode)
        reloaded = loaded.get(24, 5, mode)

        np.testing.assert_allclose(original.values, reloaded.values, rtol=1e-5)
