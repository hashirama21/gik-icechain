"""Unit tests for the exceedance Zarr writer - append schema safety (ISSUE-2)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from gik_icechain.exceedance.writer import (
    build_exceedance_dataset,
    write_exceedance_store,
)


def _exc_da(
    windows: list[int],
    rps: list[int],
    day: date,
    value: float = 0.5,
    lat: np.ndarray | None = None,
):
    """Build an exceedance DataArray (date, lat, lon, window, return_period)."""
    lat = np.array([0.0, 1.0], dtype=np.float32) if lat is None else lat
    lon = np.array([10.0, 11.0], dtype=np.float32)
    results = {
        (w, rp): xr.DataArray(
            np.full((lat.size, lon.size), value, dtype=np.float32),
            dims=("latitude", "longitude"),
            coords={"latitude": lat, "longitude": lon},
        )
        for w in windows
        for rp in rps
    }
    return build_exceedance_dataset(results, day)


def _exc_lead_da(
    windows: list[int],
    rps: list[int],
    day: date,
    leads: tuple[int, ...] = (0, 1, 2),
    value: float = 0.5,
):
    """Build an exceedance-by-lead DataArray (date, lead, lat, lon, window, rp)."""
    lat = np.array([0.0, 1.0], dtype=np.float32)
    lon = np.array([10.0, 11.0], dtype=np.float32)
    results = {
        (w, rp): xr.DataArray(
            np.full((len(leads), lat.size, lon.size), value, dtype=np.float32),
            dims=("lead", "latitude", "longitude"),
            coords={"lead": list(leads), "latitude": lat, "longitude": lon},
        )
        for w in windows
        for rp in rps
    }
    return build_exceedance_dataset(results, day)


class TestLeadDimension:
    """exceedance_prob_by_lead persists alongside exceedance_prob without
    touching it, on initial write and on append."""

    def test_written_alongside_max_horizon(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        d1 = date(2024, 1, 1)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1, value=0.1)},
            uri,
            append=False,
            lead_dict={d1: _exc_lead_da([24], [5], d1, value=0.7)},
        )
        ds = xr.open_zarr(uri, consolidated=False)
        assert "exceedance_prob" in ds and "exceedance_prob_by_lead" in ds
        assert "lead" not in ds["exceedance_prob"].dims
        assert "lead" in ds["exceedance_prob_by_lead"].dims
        assert list(ds["lead"].values) == [0, 1, 2]
        mean_d1 = float(ds["exceedance_prob_by_lead"].sel(date="2024-01-01").mean())
        assert mean_d1 == pytest.approx(0.7)

    def test_appended(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1, value=0.1)}, uri, append=False,
            lead_dict={d1: _exc_lead_da([24], [5], d1, value=0.7)},
        )
        write_exceedance_store(
            {d2: _exc_da([24], [5], d2, value=0.2)}, uri, append=True,
            lead_dict={d2: _exc_lead_da([24], [5], d2, value=0.8)},
        )
        ds = xr.open_zarr(uri, consolidated=False)
        assert ds["exceedance_prob_by_lead"].sizes["date"] == 2
        mean_d2 = float(ds["exceedance_prob_by_lead"].sel(date="2024-01-02").mean())
        assert mean_d2 == pytest.approx(0.8)

    def test_append_without_lead_pads_it(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1)}, uri, append=False,
            lead_dict={d1: _exc_lead_da([24], [5], d1, value=0.7)},
        )
        write_exceedance_store({d2: _exc_da([24], [5], d2)}, uri, append=True)
        ds = xr.open_zarr(uri, consolidated=False)
        assert ds["exceedance_prob_by_lead"].sizes["date"] == 2
        assert bool(np.isnan(ds["exceedance_prob_by_lead"].sel(date="2024-01-02")).all())

    def test_introducing_lead_on_legacy_store_raises(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store({d1: _exc_da([24], [5], d1)}, uri, append=False)
        with pytest.raises(ValueError, match="variables"):
            write_exceedance_store(
                {d2: _exc_da([24], [5], d2)}, uri, append=True,
                lead_dict={d2: _exc_lead_da([24], [5], d2, value=0.7)},
            )


class TestStorylineVariables:
    """tail_ratio (worst world) and median_ratio (median world) persist alongside
    exceedance_prob, on initial write and on append."""

    def test_median_and_tail_written_and_appended(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1, value=0.1)},
            uri,
            append=False,
            tail_dict={d1: _exc_da([24], [5], d1, value=1.5)},
            median_dict={d1: _exc_da([24], [5], d1, value=0.3)},
        )
        write_exceedance_store(
            {d2: _exc_da([24], [5], d2, value=0.2)},
            uri,
            append=True,
            tail_dict={d2: _exc_da([24], [5], d2, value=1.6)},
            median_dict={d2: _exc_da([24], [5], d2, value=0.4)},
        )
        ds = xr.open_zarr(uri, consolidated=False)
        assert "median_ratio" in ds and "tail_ratio" in ds
        assert ds.sizes["date"] == 2
        assert float(ds["median_ratio"].sel(date="2024-01-02").mean()) == pytest.approx(0.4)
        assert float(ds["tail_ratio"].sel(date="2024-01-01").mean()) == pytest.approx(1.5)


def _conf_da(day: date, value: int = 2):
    """Build an ensemble_confidence DataArray (date, lat, lon), int8."""
    lat = np.array([0.0, 1.0], dtype=np.float32)
    lon = np.array([10.0, 11.0], dtype=np.float32)
    return xr.DataArray(
        np.full((1, lat.size, lon.size), value, dtype=np.int8),
        dims=("date", "latitude", "longitude"),
        coords={"date": [pd.Timestamp(day)], "latitude": lat, "longitude": lon},
    )


class TestAppendVariableSafety:
    """A store variable absent from a run must not silently stay short and
    desync the date dimension across variables (OND-2024 corruption)."""

    def test_append_without_optional_vars_pads_them(self, tmp_path):
        """Run without tail/median/confidence appends fill values, not desync."""
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1, value=0.1)},
            uri,
            append=False,
            tail_dict={d1: _exc_da([24], [5], d1, value=1.5)},
            median_dict={d1: _exc_da([24], [5], d1, value=0.3)},
            confidence_dict={d1: _conf_da(d1, value=2)},
        )
        write_exceedance_store(
            {d2: _exc_da([24], [5], d2, value=0.2)},
            uri,
            append=True,
        )

        ds = xr.open_zarr(uri, consolidated=False)
        # Every variable advanced to 2 dates - no per-variable desync.
        for name in ("exceedance_prob", "tail_ratio", "median_ratio", "ensemble_confidence"):
            assert ds[name].sizes["date"] == 2, name
        # Day 1 values preserved; day 2 padded (NaN for floats, -1 for int8).
        assert float(ds["tail_ratio"].sel(date="2024-01-01").mean()) == pytest.approx(1.5)
        assert bool(np.isnan(ds["tail_ratio"].sel(date="2024-01-02")).all())
        assert bool(np.isnan(ds["median_ratio"].sel(date="2024-01-02")).all())
        assert int(ds["ensemble_confidence"].sel(date="2024-01-01").max()) == 2
        assert int(ds["ensemble_confidence"].sel(date="2024-01-02").max()) == -1

    def test_append_new_variable_raises(self, tmp_path):
        """Introducing a variable absent from the store fails loudly."""
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1)},
            uri,
            append=False,
        )
        with pytest.raises(ValueError, match="variables"):
            write_exceedance_store(
                {d2: _exc_da([24], [5], d2)},
                uri,
                append=True,
                tail_dict={d2: _exc_da([24], [5], d2, value=1.6)},
            )

    def test_partial_dates_within_batch_kept_and_filled(self, tmp_path):
        """A variable covering only some dates of a batch is kept and padded,
        not silently dropped (the original var-drop path)."""
        uri = str(tmp_path / "exc.zarr")
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        write_exceedance_store(
            {d1: _exc_da([24], [5], d1), d2: _exc_da([24], [5], d2)},
            uri,
            append=False,
            tail_dict={d1: _exc_da([24], [5], d1, value=1.5)},  # d2 missing
        )

        ds = xr.open_zarr(uri, consolidated=False)
        assert "tail_ratio" in ds
        assert ds["tail_ratio"].sizes["date"] == 2
        assert float(ds["tail_ratio"].sel(date="2024-01-01").mean()) == pytest.approx(1.5)
        assert bool(np.isnan(ds["tail_ratio"].sel(date="2024-01-02")).all())


class TestAppendSchemaSafety:
    """Appending a different window/return_period grid must not corrupt the store."""

    def test_append_fewer_windows_is_nan_filled(self, tmp_path):
        """A day missing the 6 h window aligns to the store (NaN-filled), no crash."""
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6, 12, 24], [2, 5], date(2024, 1, 1))},
            uri,
            append=False,
        )
        # Second day produces only 12 h / 24 h (e.g. 6 h skipped at this resolution).
        write_exceedance_store(
            {date(2024, 1, 2): _exc_da([12, 24], [2, 5], date(2024, 1, 2))},
            uri,
            append=True,
        )

        ds = xr.open_zarr(uri, consolidated=False)
        assert ds.sizes["date"] == 2
        assert list(ds["window"].values) == [6, 12, 24]
        # Day 2 had no 6 h window -> NaN-filled; 24 h preserved.
        missing = ds["exceedance_prob"].sel(date="2024-01-02", window=6)
        present = ds["exceedance_prob"].sel(date="2024-01-02", window=24)
        assert bool(np.isnan(missing).all())
        assert not bool(np.isnan(present).any())

    def test_append_new_window_raises(self, tmp_path):
        """Introducing a window absent from the store fails loudly, not cryptically."""
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6, 12], [2, 5], date(2024, 1, 1))},
            uri,
            append=False,
        )
        with pytest.raises(ValueError, match="new values"):
            write_exceedance_store(
                {date(2024, 1, 2): _exc_da([6, 12, 48], [2, 5], date(2024, 1, 2))},
                uri,
                append=True,
            )

    def test_append_changed_spatial_grid_refuses_to_destroy_the_store(self, tmp_path):
        """A grid change (new bbox, or a 0p4-beta vs 0p25 era) must not silently
        overwrite every date already written."""
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6, 12], [2, 5], date(2024, 1, 1))},
            uri,
            append=False,
        )
        new_lat = np.array([0.0, 1.0, 2.0], dtype=np.float32)

        with pytest.raises(ValueError, match="destroy"):
            write_exceedance_store(
                {date(2024, 1, 2): _exc_da([6, 12], [2, 5], date(2024, 1, 2), lat=new_lat)},
                uri,
                append=True,
            )

        ds = xr.open_zarr(uri, consolidated=False)
        assert ds.sizes["latitude"] == 2
        assert [str(d)[:10] for d in ds["date"].values] == ["2024-01-01"]

    def test_grid_reset_is_available_when_asked_for(self, tmp_path):
        """The destructive path stays reachable, but only explicitly."""
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6, 12], [2, 5], date(2024, 1, 1))},
            uri,
            append=False,
        )
        new_lat = np.array([0.0, 1.0, 2.0], dtype=np.float32)
        write_exceedance_store(
            {date(2024, 1, 2): _exc_da([6, 12], [2, 5], date(2024, 1, 2), lat=new_lat)},
            uri,
            append=True,
            allow_grid_reset=True,
        )

        ds = xr.open_zarr(uri, consolidated=False)
        assert ds.sizes["latitude"] == 3
        assert [str(d)[:10] for d in ds["date"].values] == ["2024-01-02"]


class TestSourceGridProvenance:
    """source_grid_deg records the native ECMWF grid (0.25, or 0.4 pre-2024-02),
    so a mixed-era store can be stratified instead of silently compared."""

    def test_written_as_a_per_date_variable(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        days = [date(2024, 1, 1), date(2024, 1, 2)]
        write_exceedance_store(
            {d: _exc_da([6], [2], d) for d in days},
            uri,
            append=False,
            source_grid_deg={days[0]: 0.4, days[1]: 0.25},
        )

        ds = xr.open_zarr(uri, consolidated=False)
        assert ds["source_grid_deg"].dims == ("date",)
        np.testing.assert_allclose(ds["source_grid_deg"].values, [0.4, 0.25], rtol=1e-6)

    def test_absent_when_not_supplied(self, tmp_path):
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6], [2], date(2024, 1, 1))}, uri, append=False
        )
        assert "source_grid_deg" not in xr.open_zarr(uri, consolidated=False).data_vars

    def test_append_onto_a_legacy_store_drops_it_rather_than_failing(self, tmp_path):
        """Stores written before this variable existed must stay appendable."""
        uri = str(tmp_path / "exc.zarr")
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6], [2], date(2024, 1, 1))}, uri, append=False
        )
        write_exceedance_store(
            {date(2024, 1, 2): _exc_da([6], [2], date(2024, 1, 2))},
            uri,
            append=True,
            source_grid_deg={date(2024, 1, 2): 0.25},
        )

        ds = xr.open_zarr(uri, consolidated=False)
        assert "source_grid_deg" not in ds.data_vars
        assert ds.sizes["date"] == 2
