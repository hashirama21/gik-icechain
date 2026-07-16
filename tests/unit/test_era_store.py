"""Unit tests for the era-group store layout (published full-archive store)."""

from datetime import date

import numpy as np
import pytest
import xarray as xr

from gik_icechain.exceedance.era_store import (
    list_era_dates,
    load_day_era_fallback,
    resolve_era_group,
)
from gik_icechain.shared.config import SourceStoreConfig

ERAS = [
    ("0p4/00z", date(2023, 1, 18), date(2024, 2, 28)),
    ("49r1/00z", date(2024, 2, 29), date(2026, 5, 12)),
    ("50r1/00z", date(2026, 5, 13), None),
]


class TestResolveEraGroup:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2023, 1, 18), "0p4/00z"),
            (date(2024, 2, 28), "0p4/00z"),
            (date(2024, 2, 29), "49r1/00z"),
            (date(2026, 5, 12), "49r1/00z"),
            (date(2026, 5, 13), "50r1/00z"),
            (date(2030, 1, 1), "50r1/00z"),
        ],
    )
    def test_boundaries(self, day, expected):
        assert resolve_era_group(day, ERAS) == expected

    def test_before_first_era_is_none(self):
        assert resolve_era_group(date(2023, 1, 17), ERAS) is None


class TestSourceStoreConfig:
    def test_default_is_per_date(self):
        cfg = SourceStoreConfig()
        assert cfg.layout == "per_date"
        assert cfg.anonymous is False
        assert cfg.manifest_preload is True

    def test_era_groups_layout_requires_entries(self):
        with pytest.raises(ValueError, match="at least one era_groups entry"):
            SourceStoreConfig(layout="era_groups")

    def test_overlapping_eras_rejected(self):
        with pytest.raises(ValueError, match="overlap"):
            SourceStoreConfig(
                layout="era_groups",
                era_groups=[
                    {"group": "a", "start": "2023-01-01", "end": "2024-01-01"},
                    {"group": "b", "start": "2024-01-01", "end": None},
                ],
            )

    def test_open_ended_middle_era_rejected(self):
        with pytest.raises(ValueError, match="no end date"):
            SourceStoreConfig(
                layout="era_groups",
                era_groups=[
                    {"group": "a", "start": "2023-01-01", "end": None},
                    {"group": "b", "start": "2024-01-01", "end": None},
                ],
            )

    def test_valid_era_groups(self):
        cfg = SourceStoreConfig(
            layout="era_groups",
            era_groups=[
                {"group": "0p4/00z", "start": "2023-01-18", "end": "2024-02-28"},
                {"group": "49r1/00z", "start": "2024-02-29", "end": None},
            ],
            variable_aliases={"2t": "t2m"},
        )
        assert cfg.era_groups[0].start == date(2023, 1, 18)
        assert cfg.era_groups[1].end is None


def _write_synthetic_era_store(root_dir, group, days, var_names, nlat=19, nlon=36):
    """Write a tiny era-layout group: dims (time, number, step, lat, lon)."""
    times = np.array(days, dtype="datetime64[D]").astype("datetime64[ns]")
    steps = np.array([0, 6, 12, 18, 24], dtype="int32")
    lats = np.linspace(90.0, -90.0, nlat).astype("float32")
    lons = np.linspace(0.0, 350.0, nlon).astype("float32")
    rng = np.random.default_rng(42)
    ds_vars = {
        name: (
            ["time", "number", "step", "latitude", "longitude"],
            rng.random((len(times), 3, len(steps), nlat, nlon)).astype("float32"),
        )
        for name in var_names
    }
    ds = xr.Dataset(
        ds_vars,
        coords={
            "time": times,
            "number": np.arange(3, dtype="int32"),
            "step": steps,
            "latitude": lats,
            "longitude": lons,
        },
    )
    ds.to_zarr(root_dir, group=group, mode="a", zarr_format=3, consolidated=False)
    return ds


class TestListEraDates:
    def test_dates_mapped_to_groups_and_range_filtered(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(
            store, "0p4/00z", ["2023-01-17", "2023-01-18", "2023-06-15"], ["tp"]
        )
        _write_synthetic_era_store(store, "49r1/00z", ["2024-04-26"], ["tp"])

        dates = list_era_dates(store, ERAS)

        assert dates == {
            "2023-01-18": "0p4/00z",
            "2023-06-15": "0p4/00z",
            "2024-04-26": "49r1/00z",
        }

    def test_missing_group_is_skipped(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(store, "0p4/00z", ["2023-06-15"], ["tp"])

        dates = list_era_dates(store, ERAS)

        assert dates == {"2023-06-15": "0p4/00z"}


class TestLoadDayEraFallback:
    def test_loads_one_day_with_aliases_and_member_rename(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(store, "0p4/00z", ["2023-06-14", "2023-06-15"], ["tp", "t2m"])

        day_ds = load_day_era_fallback(
            store,
            "0p4/00z",
            "2023-06-15",
            variables=["tp", "2t"],
            aliases={"2t": "t2m"},
        )

        assert set(day_ds.data_vars) == {"tp", "2t"}
        assert "member" in day_ds.dims
        assert "number" not in day_ds.dims
        assert "time" not in day_ds.coords
        assert day_ds["step"].dtype == np.int32
        assert day_ds.attrs["source_grid_deg"] == pytest.approx(10.0)

    def test_selects_the_right_time_index(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        src = _write_synthetic_era_store(store, "0p4/00z", ["2023-06-14", "2023-06-15"], ["tp"])

        day_ds = load_day_era_fallback(store, "0p4/00z", "2023-06-15", variables=["tp"])

        np.testing.assert_allclose(
            day_ds["tp"].values,
            src["tp"].isel(time=1).values,
        )

    def test_timedelta_step_converted_to_hours(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(store, "0p4/00z", ["2023-06-15"], ["tp"])
        import zarr

        zg = zarr.open_group(store, mode="r+")
        del zg["0p4/00z"]["step"]

        ds = xr.open_zarr(store, group="0p4/00z", consolidated=False, zarr_format=3)
        ds = ds.assign_coords(step=np.array([0, 6, 12, 18, 24], dtype="timedelta64[h]"))
        ds[["step"]].to_zarr(store, group="0p4/00z", mode="a", zarr_format=3, consolidated=False)

        day_ds = load_day_era_fallback(store, "0p4/00z", "2023-06-15", variables=["tp"])

        assert day_ds["step"].dtype == np.int32
        np.testing.assert_array_equal(day_ds["step"].values, [0, 6, 12, 18, 24])

    def test_missing_date_raises(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(store, "0p4/00z", ["2023-06-15"], ["tp"])

        with pytest.raises(ValueError, match="not found in era group"):
            load_day_era_fallback(store, "0p4/00z", "2023-06-16", variables=["tp"])

    def test_no_matching_variable_raises(self, tmp_path):
        store = str(tmp_path / "store.zarr")
        _write_synthetic_era_store(store, "0p4/00z", ["2023-06-15"], ["tp"])

        with pytest.raises(ValueError, match="none of"):
            load_day_era_fallback(store, "0p4/00z", "2023-06-15", variables=["ro"])
