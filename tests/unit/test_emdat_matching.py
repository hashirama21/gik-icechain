"""Unit tests for EM-DAT <-> boundary pcode reconciliation."""

from __future__ import annotations

import pandas as pd
import pytest

from gik_icechain.risk.cpt_refinement import EMDATFloodRecord
from gik_icechain.risk.emdat_matching import (
    FloodEventIndex,
    build_flood_event_index,
    load_pcode_aliases,
    resolve_pcodes,
    resolve_records,
)

VALID_PCODES = frozenset(
    {
        "SOM_Hiiraan",
        "SOM_Lower Shebelle",
        "KEN_Kisumu",
        "KEN_Siaya",
        "KEN_Nairobi",
        "ETH_Somali",
    }
)

ALIASES = {
    "SOM_Hiraan": ("SOM_Hiiraan",),
    "KEN_Nyanza": ("KEN_Kisumu", "KEN_Siaya"),
    "SOM_Gone": ("SOM_Vanished",),
}


def _record(
    pcode: str = "",
    iso3: str = "KEN",
    start: str = "2024-04-20",
    end: str = "2024-04-22",
) -> EMDATFloodRecord:
    return EMDATFloodRecord(
        event_id="2024-0001",
        country="Kenya",
        admin1_name=pcode.split("_", 1)[-1],
        admin1_pcode=pcode,
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        deaths=None,
        affected=None,
        iso3=iso3,
    )


class TestResolvePcodes:
    def test_exact_match_wins_over_alias(self):
        assert resolve_pcodes("SOM_Hiiraan", VALID_PCODES, ALIASES) == ("SOM_Hiiraan",)

    def test_alias_resolves_historical_spelling(self):
        assert resolve_pcodes("SOM_Hiraan", VALID_PCODES, ALIASES) == ("SOM_Hiiraan",)

    def test_one_to_many_alias_fans_out(self):
        assert resolve_pcodes("KEN_Nyanza", VALID_PCODES, ALIASES) == (
            "KEN_Kisumu",
            "KEN_Siaya",
        )

    def test_blank_code_resolves_to_nothing(self):
        assert resolve_pcodes("", VALID_PCODES, ALIASES) == ()

    def test_alias_target_absent_from_boundaries_is_dropped(self):
        assert resolve_pcodes("SOM_Gone", VALID_PCODES, ALIASES) == ()

    def test_unknown_code_without_alias_resolves_to_nothing(self):
        assert resolve_pcodes("UGA_Kasese", VALID_PCODES, ALIASES) == ()


class TestBuildFloodEventIndex:
    def test_attributed_record_matches_at_admin1_level(self):
        idx = build_flood_event_index(
            [_record("SOM_Hiraan", iso3="SOM")], VALID_PCODES, ALIASES
        )
        assert idx.match("2024-04-20", "SOM_Hiiraan") == "admin1"
        assert idx.match("2024-04-20", "SOM_Lower Shebelle") is None

    def test_every_day_of_the_event_window_matches(self):
        idx = build_flood_event_index(
            [_record("KEN_Nairobi", start="2024-04-20", end="2024-04-22")],
            VALID_PCODES,
            ALIASES,
        )
        for day in ("2024-04-20", "2024-04-21", "2024-04-22"):
            assert idx.match(day, "KEN_Nairobi") == "admin1"
        assert idx.match("2024-04-23", "KEN_Nairobi") is None

    def test_unattributed_record_falls_back_to_country(self):
        idx = build_flood_event_index([_record("", iso3="KEN")], VALID_PCODES, ALIASES)
        assert idx.match("2024-04-20", "KEN_Nairobi") == "country"
        assert idx.match("2024-04-20", "KEN_Kisumu") == "country"
        assert idx.match("2024-04-20", "SOM_Hiiraan") is None

    def test_unresolvable_attribution_falls_back_to_country(self):
        idx = build_flood_event_index(
            [_record("SOM_Gone", iso3="SOM")], VALID_PCODES, ALIASES
        )
        assert idx.match("2024-04-20", "SOM_Hiiraan") == "country"

    def test_admin1_match_shadows_country_match(self):
        idx = build_flood_event_index(
            [_record("KEN_Nairobi"), _record("", iso3="KEN")], VALID_PCODES, ALIASES
        )
        assert idx.match("2024-04-20", "KEN_Nairobi") == "admin1"
        assert idx.match("2024-04-20", "KEN_Kisumu") == "country"

    def test_empty_index_matches_nothing(self):
        assert FloodEventIndex().match("2024-04-20", "KEN_Nairobi") is None


class TestResolveRecords:
    def test_one_to_many_alias_replicates_the_record(self):
        out = resolve_records([_record("KEN_Nyanza")], VALID_PCODES, ALIASES)
        assert sorted(r.admin1_pcode for r in out) == ["KEN_Kisumu", "KEN_Siaya"]
        assert all(r.event_id == "2024-0001" for r in out)

    def test_unattributed_record_is_dropped_for_training(self):
        assert resolve_records([_record("")], VALID_PCODES, ALIASES) == []


class TestLoadPcodeAliases:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_pcode_aliases(tmp_path / "nope.csv") == {}

    def test_rows_group_by_emdat_code(self, tmp_path):
        p = tmp_path / "aliases.csv"
        p.write_text(
            "emdat_code,admin1_pcode\n"
            "KEN_Nyanza,KEN_Kisumu\n"
            "KEN_Nyanza,KEN_Siaya\n"
            "SOM_Hiraan,SOM_Hiiraan\n",
            encoding="utf-8",
        )
        aliases = load_pcode_aliases(p)
        assert aliases == {
            "KEN_Nyanza": ("KEN_Kisumu", "KEN_Siaya"),
            "SOM_Hiraan": ("SOM_Hiiraan",),
        }


@pytest.mark.parametrize(
    ("aliases_path", "csv_path"),
    [("data/emdat/pcode_aliases.csv", "data/emdat/east_africa_floods.csv")],
)
def test_shipped_alias_table_covers_all_orphan_codes(aliases_path, csv_path, request):
    """Every attributed EM-DAT code either exists in the boundaries or has an alias."""
    import geopandas as gpd

    root = request.config.rootpath
    boundaries = root / "data/admin_boundaries/east_africa_admin1.geojson"
    if not boundaries.exists():
        pytest.skip("admin boundaries not downloaded")
    valid = set(gpd.read_file(boundaries)["admin1_pcode"].astype(str))
    aliases = load_pcode_aliases(root / aliases_path)
    df = pd.read_csv(root / csv_path)
    codes = set(
        df.loc[(df["Disaster Type"] == "Flood") & df["Admin1 Code"].notna(), "Admin1 Code"]
    )
    unresolved = {
        c for c in codes if not resolve_pcodes(str(c), valid, aliases)
    }
    assert unresolved == set()
