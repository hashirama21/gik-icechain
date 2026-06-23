"""As-of-date skill curves — early-warning skill as a function of lead time.

Every daily batch is an IceChunk snapshot (C1 time-travel), so the per-forecast-
date risk scores are an honest **as-of-date** decision record: what the system
would have flagged using only the information available that day, with no future
leakage. This module turns that record into a **skill-vs-lead-time curve**
against EM-DAT.

For each flood event onset ``T0`` (per admin-1 unit), it reads the risk signal
from the forecast issued ``L`` days earlier (date ``T0 - L``) and aggregates,
per lead ``L``:

- **recall@tier** — the share of events already flagged at ``risk_state >= tier``
  ``L`` days ahead (the early-warning detection rate);
- **mean trigger** — the mean cumulative posterior ``P(>=tier)`` ``L`` days ahead.

It answers the Challenge-41 question directly — *how many days ahead could we
have acted?* — and is methodologically clean precisely because the versioned
store guarantees each day's score used only that day's information. A
non-versioned pipeline cannot make that guarantee.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

    from gik_icechain.risk.cpt_refinement import EMDATFloodRecord

log = structlog.get_logger(__name__)

# tier index → cumulative-posterior columns for P(>=tier)
_TIER_COLS: dict[int, tuple[str, ...]] = {
    1: ("p_yellow", "p_orange", "p_red"),
    2: ("p_orange", "p_red"),
    3: ("p_red",),
}
_TIER_NAMES: dict[int, str] = {1: "yellow", 2: "orange", 3: "red"}


def event_onsets(emdat_records: list[EMDATFloodRecord]) -> list[tuple[str, str]]:
    """One ``(admin1_pcode, onset_date_str)`` per EM-DAT event with a pcode."""
    return [
        (rec.admin1_pcode, str(rec.start_date.date()))
        for rec in emdat_records
        if rec.admin1_pcode
    ]


def lead_time_skill(
    risk_df: pd.DataFrame,
    emdat_records: list[EMDATFloodRecord],
    max_lead: int = 7,
) -> dict[int, dict[str, float]]:
    """Aggregate early-warning skill per lead time against EM-DAT onsets.

    Args:
        risk_df:       Per-day risk rows; columns ``date`` (str/ISO),
                       ``admin1_pcode``, ``risk_state`` and the posteriors
                       ``p_yellow``, ``p_orange``, ``p_red``.
        emdat_records: EM-DAT flood records (one onset = ``start_date``).
        max_lead:      Maximum lead time in days (curve covers 0…max_lead).

    Returns:
        ``{lead: {"n": n_events_with_a_forecast, "recall_<tier>": …,
        "mean_p_<tier>": …}}`` for lead 0…max_lead. Events whose forecast row at
        a given lead is absent are excluded from that lead's denominator.
    """
    df = risk_df.copy()
    df["date"] = df["date"].astype(str)
    by_key = {
        (row["date"], row["admin1_pcode"]): row
        for _, row in df.iterrows()
    }
    onsets = event_onsets(emdat_records)

    curve: dict[int, dict[str, float]] = {}
    for lead in range(max_lead + 1):
        hits: dict[int, int] = {t: 0 for t in _TIER_COLS}
        prob_sums: dict[int, float] = {t: 0.0 for t in _TIER_COLS}
        n = 0
        for pcode, onset in onsets:
            d = (date.fromisoformat(onset) - timedelta(days=lead)).isoformat()
            row = by_key.get((d, pcode))
            if row is None:
                continue
            n += 1
            state = int(row.get("risk_state", 0))
            for tier, cols in _TIER_COLS.items():
                if state >= tier:
                    hits[tier] += 1
                prob_sums[tier] += float(sum(float(row.get(c, 0.0)) for c in cols))
        entry: dict[str, float] = {"n": float(n)}
        for tier, name in _TIER_NAMES.items():
            entry[f"recall_{name}"] = hits[tier] / n if n else 0.0
            entry[f"mean_p_{name}"] = prob_sums[tier] / n if n else 0.0
        curve[lead] = entry

    log.info("lead_time_skill_computed", n_events=len(onsets), max_lead=max_lead)
    return curve


def lead_time_skill_from_risk_dir(
    risk_dir: Path,
    emdat_records: list[EMDATFloodRecord],
    max_lead: int = 7,
    start: str | None = None,
    end: str | None = None,
) -> dict[int, dict[str, float]]:
    """Load per-day ``*_risk_scores.json`` files, then compute the lead-time curve."""
    import json
    from pathlib import Path as _Path

    import pandas as pd

    rows: list[dict] = []
    for scores_path in sorted(_Path(risk_dir).glob("*_risk_scores.json")):
        data = json.loads(scores_path.read_text())
        date_str = data.get("date", scores_path.stem[:10])
        if (start and date_str < start) or (end and date_str > end):
            continue
        for pcode, score in data.get("units", {}).items():
            rows.append(
                {
                    "date": date_str,
                    "admin1_pcode": pcode,
                    "risk_state": int(score.get("risk_state", 0)),
                    "p_yellow": float(score.get("p_yellow", 0.0)),
                    "p_orange": float(score.get("p_orange", 0.0)),
                    "p_red": float(score.get("p_red", 0.0)),
                }
            )
    if not rows:
        raise ValueError(f"No *_risk_scores.json files found in {risk_dir}")
    return lead_time_skill(pd.DataFrame(rows), emdat_records, max_lead=max_lead)
