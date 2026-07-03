"""Upstream-basin pooling for the riverine hazard feed.

Populates ``riverine_ratio`` per admin-1 unit by pooling the forecast intensity
of its upstream units along a curated river topology, so catchment-routed floods
(Shabelle/Juba/Sudd) escalate Forecast_Hazard even when local rainfall is ~0.
This is the data-side counterpart of ``crma_model.riverine_aware_hazard``; it
needs no external discharge data, and can be swapped for GloFAS discharge later.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import structlog
import yaml

log = structlog.get_logger(__name__)


def load_upstream_map(path: Path) -> dict[str, list[str]]:
    """Load the {downstream unit -> [upstream units]} topology from YAML."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    upstream: dict[str, list[str]] = {str(k): [str(u) for u in (v or [])] for k, v in raw.items()}
    log.info("riverine_map_loaded", n_downstream=len(upstream), path=str(path))
    return upstream


def pool_upstream_ratio(
    ratio: pd.Series,
    upstream_map: dict[str, list[str]],
    attenuation: float = 0.9,
    aggregate: str = "max",
) -> pd.Series:
    """Riverine ratio per downstream unit = attenuation × agg(upstream ratios).

    ``ratio`` is an upstream-intensity signal per unit (the tail ratio, same
    scale as the riverine_* thresholds). Missing upstream units are skipped;
    downstream units with no present upstream contribute 0. The result is
    reindexed to ``ratio.index`` (units absent from the map get 0).
    """
    out = pd.Series(0.0, index=ratio.index, dtype="float64")
    for downstream, ups in upstream_map.items():
        if downstream not in out.index:
            continue
        vals = [float(ratio[u]) for u in ups if u in ratio.index and pd.notna(ratio[u])]
        if not vals:
            continue
        pooled = max(vals) if aggregate == "max" else sum(vals) / len(vals)
        out[downstream] = attenuation * pooled
    return out
