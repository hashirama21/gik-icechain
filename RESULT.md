# GIK-IceChain v2.0 Results

Retrospective flood-risk skill of the GIK-IceChain pipeline over East Africa,
evaluated against independent satellite ground truth. All figures are reproduced
end to end from real ECMWF IFS ensemble GRIB2 (byte-range streamed from
`s3://ecmwf-forecasts`) and GPM IMERG observations; no synthetic data.

## 1. Configuration

| Parameter | Value |
|---|---|
| Ensemble | ECMWF IFS ENS, 51 members |
| Accumulation windows | 3, 6, 12, 24, 48, 72, 168, 360 h |
| Return periods | 2, 5, 10, 20, 40, 100 yr |
| Thresholds | CMORPH GEV, stratified by season × ENSO × IOD |
| Admin-1 units | 238 (16 East African countries) |
| Domain | bbox `[-14.5, 25, 20, 54]` |
| Source store | published E4DRR IceChunk virtual store (Source Cooperative, anonymous) |

## 2. Pipeline performance

Manifest-aware C2 reads virtual chunk references from the IceChunk store and
streams only the required GRIB2 byte ranges, coalescing ~1530 per-chunk reads
into ~30 per-file multi-range requests (×51 reduction).

| Step | Per day | 1200-day estimate |
|---|---|---|
| C1 convert | ~1 min | ~20 h |
| C2 exceedance (in-region) | ~1 min | ~20 h |
| C3 risk (238 units) | ~30 s | ~10 h |

| Store | Size | Nature |
|---|---|---|
| C1 virtual store | ~18.5 GB metadata | GRIB2 never copied; byte-range refs only |
| C2 exceedance | ~3.4 MB / 1200 d | Zarr v3, `date × lat × lon × window × RP` |
| C3 risk | ~62 MB / 1200 d | GeoJSON boundaries + per-day scores |

## 3. Retrospective skill: November 2024 East Africa floods

Validated against two independent, public, satellite-derived flood products, at
the admin-1 resolution at which the system issues alerts.

### 3.1 FAO/VIIRS panel (105 admin-1 units, 8 countries)

FAO EVE Global Flood Monitoring (NOAA VIIRS) reports every monitored unit, so it
supplies genuine dry-unit negatives and a well-posed AUC / precision / recall.
The initial run exposed a local-rainfall blind spot; a riverine upstream-pooling
hazard feed was then added and the window fully re-scored.

| Metric | Pre-remediation | Post-remediation |
|---|---:|---:|
| AUC (peak `p_red` vs VIIRS flood) | 0.715 | **0.734** |
| Precision @ Orange+ | 0.96 (26/27) | **0.96 (46/48)** |
| Recall @ Orange+ | 0.29 (26/91) | **0.51 (46/91)** |

Recall nearly doubles at unchanged precision. The driver is the riverine feed:
each unit inherits its catchment's forecast tail signal pooled along a curated
admin-1 river topology (Shabelle, Juba, White Nile/Sudd, Blue Nile, Tana), so
catchment-routed floods escalate hazard even at near-zero local rainfall. The
Somali basins previously missed outright (Middle/Lower Shabelle, Lower/Middle
Juba) now sustain month-long alerting matching the observed Deyr persistence.
Residual misses are coastal/urban units not on the river topology (Banadir,
Bakool, Bay); recall is now bounded by topology coverage, not model structure.

### 3.2 UNOSAT South Sudan (Sentinel-1)

Against UNOSAT water-extent exposure across all ten states, C3 attains **100 %
Yellow-or-higher recall** (it never declares a flooded state clear), with its
strongest alerts concentrated over the Sudd and White-Nile corridor.

## 4. Case study: April 2024 compound flood (Kenya/Tanzania)

A single-event probe of the compound-flood pathway. The epicentre saw sustained
sub-return-period rain on saturated soil, not a single >RP extreme, so forecast
exceedance was structurally 0. Once the antecedent window covers the saturation
build-up, Nairobi escalates Green → Yellow → Orange as `Soil_Memory` crosses to
"prolonged":

| Date | API (mm) | Label |
|---|---:|---|
| 2024-04-24 | 115 | Yellow |
| 2024-04-28 | 137 | Orange |
| 2024-04-30 | 156 | Orange |

4/41 flooded units reach Orange once the window covers the build-up, with false
alarms held at 0 to 2. Detection lags onset by ~4 days, physically expected for
soil-saturation floods, an operational lead-time constraint. Purely fluvial/urban
floods (Tana River, Nairobi urban) remain invisible to a pixel-local
precip-exceedance method and motivate the riverine feed above.

## 5. Controlled experiments

Single-variable ablations over a 7-day Deyr-peak window (2024-11-16 → 22), scored
against the FAO/VIIRS panel. Each isolates one component with the riverine feed
off, so absolute recall is below section 3 but the deltas are clean.

### 5.1 API antecedent node

| Metric | With API | No API | Δ |
|---|---:|---:|---:|
| AUC | 0.734 | 0.728 | +0.006 |
| Precision @ Orange+ | 0.931 | 0.929 | +0.002 |
| Recall @ Orange+ | 0.297 | 0.286 | +0.011 |

The antecedent-precipitation node contributes a small positive lift at constant
precision.

### 5.2 Adaptive vs static GEV thresholds

<!-- GEV_ABLATION_RESULT: to be completed from the static-threshold recompute -->
Adaptive (season × ENSO/IOD stratified) vs static (season-only, phase-neutral)
climatology, identical pipeline otherwise.

### 5.3 Climate-mode node

An ENSO/IOD climate-mode node was added as a centred parent of `Compound_Risk`
(Suppressing / Neutral / Enhancing), fed per forecast date from the Niño-3.4 and
DMI indices. Neutral contributes exactly zero, preserving the prior calibration;
a compound El Niño + positive-IOD state escalates risk, a La Niña + negative-IOD
state dampens it.

| Climate mode | Label | `p_red` |
|---|---|---:|
| Suppressing | Yellow | 0.179 |
| Neutral | Red | 0.392 |
| Enhancing | Red | 0.392 |

### 5.4 EM-DAT CPT refinement

The Risk_State CPD was refined by Bayesian MLE on an EM-DAT × GPM training set
(876 rows, 358 events, 116 admin-1 units, Risk_State distribution
{Yellow: 42, Orange: 225, Red: 609}). Refined tables trained without negatives
over-escalate, so `use_refined_cpts` is kept `false` in production; a
negatives-balanced training set is the prerequisite to activate it.

## 6. Known limitations

- **Recall bounded by river-topology coverage.** Coastal/urban units off the
  curated topology (Banadir, Bakool, Bay) are not reachable by the catchment
  feed; a coastal-surge hazard and an extended topology are the next levers.
- **Bayesian-network saturation.** `Risk_State` has a single parent, so `p_red`
  saturates at ≈0.39 for any Red; severity gradation is carried by a continuous
  `severity_score`, not by the network posterior.
- **Detection lag on slow-onset floods** (~4 days), intrinsic to the
  soil-saturation pathway.
- **Southern frontier at −14.5°**: units further south are `No_Data` by
  construction; no southern (CHIRPS) climatology is built.
- **Public ECMWF S3 retention** bounds live validation on older events without a
  MARS subscription or a local mirror.
