"""Unit tests for AIFS ENS discovery and virtualisation."""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from gik_icechain.conversion.aifs_discovery import (
    _ECMWF_BUCKET,
    _VALID_RUN_HOURS,
    _extract_shortname,
    _ref_matches_variables,
    discover_aifs_files,
    scan_aifs_grib,
)


def _ensure_kerchunk_mock():
    """Ensure kerchunk.grib2 is importable (real or mock) so @patch works."""
    if "kerchunk" not in sys.modules:
        kerchunk = ModuleType("kerchunk")
        kerchunk.grib2 = ModuleType("kerchunk.grib2")
        kerchunk.grib2.scan_grib = MagicMock()
        sys.modules["kerchunk"] = kerchunk
        sys.modules["kerchunk.grib2"] = kerchunk.grib2


_ensure_kerchunk_mock()


class TestDiscoverAifsFiles:
    """Tests for deterministic S3 URI construction."""

    def test_basic_uri_count(self):
        """One ef + one cf per step, from 0h to max_step_h inclusive."""
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=12, step_resolution_h=6,
        )
        # steps: 0, 6, 12 -> 3 steps x 2 (ef + cf) = 6
        assert len(uris) == 6

    def test_ef_only_when_no_control(self):
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=12, step_resolution_h=6,
            include_control=False,
        )
        assert len(uris) == 3
        assert all("-ef.grib2" in u for u in uris)
        assert not any("-cf.grib2" in u for u in uris)

    def test_uri_format(self):
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=6, step_resolution_h=6,
        )
        ef_0 = uris[0]
        assert ef_0.startswith(f"s3://{_ECMWF_BUCKET}/")
        assert "20250715/00z/aifs/0p25/enfo/" in ef_0
        assert "20250715000000-0h-enfo-ef.grib2" in ef_0

    def test_step_6_uri(self):
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=6, step_resolution_h=6,
        )
        # ef at step 6 should be the 3rd URI (0: ef-0h, 1: cf-0h, 2: ef-6h)
        ef_6 = uris[2]
        assert "20250715000000-6h-enfo-ef.grib2" in ef_6

    def test_run_hour_12(self):
        uris = discover_aifs_files(
            date(2025, 8, 1), run_hour=12, max_step_h=0, step_resolution_h=6,
        )
        assert len(uris) == 2  # ef + cf at step 0
        assert "12z/aifs" in uris[0]
        assert "2025080112" in uris[0]

    def test_date_formatting(self):
        uris = discover_aifs_files(
            date(2025, 1, 5), run_hour=0, max_step_h=0, step_resolution_h=6,
        )
        assert "20250105" in uris[0]

    def test_full_range_360h(self):
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=360, step_resolution_h=6,
        )
        # 0 to 360 in steps of 6 = 61 steps x 2 = 122
        assert len(uris) == 122

    def test_max_step_0(self):
        uris = discover_aifs_files(
            date(2025, 7, 15), run_hour=0, max_step_h=0, step_resolution_h=6,
        )
        assert len(uris) == 2  # only step 0, ef + cf

    def test_invalid_run_hour_raises(self):
        with pytest.raises(ValueError, match="run_hour must be one of"):
            discover_aifs_files(date(2025, 7, 15), run_hour=6)

    def test_valid_run_hours_constant(self):
        assert _VALID_RUN_HOURS == (0, 12)


class TestScanAifsGrib:
    """Tests for kerchunk GRIB scanning with mocked kerchunk."""

    @patch("kerchunk.grib2.scan_grib")
    def test_returns_all_refs_when_no_filter(self, mock_scan):
        mock_scan.return_value = [
            {"refs": {"shortName": "tp"}},
            {"refs": {"shortName": "2t"}},
        ]
        refs = scan_aifs_grib("s3://bucket/test.grib2")
        assert len(refs) == 2

    @patch("kerchunk.grib2.scan_grib")
    def test_filters_by_variable(self, mock_scan):
        mock_scan.return_value = [
            {"refs": {"shortName": "tp"}},
            {"refs": {"shortName": "2t"}},
            {"refs": {"shortName": "ro"}},
        ]
        refs = scan_aifs_grib("s3://bucket/test.grib2", variables=["tp"])
        assert len(refs) == 1
        assert refs[0]["refs"]["shortName"] == "tp"

    @patch("kerchunk.grib2.scan_grib")
    def test_filters_by_zarray_key(self, mock_scan):
        """Real kerchunk refs have variable in .zarray key names."""
        mock_scan.return_value = [
            {"refs": {".zgroup": "{}", "tp/.zarray": "{}", "tp/0.0": []}},
            {"refs": {".zgroup": "{}", "2t/.zarray": "{}", "2t/0.0": []}},
        ]
        refs = scan_aifs_grib("s3://bucket/test.grib2", variables=["tp"])
        assert len(refs) == 1


class TestRefMatchesVariables:
    """Tests for the variable matching helper."""

    def test_matches_legacy_shortname(self):
        assert _ref_matches_variables({"refs": {"shortName": "tp"}}, {"tp"})
        assert not _ref_matches_variables({"refs": {"shortName": "2t"}}, {"tp"})

    def test_matches_zarray_key(self):
        ref = {"refs": {"tp/.zarray": "{}", "tp/0.0": []}}
        assert _ref_matches_variables(ref, {"tp"})
        assert not _ref_matches_variables(ref, {"2t"})

    def test_matches_zattrs_grib_shortname(self):
        import json

        ref = {"refs": {"tp/.zattrs": json.dumps({"GRIB_shortName": "tp"})}}
        assert _ref_matches_variables(ref, {"tp"})

    def test_no_match_empty_refs(self):
        assert not _ref_matches_variables({"refs": {}}, {"tp"})
        assert not _ref_matches_variables({}, {"tp"})


class TestExtractShortname:
    def test_shortname_key(self):
        assert _extract_shortname({"refs": {"shortName": "tp"}}) == "tp"

    def test_cfvarname_key(self):
        assert _extract_shortname({"refs": {"cfVarName": "2t"}}) == "2t"

    def test_zarray_key(self):
        assert _extract_shortname(
            {"refs": {"tp/.zarray": "{}", "tp/0.0": []}},
        ) == "tp"

    def test_missing_returns_none(self):
        assert _extract_shortname({"refs": {}}) is None
        assert _extract_shortname({}) is None


class TestAifsToVirtualDataset:
    """Integration tests for the full virtualisation pipeline (mocked I/O)."""

    def test_raises_file_not_found_when_no_refs(self):
        from gik_icechain.conversion.aifs_discovery import aifs_to_virtual_dataset

        with (
            patch.object(
                __import__(
                    "gik_icechain.conversion.aifs_discovery",
                    fromlist=["scan_aifs_grib"],
                ),
                "scan_aifs_grib",
                return_value=[],
            ),
            pytest.raises(FileNotFoundError, match="No valid AIFS"),
        ):
            aifs_to_virtual_dataset(
                date(2025, 7, 15), run_hour=0, max_step_h=0,
            )

    def test_raises_file_not_found_when_all_scan_fail(self):
        from gik_icechain.conversion.aifs_discovery import aifs_to_virtual_dataset

        with (
            patch.object(
                __import__(
                    "gik_icechain.conversion.aifs_discovery",
                    fromlist=["scan_aifs_grib"],
                ),
                "scan_aifs_grib",
                side_effect=Exception("S3 error"),
            ),
            pytest.raises(FileNotFoundError, match="No valid AIFS"),
        ):
            aifs_to_virtual_dataset(
                date(2025, 7, 15), run_hour=0, max_step_h=6,
            )
