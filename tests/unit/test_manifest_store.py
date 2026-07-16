"""Unit tests for the manifest-aware data loader."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gik_icechain.exceedance.manifest_store import (
    _assemble_dataset,
    _bbox_to_slices,
    _parse_chunk_location,
)


def _make_mock_eccodes(nlat: int, nlon: int):
    """Build a mock eccodes module returning a grid of (nlat, nlon)."""
    mock = MagicMock()
    mock.codes_new_from_message = MagicMock(return_value=42)
    mock.codes_get_values = MagicMock(
        return_value=np.random.rand(nlat * nlon).astype(np.float64),
    )
    mock.codes_get = MagicMock(
        side_effect=lambda _mid, key: nlat if key == "Nj" else nlon,
    )
    mock.codes_release = MagicMock()
    return mock


class TestBboxToSlices:
    """Tests for _bbox_to_slices with ECMWF 0.25-deg grid."""

    def test_east_africa_bbox(self):
        """East Africa [-12, 23, 22, 52] should produce correct index slices."""
        lat_sl, lon_sl = _bbox_to_slices((-12.0, 23.0, 22.0, 52.0))
        assert lat_sl.start == 268
        assert lat_sl.stop == 409
        assert lon_sl.start == 88
        assert lon_sl.stop == 209

    def test_equator_region(self):
        """Small region around equator."""
        lat_sl, lon_sl = _bbox_to_slices((-5.0, 5.0, 35.0, 40.0))
        assert lat_sl.start == 340
        assert lat_sl.stop == 381
        assert lon_sl.start == 140
        assert lon_sl.stop == 161

    def test_north_pole(self):
        """90N should map to index 0."""
        lat_sl, _lon_sl = _bbox_to_slices((89.0, 90.0, 0.0, 1.0))
        assert lat_sl.start == 0
        assert lat_sl.stop == 5

    def test_southern_hemisphere(self):
        """Negative latitudes (southern hemisphere)."""
        lat_sl, _ = _bbox_to_slices((-45.0, -30.0, 0.0, 10.0))
        assert lat_sl.start == 480
        assert lat_sl.stop == 541


class TestBboxToSlicesMixedResolution:
    """s3://ecmwf-forecasts serves 0p4-beta (451x900) before ~2024-02 and 0p25
    (721x1440) after. Slicing a 0.4-deg grid on 0.25-deg indices silently returns
    a different region - the archive spans both, so the step must follow the grid.
    """

    EA = (-12.0, 23.0, 22.0, 52.0)

    def test_slices_differ_between_grids(self):
        assert _bbox_to_slices(self.EA) != _bbox_to_slices(self.EA, 451, 900)

    def test_east_africa_on_0p4_grid(self):
        lat_sl, lon_sl = _bbox_to_slices(self.EA, 451, 900)
        # 0.4 deg: lat idx = (90 - lat) / 0.4 ; lon idx = lon / 0.4
        assert lat_sl.start == 168  # 23N
        assert lat_sl.stop == 256  # -12N inclusive
        assert lon_sl.start == 55  # 22E
        assert lon_sl.stop == 131  # 52E inclusive

    def test_both_grids_select_the_same_geography(self):
        """The whole point: same bbox -> same corner coordinates, either grid."""
        for nlat, nlon in ((721, 1440), (451, 900)):
            lat_res, lon_res = 180.0 / (nlat - 1), 360.0 / nlon
            lat_sl, lon_sl = _bbox_to_slices(self.EA, nlat, nlon)
            top_lat = 90.0 - lat_sl.start * lat_res
            left_lon = lon_sl.start * lon_res
            assert top_lat == pytest.approx(23.0, abs=lat_res)
            assert left_lon == pytest.approx(22.0, abs=lon_res)


class TestDecodeGribMessage:
    """Tests for _decode_grib_message with mocked eccodes."""

    def test_basic_decode(self):
        """Decode should return reshaped float32 array."""
        nlat, nlon = 10, 20
        mock_eccodes = _make_mock_eccodes(nlat, nlon)

        with patch.dict("sys.modules", {"eccodes": mock_eccodes}):
            # Force re-evaluation of module-level state
            import gik_icechain.exceedance.manifest_store as ms

            old_has, old_mod = ms._HAS_ECCODES, ms.eccodes
            ms._HAS_ECCODES = True
            ms.eccodes = mock_eccodes
            try:
                result = ms._decode_grib_message(b"\x00" * 100)
            finally:
                ms._HAS_ECCODES, ms.eccodes = old_has, old_mod

        assert result is not None
        assert result.shape == (nlat, nlon)
        assert result.dtype == np.float32

    def test_decode_with_bbox_slicing(self):
        """Decode with bbox slices should return subsetted array."""
        nlat, nlon = 721, 1440
        mock_eccodes = _make_mock_eccodes(nlat, nlon)

        import gik_icechain.exceedance.manifest_store as ms

        old_has, old_mod = ms._HAS_ECCODES, ms.eccodes
        ms._HAS_ECCODES = True
        ms.eccodes = mock_eccodes
        try:
            bbox_slices = (slice(100, 200), slice(50, 150))
            result = ms._decode_grib_message(b"\x00" * 100, bbox_slices)
        finally:
            ms._HAS_ECCODES, ms.eccodes = old_has, old_mod

        assert result is not None
        assert result.shape == (100, 100)

    def test_decode_returns_none_when_eccodes_missing(self):
        """Should return None if _HAS_ECCODES is False."""
        import gik_icechain.exceedance.manifest_store as ms

        old_has = ms._HAS_ECCODES
        ms._HAS_ECCODES = False
        try:
            result = ms._decode_grib_message(b"\x00" * 100)
        finally:
            ms._HAS_ECCODES = old_has

        assert result is None


class TestParseChunkLocation:
    """Tests for _parse_chunk_location."""

    def test_dict_with_url(self):
        url, offset, length = _parse_chunk_location(
            {"url": "s3://bucket/key", "offset": 100, "length": 200}
        )
        assert url == "s3://bucket/key"
        assert offset == 100
        assert length == 200

    def test_dict_with_uri(self):
        url, offset, _length = _parse_chunk_location(
            {"uri": "s3://bucket/key", "offset": 50, "length": 75}
        )
        assert url == "s3://bucket/key"
        assert offset == 50

    def test_tuple(self):
        url, offset, length = _parse_chunk_location(("s3://bucket/key", 10, 20))
        assert url == "s3://bucket/key"
        assert offset == 10
        assert length == 20

    def test_object_with_attributes(self):
        loc = MagicMock()
        loc.url = "s3://bucket/key"
        loc.offset = 5
        loc.length = 15
        url, offset, length = _parse_chunk_location(loc)
        assert url == "s3://bucket/key"
        assert offset == 5
        assert length == 15

    def test_unknown_returns_none(self):
        url, _offset, _length = _parse_chunk_location(42)
        assert url is None


class TestAssembleDataset:
    """Tests for _assemble_dataset."""

    def test_basic_assembly(self):
        """Assemble a small dataset and verify dims and coords."""
        nlat, nlon = 5, 8
        decoded = {
            (m, s, "tp"): np.random.rand(nlat, nlon).astype(np.float32)
            for m in range(3)
            for s in range(4)
        }

        ds = _assemble_dataset(
            decoded,
            variables=["tp"],
            member_indices=[0, 1, 2],
            max_steps=4,
            step_hours=np.array([0, 6, 12, 18], dtype=np.int32),
            bbox=None,
        )

        assert "tp" in ds.data_vars
        assert ds["tp"].dims == (
            "member",
            "step",
            "latitude",
            "longitude",
        )
        assert ds.sizes["member"] == 3
        assert ds.sizes["step"] == 4
        assert ds.sizes["latitude"] == nlat
        assert ds.sizes["longitude"] == nlon
        np.testing.assert_array_equal(ds.coords["step"].values, [0, 6, 12, 18])
        np.testing.assert_array_equal(ds.coords["member"].values, [0, 1, 2])

    def test_missing_grids_become_nan(self):
        """Positions without decoded grids should be NaN."""
        nlat, nlon = 3, 4
        decoded = {
            (0, 0, "tp"): np.ones((nlat, nlon), dtype=np.float32),
            (1, 0, "tp"): np.full((nlat, nlon), 2.0, dtype=np.float32),
        }

        ds = _assemble_dataset(
            decoded,
            variables=["tp"],
            member_indices=[0, 1],
            max_steps=2,
            step_hours=np.array([0, 6], dtype=np.int32),
            bbox=None,
        )

        assert float(ds["tp"].sel(member=0, step=0).mean()) == 1.0
        assert np.all(np.isnan(ds["tp"].sel(member=0, step=6).values))
        assert float(ds["tp"].sel(member=1, step=0).mean()) == 2.0

    def test_empty_decoded_raises(self):
        """Empty decoded dict should raise ValueError."""
        with pytest.raises(ValueError, match="No decoded grids"):
            _assemble_dataset({}, ["tp"], [0], 1, np.array([0], dtype=np.int32), None)

    def test_multiple_variables(self):
        """Assembly with two variables."""
        nlat, nlon = 2, 3
        decoded = {
            (0, 0, "tp"): np.ones((nlat, nlon), dtype=np.float32),
            (0, 0, "2t"): np.full((nlat, nlon), 300.0, dtype=np.float32),
        }

        ds = _assemble_dataset(
            decoded,
            variables=["tp", "2t"],
            member_indices=[0],
            max_steps=1,
            step_hours=np.array([0], dtype=np.int32),
            bbox=None,
        )

        assert "tp" in ds.data_vars
        assert "2t" in ds.data_vars
        assert float(ds["2t"].sel(member=0, step=0).mean()) == pytest.approx(300.0)


class TestLoadDayManifestAwareValidation:
    """Tests for min_members validation in load_day_manifest_aware."""

    def test_no_refs_raises(self):
        """Should raise ValueError when no chunk refs are found."""
        from gik_icechain.exceedance.manifest_store import (
            load_day_manifest_aware,
        )

        mock_session = MagicMock()

        with (
            patch(
                "gik_icechain.exceedance.manifest_store._extract_virtual_chunk_refs",
                return_value=[],
            ),
            pytest.raises(ValueError, match="No virtual chunk refs"),
        ):
            load_day_manifest_aware(
                mock_session,
                "2024-01-01",
                variables=["tp"],
                max_step_h=24,
                step_resolution_h=6,
                step_buffer=1,
                bbox=None,
                min_members=10,
            )


class _AsyncBatchIter:
    """Minimal async iterator over pre-built array_chunk_iterator batches."""

    def __init__(self, batches):
        self._batches = list(batches)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._batches:
            raise StopAsyncIteration
        return self._batches.pop(0)


def _era_batch(rows):
    """Build one (coords, types, uris, offsets, lengths, extra) batch.

    rows: list of (time, member, step, chunk_type, uri, offset, length).
    """
    coords = np.array([[r[0], r[1], r[2], 0, 0] for r in rows], dtype=np.int64)
    types = np.array([r[3] for r in rows], dtype=np.int8)
    uris = [r[4] for r in rows]
    offsets = np.array([r[5] for r in rows], dtype=np.int64)
    lengths = np.array([r[6] for r in rows], dtype=np.int64)
    return (coords, types, uris, offsets, lengths, None)


class TestExtractVirtualChunkRefsEra:
    """Tests for _extract_virtual_chunk_refs_era batch filtering."""

    URI = "s3://ecmwf-forecasts/20230615/00z/0p4-beta/enfo/x.grib2"

    def _session(self, batches_by_path):
        session = MagicMock()
        session.store.array_chunk_iterator = MagicMock(
            side_effect=lambda path: _AsyncBatchIter(batches_by_path[path])
        )
        return session

    def test_filters_on_time_index_and_max_steps(self):
        from gik_icechain.exceedance.manifest_store import (
            _extract_virtual_chunk_refs_era,
        )

        rows = [
            (0, 0, 0, 2, self.URI, 0, 100),
            (1, 0, 0, 2, self.URI, 100, 100),
            (1, 1, 2, 2, self.URI, 200, 100),
            (1, 0, 9, 2, self.URI, 300, 100),
            (2, 0, 0, 2, self.URI, 400, 100),
        ]
        session = self._session({"0p4/00z/tp": [_era_batch(rows)]})

        refs = _extract_virtual_chunk_refs_era(
            session, "0p4/00z", time_idx=1, variables={"tp": "tp"}, max_steps=5
        )

        assert len(refs) == 2
        assert {(r.metadata["member_idx"], r.metadata["step_idx"]) for r in refs} == {
            (0, 0),
            (1, 2),
        }

    def test_alias_maps_array_path_and_keeps_canonical_metadata(self):
        from gik_icechain.exceedance.manifest_store import (
            _extract_virtual_chunk_refs_era,
        )

        rows = [(0, 0, 0, 2, self.URI, 0, 100)]
        session = self._session({"49r1/00z/t2m": [_era_batch(rows)]})

        refs = _extract_virtual_chunk_refs_era(
            session, "49r1/00z", time_idx=0, variables={"2t": "t2m"}, max_steps=5
        )

        session.store.array_chunk_iterator.assert_called_once_with("49r1/00z/t2m")
        assert len(refs) == 1
        assert refs[0].metadata["variable"] == "2t"

    def test_skips_non_virtual_and_zero_length_chunks(self):
        from gik_icechain.exceedance.manifest_store import (
            _extract_virtual_chunk_refs_era,
        )

        rows = [
            (0, 0, 0, 1, self.URI, 0, 100),
            (0, 1, 0, 2, self.URI, 100, 0),
            (0, 2, 0, 2, self.URI, 200, 100),
        ]
        session = self._session({"0p4/00z/tp": [_era_batch(rows)]})

        refs = _extract_virtual_chunk_refs_era(
            session, "0p4/00z", time_idx=0, variables={"tp": "tp"}, max_steps=5
        )

        assert len(refs) == 1
        assert refs[0].metadata["member_idx"] == 2


class TestEraTimeIndex:
    """Tests for _era_time_index against a real zarr group."""

    def _store(self, tmp_path, days):
        import xarray as xr

        store = str(tmp_path / "store.zarr")
        times = np.array(days, dtype="datetime64[D]").astype("datetime64[ns]")
        ds = xr.Dataset(
            {"tp": (["time"], np.zeros(len(times), dtype="float32"))},
            coords={"time": times},
        )
        ds.to_zarr(store, group="0p4/00z", zarr_format=3, consolidated=False)
        return store

    def test_resolves_index(self, tmp_path):
        from types import SimpleNamespace

        from gik_icechain.exceedance.manifest_store import _era_time_index

        store = self._store(tmp_path, ["2023-06-14", "2023-06-15", "2023-06-16"])
        session = SimpleNamespace(store=store)

        assert _era_time_index(session, "0p4/00z", "2023-06-15") == 1

    def test_missing_date_raises(self, tmp_path):
        from types import SimpleNamespace

        from gik_icechain.exceedance.manifest_store import _era_time_index

        store = self._store(tmp_path, ["2023-06-14"])
        session = SimpleNamespace(store=store)

        with pytest.raises(ValueError, match="not found in era group"):
            _era_time_index(session, "0p4/00z", "2023-06-15")


class TestLoadDayManifestAwareEraValidation:
    def test_no_refs_raises(self):
        from gik_icechain.exceedance.manifest_store import (
            load_day_manifest_aware_era,
        )

        mock_session = MagicMock()

        with (
            patch(
                "gik_icechain.exceedance.manifest_store._extract_virtual_chunk_refs_era",
                return_value=[],
            ),
            patch(
                "gik_icechain.exceedance.manifest_store._era_time_index",
                return_value=0,
            ),
            pytest.raises(ValueError, match="No virtual chunk refs"),
        ):
            load_day_manifest_aware_era(
                mock_session,
                "0p4/00z",
                "2023-06-15",
                variables=["tp"],
                aliases=None,
                max_step_h=24,
                step_resolution_h=6,
                step_buffer=1,
                bbox=None,
                min_members=10,
            )
