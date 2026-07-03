"""Cloud storage helpers for S3 and GCS byte-range access."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import s3fs

try:
    from tenacity import retry, stop_after_attempt, wait_exponential

    _s3_retry = retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
except ImportError:
    # tenacity not installed - fall back to no-op decorator
    def _s3_retry(fn):  # type: ignore[misc]
        return fn


def _fs_and_path(uri: str, storage_options: dict | None = None):
    """Resolve an fsspec filesystem + path for a local path or remote URI."""
    import fsspec

    return fsspec.core.url_to_fs(str(uri), **(storage_options or {}))


def is_remote_uri(uri: str) -> bool:
    """True for a remote URI (``s3://``, ``gs://`` …), False for a local path."""
    s = str(uri)
    return "://" in s and not s.startswith("file://")


def join_uri(base: str, *parts: str) -> str:
    """Join path parts under a local path or remote URI (POSIX separators)."""
    joined = str(base).rstrip("/")
    for part in parts:
        joined = f"{joined}/{str(part).strip('/')}"
    return joined


def write_text(uri: str, data: str, storage_options: dict | None = None) -> str:
    """Write *data* to a local path or remote URI, creating parent dirs.

    Path-agnostic via fsspec: the same call writes ``results/x.json`` locally or
    ``s3://bucket/x.json`` in production. Returns the URI written.
    """
    fs, path = _fs_and_path(uri, storage_options)
    parent = path.rsplit("/", 1)[0] if "/" in path.replace("\\", "/") else ""
    if parent:
        with contextlib.suppress(FileExistsError, NotImplementedError):
            fs.makedirs(parent, exist_ok=True)
    with fs.open(path, "w") as f:
        f.write(data)
    return str(uri)


def read_text(uri: str, storage_options: dict | None = None) -> str | None:
    """Read text from a local path or remote URI; return None if it doesn't exist."""
    fs, path = _fs_and_path(uri, storage_options)
    if not fs.exists(path):
        return None
    with fs.open(path, "r") as f:
        return str(f.read())


def path_exists(uri: str, storage_options: dict | None = None) -> bool:
    """True if the local path or remote URI exists."""
    fs, path = _fs_and_path(uri, storage_options)
    return bool(fs.exists(path))


def remove_path(uri: str, storage_options: dict | None = None) -> None:
    """Best-effort delete of a local path or remote URI (no error if absent)."""
    fs, path = _fs_and_path(uri, storage_options)
    with contextlib.suppress(FileNotFoundError):
        fs.rm(path)


def get_s3_filesystem(
    no_sign: bool = True,
    region: str = "eu-west-1",
) -> s3fs.S3FileSystem:
    """Return a configured S3FileSystem.

    Args:
        no_sign: If True, use unsigned (public bucket) access - no credentials.
        region:  AWS region for the bucket.
    """
    import s3fs

    return s3fs.S3FileSystem(
        anon=no_sign,
        client_kwargs={"region_name": region},
    )


@_s3_retry
def open_byte_range(
    uri: str,
    offset: int,
    length: int,
    fs: s3fs.S3FileSystem | None = None,
) -> bytes:
    """Read *length* bytes starting at *offset* from a remote file.

    Args:
        uri:    Full S3 URI (``s3://bucket/key``) or GCS URI (``gs://...``).
        offset: Byte offset from the start of the file.
        length: Number of bytes to read.
        fs:     Optional pre-configured filesystem; created via
                :func:`get_s3_filesystem` if omitted.

    Returns:
        Raw bytes from the requested byte range.
    """
    if fs is None:
        fs = get_s3_filesystem()
    with fs.open(uri, "rb") as f:
        f.seek(offset)
        return f.read(length)


@_s3_retry
def list_s3_objects(
    bucket: str,
    prefix: str,
    fs: s3fs.S3FileSystem | None = None,
) -> list[str]:
    """List all object keys under *bucket/prefix*.

    Args:
        bucket: S3 bucket name (without ``s3://``).
        prefix: Key prefix to filter results.
        fs:     Optional pre-configured filesystem.

    Returns:
        List of full ``s3://bucket/key`` URIs.
    """
    if fs is None:
        fs = get_s3_filesystem()
    keys: list[str] = fs.ls(f"{bucket}/{prefix}", detail=False)
    return [f"s3://{k}" if not k.startswith("s3://") else k for k in keys]
