"""Idempotent GRIB codec registration for numcodecs + Zarr v3.

Must be called once per process — required in multiprocessing workers
since codec registries are not inherited across fork.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_registered = False


def register_grib_codecs() -> None:
    """Register GRIBCodec in numcodecs and Zarr v3 registries (idempotent)."""
    global _registered  # noqa: PLW0603
    if _registered:
        return

    ok = False

    try:
        import numcodecs
        from kerchunk.codecs import GRIBCodec

        numcodecs.register_codec(GRIBCodec, "grib")
        ok = True
    except (ImportError, ValueError) as exc:
        log.warning("grib_codec_numcodecs_unavailable", error=str(exc))

    try:
        from zarr.codecs.numcodecs._codecs import _NumcodecsArrayBytesCodec
        from zarr.registry import register_codec

        class _GribBridge(_NumcodecsArrayBytesCodec, codec_name="grib"):
            pass

        register_codec("numcodecs.grib", _GribBridge)
        ok = True
    except (ImportError, ValueError) as exc:
        log.warning("grib_codec_zarr_v3_unavailable", error=str(exc))

    if ok:
        _registered = True
