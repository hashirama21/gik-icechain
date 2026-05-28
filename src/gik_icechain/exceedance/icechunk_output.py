"""Versioned IceChunk store for computed decision artefacts.

Unlike the C1 virtual store (manifests pointing to raw GRIB2 on S3), this
store holds the actual computed values — exceedance probabilities and ensemble
confidence — with IceChunk time-travel semantics.

Each daily commit creates a snapshot tagged ``YYYY-MM-DD``, enabling queries
like ``store.checkout_as_of(date(2023, 10, 25))`` to retrieve the exceedance
probabilities that were computed with the data available on that date.

No virtual chunk credentials are needed — this stores native Zarr arrays.
"""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, date, datetime
from typing import Any

import structlog
import xarray as xr

log = structlog.get_logger(__name__)


class DecisionStore:
    """IceChunk store for versioned C2/C3 decision artefacts."""

    def __init__(
        self,
        storage_uri: str,
        branch: str = "main",
        region: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        self.storage_uri = storage_uri
        self.branch = branch
        self._region = (
            region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        )
        self._endpoint_url: str | None = endpoint_url or os.environ.get("AWS_ENDPOINT_URL")
        self._repo: Any | None = None

    def create_or_open(self) -> None:
        import icechunk

        storage = self._build_storage()
        self._repo = icechunk.Repository.open_or_create(storage)
        log.info("decision_store_ready", uri=self.storage_uri)

    def commit_day(
        self,
        forecast_date: date,
        ds: xr.Dataset,
        message: str | None = None,
    ) -> str:
        """Write one day's artefacts and commit as a tagged snapshot."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        import zarr

        group = forecast_date.isoformat()
        session = self._repo.writable_session(self.branch)

        root = zarr.open_group(session.store, zarr_format=3)
        if group in root:
            del root[group]

        ds.to_zarr(session.store, group=group, mode="w")

        tag = forecast_date.isoformat()
        commit_hash = session.commit(
            message=message or f"decision artefact: {tag}",
            metadata={
                "forecast_date": tag,
                "commit_time": datetime.now(tz=UTC).isoformat(),
                "source": "gik-icechain",
            },
        )
        with contextlib.suppress(Exception):
            self._repo.create_tag(tag, commit_hash)

        log.info("decision_committed", date=tag, commit=commit_hash[:12])
        return commit_hash

    def open_date(self, forecast_date: date) -> xr.Dataset:
        """Open the artefact dataset for a specific forecast date."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        session = self._repo.readonly_session(branch=self.branch)
        return xr.open_zarr(session.store, group=forecast_date.isoformat(), consolidated=False)

    def checkout_as_of(self, as_of_date: date) -> xr.Dataset:
        """Time-travel: open artefacts as they existed on ``as_of_date``."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        tags = sorted(self._repo.list_tags())
        valid = [t for t in tags if t[:10] <= as_of_date.isoformat()]
        if not valid:
            raise ValueError(f"No artefacts available on or before {as_of_date}")

        latest_tag = valid[-1]
        snapshot_id = self._repo.lookup_tag(latest_tag)
        session = self._repo.readonly_session(snapshot_id=snapshot_id)
        return xr.open_zarr(session.store, group=latest_tag[:10], consolidated=False)

    def list_dates(self) -> list[str]:
        """Return sorted list of committed forecast dates."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        return sorted(self._repo.list_tags())

    def _build_storage(self) -> Any:
        import icechunk

        if self.storage_uri.startswith("s3://"):
            path = self.storage_uri[5:]
            parts = path.split("/", 1)
            bucket = parts[0]
            prefix = parts[1].rstrip("/") if len(parts) > 1 else None
            kwargs: dict[str, Any] = {"bucket": bucket, "prefix": prefix, "from_env": True}
            if self._region:
                kwargs["region"] = self._region
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
                kwargs["allow_http"] = True
                kwargs["force_path_style"] = True
            return icechunk.s3_storage(**kwargs)

        return icechunk.local_filesystem_storage(self.storage_uri)
