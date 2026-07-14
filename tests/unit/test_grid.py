"""Unit tests for the shared ECMWF grid geometry."""

import pytest

from gik_icechain.shared.grid import (
    DEFAULT_SHAPE,
    GRID_SHAPES,
    grid_deg,
    lat_lon_res,
    shape_for_uri,
)

_S3 = "s3://ecmwf-forecasts/20240301/00z"


class TestShapeForUri:
    def test_0p25(self):
        assert shape_for_uri(f"{_S3}/ifs/0p25/enfo/x.grib2") == (721, 1440)

    def test_0p4_beta(self):
        assert shape_for_uri(f"{_S3}/0p4-beta/enfo/x.grib2") == (451, 900)

    def test_unknown_returns_none(self):
        assert shape_for_uri(f"{_S3}/ifs/0p1/enfo/x.grib2") is None

    def test_no_partial_match_across_tokens(self):
        """'0p4-beta' must not be matched by the '0p25' entry, and vice versa."""
        assert shape_for_uri(f"{_S3}/0p4-beta/enfo/x.grib2") != shape_for_uri(
            f"{_S3}/ifs/0p25/enfo/x.grib2"
        )


class TestResolution:
    @pytest.mark.parametrize(
        ("nlat", "nlon", "expected"),
        [(721, 1440, 0.25), (451, 900, 0.4), (181, 360, 1.0)],
    )
    def test_step_matches_the_known_grids(self, nlat, nlon, expected):
        lat_res, lon_res = lat_lon_res(nlat, nlon)
        assert lat_res == pytest.approx(expected)
        assert lon_res == pytest.approx(expected)
        assert grid_deg(nlat) == pytest.approx(expected)

    def test_every_declared_shape_is_square_stepped(self):
        for nlat, nlon in GRID_SHAPES.values():
            lat_res, lon_res = lat_lon_res(nlat, nlon)
            assert lat_res == pytest.approx(lon_res)

    def test_default_is_the_current_operational_grid(self):
        assert DEFAULT_SHAPE == (721, 1440)
        assert grid_deg(DEFAULT_SHAPE[0]) == pytest.approx(0.25)
