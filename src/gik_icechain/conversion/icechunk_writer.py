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

from gik_icechain.shared.codec_registry import register_grib_codecs  # noqa: E402

register_grib_codecs()

VIRTUALIZARR_AVAILABLE = importlib.util.find_spec("virtualizarr") is not None
if not VIRTUALIZARR_AVAILABLE:
    log.warning("virtualizarr_not_installed", msg="pip install virtualizarr")


_DEFAULT_MESSAGE_TEMPLATE = "GIK ingest: {date}T{run_hour:02d}Z"
_DEFAULT_TAG_FORMAT = "{date}T{run_hour:02d}Z"


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
        commit_message_template: str = _DEFAULT_MESSAGE_TEMPLATE,
        tag_format: str = _DEFAULT_TAG_FORMAT,
        manifest_splitting: bool = True,
        manifest_split_dim: str = "step",
        manifest_split_size: int = 1000,
    ) -> None:
        """
        Args:
            storage_uri:              S3 URI (s3://bucket/prefix) or local path.
            branch:                   IceChunk branch name.
            endpoint_url:             Custom S3 endpoint (MinIO/on-prem).
                                      Falls back to AWS_ENDPOINT_URL env var.
            region:                   AWS region for the S3 bucket.  Falls back to
                                      AWS_REGION / AWS_DEFAULT_REGION environment variables.
            commit_message_template:  Python format-string for commit messages.
                                      Receives ``date`` (ISO str) and ``run_hour`` (int).
            tag_format:               Python format-string for snapshot tags.
                                      Receives ``date`` (ISO str) and ``run_hour`` (int).
            manifest_splitting:       Enable IceChunk manifest splitting (bounds
                                      manifest fragment size at scale).
            manifest_split_dim:       Dimension name to split manifests along.
            manifest_split_size:      Max index positions per manifest fragment.
        """
        self.storage_uri = storage_uri
        self.branch = branch
        self._commit_message_template = commit_message_template
        self._tag_format = tag_format
        self._manifest_splitting = manifest_splitting
        self._manifest_split_dim = manifest_split_dim
        self._manifest_split_size = manifest_split_size
        self._repo: Any | None = None
        self._endpoint_url: str | None = endpoint_url or os.environ.get("AWS_ENDPOINT_URL")
        self._region: str | None = (
            region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        )

    def _check_deps(self) -> None:
        if not ICECHUNK_AVAILABLE:
            raise ImportError("icechunk is required: pip install icechunk")
        if not VIRTUALIZARR_AVAILABLE:
            raise ImportError("virtualizarr is required: pip install virtualizarr")

    def create(self) -> None:
        """Create a new IceChunk repository at storage_uri."""
        self._check_deps()
        storage = self._build_storage()

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

        tag = self._tag_format.format(date=forecast_date.isoformat(), run_hour=run_hour)
        date_group = forecast_date.isoformat()
        session = self._repo.writable_session(self.branch)

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
            message=message
            or self._commit_message_template.format(
                date=forecast_date.isoformat(), run_hour=run_hour
            ),
            metadata={
                "forecast_date": forecast_date.isoformat(),
                "run_hour": str(run_hour),
                "commit_time": datetime.now(tz=UTC).isoformat(),
                "source": "gik-icechain",
            },
        )
        # Tags are a best-effort human label for the *first* ingest of a date.
        # IceChunk tags are immutable and names cannot be reused even after
        # deletion, so a re-ingest cannot move its tag — which is why time-travel
        # resolution relies on branch ancestry + commit metadata, not tags
        # (see _snapshots_by_date). A failing create_tag here is therefore benign.
        try:
            self._repo.create_tag(tag, commit_hash)
        except Exception as exc:
            log.debug("icechunk_tag_skipped", tag=tag, reason=str(exc)[:120])
        log.info(
            "icechunk_commit",
            date=forecast_date.isoformat(),
            run_hour=run_hour,
            commit=commit_hash[:12],
            tag=tag,
        )
        return commit_hash

    def _snapshots_by_date(self) -> dict[str, Any]:
        """Map each forecast_date to its current snapshot via branch ancestry.

        ``ancestry`` yields snapshots newest-first, so the first snapshot seen
        for a given ``forecast_date`` is its most recent (re-)ingest. This is the
        source of truth for time-travel and listing: IceChunk tags are immutable
        and non-reusable, so a re-ingested date keeps a stale tag but advances the
        branch — only ancestry reflects the fresh snapshot.

        Returns:
            Ordered ``{forecast_date_iso: SnapshotInfo}`` (insertion = newest-first).
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        latest: dict[str, Any] = {}
        for snap in self._repo.ancestry(branch=self.branch):
            meta = snap.metadata or {}
            fdate = meta.get("forecast_date")
            if not fdate or fdate in latest:
                continue
            latest[fdate] = snap
        return latest

    def checkout_as_of(self, as_of_date: date) -> xr.Dataset:
        """Return the store as it existed on as_of_date (read-only).

        Resolves to the most recent commit for the latest forecast_date on or
        before as_of_date, using branch ancestry (re-ingest aware).

        Args:
            as_of_date: The historical date to check out.

        Returns:
            xr.Dataset opened at the resolved snapshot.

        Example::

            ds = store.checkout_as_of(date(2023, 10, 22))
        """
        snaps = self._snapshots_by_date()
        valid = [d for d in snaps if date.fromisoformat(d) <= as_of_date]
        if not valid:
            earliest = min(snaps) if snaps else "none"
            raise ValueError(f"No commits on or before {as_of_date}. Earliest: {earliest}")

        target_date = max(valid)  # ISO date strings sort chronologically
        snap = snaps[target_date]
        log.info(
            "time_travel_checkout",
            as_of_date=as_of_date.isoformat(),
            resolved_date=target_date,
            snapshot=snap.id[:12],
        )

        session = self._repo.readonly_session(snapshot_id=snap.id)
        return xr.open_zarr(session.store, group=target_date, consolidated=False)

    def readonly_session(self) -> Any:
        """Return a read-only session on the current branch.

        Raises RuntimeError if the store has not been opened yet.
        """
        if self._repo is None:
            raise RuntimeError("Store not opened. Call create_or_open() first.")
        return self._repo.readonly_session(branch=self.branch)

    def open_latest(self) -> xr.Dataset:
        """Open the most recent snapshot of the store, returning the latest date's group."""
        snaps = self._snapshots_by_date()
        if not snaps:
            raise RuntimeError("Store has no committed snapshots.")
        latest_date = max(snaps)  # ISO date strings sort chronologically
        session = self._repo.readonly_session(branch=self.branch)
        ds = xr.open_zarr(session.store, group=latest_date, consolidated=False)
        log.info("store_opened_latest", date=latest_date, dims=dict(ds.sizes))
        return ds

    def list_snapshots(self) -> list[dict[str, str]]:
        """Return the current snapshot per forecast_date, sorted by date.

        One row per date (the most recent re-ingest), resolved from branch
        ancestry rather than tags so re-ingested days report their fresh commit.
        """
        snaps = self._snapshots_by_date()
        snapshots = []
        for fdate in sorted(snaps):
            snap = snaps[fdate]
            meta = snap.metadata or {}
            run_hour = int(meta.get("run_hour", "0") or 0)
            written = getattr(snap, "written_at", None)
            snapshots.append(
                {
                    "tag": f"{fdate}T{run_hour:02d}Z",
                    "commit": snap.id[:12],
                    "forecast_date": fdate,
                    "commit_time": meta.get("commit_time")
                    or (written.isoformat() if written else ""),
                    "message": snap.message or "",
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

        When ``manifest_splitting`` is enabled, the manifest is split along the
        configured dimension every ``manifest_split_size`` index positions, so
        that manifest fragments stay bounded as the archive grows (mitigates the
        single-growing-manifest problem from VirtualiZarr #884).
        """
        # Explicitly set the AWS endpoint so that AWS_ENDPOINT_URL (used for
        # the MinIO store) is not inherited by the ECMWF virtual-chunk store.
        ecmwf_store = icechunk.s3_store(
            region="eu-central-1",
            endpoint_url="https://s3.eu-central-1.amazonaws.com",
            anonymous=True,
        )
        container = icechunk.VirtualChunkContainer(
            url_prefix="s3://ecmwf-forecasts/",
            store=ecmwf_store,
        )
        config = icechunk.RepositoryConfig.default()
        config.set_virtual_chunk_container(container)

        if self._manifest_splitting:
            split_cfg = icechunk.ManifestSplittingConfig(
                split_sizes=(
                    (
                        icechunk.ManifestSplitCondition.AnyArray(),
                        (
                            (
                                icechunk.ManifestSplitDimCondition.DimensionName(
                                    self._manifest_split_dim
                                ),
                                self._manifest_split_size,
                            ),
                        ),
                    ),
                )
            )
            config.manifest = icechunk.ManifestConfig(splitting=split_cfg)
            log.info(
                "icechunk_manifest_splitting",
                dim=self._manifest_split_dim,
                size=self._manifest_split_size,
            )
        return config

    def _build_storage(self) -> Any:
        """Build an icechunk Storage from storage_uri (S3 or local).

        Uses explicit credentials to avoid leaking store credentials into
        IceChunk's virtual chunk fetcher (which needs anonymous access for ECMWF).
        """
        if self.storage_uri.startswith("s3://"):
            path = self.storage_uri[5:]
            parts = path.split("/", 1)
            bucket = parts[0]
            prefix = parts[1].rstrip("/") if len(parts) > 1 else None
            kwargs: dict[str, Any] = {"bucket": bucket, "prefix": prefix}
            if self._region:
                kwargs["region"] = self._region
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

        return icechunk.local_filesystem_storage(self.storage_uri)
