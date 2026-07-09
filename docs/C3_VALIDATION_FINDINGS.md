# C3 Validation Findings & Remediation Roadmap

Institutional memo recording the irregularities surfaced by the **November 2024
admin-1 satellite validation** of Component 3 (CRMA-Live flood risk), together
with their **root cause verified in code** and a **prioritised remediation
roadmap**. Written before the corrective work is engaged, so the rationale is
preserved. Complements [`ISSUES.md`](ISSUES.md) and the README section
"Live dashboard & validation".

## 1. Evidence base

Independent, satellite-derived ground truth (public, no auth), reproduced by
`python scripts/satellite_validation.py`:

- **FAO EVE Global Flood Monitoring** (NOAA VIIRS): flooded area (km²) and
  population exposed per admin unit per 15-day composite, over 8 East African
  countries. Reports *every* monitored unit → genuine dry-unit negatives.
  Ground truth: `data/emdat/nov2024_satellite_fao_viirs.csv`.
- **UNOSAT** Sentinel-1 water-extent exposure tables, South Sudan (all 10
  states). Ground truth: `data/emdat/nov2024_satellite_ssd_unosat.csv`.

Headline panel (FAO/VIIRS, 105 admin-1 units, flooded ≥ 50 km²):

| Metric | Value |
|--------|-------|
| AUC (C3 peak `p_red` vs VIIRS flood) | 0.715 |
| Precision @ Orange+ | 0.96 (26 / 27) |
| Recall @ Orange+ | 0.29 (26 / 91) |

South Sudan (UNOSAT Sentinel-1): 100 % Yellow-or-higher recall, 50 % Orange+
recall, alerts correctly concentrated on the Sudd / White-Nile corridor.

## 2. Detected irregularities (all confirmed, with code-level root cause)

| Irregularity | Severity | Consequence | Root cause (verified) |
|--------------|----------|-------------|-----------------------|
| Every Red alert has identical `p_red = 0.39` | High | No severity hierarchy between events | `Risk_State` (4 states) has a **single parent** `Compound_Risk` (4 states); `p_red = risk_cpt[3,:] @ compound_probs`. Once `Compound_Risk` saturates to *High*, `p_red = risk_cpt[3,3] ≈ 0.39`, constant. Continuous exceedance magnitude is discretised away upstream (`Forecast_Hazard`, 4 states). See `src/gik_icechain/risk/crma_model.py`. |
| Severity driven by the cost-loss rule, not the posterior | Medium | The probabilistic score loses meaning | `_cost_loss_state` (`crma_model.py:162`) triggers the top tier where `P(≥T) ≥ tau_T`. In `configs/default.yaml` **`tau_orange = tau_red = 0.19`** (tau_red's raw optimum 0.06 was clamped up to keep non-decreasing order) → the Orange/Red boundary is degenerate. |
| Local-rainfall bias — major riverine floods missed | **Very high** | Large fluvial floods systematically undetected | **All 8 BN evidence nodes are local** (`Forecast_Hazard` = local IFS exceedance, `Obs_Antecedent`, `API_State`, `Soil_Memory`, `Rainfall_Trend` = local IMERG). **No upstream / discharge / routing node exists.** The model structurally cannot see catchment-routed floods (Shabelle, Juba, Sudd). |
| Very low recall (29 %) | **Very high** | Most flooded units never alerted | Direct consequence of the local-rainfall bias (measured 26/91 on the VIIRS panel). |
| "False hotspots" in the northern Somali highlands | High | Over-grading in lightly-flooded zones | Not false positives in the binary sense — VIIRS confirms minor floods (Sool 69 km², Togdheer 13 km²). The fault is **over-grading**: a 13 km² flood receives the same Red as the undetected 3 016 km² Middle Shabelle flood. Same root cause as the `p_red` saturation. |
| Country-level validation too coarse | Medium (resolved) | Local errors masked | Superseded by this admin-1 satellite panel. |

### Diagnostic table — Somalia (VIIRS flood vs C3), the spatial mismatch

| Region | Basin | Flood (km²) | Pop. exposed | C3 Orange+ days |
|--------|-------|------------:|-------------:|----------------:|
| Middle Shabelle | Shabelle (south) | 3 016 | 245 149 | 0 |
| Lower Juba | Juba (south) | 1 812 | 126 773 | 0 |
| Lower Shabelle | Shabelle (south) | 1 568 | 157 215 | 0 |
| Middle Juba | Juba (south) | 1 064 | 51 603 | 0 |
| Banadir (Mogadishu) | coastal (south) | 94 | 561 738 | 0 |
| Sool | northern highlands | 69 | 427 | 28 |
| Togdheer | northern highlands | 13 | 101 | 28 |

## 3. Key nuances

- The northern "false hotspots" and the `p_red` saturation are **the same
  defect**: without severity gradation, a tiny real flood gets the same Red as a
  major one would.
- **Low recall is not fixable by thresholds.** The missed southern floods sit at
  `p_red ≈ 0` (Green); lowering `tau` cannot lift them. The 96 % precision leaves
  head-room, but the lever is *structural*, not a tuning knob.

## 4. Remediation roadmap (by decreasing leverage)

1. **Add an upstream / riverine hazard node — the only real recall lever.**
   - Introduce a `Riverine_Hazard` node feeding `Compound_Risk`, driven by
     **GloFAS river-discharge reforecast + flood-return thresholds** (Copernicus).
   - Alternative without GloFAS: an **upstream-rainfall index** — route the IFS
     exceedance along the river network (flow accumulation from a HydroSHEDS /
     MERIT DEM) so each unit sees its catchment's rainfall, not only local cells.
   - Expected to unblock the ~65 currently-missed units.

2. **Restore probabilistic gradation (fixes `p_red` saturation + Orange/Red).**
   - Make `Risk_State` depend on a **continuous** severity signal (return period ×
     spatial coverage × population exposed) instead of the 4-state `Compound_Risk`.
   - **Calibrate** `p_red` (isotonic / Platt) against observed flood frequency —
     the VIIRS panel is now available as a calibration set.
   - Increase `Forecast_Hazard` granularity (> 4 states) so `p_red` actually
     varies between Orange- and Red-worthy events.

3. **Recalibrate arid-regime thresholds (fixes northern over-grading).**
   - Re-derive the cost-loss taus **without the clamp** that collapsed
     `tau_red = tau_orange`, separating tiers via the continuous severity score.
   - Use the satellite **dry-unit negatives** to recalibrate the arid GEV
     thresholds and set a minimum flood-relevant rainfall floor before
     `Forecast_Hazard = High`.

4. **Extend the standing validation set.**
   - Adopt the FAO/VIIRS + UNOSAT admin-1 panel as the permanent validation
     harness. ETH / KEN are absent from FAO EVE — cover them via GloFAS or UNOSAT.

## 5. Implementation status

All changes are config-flagged and default to the legacy behaviour (existing
outputs unchanged); covered by `tests/unit/test_severity_gradation.py` and
`tests/unit/test_riverine_hazard.py`.

| Roadmap item | Status | How |
|--------------|--------|-----|
| 2 — Severity gradation | Done | `severity_index()` blends the continuous evidence into `severity_score` (config `component3.crma_model.severity`), emitted per unit. Additive; label unchanged. |
| 3 — Orange/Red decoupling | Done | `cost_loss.severity_red_split` demotes a low-magnitude saturated Red to Orange. Arid floor (`component2.flood_floor_mm`) already clips GEV thresholds. |
| 1 — Riverine hazard | Done (end to end) | `riverine_aware_hazard` escalates `Forecast_Hazard` on `riverine_ratio` (independent of local rain), folded into severity. The feed (`component3.riverine`) pools each unit's upstream tail ratios along a curated admin-1 river topology (`data/river_basins/upstream_admin1.yaml`) in `risk_engine`. GloFAS discharge is a drop-in upgrade for the feed. |
| 4 — Satellite validation harness | Done | `scripts/satellite_validation.py` (FAO/VIIRS + UNOSAT). Gap: ETH/KEN absent from FAO EVE — cover via GloFAS/UNOSAT. |

All levers are **enabled by default** in `configs/default.yaml`
(`severity`, `cost_loss.severity_red_split: 0.30`, `riverine_aware_hazard: true`,
`component3.riverine.enabled: true`). `severity_red_split` and the river topology
are initial values to be tuned against the satellite panel. The topology seeds
the major basins (Shabelle, Juba, White Nile/Sudd, Blue Nile, Tana) and extends
by editing the YAML.

## 6. One-line verdict (pre-remediation)

C3 is already near-operational on **reliability** (96 % precision) but far from it
on **completeness** (29 % recall). The route forward is **hydrological information
(discharge, flood routing, upstream rainfall) and probability calibration**, not
further threshold tuning.

## 7. Post-remediation re-runs (2026-07-09)

The Nov-2024 window (31 Oct – 30 Nov, 238 units) was re-scored with all four
levers enabled and re-validated against the same FAO/VIIRS panel, in two
passes: first against the repaired exceedance store where real `tail_ratio`
existed on only 7 of 31 days (`results/admin1_risk_postlevers/`), then against
a fully recomputed store with real tail/median on all 31 days
(`results/exceedance_nov2024full.zarr` → `results/admin1_risk_fullwindow/`).
The pre-lever baseline is preserved in `results/admin1_risk/`.

| Metric (105 units, flooded ≥ 50 km²) | Pre-levers | Levers, tail 7/31 d | Levers, tail 31/31 d |
|---------------------------------------|-----------:|--------------------:|---------------------:|
| AUC (C3 peak `p_red` vs VIIRS flood)  | 0.715 | 0.724 | **0.734** |
| Recall @ Orange+                      | 0.29 (26/91) | 0.46 (42/91) | **0.51 (46/91)** |
| Precision @ Orange+                   | 0.96 (26/27) | 0.95 (42/44) | **0.96 (46/48)** |

The riverine lever resolves the headline misses, and the effect scales with
tail coverage: **Middle Shabelle (3 016 km², 245 k exposed) goes 0 → 6 → 30
Orange+ days**; Lower Juba, Lower Shabelle and Middle Juba follow the same
0 → 6 → ~30 pattern, via the pooled upstream `riverine_ratio` (≈ 2.6 on the
Shabelle/Juba corridor, ≈ 2.7–3.0 on the Blue Nile/Gezira reach). Sustained
month-long alerting matches the observed Deyr flood persistence, and the
doubled Red count (311 → 632 unit-days) costs no precision (0.96).

Residual limitations:

- `p_red` still saturates at 0.392 (BN structure unchanged by design);
  gradation is carried by the additive `severity_score`, emitted on every
  scored unit-day.
- Banadir (coastal, 94 km² but 562 k exposed), Bakool and Bay remain at 0
  alert-days — coastal/urban flooding is not on the riverine topology; a
  coastal pathway is future work.
- Recall is bounded near ~0.5 by units outside the curated river topology and
  outside FAO EVE's forecast-signal reach; extending the topology YAML and a
  GloFAS discharge feed are the next levers.

Provenance note: every figure above comes from real ECMWF ensemble GRIBs
(C1 store) and real GPM IMERG observations — no padded or synthetic values
remain in the full-window chain. The 7-day-tail pass is kept for the record
because it isolates the riverine lever's marginal contribution.
