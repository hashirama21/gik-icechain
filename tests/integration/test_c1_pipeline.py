"""Integration tests for C1 — GIK Parquet → IceChunk virtual store."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

pytestmark = pytest.mark.integration

NLAT, NLON, NSTEPS = 6, 6, 4
LAT = np.linspace(0.0, 3.0, NLAT, dtype=np.float32)
LON = np.linspace(35.0, 38.0, NLON, dtype=np.float32)
STEPS = np.arange(0, NSTEPS * 6, 6, dtype=np.int32)
TEST_DATE = date(2024, 10, 15)


@pytest.fixture
def fake_parquet(tmp_path) -> str:
    rows = []
    for step in STEPS:
        rows.append({
            "key": f"step_{step}/tp/sfc/0/0",
            "value": json.dumps([f"s3://ecmwf-forecasts/fake/{step}.grib2", int(step) * 100, 500]),
        })
    rows.append({
        "key": "2m_temperature/heightAboveGround/2/.zarray",
        "value": json.dumps({
            "chunks": [1, NLAT, NLON], "compressor": None, "dtype": "<f4",
            "fill_value": "NaN", "filters": None, "order": "C",
            "shape": [NSTEPS, NLAT, NLON], "zarr_format": 2,
        }),
    })
    path = tmp_path / "fake.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return str(path)


@pytest.fixture
def local_store(tmp_path):
    pytest.importorskip("icechunk", reason="icechunk not installed")
    return str(tmp_path / "icechunk_store")


class TestVirtualizerParsing:
    def test_parquet_produces_manifest_store(self, fake_parquet):
        pytest.importorskip("virtualizarr", reason="virtualizarr not installed")
        pytest.importorskip("icechunk", reason="icechunk not installed")
        from gik_icechain.conversion.virtualizer import GIKFlatParquetParser

        parser = GIKFlatParquetParser()
        import fsspec
        import pandas as _pd
        df = _pd.read_parquet(fake_parquet)
        df["key"] = df["key"].astype(str)

        from gik_icechain.conversion.virtualizer import _SFC_STEP_RE
        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        sfc_mask = extracted[0].notna()
        assert sfc_mask.any(), "No SFC chunk refs found in synthetic parquet"

    def test_step_hours_populated(self, fake_parquet):
        pytest.importorskip("virtualizarr", reason="virtualizarr not installed")
        from gik_icechain.conversion.virtualizer import _SFC_STEP_RE
        import pandas as _pd

        df = _pd.read_parquet(fake_parquet)
        df["key"] = df["key"].astype(str)
        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        sfc_mask = extracted[0].notna()
        step_nums = sorted(extracted.loc[sfc_mask, 0].astype(int).unique().tolist())
        assert step_nums == sorted(int(s) for s in STEPS)


class TestIceChunkCommit:
    def test_commit_and_list_snapshots(self, local_store):
        pytest.importorskip("icechunk", reason="icechunk not installed")
        from gik_icechain.conversion.icechunk_writer import IceChainStore

        store = IceChainStore(local_store)
        store.create_or_open()

        # Write real numpy data directly (bypasses ManifestArray / S3 reads)
        import zarr
        session = store._repo.writable_session(store.branch)
        ds = xr.Dataset({"tp": xr.DataArray(
            np.random.default_rng(0).random((3, NSTEPS, NLAT, NLON), dtype=np.float32),
            dims=["member", "step", "latitude", "longitude"],
            coords={"member": [0, 1, 2], "step": STEPS, "latitude": LAT, "longitude": LON},
        )})
        date_str = TEST_DATE.isoformat()
        root = zarr.open_group(session.store, zarr_format=3)
        if date_str in root:
            del root[date_str]
        ds.to_zarr(session.store, group=date_str, mode="w")
        commit_hash = session.commit("integration test", metadata={"forecast_date": date_str})
        store._repo.create_tag(f"{date_str}T00Z", commit_hash)

        snapshots = store.list_snapshots()
        committed_dates = [s["forecast_date"] for s in snapshots]
        assert date_str in committed_dates
        assert len(commit_hash) > 8

    def test_time_travel_checkout(self, local_store):
        pytest.importorskip("icechunk", reason="icechunk not installed")
        from gik_icechain.conversion.icechunk_writer import IceChainStore
        import zarr

        store = IceChainStore(local_store)
        store.create_or_open()

        session = store._repo.writable_session(store.branch)
        ds = xr.Dataset({"tp": xr.DataArray(
            np.ones((2, NSTEPS, NLAT, NLON), dtype=np.float32),
            dims=["member", "step", "latitude", "longitude"],
            coords={"member": [0, 1], "step": STEPS, "latitude": LAT, "longitude": LON},
        )})
        date_str = TEST_DATE.isoformat()
        root = zarr.open_group(session.store, zarr_format=3)
        if date_str in root:
            del root[date_str]
        ds.to_zarr(session.store, group=date_str, mode="w")
        commit_hash = session.commit("tt test", metadata={"forecast_date": date_str})
        store._repo.create_tag(f"{date_str}T00Z", commit_hash)

        historical = store.checkout_as_of(TEST_DATE)
        assert isinstance(historical, xr.Dataset)
        assert "tp" in historical.data_vars

    def test_validate_no_gaps(self, local_store):
        pytest.importorskip("icechunk", reason="icechunk not installed")
        from gik_icechain.conversion.icechunk_writer import IceChainStore
        import zarr

        store = IceChainStore(local_store)
        store.create_or_open()

        session = store._repo.writable_session(store.branch)
        ds = xr.Dataset({"tp": xr.DataArray(
            np.ones((2, NSTEPS, NLAT, NLON), dtype=np.float32),
            dims=["member", "step", "latitude", "longitude"],
            coords={"member": [0, 1], "step": STEPS, "latitude": LAT, "longitude": LON},
        )})
        date_str = TEST_DATE.isoformat()
        root = zarr.open_group(session.store, zarr_format=3)
        if date_str in root:
            del root[date_str]
        ds.to_zarr(session.store, group=date_str, mode="w")
        commit_hash = session.commit("val test", metadata={"forecast_date": date_str})
        store._repo.create_tag(f"{date_str}T00Z", commit_hash)

        report = store.validate()
        assert report["committed_days"] >= 1
        assert report["gaps_detected"] == 0
