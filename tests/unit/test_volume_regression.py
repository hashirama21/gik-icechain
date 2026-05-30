"""Volume regression tests for byte-range coalescing.

Verifies that coalescing reduces both the number of requests and total
bytes transferred compared to fetching each range individually.
"""

from gik_icechain.shared.byte_range import (
    ByteRange,
    coalesce_byte_ranges,
)


def _make_realistic_ranges(
    n_members: int = 51,
    n_steps: int = 14,
    msg_size: int = 500_000,
    gap_between_steps: int = 100,
    base_offset: int = 0,
) -> list[ByteRange]:
    """Generate byte ranges mimicking ECMWF IFS ensemble GRIB2 layout.

    In a typical GRIB2 file, messages for different members/steps are
    laid out sequentially with small gaps between them.
    """
    ranges = []
    offset = base_offset
    for step_idx in range(n_steps):
        for member_idx in range(n_members):
            ranges.append(
                ByteRange(
                    uri="s3://ecmwf-forecasts/ifs/20240115/00z/0p25/oper/sfc.grib2",
                    offset=offset,
                    length=msg_size,
                    metadata={
                        "member_idx": member_idx,
                        "step_idx": step_idx,
                        "variable": "tp",
                    },
                )
            )
            offset += msg_size + gap_between_steps
    return ranges


class TestCoalescingReducesRequests:
    """Verify that coalescing reduces the number of S3 requests."""

    def test_coalescing_reduces_request_count(self):
        """With coalescing, n_requests should be < 20% of n_original."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=14)
        n_original = len(ranges)
        assert n_original == 714  # 51 * 14

        coalesced = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )
        n_coalesced = len(coalesced)

        ratio = n_coalesced / n_original
        assert ratio < 0.20, (
            f"Coalescing ratio {ratio:.2%} exceeds 20%: "
            f"{n_coalesced} requests vs {n_original} original"
        )

    def test_no_coalescing_baseline(self):
        """With max_gap=0 and small max_merged, each range stays separate."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=5)
        n_original = len(ranges)

        # max_gap=0 but messages are adjacent with 100-byte gaps
        coalesced_no_merge = coalesce_byte_ranges(
            ranges, max_gap_bytes=0, max_merged_bytes=500_000
        )
        # With 100-byte gaps and max_gap=0, nothing should merge
        assert len(coalesced_no_merge) == n_original

    def test_coalescing_enabled_fewer_than_disabled(self):
        """Coalescing enabled should always produce fewer requests."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=5)

        coalesced_enabled = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )
        coalesced_disabled = coalesce_byte_ranges(
            ranges, max_gap_bytes=0, max_merged_bytes=500_000
        )

        assert len(coalesced_enabled) < len(coalesced_disabled)


class TestCoalescingVolumeOverhead:
    """Verify that coalescing doesn't add excessive byte overhead."""

    def test_volume_overhead_bounded(self):
        """Extra bytes fetched (gap fill) should be small relative to total."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=14)
        original_bytes = sum(r.length for r in ranges)

        coalesced = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )
        coalesced_bytes = sum(cr.length for cr in coalesced)

        overhead_ratio = coalesced_bytes / original_bytes
        # Overhead should be minimal — less than 5% extra bytes
        assert overhead_ratio < 1.05, (
            f"Coalescing overhead {overhead_ratio:.2%} exceeds 5%: "
            f"{coalesced_bytes} vs {original_bytes}"
        )

    def test_all_original_bytes_preserved(self):
        """Every original range should be recoverable from coalesced slices."""
        ranges = _make_realistic_ranges(n_members=10, n_steps=3)
        coalesced = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )

        # Count total original ranges across all coalesced ranges
        total_originals = sum(len(cr.original_ranges) for cr in coalesced)
        assert total_originals == len(ranges)

        # Verify each slice length matches original range length
        for cr in coalesced:
            for sl, orig in zip(cr.slices, cr.original_ranges, strict=True):
                slice_len = sl[1] - sl[0]
                assert slice_len == orig.length, (
                    f"Slice length {slice_len} != original length {orig.length}"
                )


class TestFlashFloodProfile:
    """Volume regression for the flash_flood profile (5 steps)."""

    def test_flash_flood_requests(self):
        """flash_flood: 51 members x 5 steps = 255 ranges → < 50 requests."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=5)
        assert len(ranges) == 255

        coalesced = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )
        assert len(coalesced) < 50, f"Too many requests: {len(coalesced)}"


class TestFullProfile:
    """Volume regression for the full profile (30 steps)."""

    def test_full_profile_requests(self):
        """full: 51 members x 30 steps = 1530 ranges → < 200 requests."""
        ranges = _make_realistic_ranges(n_members=51, n_steps=30)
        assert len(ranges) == 1530

        coalesced = coalesce_byte_ranges(
            ranges, max_gap_bytes=65_536, max_merged_bytes=5_242_880
        )
        assert len(coalesced) < 200, f"Too many requests: {len(coalesced)}"
