"""Ablation studies for the self-imposed evaluation framework (proposal section 7).

Scores two arms of the risk pipeline against the FAO/VIIRS satellite panel over
a date window and reports the AUC / precision / recall delta:

    api   - API_State node active vs neutralised (cluster api weight -> 0)
    gev   - adaptive stratified GEV exceedance vs a static exceedance store

Each arm reuses ``run_risk_batch`` on a pre-computed C2 exceedance store, so the
expensive GRIB fetch happens once.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from satellite_validation import _FLOOD_KM2, _fao_nov_adm1, _matcher, _pipeline_units  # noqa: E402

from gik_icechain.risk.crma_model import CRMAModel, EastAfricaCluster  # noqa: E402
from gik_icechain.risk.risk_engine import run_risk_batch  # noqa: E402
from gik_icechain.shared.config import load_config  # noqa: E402

app = typer.Typer(add_completion=False, help=__doc__)


def _daterange(start: date, end: date) -> list[str]:
    days, cur = [], start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def _score(risk_dir: Path, days: list[str]) -> dict[str, float]:
    """AUC / precision / recall of C3 vs the FAO/VIIRS flood panel over *days*."""
    from sklearn.metrics import roc_auc_score

    match = _matcher(_pipeline_units(risk_dir))
    fao = _fao_nov_adm1()
    fao["unit_key"] = [match(r.adm0_iso3, r.adm1_name) for r in fao.itertuples()]
    fao = fao[fao.unit_key.notna()].drop_duplicates("unit_key")

    daily = {}
    for d in days:
        f = risk_dir / f"{d}_risk_scores.json"
        if f.exists():
            daily[d] = json.loads(f.read_text())["units"]

    maxpred, alertdays = {}, {}
    for key in fao.unit_key:
        pr = [daily[d][key]["p_red"] for d in daily if key in daily[d]]
        maxpred[key] = max(pr) if pr else float("nan")
        alertdays[key] = sum(daily[d][key]["risk_state"] >= 2 for d in daily if key in daily[d])
    fao["c3_maxpred"] = fao.unit_key.map(maxpred)
    fao["c3_alertdays"] = fao.unit_key.map(alertdays)
    fao = fao[fao.c3_maxpred.notna()].copy()
    fao["viirs_flood"] = (fao.flood_km2 >= _FLOOD_KM2).astype(int)

    y = fao.viirs_flood.values
    alert = (fao.c3_alertdays > 0).astype(int).values
    tp = int(((alert == 1) & (y == 1)).sum())
    fp = int(((alert == 1) & (y == 0)).sum())
    fn = int(((alert == 0) & (y == 1)).sum())
    auc = roc_auc_score(y, fao.c3_maxpred.values) if 0 < y.sum() < len(y) else float("nan")
    return {
        "n_units": len(fao),
        "n_flood": int(y.sum()),
        "auc": float(auc),
        "precision": tp / (tp + fp) if (tp + fp) else float("nan"),
        "recall": tp / (tp + fn) if (tp + fn) else float("nan"),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def _build_models(cfg, api_weight: float | None, cpt_path: Path | None = None) -> dict:
    models = {}
    crma_cfg = cfg.component3.crma_model
    if api_weight is not None:
        weights = {
            k: v.model_copy(update={"api": api_weight}) for k, v in crma_cfg.cluster_weights.items()
        }
        crma_cfg = crma_cfg.model_copy(update={"cluster_weights": weights})
    for cluster in EastAfricaCluster:
        m = CRMAModel(cluster=cluster, crma_cfg=crma_cfg)
        m.build()
        if cpt_path is not None:
            m.load_cpts(cpt_path)
        models[cluster] = m
    return models


def _run_arm(
    cfg,
    exc_uri: str,
    out_dir: Path,
    start: date,
    end: date,
    api_weight: float | None,
    cpt_path: Path | None = None,
):
    models = _build_models(cfg, api_weight, cpt_path)
    c3 = cfg.component3
    run_risk_batch(
        exceedance_store_uri=exc_uri,
        gpm_dir=Path(cfg.sources.gpm_imerg_path),
        admin_boundaries_path=Path(cfg.sources.admin_boundaries_path),
        crma_models=models,
        output_dir=str(out_dir),
        start=start,
        end=end,
        api_decay=c3.api.decay_factor,
        initial_api_mm=c3.api.initial_api_mm,
        signal_threshold=cfg.component3.crma_model.signal_threshold_prob,
        rp_signal=cfg.component3.crma_model.rp_signal,
        rp_signal_options=cfg.component3.crma_model.rp_signal_options,
        hazard_stat=c3.aggregation.method,
        min_coverage=c3.aggregation.min_coverage_fraction,
        emdat_path=Path(cfg.sources.emdat_path) if cfg.sources.emdat_path else None,
        enso_iod_path=Path(cfg.component2.thresholds.enso_iod_index_path),
        enso_nino34_threshold=cfg.component2.thresholds.enso_nino34_threshold,
        iod_dmi_threshold=cfg.component2.thresholds.iod_dmi_threshold,
    )


def _report(title: str, base: dict, variant: dict, base_name: str, var_name: str) -> None:
    typer.echo(f"\n=== {title} ===")
    typer.echo(f"  panel: {base['n_units']} admin-1 units, {base['n_flood']} flooded")
    hdr = f"  {'metric':12s} {base_name:>12s} {var_name:>12s} {'delta':>10s}"
    typer.echo(hdr)
    for k in ("auc", "precision", "recall"):
        b, v = base[k], variant[k]
        typer.echo(f"  {k:12s} {b:12.3f} {v:12.3f} {v - b:+10.3f}")
    typer.echo(f"  base   tp/fp/fn: {base['tp']}/{base['fp']}/{base['fn']}")
    typer.echo(f"  variant tp/fp/fn: {variant['tp']}/{variant['fp']}/{variant['fn']}")


@app.command()
def api(
    exceedance_store: Annotated[Path, typer.Option(help="C2 exceedance Zarr store.")],
    start: Annotated[str, typer.Option()],
    end: Annotated[str, typer.Option()],
    out: Annotated[Path, typer.Option()] = Path("results/ablation/api"),
    config: Annotated[Path, typer.Option()] = Path("configs/default.yaml"),
) -> None:
    """API node benefit: full model vs API_State weight neutralised to 0."""
    cfg = load_config(config)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days = _daterange(s, e)
    _run_arm(cfg, str(exceedance_store), out / "with_api", s, e, api_weight=None)
    _run_arm(cfg, str(exceedance_store), out / "no_api", s, e, api_weight=0.0)
    base = _score(out / "with_api", days)
    var = _score(out / "no_api", days)
    _report("API node ablation (proposal 7.3)", base, var, "with_api", "no_api")


@app.command()
def gev(
    adaptive_store: Annotated[Path, typer.Option(help="Adaptive stratified-GEV exceedance store.")],
    static_store: Annotated[Path, typer.Option(help="Static climatological exceedance store.")],
    start: Annotated[str, typer.Option()],
    end: Annotated[str, typer.Option()],
    out: Annotated[Path, typer.Option()] = Path("results/ablation/gev"),
    config: Annotated[Path, typer.Option()] = Path("configs/default.yaml"),
) -> None:
    """Adaptive GEV vs static climatological thresholds (proposal 7.2)."""
    cfg = load_config(config)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days = _daterange(s, e)
    _run_arm(cfg, str(adaptive_store), out / "adaptive", s, e, api_weight=None)
    _run_arm(cfg, str(static_store), out / "static", s, e, api_weight=None)
    base = _score(out / "adaptive", days)
    var = _score(out / "static", days)
    _report("Adaptive GEV vs static thresholds (proposal 7.2)", base, var, "adaptive", "static")


@app.command()
def cpt(
    exceedance_store: Annotated[Path, typer.Option(help="C2 exceedance Zarr store.")],
    refined_cpts: Annotated[Path, typer.Option(help="refined_cpts.json from refine_cpts.py.")],
    start: Annotated[str, typer.Option()],
    end: Annotated[str, typer.Option()],
    out: Annotated[Path, typer.Option()] = Path("results/ablation/cpt"),
    config: Annotated[Path, typer.Option()] = Path("configs/default.yaml"),
) -> None:
    """EM-DAT-refined CPTs vs expert-elicited base CPTs (proposal innovation 3)."""
    cfg = load_config(config)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    days = _daterange(s, e)
    _run_arm(cfg, str(exceedance_store), out / "base", s, e, api_weight=None)
    _run_arm(
        cfg, str(exceedance_store), out / "refined", s, e, api_weight=None, cpt_path=refined_cpts
    )
    base = _score(out / "base", days)
    var = _score(out / "refined", days)
    _report("EM-DAT CPT refinement (proposal innovation 3)", base, var, "base", "refined")


if __name__ == "__main__":
    app()
