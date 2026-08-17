"""Unit tests for the real-time ECMWF .index source."""

import json
from unittest.mock import patch

import numpy as np
import pytest

from gik_icechain.exceedance.ecmwf_direct import (
    INDEX_STEP_HOURS,
    _step_file_uri,
    _steps_for_horizon,
    load_day_ecmwf_direct,
    parse_index_lines,
)
from gik_icechain.shared.byte_range import ByteRange

GRIB = "s3://ecmwf-forecasts/20260715/00z/ifs/0p25/enfo/20260715000000-24h-enfo-ef.grib2"


def _line(**kw) -> str:
    base = {
        "domain": "g",
        "date": "20260715",
        "time": "0000",
        "type": "pf",
        "stream": "enfo",
        "step": "24",
        "levtype": "sfc",
        "number": "3",
        "param": "tp",
        "_offset": 100,
        "_length": 500,
    }
    base.update(kw)
    return json.dumps(base)


class TestStepHours:
    def test_85_steps_matching_ifs_layout(self):
        assert len(INDEX_STEP_HOURS) == 85
        assert INDEX_STEP_HOURS[0] == 0
        assert INDEX_STEP_HOURS[48] == 144
        assert INDEX_STEP_HOURS[49] == 150
        assert INDEX_STEP_HOURS[-1] == 360


class TestStepsForHorizon:
    def test_168h_horizon_is_fully_covered(self):
        # Regression: the old count-based slice stopped at 87h for a 168h horizon
        # because INDEX_STEP_HOURS is 3-hourly early. Hour-based selection reaches 168h.
        steps = _steps_for_horizon(168)
        assert steps[-1] == 168
        assert 168 in steps
        assert len(steps) == 53

    def test_full_15day_horizon(self):
        steps = _steps_for_horizon(360)
        assert steps == INDEX_STEP_HOURS
        assert steps[-1] == 360

    def test_short_horizon_keeps_3hourly_head(self):
        # Preserves the small-horizon behaviour relied on elsewhere (0,3,6).
        assert _steps_for_horizon(6) == [0, 3, 6]

    def test_non_aligned_horizon_clips_to_last_published_step(self):
        assert _steps_for_horizon(170)[-1] == 168  # next index step is 174h


class TestStepFileUri:
    def test_grib_and_index_uris(self):
        assert _step_file_uri("2026-07-15", 0, 24, "grib2") == GRIB
        assert _step_file_uri("2026-07-15", 0, 24, "index") == GRIB.replace(".grib2", ".index")

    def test_era_boundary(self):
        assert "0p4-beta/enfo" in _step_file_uri("2024-02-28", 0, 24, "index")
        assert "ifs/0p25/enfo" in _step_file_uri("2024-02-29", 0, 24, "index")
        assert (
            _step_file_uri("2023-06-15", 0, 24, "grib2")
            == "s3://ecmwf-forecasts/20230615/00z/0p4-beta/enfo/20230615000000-24h-enfo-ef.grib2"
        )


class TestParseIndexLines:
    def test_perturbed_and_control_members(self):
        text = "\n".join(
            [
                _line(number="7"),
                _line(type="cf", number=None),
            ]
        )
        refs = parse_index_lines(text, GRIB, step_idx=4, variables=["tp"])
        assert [r.metadata["member_idx"] for r in refs] == [7, 0]
        assert all(r.metadata["step_idx"] == 4 for r in refs)
        assert all(r.uri == GRIB for r in refs)

    def test_filters_levtype_param_and_zero_length(self):
        text = "\n".join(
            [
                _line(),
                _line(levtype="pl"),
                _line(param="sd"),
                _line(_length=0),
                "not json",
                "",
            ]
        )
        refs = parse_index_lines(text, GRIB, step_idx=0, variables=["tp"])
        assert len(refs) == 1
        assert refs[0].offset == 100
        assert refs[0].length == 500

    def test_variable_selection_keeps_canonical_names(self):
        text = "\n".join([_line(param="2t"), _line(param="tp"), _line(param="ro")])
        refs = parse_index_lines(text, GRIB, step_idx=0, variables=["tp", "2t"])
        assert {r.metadata["variable"] for r in refs} == {"tp", "2t"}


class TestLoadDayEcmwfDirect:
    def test_no_refs_raises(self):
        with (
            patch(
                "gik_icechain.exceedance.ecmwf_direct._fetch_index_refs",
                return_value=[],
            ),
            pytest.raises(ValueError, match="No ECMWF index references"),
        ):
            load_day_ecmwf_direct(
                "2026-07-15",
                variables=["tp"],
                max_step_h=24,
                step_resolution_h=6,
                step_buffer=1,
                bbox=None,
            )

    def test_assembles_dataset_from_mocked_fetch_and_decode(self):
        refs = [
            ByteRange(
                uri=GRIB,
                offset=i * 100,
                length=100,
                metadata={"member_idx": m, "step_idx": s, "variable": "tp"},
            )
            for i, (m, s) in enumerate((m, s) for m in range(12) for s in range(3))
        ]
        raw = {(r.metadata["member_idx"], r.metadata["step_idx"], "tp"): b"x" for r in refs}
        grid = np.ones((4, 8), dtype=np.float32)

        with (
            patch(
                "gik_icechain.exceedance.ecmwf_direct._fetch_index_refs",
                return_value=refs,
            ),
            patch(
                "gik_icechain.exceedance.ecmwf_direct.fetch_coalesced_ranges",
                return_value=raw,
            ),
            patch(
                "gik_icechain.exceedance.ecmwf_direct._decode_grib_message",
                return_value=grid,
            ),
        ):
            ds = load_day_ecmwf_direct(
                "2026-07-15",
                variables=["tp"],
                max_step_h=6,
                step_resolution_h=6,
                step_buffer=1,
                bbox=None,
                min_members=10,
            )

        assert ds.sizes["member"] == 12
        assert ds.sizes["step"] == 3
        np.testing.assert_array_equal(ds["step"].values, [0, 3, 6])
        assert ds.attrs["source_grid_deg"] == pytest.approx(0.25)

    def test_too_few_members_raises(self):
        refs = [
            ByteRange(
                uri=GRIB,
                offset=0,
                length=100,
                metadata={"member_idx": 0, "step_idx": 0, "variable": "tp"},
            )
        ]
        with (
            patch(
                "gik_icechain.exceedance.ecmwf_direct._fetch_index_refs",
                return_value=refs,
            ),
            patch(
                "gik_icechain.exceedance.ecmwf_direct.fetch_coalesced_ranges",
                return_value={(0, 0, "tp"): b"x"},
            ),
            patch(
                "gik_icechain.exceedance.ecmwf_direct._decode_grib_message",
                return_value=np.ones((4, 8), dtype=np.float32),
            ),
            pytest.raises(ValueError, match="minimum required"),
        ):
            load_day_ecmwf_direct(
                "2026-07-15",
                variables=["tp"],
                max_step_h=6,
                step_resolution_h=6,
                step_buffer=1,
                bbox=None,
                min_members=10,
            )
