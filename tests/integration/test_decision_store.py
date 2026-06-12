"""Integration tests for the DecisionStore IceChunk artefact store (C2/C3 output)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr

pytestmark = pytest.mark.integration

TEST_DATE = date(2025, 1, 1)


def _artefact(value: float) -> xr.Dataset:
    return xr.Dataset(
        {
            "exceedance_prob": xr.DataArray(
                np.full((2, 2), value, dtype=np.float32),
                dims=["latitude", "longitude"],
                coords={"latitude": [0.0, 1.0], "longitude": [35.0, 36.0]},
            )
        }
    )


@pytest.fixture
def store(tmp_path):
    pytest.importorskip("icechunk", reason="icechunk not installed")
    from gik_icechain.exceedance.icechunk_output import DecisionStore

    s = DecisionStore(str(tmp_path / "decision_store"))
    s.create_or_open()
    return s


def test_commit_and_list_dates(store):
    store.commit_day(TEST_DATE, _artefact(1.0))
    assert store.list_dates() == [TEST_DATE.isoformat()]
    ds = store.checkout_as_of(TEST_DATE)
    assert float(ds["exceedance_prob"].mean()) == pytest.approx(1.0)


def test_reingest_resolves_fresh(store):
    """Re-committing a date surfaces the new snapshot, not the stale tag.

    IceChunk tags are immutable and non-reusable, so the date's tag stays on the
    first commit; checkout_as_of/list_dates must resolve from branch ancestry.
    """
    first = store.commit_day(TEST_DATE, _artefact(1.0))
    second = store.commit_day(TEST_DATE, _artefact(2.0))

    assert first != second
    # Tag is stuck on the first commit (expected IceChunk semantics).
    assert store._repo.lookup_tag(TEST_DATE.isoformat()) == first
    # Public API resolves the fresh re-ingest.
    assert store.list_dates() == [TEST_DATE.isoformat()]  # de-duplicated
    ds = store.checkout_as_of(TEST_DATE)
    assert float(ds["exceedance_prob"].mean()) == pytest.approx(2.0)
