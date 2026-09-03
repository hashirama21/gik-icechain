# Candidate titles — TCCML @ NeurIPS 2026 (Papers track)

Four options, one per framing axis. Pick by how each renders in the compiled PDF.
The LaTeX snippet under each can be pasted directly into `paper.tex` (`\title{...}`).

---

## Axis 1 — Method framing ("from X to Y")

**From Ensemble Rainfall Extremes to Compound Flood Risk: A Dynamic Bayesian Network with Catchment Routing for Anticipatory Action in East Africa**

- *Feel:* narrative, method-forward; foregrounds the pipeline logic (extremes → risk).
- *Best if:* you want the storyline (ensemble tails → compound/riverine flood risk) to read first.

```latex
\title{From Ensemble Rainfall Extremes to Compound Flood Risk:
A Dynamic Bayesian Network with Catchment Routing for
Anticipatory Action in East Africa}
```

---

## Axis 2 — Probabilistic / admin-level

**Probabilistic Admin-Level Flood Risk from Open Weather Ensembles: Coupling Extreme-Value Exceedance and Dynamic Bayesian Inference for Anticipatory Action in East Africa**

- *Feel:* most "NeurIPS/ML"; leads with "Probabilistic" and "Bayesian Inference".
- *Best if:* you want reviewers to read the statistical contribution first.

```latex
\title{Probabilistic Admin-Level Flood Risk from Open Weather Ensembles:
Coupling Extreme-Value Exceedance and Dynamic Bayesian Inference for
Anticipatory Action in East Africa}
```

---

## Axis 3 — Calibrated framework (compound + riverine)

**Calibrated Compound- and Riverine-Flood Risk from Open Ensemble Forecasts: An Extreme-Value and Dynamic Bayesian Framework for East Africa**

- *Feel:* emphasizes the hydrological novelty (compound + riverine) and calibration.
- *Best if:* you want the two flood mechanisms and the "framework" claim up front.

```latex
\title{Calibrated Compound- and Riverine-Flood Risk from Open Ensemble
Forecasts: An Extreme-Value and Dynamic Bayesian Framework for East Africa}
```

---

## Axis 4 — Climate-adaptation first

**Anticipatory Flood Risk under a Changing Climate: Extreme-Value Ensemble Post-Processing and Dynamic Bayesian Inference over East African Admin Units**

- *Feel:* most "CCAI/climate"; opens on adaptation, then the method.
- *Best if:* you want to signal climate impact before methodology.

```latex
\title{Anticipatory Flood Risk under a Changing Climate: Extreme-Value
Ensemble Post-Processing and Dynamic Bayesian Inference over East African
Admin Units}
```

---

### Quick chooser
| Axis | Leads with | Vibe |
|------|-----------|------|
| 1 | the method storyline | narrative |
| 2 | Probabilistic / Bayesian | ML / NeurIPS |
| 3 | compound + riverine, calibrated | hydrological novelty |
| 4 | anticipatory action, climate | CCAI / adaptation |

All four keep: open ECMWF ensemble, extreme-value thresholds, dynamic Bayesian
network, and East Africa. Currently active in `paper.tex`: **Axis 1**
(the other three are commented just above `\title{}`).
