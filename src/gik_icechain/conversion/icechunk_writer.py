"""Write VirtualiZarr virtual datasets into an IceChunk Zarr v3 store.

Each daily GIK batch is committed as an atomic IceChunk snapshot and tagged
with the forecast date. Users can check out any past snapshot for reproducible
"as-of date X" retrospective queries — directly applicable to anticipatory
action protocols.

The store holds only Zarr chunk manifests (byte-range references); the
underlying GRIB2 files on s3://ecmwf-forecasts are never copied.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

try:
    import icechunk

    ICECHUNK_AVAILABLE = True
except ImportError:
    ICECHUNK_AVAILABLE = False
    log.warning("icechunk_not_installed", msg="pip install icechunk")

# Register the kerchunk GRIBCodec so Zarr can decode GRIB2 virtual chunks at read time.
try:
    import numcodecs
    from kerchunk.codecs import GRIBCodec

    numcodecs.register_codec(GRIBCodec, "grib")
except (ImportError, ValueError):
    pass

# Bridge GRIBCodec into Zarr v3 registry (see virtualizer.py for rationale).
try:
    from zarr.codecs.numcodecs._codecs import _NumcodecsArrayBytesCodec
    from zarr.registry import register_codec

    class GribNumcodecs(_NumcodecsArrayBytesCodec, codec_name="grib"):
        pass

    register_codec("numcodecs.grib", GribNumcodecs)
except (ImportError, ValueError):
    pass

VIRTUALIZARR_AVAILABLE = importlib.util.find_spec("virtualizarr") is not None
if not VIRTUALIZARR_AVAILABLE:
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
        region: str | None = None,
    ) -> None:
        """
        Args:
            storage_uri:  S3 URI (s3://bucket/prefix) or local path for the store.
            branch:       IceChunk branch name.
            endpoint_url: Custom S3 endpoint for MinIO/on-prem storage.
                          Falls back to the AWS_ENDPOINT_URL environment variable.
            region:       AWS region for the S3 bucket.  Falls back to
                          AWS_REGION / AWS_DEFAULT_REGION environment variables.
        """
        self.storage_uri = storage_uri
        self.branch = branch
        self._repo: Any | None = None
        self._endpoint_url: str | None = endpoint_url or os.environ.get("AWS_ENDPOINT_URL")
        self._region: str | None = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )

    def _check_deps(self) -> None:
        if not ICECHUNK_AVAILABLE:
            raise ImportError("icechunk is required: pip install icechunk")
        if not VIRTUALIZARR_AVAILABLE:
            raise ImportError("virtualizarr is required: pip install virtualizarr")

    @staticmethod
    def _clear_aws_credential_env() -> None:
        """Remove AWS credential env vars so IceChunk uses explicit credentials.

        IceChunk reads AWS_* env vars at virtual-chunk-fetch time, overriding
        the anonymous credentials we set for the public ECMWF bucket.  Since we
        pass explicit credentials to s3_storage() for the metadata store, the
        env vars are no longer needed after storage is built.

        AWS_REGION is preserved — it is harmless and may be needed by other
        libraries in the same process.
        """
        for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                     "AWS_SESSION_TOKEN", "AWS_ENDPOINT_URL"):
            os.environ.pop(key, None)

    def create(self) -> None:
        """Create a new IceChunk repository at storage_uri."""
        self._check_deps()
        storage = self._build_storage()
        self._clear_aws_credential_env()
        self._repo = icechunk.Repository.create(
            storage,
            config=self._build_repo_config(),
            authorize_virtual_chunk_access=self._virtual_chunk_credentials(),
        )
        log.info("icechunk_store_created", uri=self.storage_uri)

    def open(self) -> None:
        """Open an existing IceChunk repository."""
        self._check_deps()
        storage = self._build_storage()
        self._clear_aws_credential_env()
        self._repo = icechunk.Repository.open(
            storage,
            config=self._build_repo_config(),
            authorize_virtual_chunk_access=self._virtual_chunk_credentials(),
        )
        log.info("icechunk_store_opened", uri=self.storage_uri)

    def create_or_open(self) -> None:
        """Open the store if it exists, otherwise create it."""
        self._check_deps()
        storage = self._build_storage()
        self._clear_aws_credential_env()
        self._repo = icechunk.Repository.open_or_create(
            storage,
            config=self._build_repo_config(),
            authorize_virtual_chunk_access=self._virtual_chunk_credentials(),
        )
        log.info("icechunk_store_ready", uri=self.storage_uri)

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
        # Each day is written to its own zarr group so multiple dates can
        # coexist in the same IceChunk store without path conflicts.
        date_group = forecast_date.isoformat()
        session = self._repo.writable_session(self.branch)

        # Idempotent re-run: delete the group if it already exists in this branch.
        # This allows re-ingesting a date without creating a new store.
        try:
            import zarr

            root = zarr.open_group(session.store, zarr_format=3)
            if date_group in root:
                del root[date_group]
                log.debug("icechunk_group_cleared", group=date_group)
        except Exception as exc:
            log.debug("icechunk_group_clear_skipped", group=date_group, reason=str(exc))

        virtual_ds.virtualize.to_icechunk(session.store, group=date_group)

        commit_hash = session.commit(
            message=message or f"GIK ingest: {tag}",
            metadata={
                "forecast_date": forecast_date.isoformat(),
                "run_hour": str(run_hour),
                "commit_time": datetime.now(tz=UTC).isoformat(),
                "source": "gik-icechain",
            },
        )
        try:
            self._repo.create_tag(tag, commit_hash)
        except Exception as exc:
            # Tags are immutable in IceChunk — skip re-creation on idempotent re-runs.
            log.debug("icechunk_tag_skipped", tag=tag, reason=str(exc)[:120])
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
        snapshot_id = self._repo.lookup_tag(latest_tag)
        log.info(
            "time_travel_checkout",
            as_of_date=as_of_date.isoformat(),
            resolved_tag=latest_tag,
            snapshot=snapshot_id[:12],
        )

        session = self._repo.readonly_session(snapshot_id=snapshot_id)
        # Each date's data lives in its own zarr group named after the date.
        # Resolve the tag date to find the matching group.
        target_date = latest_tag[:10]
        return xr.open_zarr(session.store, group=target_date, consolidated=False)

    def open_latest(self) -> xr.Dataset:
        """Open the most recent snapshot of the store, returning the latest date's group."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        session = self._repo.readonly_session(branch=self.branch)
        tags = sorted(self._repo.list_tags())
        if not tags:
            raise RuntimeError("Store has no committed snapshots.")
        latest_date = tags[-1][:10]
        ds = xr.open_zarr(session.store, group=latest_date, consolidated=False)
        log.info("store_opened_latest", date=latest_date, dims=dict(ds.sizes))
        return ds

    def list_snapshots(self) -> list[dict[str, str]]:
        """Return all committed snapshots with metadata, sorted by tag."""
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        snapshots = []
        for tag in sorted(self._repo.list_tags()):
            snapshot_id = self._repo.lookup_tag(tag)
            info = self._repo.inspect_snapshot(snapshot_id)
            meta = info.get("metadata") or {}
            snapshots.append(
                {
                    "tag": tag,
                    "commit": snapshot_id[:12],
                    "forecast_date": meta.get("forecast_date", ""),
                    "commit_time": meta.get("commit_time", ""),
                    "message": info.get("commit_message", ""),
                }
            )
        return snapshots

    def compact(self, keep_days: int = 30) -> dict[str, int]:
        """Expire snapshots older than keep_days days (monthly maintenance).

        Args:
            keep_days: Retain snapshots from the last keep_days days (default: 30).

        Returns:
            Dict with ``expired`` count (number of snapshot IDs removed).
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")

        cutoff = datetime.now(tz=UTC) - timedelta(days=keep_days)
        expired = self._repo.expire_snapshots(cutoff)
        log.info("icechunk_compacted", expired=len(expired), keep_days=keep_days)
        return {"expired": len(expired)}

    def validate(self) -> dict[str, Any]:
        """Validate store integrity: day count, gaps, and variable accessibility."""
        ds = self.open_latest()
        snapshots = self.list_snapshots()

        committed_dates = sorted({s["forecast_date"] for s in snapshots if s["forecast_date"]})
        n_days = len(committed_dates)

        gaps: list[str] = []
        if committed_dates:
            current = date.fromisoformat(committed_dates[0])
            for ds_date in committed_dates[1:]:
                next_d = date.fromisoformat(ds_date)
                if (next_d - current).days > 1:
                    gaps.append(f"{current} -> {next_d}")
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

    def _virtual_chunk_credentials(self) -> dict[str, Any]:
        """Return the authorize_virtual_chunk_access map for ECMWF public S3.

        IceChunk requires explicit authorization per virtual chunk container
        prefix as a security measure.  We use explicit anonymous credentials
        rather than ``None`` (which falls back to env vars that may contain
        MinIO credentials inappropriate for the ECMWF bucket).
        """
        return icechunk.containers_credentials(
            {"s3://ecmwf-forecasts/": icechunk.s3_anonymous_credentials()}
        )

    def _build_repo_config(self) -> Any:
        """Build a RepositoryConfig that authorises virtual chunks from s3://ecmwf-forecasts/.

        IceChunk requires explicit virtual chunk container declarations so it
        knows which external S3 prefixes are trusted for byte-range references.
        The ECMWF bucket is public (anonymous S3 access).
        """
        ecmwf_store = icechunk.s3_store(region="eu-central-1", anonymous=True)
        container = icechunk.VirtualChunkContainer(
            url_prefix="s3://ecmwf-forecasts/",
            store=ecmwf_store,
        )
        config = icechunk.RepositoryConfig.default()
        config.set_virtual_chunk_container(container)
        return config

    def _build_storage(self) -> Any:
        """Build an icechunk Storage from storage_uri.

        Supports S3 (AWS or MinIO) and local filesystem paths.
        MinIO: set AWS_ENDPOINT_URL env var or pass endpoint_url to __init__.

        Note: we pass explicit credentials instead of ``from_env=True`` because
        ``from_env`` leaks the storage credentials into IceChunk's virtual chunk
        fetcher, overriding the anonymous credentials required for the public
        ECMWF S3 bucket.
        """
        if self.storage_uri.startswith("s3://"):
            path = self.storage_uri[5:]
            parts = path.split("/", 1)
            bucket = parts[0]
            prefix = parts[1].rstrip("/") if len(parts) > 1 else None
            kwargs: dict[str, Any] = {"bucket": bucket, "prefix": prefix}
            if self._region:
                kwargs["region"] = self._region
            # Read AWS credentials explicitly to avoid from_env leaking into
            # virtual chunk access (IceChunk bug/design issue).
            access_key = os.environ.get("AWS_ACCESS_KEY_ID")
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            session_token = os.environ.get("AWS_SESSION_TOKEN")
            if access_key and secret_key:
                kwargs["access_key_id"] = access_key
                kwargs["secret_access_key"] = secret_key
                if session_token:
                    kwargs["session_token"] = session_token
            else:
                kwargs["from_env"] = True
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
                kwargs["allow_http"] = True
                kwargs["force_path_style"] = True
                log.info("icechunk_minio_endpoint", endpoint=self._endpoint_url, bucket=bucket)
            return icechunk.s3_storage(**kwargs)

        # Local filesystem (for testing)
        return icechunk.local_filesystem_storage(self.storage_uri)
