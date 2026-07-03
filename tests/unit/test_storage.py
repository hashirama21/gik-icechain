"""Unit tests for the path-agnostic storage helpers (local + remote via fsspec).

Remote behaviour is exercised against the in-process ``memory://`` filesystem so
no network/credentials are needed; the same code path serves ``s3://`` in prod.
"""

import pytest

from gik_icechain.shared.storage import (
    is_remote_uri,
    join_uri,
    path_exists,
    read_text,
    remove_path,
    write_text,
)


class TestUriHelpers:
    def test_join_uri_local(self):
        assert join_uri("results/risk", "2024-11-15.json") == "results/risk/2024-11-15.json"

    def test_join_uri_trims_slashes(self):
        assert join_uri("s3://b/pre/", "/a.json") == "s3://b/pre/a.json"

    def test_join_uri_multi(self):
        assert join_uri("s3://b", "x", "y.json") == "s3://b/x/y.json"

    @pytest.mark.parametrize(
        ("uri", "remote"),
        [
            ("s3://bucket/key", True),
            ("gs://bucket/key", True),
            ("results/x.json", False),
            ("file:///tmp/x.json", False),
            ("C:/Users/x.json", False),
        ],
    )
    def test_is_remote_uri(self, uri, remote):
        assert is_remote_uri(uri) is remote


class TestLocalRoundTrip:
    def test_write_read_creates_parents(self, tmp_path):
        uri = str(tmp_path / "a" / "b" / "scores.json")
        write_text(uri, '{"x": 1}')
        assert path_exists(uri)
        assert read_text(uri) == '{"x": 1}'

    def test_read_missing_returns_none(self, tmp_path):
        assert read_text(str(tmp_path / "nope.json")) is None

    def test_remove(self, tmp_path):
        uri = str(tmp_path / "z.json")
        write_text(uri, "hi")
        remove_path(uri)
        assert not path_exists(uri)

    def test_remove_missing_is_noop(self, tmp_path):
        remove_path(str(tmp_path / "ghost.json"))  # must not raise


class TestRemoteRoundTrip:
    """Same code path against fsspec memory:// (stands in for s3://)."""

    def test_write_read_remote(self):
        uri = "memory://gik-test/day/2024-11-15_risk_scores.json"
        write_text(uri, '{"units": {}}')
        assert path_exists(uri)
        assert read_text(uri) == '{"units": {}}'
        remove_path(uri)
        assert not path_exists(uri)

    def test_missing_remote_returns_none(self):
        assert read_text("memory://gik-test/absent.json") is None
