"""Unit tests for GIKCatalog and GIKManifestEntry."""

from __future__ import annotations

from datetime import date

import pytest

from gik_icechain.conversion.gik_loader import (
    FLOOD_RELEVANT_VARS,
    VALID_RUN_HOURS,
    GIKManifestEntry,
)


class TestGIKManifestEntry:
    def _make(self, **kwargs) -> GIKManifestEntry:
        defaults = dict(
            date=date(2024, 10, 15),
            run_hour=0,
            step=24,
            member=1,
            variable="tp",
            level=None,
            s3_uri="s3://ecmwf-forecasts/fake.grib2",
            byte_offset=0,
            byte_length=1000,
        )
        defaults.update(kwargs)
        return GIKManifestEntry(**defaults)

    def test_valid_entry(self):
        entry = self._make()
        assert entry.variable == "tp"
        assert entry.member == 1

    def test_invalid_run_hour(self):
        with pytest.raises(ValueError):
            self._make(run_hour=3)

    def test_invalid_member_negative(self):
        with pytest.raises(ValueError):
            self._make(member=-1)

    def test_invalid_member_too_large(self):
        with pytest.raises(ValueError):
            self._make(member=51)

    def test_control_member(self):
        entry = self._make(member=0)
        assert entry.member == 0

    def test_max_member(self):
        entry = self._make(member=50)
        assert entry.member == 50

    @pytest.mark.parametrize("run_hour", VALID_RUN_HOURS)
    def test_all_valid_run_hours(self, run_hour):
        entry = self._make(run_hour=run_hour)
        assert entry.run_hour == run_hour


class TestFloodRelevantVars:
    def test_tp_present(self):
        assert "tp" in FLOOD_RELEVANT_VARS

    def test_runoff_present(self):
        assert "ro" in FLOOD_RELEVANT_VARS

    def test_no_duplicates(self):
        assert len(FLOOD_RELEVANT_VARS) == len(set(FLOOD_RELEVANT_VARS))