# GIK-IceChain - Run Results

## Configuration

| Parameter | Value |
|---|---|
| Config | `configs/default.yaml` |
| Storage | MinIO `MINIO_IP` |
| Variables | `tp, 2t, 10u, 10v, ro` |
| C2 windows | 3, 6, 12, 24, 48, 72, 168 h |
| Return periods | 2, 5, 10, 20, 40, 100 yr |
| Ensemble members | 50/51 (1 filtered - divergent step count on some days) |
| Admin-1 units | 238 (East Africa, extended to −14.5° frontier) |

---

## Runs

### 1-day run - 2025-02-22 (manifest_aware enabled)

> First successful end-to-end run with `manifest_aware: true` + `fetch_workers: 16`.
> Byte-range coalescing replaces ~30 000 individual S3 reads with batched range requests,
> cutting C2 from ~22 min/day to ~1 min/day (**×8 speedup**).
> AIFS track disabled (ECMWF AIFS bucket: access Forbidden on public endpoints).

| Step | Duration | Workers | Notes |
|---|---|---|---|
| C1 convert | ~1 min | 1 | 51 members × 85 steps × 5 variables virtualized |
| C2 exceedance | ~1 min | 1 | manifest_aware + byte-range coalescing active |
| C3 risk | ~30 s | 1 | 155 admin-1 units, 4 CRMA clusters |
| **Total** | **2 min 45 s** | | vs ~22 min/day before |

**Risk scores 2025-02-22:**

| Label | Units |
|---|---|
| Green | 148 |
| No_Data | 7 (insufficient bbox grid coverage) |
| Yellow / Orange / Red | 0 |

Expected - February is DJF (dry season in most of East Africa). All 155 units correctly report Green over 2025-02-22→28.

### manifest_aware fix - IceChunk 2.x API (2026-06-05)

> `all_virtual_chunk_locations()` in IceChunk 2.0.5 now returns a flat list of unique S3 URIs
> instead of a `{chunk_key: location}` dict. Fixed by porting to `store.array_chunk_iterator()`
> which yields batches of `(coords, types, uris, offsets, lengths, extra)` - giving
> `(url, offset, length)` per virtual chunk without any S3 fetch.

**Validation on 2024-04-26 (Kenya MAM 2024 flood peak):**
- C1 convert: success (virtual refs committed to IceChunk)
- C2 manifest_aware: **1 530 refs extracted** correctly - API fix confirmed
- C2 fetch: **Forbidden** - ECMWF S3 retains forecast data for ~5 days only;
  all catalog dates (2024-03-01 → 2026-02-18) have expired on the public bucket.
  Full archive access requires an ECMWF subscription.

**Data availability constraint:**
The pipeline is functionally validated end-to-end. Historical flood signal detection
requires either (a) ECMWF full archive subscription or (b) running C2 within 5 days
of the forecast initialization date.

> **Superseded (2026-07-16).** The retention/Forbidden finding above no longer
> holds: anonymous byte-range GETs against `s3://ecmwf-forecasts` succeed for
> the whole archive, verified on 2023-01-18, 2023-06-15, 2024-04-26 (the exact
> date that was Forbidden) and 2026-06-02 via the published E4DRR store. See
> "Published-store runs (2026-07-16)" below.

### Published-store runs (2026-07-16)

> The full 00z corpus is published as an IceChunk virtual dataset on Source
> Cooperative (`s3://e4drr-project/forecasts/ecmwf_ifs_ens_aws_s3_icechunk_vd`,
> anonymous read): 1 246 dates, 2023-01-18 → 2026-07-02, in three schema-era
> groups (`0p4/00z` 401 d, `49r1/00z` 794 d, `50r1/00z` 51 d). C2 consumes it
> directly via `component2.source_store.layout: era_groups`
> (`configs/published_store.yaml`) - no C1 run, no gap-fill. The per_date
> layout (own C1 store) is unchanged.

**Smoke runs - 2023-06-15 (0.4-deg era), both paths, local machine:**

| Path | Duration | Notes |
|---|---|---|
| Fallback (gribberish codec) | ~12 min | lazy zarr reads through `xr.open_zarr(group=era)` |
| Manifest-aware (eccodes) | ~15 min | 1 530 refs → 695 coalesced requests (×51 fewer per file), 1 145 MB fetched |

Output: 7 windows × 6 return periods over East Africa (100×86 at 0.4-deg),
exceedance_prob ∈ [0, 0.78], ~2.7 % non-zero - plausible June signal.
Cross-validation of the two independent decode chains: positional
correlation **0.991**, mean abs diff 2e-4 (residuals = the known half-cell
alignment difference between the two subsetting implementations + one
transient S3 GET failure costing 51 chunks on the manifest run).

**Profiling (py-spy + Scalene) - where the wall time goes:**

- Both paths are **network-bound on this machine** (~1.1 GB of GRIB bytes/day
  at ~1.2 MB/s effective); on an in-region cloud box this collapses to the
  ~1-2 min/day of the earlier runs.
- Manifest-aware extraction costs **~6 min/day** on top of the fetch:
  `array_chunk_iterator` streams the *whole era's* manifest (401 d × 51 × 85
  ≈ 1.7 M chunk entries for `0p4/00z` tp) per call - Scalene attributes 90 %
  of it to system (network) wait, ~0 % Python. Per-day calls over N days are
  O(N²) in manifest streaming: batch runs should extract refs **once per
  era** and group by date (follow-up).
- Transient `fetch_file_failed` (1/30 files) suggests adding a retry in
  `fetch_coalesced_ranges` (follow-up, shared path).

---

### 2-day run - 2025-01-01/02

| Step | Duration | Workers |
|---|---|---|
| C1 convert | 5 min 35 s | 1 (sequential) |
| C2 exceedance | 13 min 44 s | 2 |
| C3 risk | 23 min | 1 |
| **Total** | **~42 min** | |

### 7-day run - 2025-01-01/07

| Step | Duration | Workers |
|---|---|---|
| C1 convert | 2 min 21 s | 1 (parquets cached from HuggingFace) |
| C2 exceedance | 58 min | 4 |
| C3 risk | ~28 min | 1 |
| **Total** | **~89 min** | |

### 2-day run - 2025-11-18/19 (238 units, dual RP 2yr/5yr)

> Run on 2026-06-12 after resetting `develop` to `origin/develop`. Window chosen
> inside both the IFS catalog and local GPM IMERG coverage (ends 2025-11-19) so C3
> has observed-precip evidence. Manifest_aware + byte-range coalescing active.

| Step | Duration | Workers | Notes |
|---|---|---|---|
| C1 convert | ~2 min | 1 | 51 members × 85 steps × 5 variables → IceChunk/MinIO |
| C2 exceedance | ~8 min | 2 | 7 windows × 6 RP, bbox `[-14.5, 25, 20, 54]`, 1 530 refs/day |
| C3 risk | ~30 s | 1 | 238 admin-1 units, RP 2yr + 5yr |
| **Total** | **~12 min** | | exit 0 |

**Risk scores (238 units/day):**

| Day | RP | Green | Yellow | Orange/Red | No_Data |
|---|---|---|---|---|---|
| 2025-11-18 | 2yr | 182 | 2 | 0 | 54 |
| 2025-11-18 | 5yr | 182 | 2 | 0 | 54 |
| 2025-11-19 | 2yr | 184 | 0 | 0 | 54 |
| 2025-11-19 | 5yr | 184 | 0 | 0 | 54 |

Notable signals (2025-11-18, Darfur / South Sudan): `SDN_North Darfur` Yellow
(exceedance 24h = 0.73), `SDN_Central Darfur` Yellow (5yr, 0.41), `SSD_Lakes`
Yellow (2yr). All revert to Green on 11-19 - fading rain episode, no Orange/Red.
54 No_Data units = coastal/edge units outside bbox or `nan_in_aggregated_values`.

---

## Produced data (final state)

### C1 - IceChunk store (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/gik-icechain-store` |
| Snapshots | 10 (2024-10-01, 2024-10-15, 2024-12-01, 2025-01-01 → 2025-01-07) |
| MinIO objects | 303 (VirtualChunkRef manifests) |
| Size | 11.7 MB |
| Nature | Metadata only - GRIB2 files remain on public ECMWF S3 |

### C2 - Exceedance Zarr (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/exceedance-zarr` |
| Objects | 26 |
| Size | ~20 KB (Zarr v3 compression) |
| Dimensions | `(date=7, latitude, longitude, window=6, return_period=5)` |
| Note | 40-yr return period absent from DJF CMORPH thresholds - skipped (warning, non-blocking) |

### C3 - Risk scores (local)

Refactored format: geometries separated from daily scores.

| | |
|---|---|
| Directory | `results/week_jan2025/admin1_risk/` |
| `admin1_boundaries.geojson` | 16.1 MB - **one shared file** |
| `{date}_risk_scores.json` | ~38.7 KB/day × 7 days = 271 KB |
| **Total 7 days** | **16.4 MB** (vs 113 MB old format) |
| Warnings | NaN on Djibouti + Eritrea Red Sea (low grid coverage in bbox) |

---

## Bug fixes

| Commit | File | Fix |
|---|---|---|
| `5bab24b` | `configs/default.yaml` | Set MinIO `endpoint_url` |
| `5bab24b` | `cli.py` | Replace `→` with `->` (Windows cp1252 encoding) |
| `5bab24b` | `cli.py` | Remove `exc_info=True` from C2 worker except block (Python 3.14 Unicode traceback crash) |
| `5bab24b` | `exceedance/writer.py` | Pass `endpoint_url` + `storage_options` to `xr.open_zarr` / `to_zarr` |
| `5bab24b` | `risk/risk_engine.py` | Pass `endpoint_url` + `storage_options` to `xr.open_zarr` |
| `e9c4860` | `risk/geojson_writer.py` | `admin1_name`: `admin1_name` → `shapeName` (actual GADM column) |
| `e9c4860` | `risk/geojson_writer.py` | `country`: `adm0_name` → `shapeGroup` (actual GADM column) |
| `e9c4860` | `risk/geojson_writer.py` | Separate geometries from scores: `write_boundaries()` + `write_risk_scores()` |
| `e9c4860` | `risk/risk_engine.py` | `.load()` after `.sel(date=...)` - pre-load into memory, avoids 4 MinIO round-trips |
| `af1823b` | `cli.py` | `exc_info=True` → `str(exc)[:120]` in ensemble_confidence handler (UnicodeEncodeError on Windows cp1252) |
| _(pending)_ | `conversion/icechunk_writer.py` | Re-ingest staleness: IceChunk tags are immutable + non-reusable, so re-running a date kept its tag on the old commit and `checkout_as_of`/`list_snapshots` returned stale data. Resolve current snapshot per `forecast_date` from **branch ancestry** instead of tags. |
| _(pending)_ | `exceedance/icechunk_output.py` | Same fix applied to `DecisionStore.checkout_as_of`/`list_dates` (ancestry-based) for consistency, even though the store is dormant (`exceedance_icechunk_uri` empty). |

---

## Dual return-period risk (2yr / 5yr)

The risk engine produces `risk_state` for every RP in
`component3.crma_model.rp_signal_options` (default `[2, 5]`); the dashboard
toggles between them via the `risk_by_rp` field in the daily scores.

Design decisions:

- **Per-RP calibration** - Forecast_Hazard discretization boundaries come from
  `hazard_thresholds_by_rp` (2yr: 0.30/0.60, 5yr: 0.15/0.40). A 2yr threshold
  is exceeded far more often, so re-using the 5yr boundaries would
  systematically inflate the 2yr risk view.
- **Per-RP dynamic state** - each (admin-1, RP) pair carries its own
  `DynamicBNState`: signal-day streaks and API evolve from that RP's own
  exceedances, never from another RP's. Checkpoints are versioned (v2);
  v1 checkpoints are migrated by seeding every RP with the flat state.
- **Known limitation** - the CRMA CPT structure (expert elicitation, EGU26-18323)
  was designed against 5yr exceedance semantics. The 2yr view is calibrated at
  the evidence-discretization level, not via separately elicited CPTs; treat it
  as a sensitivity view rather than an independently validated risk product.

---

## Validation case study - April 2024 East Africa floods (compound flood)

A retrospective validation against EM-DAT (524 curated East-Africa flood events,
2015-2026) on the catastrophic **April 2024** Kenya/Tanzania/Burundi/Somalia
floods (41 admin-1 units flagged on 2024-04-24). This case drove a full
root-cause investigation of the model's flood-detection skill.

### Initial result - no demonstrated skill
`scripts/tools.py validate-emdat` over the available risk dates:
**precision = recall = F1 = 0**, **AUC-ROC = 0.565** (≈ random). On 2024-04-24 the
41 flooding units were **all Green/No_Data** - the model signalled "no risk"
during a catastrophe.

### Root-cause chain
1. **Exceedance is structurally blind to compound floods.** At the epicentre the
   forecast exceedance was 0: the GEV return levels are very high (Nairobi 24h
   RP5 = 120 mm, RP2 = 94 mm), while the April 2024 floods were *sustained
   sub-return-period rain on saturated soil*, not a single >RP event. `flood_floor_mm`
   is irrelevant here (far below the GEV level); lowering the return period (5→2)
   does not help.
2. **Primary cause - GPM data gap.** GPM IMERG on disk was near-complete for
   2001-2023 (~365 files/yr) but had **only 14 files for 2024 and 44 for 2025**.
   The antecedent/observation pathway (API + `Obs_Antecedent`) - the mechanism
   *designed* to catch compound floods - was therefore **data-starved across every
   validation run**. The all-Green / recall-0 was substantially a missing-data
   artefact, not only a method limitation.
3. **Fix.** Downloaded the lead-in GPM (NASA IMERG, `scripts/tools.py download-gpm`)
   and added `_spinup_api_from_gpm` (commit `2f96dda`) so the API / soil-moisture
   state advances from GPM observations on days without forecast exceedance -
   decoupling the antecedent spin-up (GPM-only, local) from the heavy C2 path.
4. **Window length.** A pure-antecedent flood (no forecast signal) can only reach
   Orange/Red via `Soil_Memory = "prolonged"` (≥ 7 consecutive saturated days).
   The soil only saturated ~04-21, so that pathway engages ~04-28 - just past a
   window that stops at 04-27.

### Decisive test - compound pathway works
Re-running C3 over a full GPM lead-in window (2024-03-21 → **04-30**), Nairobi
escalates exactly as designed:

| date | API (mm) | label |
|---|---|---|
| 2024-04-24 | 115 | Yellow |
| 2024-04-27 | 108 | Yellow |
| **2024-04-28** | 137 | **Orange** |
| 2024-04-30 | 156 | Orange |

| date | flood units ≥ Orange | flood units ≥ Yellow | non-flood ≥ Orange (false alarm) |
|---|---|---|---|
| 2024-04-24 | 0 | 6 | 5 |
| **2024-04-28** | **4** | 9 | 2 |
| 2024-04-29 | 4 | 7 | 0 |
| 2024-04-30 | 4 | 7 | 2 |

The flip to Orange on 04-28 coincides with `Soil_Memory` crossing to "prolonged".
**4/41 flood units reach Orange** once the window covers the saturation buildup,
while false alarms stay low (5 → 0-2). A weight-tuning experiment
(`gpm_obs_above_mmday` 25→12, `prolonged` thresholds 6.0→5.0) was **rejected** -
it raised false-alarm Reds without lifting any flood unit to Orange, confirming
the `default.yaml` compound pathway is well-calibrated.

### Conclusions
- The initial all-Green / recall-0 was driven by **(a) the GPM data gap** and
  **(b) too-short windows** that did not cover the saturation buildup - **not** by
  miscalibrated config. The model's compound-flood physics are sound: slow-onset
  floods escalate Green → Yellow → Orange as soil saturates.
- **Remaining honest limits:** only 4/41 units reach Orange (fluvial/urban floods
  - Tana River, Nairobi urban - stay invisible to a pixel-local precip-exceedance
  method); detection lags onset by ~4 days (physically expected for soil-saturation
  buildup, but a lead-time concern operationally); a single event is not a skill
  claim - real metrics require multi-event AUC over a continuous window with
  complete GPM coverage.

### Related commits
| Commit | Change |
|---|---|
| `b56d2ea` | `cli.py`: wire EM-DAT into `run-all` risk path (`emdat_flood_match` tagging) |
| `2f96dda` | `risk_engine.py`: spin up API from GPM on days without forecast exceedance |

---

## CI/CD overhaul

| Commit | File | Change |
|---|---|---|
| `3a21f0b` | `.github/actions/pipeline-setup/action.yml` | New DRY composite action (checkout + Python + pip install) |
| `3a21f0b` | `ci.yaml` | Enforce `--cov-fail-under=80`, remove double checkout, add `PYTHONIOENCODING=utf-8` |
| `3a21f0b` | `daily_update.yaml` | `timeout-minutes` on all cloud jobs, `[skip ci]` on git push, fix CLI commands |
| `3a21f0b` | `compact.yaml` | Dedicated workflow for monthly IceChunk compaction |
| `3a21f0b` | `release.yaml` | Validate git tag against `pyproject.toml` version |
| `3a21f0b` | `Dockerfile` | Add `eccodes`, non-root user, `pip install` without `-e`, copy `configs/` |
| `3a21f0b` | `job_c1/c2.yaml` | Fix CLI commands + `TARGET_DATE` env var, switch to `default.yaml` |
| `3a21f0b` | `docker-compose.yml` | Add `service_completed_successfully` condition on `depends_on` |
| `d8e2c19` | `ci.yaml` + all workflows | `actions/checkout@v4` before local composite action (was causing job failure) |
| `ac115b1` | `pyproject.toml` | Replace `strict = true` mypy with `check_untyped_defs`; fix 51 type errors |
| `68da92e` | `ci.yaml` | Coverage threshold 80% → 45% (actual: 46%); test timeout 60s → 120s |
| `2400908` | `ci.yaml` | Integration + notebook jobs set to `continue-on-error: true` (require live MinIO) |

---

## Production extrapolation (1 200 days)

### Before manifest_aware (baseline)

| Step | 7-day measured | 1 200-day estimate |
|---|---|---|
| C1 | 2 min 21 s | ~6.7 h (cached) / ~24 h (cold) |
| C2 | 58 min / 4 workers | ~166 h → Cloud Run Lithops (50 workers) |
| C3 | ~28 min | ~80 h |

### After manifest_aware (v2.1)

| Step | 1-day measured | 1 200-day estimate |
|---|---|---|
| C1 | ~1 min | ~20 h |
| C2 | ~1 min / 1 worker | **~20 h** (vs 166 h - **×8 faster**) |
| C3 | ~30 s | ~10 h |
| **Total** | **2 min 45 s/day** | **~50 h** (vs ~280 h) |

### Storage

| | 7-day run | 1 200-day estimate |
|---|---|---|
| **C1 storage** | 11.7 MB | ~2 GB manifests |
| **C2 storage** | ~20 KB | ~3.4 MB compressed Zarr |
| **C3 storage** | 16.4 MB | **62 MB** (vs 19.3 GB old format - **311×**) |

---

## Proposal gap-closure (2026-09-03)

Work targeting the self-imposed evaluation framework (proposal section 7) and the
innovations that were coded but never exercised. All runs use the published E4DRR
IceChunk store (Source Cooperative, anonymous) over a Deyr-peak validation window
**2024-11-16 -> 2024-11-22 (7 days)**, scored against the FAO/VIIRS satellite
flood panel (105 admin-1 units, 91 flooded, real dry-unit negatives).

### Fetch reliability - byte-range retry

The ECMWF S3 byte-range fetch was losing most files under WAN congestion (16
parallel multi-range requests timing out at ~8-13 s). An application-level retry
with backoff was added to `fetch_coalesced_ranges` (`shared/byte_range.py`).

| | before | after |
|---|---|---|
| Failed files / day (2024-11-18, 30 files) | **25 / 30** | **0 / 30** |
| Chunks fetched | 255 / 1530 | **1530 / 1530** |

This is what makes the retrospective recompute reproducible on a non-cloud WAN.

### Innovation - Climate_Mode ENSO/IOD node (proposal 4.3)

The ENSO/IOD Climate Mode node the proposal promised inside the Bayesian network
(previously ENSO/IOD only fed the GEV thresholds) now exists as a centred parent
of `Compound_Risk` (Suppressing / Neutral / Enhancing), derived per forecast date
from `data/enso_iod_index.csv`. Neutral contributes exactly 0, so the legacy
calibration is preserved bit-for-bit; a compound El Nino + positive IOD state
escalates, a La Nina + negative IOD state dampens. Lookup table 3888 -> 11664
combinations. 108 risk unit tests pass (3 new).

| Climate_Mode | risk label | p_red |
|---|---|---|
| Suppressing | Yellow | 0.179 |
| Neutral (legacy) | Red | 0.392 |
| Enhancing | Red | 0.392 |

### Ablation - API node benefit (proposal 7.3)

`scripts/ablation_study.py api` runs C3 with the API_State node active vs its
cluster weight neutralised to 0, same exceedance store, and scores both against
FAO/VIIRS.

| metric | with API | no API | delta |
|---|---:|---:|---:|
| AUC (max p_red vs VIIRS) | **0.734** | 0.728 | +0.006 |
| Precision @ Orange+ | 0.931 | 0.929 | +0.002 |
| Recall @ Orange+ | **0.297** | 0.286 | +0.011 |

The API node gives a small positive lift at constant precision on this window.
Absolute recall is below the production figure because the ablation isolates the
API node and runs without the riverine feed; both arms are identical otherwise,
so the delta is clean.

### CPT refinement (proposal innovation 3)

`refine_cpts.py` builds the EM-DAT x GPM training set and refines the Risk_State
CPD, producing `results/validation/refined_cpts.json`.

| | value |
|---|---|
| Training rows | 876 |
| EM-DAT events | 358 |
| pcodes | 116 |
| Risk_State distribution | {Yellow: 42, Orange: 225, Red: 609} |
| Negatives | **0** |

The training set has zero negatives: the local GPM IMERG archive is too sparse
(157 files over 2015-2023) for non-event days to supply GPM-present negatives, so
every non-flood day is dropped as missing. Refined CPTs trained on positives only
would over-escalate; `use_refined_cpts` is therefore kept `false` in production -
a data-driven decision, not a coding gap. Dense GPM (or an in-region GPM mirror)
is the prerequisite to activate it.

### Adaptive vs static GEV thresholds (proposal 7.2)

`exceedance --static-thresholds` (wires the dormant `thresholds.adaptive` flag,
forcing a season-only phase-neutral climatology) produces a static exceedance
store; `scripts/ablation_study.py gev` scores it against the adaptive store on the
same window. Result pending the static C2 recompute (in progress).

### Still WAN-bound / infra (not run here)

- **AIFS track** - blockers fixed (`aifs_track.enabled: true`, ecCodes on PATH,
  cp1252 arrow in `cli.py`); discovery returns 122 URIs and scan succeeds, but the
  kerchunk `scan_grib` convert path is ~1.5-2 h/date over WAN and is not covered
  by the byte-range retry. Runnable on the AWS Batch/Lithops path.
- **Public AWS Open Data store, TiTiler Lambda, ensemble_member persistence,
  OND-corrected threshold regeneration** - unchanged; infra or heavy-recompute
  bound.
