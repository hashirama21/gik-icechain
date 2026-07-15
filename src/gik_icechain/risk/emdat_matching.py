"""EM-DAT <-> admin-1 boundary reconciliation.

EM-DAT admin1 attributions diverge from the pipeline's admin-1 boundaries in
two ways: (1) about a third of flood records carry no ``Admin1 Code`` at all
(national-level entries), and (2) attributed codes use historical or local
names (pre-2013 Kenyan provinces, Somali-language region names, renamed
Sudanese states, Ugandan districts vs. macro-regions) that do not exist in the
boundaries file. Both silently defeat a naive ``(date, pcode)`` equality match.

This module is the single source of truth for that reconciliation:

- an alias table (``data/emdat/pcode_aliases.csv``) maps each EM-DAT code to
  one or more boundary pcodes (one CSV row per target);
- :class:`FloodEventIndex` matches a day x unit at ``"admin1"`` level when the
  (possibly alias-resolved) attribution covers it, and falls back to
  ``"country"`` level for records with no usable attribution, so national
  EM-DAT entries still surface in the validation overlay.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from gik_icechain.risk.cpt_refinement import EMDATFloodRecord

log = structlog.get_logger(__name__)

MatchLevel = Literal["admin1", "country"]


def load_pcode_aliases(path: Path) -> dict[str, tuple[str, ...]]:
    """Load the EM-DAT -> boundary pcode alias table.

    Args:
        path: CSV with header ``emdat_code,admin1_pcode``; one row per target,
              so a one-to-many alias (e.g. a pre-2013 Kenyan province mapping
              to its current counties) spans several rows.

    Returns:
        Mapping of EM-DAT code to its boundary pcode targets. Empty when the
        file does not exist (callers degrade to exact matching).
    """
    if not path.exists():
        log.warning("pcode_aliases_missing", path=str(path))
        return {}
    targets: dict[str, list[str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["emdat_code"].strip()
            pcode = row["admin1_pcode"].strip()
            if code and pcode:
                targets.setdefault(code, []).append(pcode)
    aliases = {code: tuple(pcodes) for code, pcodes in targets.items()}
    log.info("pcode_aliases_loaded", path=str(path), n_codes=len(aliases))
    return aliases


def resolve_pcodes(
    emdat_pcode: str,
    valid_pcodes: Collection[str],
    aliases: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Resolve one EM-DAT admin1 code to boundary pcodes.

    A code already present in the boundaries wins as-is; otherwise the alias
    table is consulted, keeping only targets that exist in the boundaries.

    Returns:
        Boundary pcodes covered by the EM-DAT attribution; empty when the code
        is blank or cannot be reconciled.
    """
    if not emdat_pcode:
        return ()
    if emdat_pcode in valid_pcodes:
        return (emdat_pcode,)
    return tuple(p for p in aliases.get(emdat_pcode, ()) if p in valid_pcodes)


@dataclass(frozen=True)
class FloodEventIndex:
    """Precomputed (day, unit) lookup over EM-DAT flood events.

    ``admin1_days`` holds exact ``(date_str, pcode)`` keys from attributed
    (alias-resolved) records; ``country_days`` holds ``(date_str, iso3)`` keys
    from records with no usable admin1 attribution.
    """

    admin1_days: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    country_days: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    def match(self, date_str: str, pcode: str) -> MatchLevel | None:
        """Match one day x admin-1 unit against the indexed EM-DAT events.

        Args:
            date_str: ISO date (``YYYY-MM-DD``).
            pcode:    Boundary pcode (``{ISO3}_{Admin1 name}``).

        Returns:
            ``"admin1"`` for an attributed match, ``"country"`` when only a
            national-level event covers the unit's country, else ``None``.
        """
        if (date_str, pcode) in self.admin1_days:
            return "admin1"
        iso3 = pcode.split("_", 1)[0]
        if (date_str, iso3) in self.country_days:
            return "country"
        return None


def build_flood_event_index(
    records: Iterable[EMDATFloodRecord],
    valid_pcodes: Collection[str],
    aliases: dict[str, tuple[str, ...]],
) -> FloodEventIndex:
    """Index EM-DAT flood records for day x unit matching.

    Attributed records land in ``admin1_days`` through :func:`resolve_pcodes`;
    records whose attribution is blank or unreconcilable fall back to
    ``country_days`` so they are surfaced (more weakly) instead of dropped.
    """
    admin1_days: set[tuple[str, str]] = set()
    country_days: set[tuple[str, str]] = set()
    n_admin1 = n_country = 0
    unresolved: set[str] = set()

    for rec in records:
        targets = resolve_pcodes(rec.admin1_pcode, valid_pcodes, aliases)
        if rec.admin1_pcode and not targets:
            unresolved.add(rec.admin1_pcode)
        day = rec.start_date.date()
        end = rec.end_date.date()
        while day <= end:
            date_str = str(day)
            if targets:
                admin1_days.update((date_str, p) for p in targets)
            elif rec.iso3:
                country_days.add((date_str, rec.iso3))
            day += timedelta(days=1)
        if targets:
            n_admin1 += 1
        elif rec.iso3:
            n_country += 1

    if unresolved:
        log.warning(
            "emdat_pcodes_unresolved",
            n_codes=len(unresolved),
            codes=sorted(unresolved)[:20],
        )
    log.info(
        "flood_event_index_built",
        n_admin1_events=n_admin1,
        n_country_events=n_country,
        n_admin1_days=len(admin1_days),
        n_country_days=len(country_days),
    )
    return FloodEventIndex(
        admin1_days=frozenset(admin1_days),
        country_days=frozenset(country_days),
    )


def resolve_records(
    records: Iterable[EMDATFloodRecord],
    valid_pcodes: Collection[str],
    aliases: dict[str, tuple[str, ...]],
) -> list[EMDATFloodRecord]:
    """Rewrite records onto boundary pcodes for training-style consumers.

    Each attributed record is replicated once per resolved boundary pcode
    (one-to-many aliases fan out). Unattributed or unreconcilable records are
    dropped: a national-level event is too coarse to serve as a unit-level
    training positive.
    """
    from dataclasses import replace

    resolved: list[EMDATFloodRecord] = []
    for rec in records:
        for pcode in resolve_pcodes(rec.admin1_pcode, valid_pcodes, aliases):
            resolved.append(replace(rec, admin1_pcode=pcode))
    return resolved
