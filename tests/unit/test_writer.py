"""Unit tests for the exceedance Zarr writer — append schema safety (ISSUE-2)."""

from datetime import date

import numpy as np
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

    def test_append_changed_spatial_grid_recreates_store(self, tmp_path):
        """A changed bbox (different latitude grid) recreates the store, no crash."""
        uri = str(tmp_path / "exc.zarr")
        # Store written on a 2-cell latitude grid (old bbox).
        write_exceedance_store(
            {date(2024, 1, 1): _exc_da([6, 12], [2, 5], date(2024, 1, 1))},
            uri,
            append=False,
        )
        # New run on a 3-cell latitude grid (bbox extended) — cannot append.
        new_lat = np.array([0.0, 1.0, 2.0], dtype=np.float32)
        write_exceedance_store(
            {date(2024, 1, 2): _exc_da([6, 12], [2, 5], date(2024, 1, 2), lat=new_lat)},
            uri,
            append=True,
        )

        ds = xr.open_zarr(uri, consolidated=False)
        # Store recreated on the new grid: only the new date, new latitude size.
        assert ds.sizes["latitude"] == 3
        assert list(str(d)[:10] for d in ds["date"].values) == ["2024-01-02"]
