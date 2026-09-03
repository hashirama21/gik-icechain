"""Unit tests for byte-range coalescence."""

from gik_icechain.shared.byte_range import (
    ByteRange,
    CoalescedRange,
    coalesce_byte_ranges,
    fetch_coalesced_ranges,
)


def _br(uri: str, offset: int, length: int, member: int = 0, step: int = 0) -> ByteRange:
    """Shorthand for building a ByteRange in tests."""
    return ByteRange(
        uri=uri,
        offset=offset,
        length=length,
        metadata={"member_idx": member, "step_idx": step, "variable": "tp"},
    )


class TestCoalesceByteRanges:
    """Tests for the coalesce_byte_ranges function."""

    def test_empty_input(self):
        assert coalesce_byte_ranges([]) == []

    def test_single_range(self):
        ranges = [_br("s3://bucket/key", 0, 100)]
        result = coalesce_byte_ranges(ranges)
        assert len(result) == 1
        cr = result[0]
        assert cr.uri == "s3://bucket/key"
        assert cr.offset == 0
        assert cr.length == 100
        assert len(cr.original_ranges) == 1
        assert cr.slices == ((0, 100),)

    def test_adjacent_ranges_merged(self):
        """Two adjacent ranges (gap=0) should merge into one."""
        ranges = [
            _br("s3://bucket/key", 0, 100, member=0),
            _br("s3://bucket/key", 100, 200, member=1),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=0)
        assert len(result) == 1
        cr = result[0]
        assert cr.offset == 0
        assert cr.length == 300
        assert len(cr.original_ranges) == 2
        assert cr.slices == ((0, 100), (100, 300))

    def test_small_gap_merged(self):
        """Ranges with a gap smaller than max_gap_bytes should merge."""
        ranges = [
            _br("s3://bucket/key", 0, 100),
            _br("s3://bucket/key", 200, 100),  # 100-byte gap
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=100)
        assert len(result) == 1
        assert result[0].length == 300

    def test_large_gap_not_merged(self):
        """Ranges with a gap larger than max_gap_bytes should stay separate."""
        ranges = [
            _br("s3://bucket/key", 0, 100),
            _br("s3://bucket/key", 200, 100),  # 100-byte gap
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=50)
        assert len(result) == 2

    def test_max_merged_bytes_respected(self):
        """Merging should stop if total length exceeds max_merged_bytes."""
        ranges = [
            _br("s3://bucket/key", 0, 300),
            _br("s3://bucket/key", 300, 300),
            _br("s3://bucket/key", 600, 300),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=0, max_merged_bytes=500)
        # 300+300=600 > 500, so second range can't merge with first
        assert len(result) == 3

    def test_different_uris_never_merged(self):
        """Ranges from different URIs must never be merged."""
        ranges = [
            _br("s3://bucket/key1", 0, 100),
            _br("s3://bucket/key2", 0, 100),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=1_000_000)
        assert len(result) == 2
        uris = {cr.uri for cr in result}
        assert uris == {"s3://bucket/key1", "s3://bucket/key2"}

    def test_slices_correct_for_demultiplex(self):
        """Verify slice offsets allow correct demultiplexing from merged buffer."""
        ranges = [
            _br("s3://bucket/key", 1000, 50, member=0, step=0),
            _br("s3://bucket/key", 1050, 75, member=0, step=1),
            _br("s3://bucket/key", 1125, 25, member=1, step=0),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=0, max_merged_bytes=10_000)
        assert len(result) == 1
        cr = result[0]
        assert cr.offset == 1000
        assert cr.length == 150
        # Slices are relative to the merged buffer start
        assert cr.slices == ((0, 50), (50, 125), (125, 150))

    def test_unsorted_input_handled(self):
        """coalesce_byte_ranges should sort by offset internally."""
        ranges = [
            _br("s3://bucket/key", 200, 100, member=1),
            _br("s3://bucket/key", 0, 100, member=0),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=100)
        assert len(result) == 1
        assert result[0].offset == 0
        assert result[0].length == 300

    def test_multiple_groups(self):
        """Multiple URIs should each be coalesced independently."""
        ranges = [
            _br("s3://a", 0, 100),
            _br("s3://a", 100, 100),
            _br("s3://b", 0, 50),
            _br("s3://b", 50, 50),
        ]
        result = coalesce_byte_ranges(ranges, max_gap_bytes=0)
        assert len(result) == 2
        by_uri = {cr.uri: cr for cr in result}
        assert by_uri["s3://a"].length == 200
        assert by_uri["s3://b"].length == 100

    def test_coalesced_range_is_frozen(self):
        """CoalescedRange should be immutable."""
        cr = CoalescedRange(
            uri="s3://x",
            offset=0,
            length=100,
            slices=((0, 100),),
            original_ranges=(_br("s3://x", 0, 100),),
        )
        try:
            cr.uri = "s3://y"  # type: ignore[misc]
            raise AssertionError("Should have raised AttributeError")
        except AttributeError:
            pass


class TestFetchCoalescedRanges:
    """Tests for the obstore-backed multi-range fetch (ISSUE-4 / ISSUE-5)."""

    def test_groups_by_file_and_tolerates_failure(self, monkeypatch):
        """One get_ranges call per healthy file; a failing file is retried then
        skipped, not fatal."""
        import obstore
        import obstore.store

        from gik_icechain.shared import byte_range as _br_mod

        monkeypatch.setattr(_br_mod, "_FETCH_BACKOFF_S", 0.0)

        # Two chunks in file_a, one in file_b - distinct files.
        ranges = [
            _br("s3://bucket/file_a", 0, 4, member=0, step=0),
            _br("s3://bucket/file_a", 4, 4, member=1, step=0),
            _br("s3://bucket/file_b", 0, 4, member=0, step=1),
        ]
        coalesced = coalesce_byte_ranges(ranges, max_gap_bytes=0)

        calls: list[str] = []

        def _fake_get_ranges(store, key, *, starts, ends):
            calls.append(key)
            if key == "file_b":
                raise RuntimeError("simulated S3 timeout")
            return [b"ABCDEFGH"[s:e] for s, e in zip(starts, ends, strict=True)]

        monkeypatch.setattr(obstore.store, "S3Store", lambda *a, **k: object())
        monkeypatch.setattr(obstore, "get_ranges", _fake_get_ranges)

        result = fetch_coalesced_ranges(coalesced, max_workers=2)

        # file_a chunks present; file_b dropped (NaN downstream), no exception.
        assert (0, 0, "tp") in result
        assert (1, 0, "tp") in result
        assert (0, 1, "tp") not in result
        # file_a fetched once; file_b retried _FETCH_ATTEMPTS times before skip.
        assert calls.count("file_a") == 1
        assert calls.count("file_b") == _br_mod._FETCH_ATTEMPTS
