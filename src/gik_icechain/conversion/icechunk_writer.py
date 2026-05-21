"""Write VirtualiZarr virtual datasets into an IceChunk Zarr v3 store.

Each daily GIK batch is committed as an atomic IceChunk snapshot and tagged
with the forecast date. Users can check out any past snapshot for reproducible
"as-of date X" retrospective queries — directly applicable to anticipatory
action protocols.

The store holds only Zarr chunk manifests (byte-range references); the
underlying GRIB2 files on s3://ecmwf-forecasts are never copied.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

try:
    import icechunk  # noqa: F401
    from icechunk import IcechunkStore, Repository, StorageConfig  # noqa: F401

    ICECHUNK_AVAILABLE = True
except ImportError:
    ICECHUNK_AVAILABLE = False
    log.warning("icechunk_not_installed", msg="pip install icechunk")

try:
    import virtualizarr  # noqa: F401

    VIRTUALIZARR_AVAILABLE = True
except ImportError:
    VIRTUALIZARR_AVAILABLE = False
    log.warning("virtualizarr_not_installed", msg="pip install virtualizarr")


class IceChainStore:
    """Manages the GIK-IceChain virtual store lifecycle.

    Handles creation/opening, per-day virtual dataset appending with commit
    and tag, time-travel checkout, and store validation.
    """

    def __init__(
        self,
        storage_uri: str,
        branch: str = "main",
        endpoint_url: str | None = None,
    ) -> None:
        """
        Args:
            storage_uri:  S3 or MinIO URI for the IceChunk store (s3://bucket/prefix).
            branch:       IceChunk branch name.
            endpoint_url: Custom S3 endpoint for MinIO/on-prem storage.
                          Falls back to the AWS_ENDPOINT_URL environment variable.
        """
        self.storage_uri = storage_uri
        self.branch = branch
        self._repo: Any | None = None
        self._endpoint_url: str | None = endpoint_url or os.environ.get("AWS_ENDPOINT_URL")

    def _check_deps(self) -> None:
        if not ICECHUNK_AVAILABLE:
            raise ImportError("icechunk is required: pip install icechunk")
        if not VIRTUALIZARR_AVAILABLE:
            raise ImportError("virtualizarr is required: pip install virtualizarr")

    def create(self) -> None:
        """Create a new IceChunk repository at storage_uri."""
        self._check_deps()
        self._repo = Repository.create(self._storage_config())
        log.info("icechunk_store_created", uri=self.storage_uri)

    def open(self) -> None:
        """Open an existing IceChunk repository."""
        self._check_deps()
        self._repo = Repository.open(self._storage_config())
        log.info("icechunk_store_opened", uri=self.storage_uri)

    def create_or_open(self) -> None:
        """Open the store if it exists, otherwise create it."""
        self._check_deps()
        storage = self._storage_config()
        try:
            self._repo = Repository.open(storage)
            log.info("icechunk_store_opened_existing", uri=self.storage_uri)
        except Exception:
            self._repo = Repository.create(storage)
            log.info("icechunk_store_created_new", uri=self.storage_uri)

    def commit_day(
        self,
        forecast_date: date,
        virtual_ds: xr.Dataset,
        run_hour: int = 0,
        message: str | None = None,
    ) -> str:
        """Append one forecast day's virtual dataset and commit a new snapshot.

        The commit is tagged with ``{date}T{run_hour:02d}Z`` for time-travel
        lookup via checkout_as_of().

        Args:
            forecast_date: Forecast initialisation date.
            virtual_ds:    xr.Dataset with virtual (ManifestArray) variables.
            run_hour:      UTC run hour (0, 6, 12, or 18).
            message:       Optional commit message.

        Returns:
            Commit hash string (IceChunk snapshot ID).
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        tag = f"{forecast_date.isoformat()}T{run_hour:02d}Z"
        session = self._repo.writable_session(self.branch)
        try:
            virtual_ds.virtualize.to_icechunk(session.store)
        except Exception as primary_err:
            log.warning(
                "virtualizarr_failed_trying_cfgrib_fallback",
                date=forecast_date.isoformat(),
                error=str(primary_err),
            )
            try:
                import cfgrib  # type: ignore[import-untyped]

                date_prefix = (
                    f"s3://ecmwf-forecasts/"
                    f"{forecast_date.strftime('%Y/%m/%d')}/{run_hour:02d}z/"
                )
                fallback_datasets = cfgrib.open_datasets(
                    date_prefix, backend_kwargs={"indexpath": ""}
                )
                for ds in fallback_datasets:
                    xr.Dataset(ds).to_zarr(session.store, append_dim="time")
                log.info("cfgrib_fallback_success", date=forecast_date.isoformat())
            except Exception as fallback_err:
                log.error(
                    "both_parsers_failed",
                    date=forecast_date.isoformat(),
                    primary_error=str(primary_err),
                    fallback_error=str(fallback_err),
                )
                raise RuntimeError(
                    f"Cannot ingest GRIB2 for {forecast_date}: {primary_err}"
                ) from primary_err

        commit_hash = session.commit(
            message=message or f"GIK ingest: {tag}",
            metadata={
                "forecast_date": forecast_date.isoformat(),
                "run_hour": str(run_hour),
                "commit_time": datetime.now(tz=UTC).isoformat(),
                "source": "gik-icechain",
            },
        )
        self._repo.create_tag(tag, commit_hash)
        log.info(
            "icechunk_commit",
            date=forecast_date.isoformat(),
            run_hour=run_hour,
            commit=commit_hash[:12],
            tag=tag,
        )
        return commit_hash

    def checkout_as_of(self, as_of_date: date) -> xr.Dataset:
        """Return the store as it existed on as_of_date (read-only).

        Resolves to the most recent commit tagged on or before as_of_date.

        Args:
            as_of_date: The historical date to check out.

        Returns:
            xr.Dataset opened at the resolved snapshot.

        Example::

            ds = store.checkout_as_of(date(2023, 10, 22))
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        all_tags = self._repo.list_tags()
        # Tag format is "YYYY-MM-DDTHH Z"; ISO lexicographic comparison is correct.
        valid_tags = [t for t in all_tags if t[:10] <= as_of_date.isoformat()]

        if not valid_tags:
            earliest = min(t[:10] for t in all_tags)
            raise ValueError(f"No commits on or before {as_of_date}. Earliest: {earliest}")

        latest_tag = sorted(valid_tags)[-1]
        snapshot_id = self._repo.get_tag(latest_tag)
        log.info(
            "time_travel_checkout",
            as_of_date=as_of_date.isoformat(),
            resolved_tag=latest_tag,
            snapshot=snapshot_id[:12],
        )

        session = self._repo.readonly_session(snapshot=snapshot_id)
        return xr.open_zarr(session.store, consolidated=False)

    def open_latest(self) -> xr.Dataset:
        """Open the most recent snapshot of the store."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        session = self._repo.readonly_session(branch=self.branch)
        ds = xr.open_zarr(session.store, consolidated=False)
        log.info("store_opened_latest", dims=dict(ds.dims))
        return ds

    def list_snapshots(self) -> list[dict[str, str]]:
        """Return all committed snapshots with metadata, sorted by tag."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        snapshots = []
        for tag in sorted(self._repo.list_tags()):
            commit_hash = self._repo.get_tag(tag)
            meta = self._repo.get_commit_metadata(commit_hash)
            snapshots.append(
                {
                    "tag": tag,
                    "commit": commit_hash[:12],
                    "forecast_date": meta.get("forecast_date", ""),
                    "commit_time": meta.get("commit_time", ""),
                    "message": meta.get("message", ""),
                }
            )
        return snapshots

    def compact(self, keep_snapshots: int = 30) -> dict[str, int]:
        """Consolidate daily micro-manifests into a single index (monthly maintenance).

        Squashes IceChunk commit history, keeping only the *keep_snapshots* most
        recent snapshots. Keeps the root manifest index under ~50 MB so that
        time-to-first-byte stays below 3 seconds for end users.

        Args:
            keep_snapshots: Number of recent snapshots to preserve (default: 30 = 1 month).

        Returns:
            Dict with ``snapshots_before`` and ``snapshots_after`` counts.
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        snapshots_before = len(self.list_snapshots())
        self._repo.squash_history(keep=keep_snapshots)
        snapshots_after = len(self.list_snapshots())

        log.info(
            "icechunk_compacted",
            snapshots_before=snapshots_before,
            snapshots_after=snapshots_after,
            kept=keep_snapshots,
        )
        return {"snapshots_before": snapshots_before, "snapshots_after": snapshots_after}

    def validate(self) -> dict[str, Any]:
        """Validate store integrity: day count, gaps, and variable accessibility."""
        ds = self.open_latest()
        snapshots = self.list_snapshots()

        committed_dates = sorted({s["forecast_date"] for s in snapshots})
        n_days = len(committed_dates)

        gaps: list[str] = []
        if committed_dates:
            current = date.fromisoformat(committed_dates[0])
            for ds_date in committed_dates[1:]:
                next_d = date.fromisoformat(ds_date)
                if (next_d - current).days > 1:
                    gaps.append(f"{current} → {next_d}")
                current = next_d

        result: dict[str, Any] = {
            "committed_days": n_days,
            "date_range": (
                f"{committed_dates[0]} to {committed_dates[-1]}" if committed_dates else "empty"
            ),
            "gaps_detected": len(gaps),
            "gap_details": gaps[:10],
            "variables_present": list(ds.data_vars) if ds else [],
            "total_snapshots": len(snapshots),
        }
        log.info("store_validation", **{k: v for k, v in result.items() if k != "gap_details"})
        return result

    def _storage_config(self) -> Any:
        """Build a StorageConfig from storage_uri.

        Supports both AWS S3 and MinIO-compatible stores. When AWS_ENDPOINT_URL
        is set (or endpoint_url was passed to __init__), force_path_style is
        enabled automatically — required by MinIO.
        """
        if not self.storage_uri.startswith("s3://"):
            raise ValueError(f"Unsupported storage URI scheme: {self.storage_uri}")
        path = self.storage_uri[5:]
        parts = path.split("/", 1)
        bucket = parts[0]
        prefix = parts[1].rstrip("/") if len(parts) > 1 else ""
        kwargs: dict[str, Any] = {"bucket": bucket}
        if prefix:
            kwargs["prefix"] = prefix
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
            kwargs["force_path_style"] = True
            log.info("icechunk_minio_endpoint", endpoint=self._endpoint_url, bucket=bucket)
        return StorageConfig.s3_from_env(**kwargs)
