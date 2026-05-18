"""Cloud storage helpers for S3 and GCS byte-range access."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import s3fs


def get_s3_filesystem(
    no_sign: bool = True,
    region: str = "eu-west-1",
) -> s3fs.S3FileSystem:
    """Return a configured S3FileSystem.

    Args:
        no_sign: If True, use unsigned (public bucket) access — no credentials.
        region:  AWS region for the bucket.
    """
    import s3fs

    return s3fs.S3FileSystem(
        anon=no_sign,
        client_kwargs={"region_name": region},
    )


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
