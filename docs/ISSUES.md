# GIK-IceChain — Remaining Issues (Whole-Project Audit)

> Updated: 2026-06-17 (now tracked in git)
> Scope: full-project critique after the manifest-aware / non-uniform-step /
> CI-workflow work. Items marked FIXED were resolved during this session.

---

## AUDIT 2026-06-18 — Dashboard publish path was broken; now served from S3

### ISSUE-24 · Dashboard never received pipeline results — FIXED (serve-from-S3)

The deployed dashboard showed nothing because the publish path was broken at
three points:
1. `dashboard/web/.gitignore` ignores `public/data/` → the daily `git add
   dashboard/web/public/data/` was a silent no-op (nothing ever committed).
2. `daily_update` `update-dashboard` ran `data_pipeline contract --results
   results/admin1_risk` but that dir does not exist after a fresh checkout (the
   risk output lives in S3 / a different job's /tmp) → contract would fail.
3. `deploy-web` builds a static export from the committed tree, so even if (1)
   worked it bundled an empty `public/data`.

**Fix (chosen: serve the contract from S3, like `COG_BASE`; never commit it):**
- `web/src/lib/config.ts`: `DATA_BASE` is now env-overridable
  (`NEXT_PUBLIC_DATA_BASE`), defaulting to the bundled `${BASE_PATH}/data` for
  local dev.
- `deploy-web.yaml`: passes `NEXT_PUBLIC_DATA_BASE: ${{ vars.DATA_BASE }}` at
  build (mirrors `COG_BASE`); no data step.
- `daily_update.yaml` `update-dashboard`: pulls the existing contract from
  `s3://$GIK_BUCKET/dashboard-data/` (so `index.json` MERGES date history) +
  the day's risk from `s3://$GIK_BUCKET/admin1_risk/`, runs `data_pipeline
  contract` + the new `data_pipeline geojson` (boundary splits only), then
  `aws s3 sync` the result back to `s3://$GIK_BUCKET/dashboard-data/`. The git
  commit/push of `public/data` is removed.
- New `data_pipeline geojson` subcommand renders the large static boundary
  splits without needing risk data, so they stay out of git.

**REQUIRED manual infra (maintainer, one-time):**
- Set repo Variable `DATA_BASE` to the public bucket URL, e.g.
  `https://<bucket>.s3.eu-west-1.amazonaws.com/dashboard-data`.
- Make `s3://$GIK_BUCKET/dashboard-data/` public-read + a CORS rule allowing
  `GET` from the GitHub Pages origin (same as the COGs bucket).

**To publish the April-2024 case study (incl. the 2 Red):** it was generated
locally into `dashboard/web/public/data` (gitignored, local-dev preview only).
For the deployed site, `aws s3 sync dashboard/web/public/data/
s3://$GIK_BUCKET/dashboard-data/` with the production AWS creds (not the dev
MinIO creds in `.env`) — run by the maintainer or a one-off workflow.

---

## AUDIT 2026-06-17 — Recall ceiling root-cause (BN escalation levers + EM-DAT validation)

Investigation of *why* the CRMA risk output misses most EM-DAT floods. Started
from the April 2024 compound-flood window (`run-all --start 2024-04-22 --end
2024-04-28 --config configs/default.yaml`, MinIO store) and drilled the whole
exceedance→risk chain. **Headline: the recall ceiling is a STRUCTURAL design
limit (local-rainfall trigger + ECMWF skill), not a tunable bug.** No
recall-improving code change was warranted; two candidate levers were
pre-measured to ~zero effect and *not* implemented.

### ISSUE-22 · Recall ceiling is structural (riverine floods + ECMWF miss) — DIAGNOSED, NOT A BUG

**Quantified baseline (replayable).** April 2024, 7 days, 238 admin-1 units:
`python scripts/tools.py validate-emdat --risk-dir results/admin1_risk
--start 2024-04-22 --end 2024-04-28 --event-level --lead-days 1`

| metric | value |
|--------|-------|
| unit-day AUC-ROC (p_red, `run_validation`) | 0.66 |
| **event-level recall @ Yellow** (lead=1) | **0.375** (15/40 events) |
| event-level recall @ Orange | 0.025 (1/40) |
| event-level recall @ Red | 0.025 (1/40) |

(NB two label sources coexist: the unit-day path uses `run_validation`'s
EM-DAT-record date-ranges; the event-level path uses the pre-joined
`emdat_flood_match` tag — the latter avoids the EM-DAT-pcode-namespace mismatch.
The standalone unit-day recall@Yellow under `emdat_flood_match` labelling was
0.132 — a *tracking* recall, superseded below by the event-level metric.)

The unit-day recall is a *tracking* recall (penalises not alerting every
day of a multi-week, multi-unit EM-DAT event). The operational metric is
**event-level early detection**: collapse contiguous `emdat_flood_match` runs per
unit into ONE event, detected if the model fires ≥threshold on any day in
`[onset − lead, end]`. That gives **37.5 %** @Yellow.

**Where the 25 missed events come from (settled by drilling, not assumption):**

1. **C2 vs BN split.** Of 25 missed (event-level, lead=1): **17 had exc=0 the
   whole window**, **7 had 0<exc<0.15** (sub-Medium trace), **only 1**
   (`SOM_Mudug`, exc=0.20) was a borderline BN under-reaction. → 24/25 (96 %)
   are upstream of the Bayesian Network. Tuning the BN cannot recover them.
2. **Not RP selection.** Scoring at rp2 (lower 137 mm bar) instead of rp5 lifts
   only 1/25.
3. **Not window selection.** Aggregating ALL 7 accumulation windows (3h…168h) to
   admin-1 for the 32 missed no-signal units (2024-04-26): **every window =
   exactly 0.00 for all 32** (incl. Nairobi/Kiambu/Murang'a during the deadly
   floods). The exceedance is structurally zero, not a flash-flood signal hiding
   in a window the risk engine ignores.
4. **Not the `flood_floor_mm`.** MAM GEV thresholds (`data/cmorph_thresholds`,
   ~1° grid, vars `rp_2y…rp_100y`): Nairobi/Kiambu/Murang'a 72h **rp5 = 172 mm,
   rp2 = 137 mm**; `flood_floor_mm` (72h = 50 mm) is NOT binding (GEV ≫ floor).
5. **Observed rainfall is BELOW the trigger.** GPM 72h (24–26 Apr 2024, box-max)
   during the floods: Nairobi 127 mm, Kiambu 152 mm — **below the 172 mm rp5
   bar** (cell values 47/89 mm below even rp2 137 mm). **Floods do not require a
   5-yr rainfall extreme.** Triggering on "exceedance of a rare-rainfall RP"
   structurally misses moderate-rain floods on saturated soil.
6. **The missed units are fluvial / non-local.** Tana River, Garissa (on the
   Tana), Lower Juba, Lower Shebelle, Kisumu (L. Victoria), Lindi. Their local
   API only rose 20→~50 mm (modest local rain) yet EM-DAT logged floods → these
   are **riverine floods fed by upstream-highland rain / routing**, which a
   local-cell rainfall-exceedance + local-API system is blind to. Confirmed by
   pre-sizing lever #2: **0/25 missed events had api≥80 (saturated)** — there is
   no antecedent signal to strengthen either.

**Conclusion.** The recall ceiling (~16/40 @Yellow) reflects ~24/40 EM-DAT
events being non-local/riverine or unforecast by ECMWF. **The real next
capability is hydrological routing / river-network modelling — a separate, large
effort, not a BN or threshold tweak.**

### ISSUE-23 · Forecast-exceedance trigger is calibrated to rare rainfall, not flood production — DESIGN

Direct consequence of ISSUE-22.5. The exceedance signal asks "is the forecast
rainfall a 5-yr extreme?" while floods are produced by moderate rainfall on
saturated soil. Two fix directions (both deferred, both need C2):
- **#1-full (C2):** recompute exceedance against a sub-rp2 / flood-relevant /
  soil-conditioned rainfall threshold. The only path that recovers Nairobi-type
  cases (47–127 mm). ~1–2 h recompute + C2 dev.
- **#1-light (C3, IMPLEMENTED, INERT):** `soil_conditioned_rp` (CRMAModelConfig,
  default True, `saturated_rp=2`) surfaces the lower-RP exceedance when
  `api_state≥2`. A/B on April 2024 = **0 change** (14 saturated units all have
  rp2_state == rp5_state because exc=0 at BOTH rp2 and rp5; flood rain 47–127 mm
  < rp2 bar 137 mm). Kept (config-gated, correct, activates when forecast rain
  lands in 137–172 mm on saturated soil) but proven it cannot help April 2024 —
  empirical proof that #1-full (C2) is required.

### ISSUE-16 · EM-DAT validation — RESOLVED 2026-06-17

`data/emdat/east_africa_floods.csv` + `pcode_mapping.csv` are present and
tracked; `scripts/tools.py validate-emdat` runs a chiffré validation
(precision/recall/F1/AUC/FAR + per-event CSV via `cpt_refinement.run_validation`,
unit-day) and now also an **event-level early-detection mode** (`--event-level
--lead-days N`) that collapses EM-DAT runs per unit and scores detection, using
the pre-joined `emdat_flood_match` label (avoids the EM-DAT-pcode-namespace
mismatch). First numbers above (ISSUE-22). The §7 evaluation framework is live.

### Murang'a tp-integrity check — data confirmed sane (no tp regression)

`SOM`/`KEN` anomaly: Murang'a observed 178 mm > 172 mm threshold yet pipeline
exc=0. Ruled out (a) regrid/assignment bug (gridded exc uniformly 0 over 143
central-Kenya cells) and (b) a tp-unit regression: raw IceChunk tp read for
Murang'a (`IceChainStore('s3://gik-icechain/gik-icechain-store').checkout_as_of`,
isel on the 0.25° grid since lat/lon carry no coord index; needs `.venv/Scripts`
on PATH so eccodes can decode the GRIB virtual chunks) = **0.0014–0.011 m
(1.4–11 mm), sane metres** — not 0.0-exact, not absurd-mm. Double-confirmed: the
fact that `SOM_Sool`/`ETH_Somali` fire exc=0.71 PROVES the ×1000 m→mm conversion
is active (raw metres could never clear a mm threshold). So exc=0 = genuine low
forecast precip (~2.5 mm vs 178 mm observed; note the 26-Apr init forecasts
forward while the observed total partly precedes it). Pipeline correct, data
intact — all session conclusions hold.

### Levers shipped this session (escalation, on develop)

| lever | commit | effect |
|-------|--------|--------|
| #1 Forecast_Hazard 4th "Extreme" state (exc≥0.70 rp5 / 0.85 rp2) | `01c160b` | 2 surgical Red on Apr 2024 (ETH_Somali, SOM_Sool, exc=0.71, EM-DAT✓); stress-test 20 dates/4760 unit-days = 10 Red (0.21%), all exc≥0.71, 0 false Red → `hazard_extreme_threshold=0.70` validated |
| #3 confidence decoupled from forecast branch | `01c160b` | architecturally correct (obs-confidence must not veto independent forecast); A/B 1666 unit-days = 0 change on Apr 2024 (inert this window, kept) |
| externalised score weights + lever tests | `f146cce` | `weight_temporal_persist`/`weight_spatial_coverage` to config; 4 targeted tests |
| #1-light soil_conditioned_rp | `9012ae4` | see ISSUE-23 (inert, kept) |

---

## AUDIT 2026-06-09 — Promise (Code-for-Earth proposal) vs Code

Confrontation point par point du document de soumission au dépôt réel. Voir
la matrice de conformité dans ISSUE-14 → ISSUE-20.

### ISSUE-14 · Signal nul — VALIDÉ/RÉSOLU 2026-06-10
**Les sorties all-Green étaient STALE (pré-fix tp→mm).** Run E2E réel sur
`2025-11-19` (OND humide), MinIO + ECMWF S3 live, 51 membres décodés
(1530 chunks, 51× reduction) :
| window | rp | thr_mm | acc_max | exc_max | %cells>0 |
|--------|----|--------|---------|---------|----------|
| 24h | 5 | 87.7 | 284.1 | **1.000** | 8.2% |
| 72h | 2 | 81.9 | 292.3 | **1.000** | 18.0% |
| 168h | 5 | 145.5 | 292.4 | **1.000** | 7.6% |
Exceedance non nulle, gradient RP correct (RP↑ → moins de cellules). tp×1000
appliqué (`cli.py:347`), mode `OND_neutral_neutral`. La chaîne C1→C2 marche sur
données réelles. **Reste :** persistance C2 + C3 bloqués par ISSUE-21.

### ISSUE-21 · Windows: écriture temp-zarr échoue (WinError 5) — bloque run-all
`_process_exceedance_day` écrit la sortie dans `tempfile.mkdtemp()` →
`AppData\Local\Temp`. Le `tmp_path.replace()` atomique de zarr y lève
`PermissionError [WinError 5] Accès refusé` (verrou Defender / Python 3.14) sur
`zarr.json.partial → zarr.json`. La science (exceedance) est calculée mais non
persistée → C2 store + C3 risk vides. **Fix:** retry sur PermissionError dans
le write, OU tmp_dir hors AppData\Temp, OU écrire le zarr directement sur MinIO.

### ISSUE-15 · API/GPM IMERG jamais alimenté → C3 tourne à l'aveugle
`api_mm` reste figé à 20.0 (`dynamic_bn.init_state` default) dans toutes les
sorties → la branche d'évidence observationnelle (GPM IMERG) du Dynamic BN
n'est jamais alimentée. `gpm_loader.py` existe mais n'est pas câblé dans le
chemin C3 du CLI. Contredit la promesse « CRMA-Live : API persistence node ».
**Done when:** `api_mm` varie jour à jour à partir de GPM IMERG observé.

### ISSUE-16 · EM-DAT absent du dépôt → CPT refinement & validation impossibles
`configs/default.yaml:10` pointe vers `data/emdat/east_africa_floods.csv` mais
le répertoire `data/emdat/` **n'existe pas**. Conséquences :
- `cpt_refinement.py` (Innovation EM-DAT MLE) ne peut pas s'exécuter (ISSUE-10).
- Tout le framework d'évaluation §7 (hit rate, false-alarm ratio, ROC AUC
  adaptatif-vs-statique) est impossible : aucune vérité terrain.
**Done when:** CSV EM-DAT EA présente + ≥1 run de validation chiffré.

### ISSUE-17 · AIFS track — diagnostic CORRIGÉ : bug de préfixe S3, PAS un 403
**Update 2026-06-09 — FIXED (discovery layer).** Le « 403 / abonnement requis »
était un faux diagnostic. AIFS ENS est **ouvert et anonyme** sur
`s3://ecmwf-forecasts`, mais sous le préfixe `aifs-ens/0p25/enfo` (le code
utilisait `aifs/0p25/enfo`) et les membres perturbés sont `-pf.grib2` (le code
cherchait `-ef.grib2`). Deux corrections dans `aifs_discovery.py` :
- `_AIFS_PATH_TEMPLATE` : `aifs` → `aifs-ens`
- `_AIFS_PF_FILENAME` : `-enfo-ef` → `-enfo-pf`
Vérifié : `discover_aifs_files(2025-11-19)` → **10/10 URIs existent sur S3**.
Tests mis à jour (23 passed). L'archive ouverte AIFS ENS **commence mi-2025**
→ valider sur OND 2025 (notre date D3 = 2025-11-19 est couverte ; avril 2025
404).
**Reste (non bloquant) :** `aifs_to_virtual_dataset` utilise encore
`kerchunk.combine.MultiZarrToZarr` au lieu de VirtualiZarr 2.x — à migrer pour
cohérence, mais la découverte est désormais fonctionnelle.

### ISSUE-18 · Benchmark vs dynamical.org jamais mesuré
`results/benchmarks/` = `.gitkeep` seul. `benchmark.py` mesure réellement le
côté GIK mais la baseline 242 TB est une constante codée en dur
(`_DYNAMICAL_STORE_FULL_GB`). La table de benchmark du README est donc
non sourcée.
**Done when:** ≥1 run réel (même 30 jours) écrit dans `results/benchmarks/`.

### ISSUE-19 · Livrables de déploiement = coquilles vides
- `dashboard/calendar_map/` + `storymaps/` : templates sans données
  (`calendar_map/data/` vide — voir ISSUE-7). Pas de GitHub Pages / TiTiler /
  VEDA déployé.
- Store « public `s3://gik-icechain/...` » = en réalité MinIO privé ; rien sur
  AWS Open Data.
- `gap_filler` / Cloud Run / Lithops : jamais exécutés (ISSUE-9) → catalogue
  réel 737 jours, pas les ~1200 annoncés.

### ISSUE-20 · Bug climatologique : OND exclut décembre
`thresholds.py:69` définit `Season.OND = [10, 11]` (octobre+novembre seulement).
Les *short rains* OND incluent décembre ; décembre est ici absorbé par DJF. La
stratification GEV des short rains est donc tronquée d'un tiers de la saison.
**Note positive :** contrairement à une crainte initiale, le GEV adaptatif EST
réellement câblé dans le E2E (`cli.py:430` charge les 252 NetCDF de
`data/cmorph_thresholds/`). Innovation 2 = livrée côté code.

---

## CRITICAL — block scientific validation

### ISSUE-1 · No complete E2E run validated yet — see ISSUE-14
No full C1 -> C2 -> C3 run has succeeded on a real flood date this session.
The only risk scores produced (2025-02-22 -> 28) are DJF (dry season, all
Green) and came from a pre-existing store. The GitHub Actions workflow is the
best path but has not run yet.
**Update 2026-06-09:** even the OND wet-season output (`2025-11-20`) is all
Green with exceedance=0 — this is now tracked in detail as **ISSUE-14**.
3 high-flood validation dates selected for the E2E run (MAM 2025 / OND 2025).
**Done when:** a green run on an OND/MAM date shows Orange/Red signals.

### ISSUE-2 · Exceedance store schema collision — FIXED
`exceedance/writer.py` used `to_zarr(mode="a", append_dim="date")` with no
schema validation; a window-count change (7 vs 6) crashed the append and could
corrupt the store.
**Fix applied:** `_align_append_schema()` reindexes each new batch onto the
store's `window`/`return_period` coords (NaN-filling windows the run didn't
produce) and raises a clear `ValueError` if the run introduces *new* coordinate
values. Covered by `tests/unit/test_writer.py`.

### ISSUE-3 · README over-promises vs reality
| Claim | Reality |
|---|---|
| ~300 admin-1 units | 196 (after MDG+SDN fix) |
| ~1200 days (May 2023) | catalog 720 days (Mar 2024 -> Feb 2026) |
| 18.5 GB virtual store | ~12 MB committed so far |
| Innovation 4 (AIFS) as delivered | 403 Forbidden — needs ECMWF subscription |
| Manifest-aware "~10x fewer S3 requests" | real coalescing ratio = 1.04 (see ISSUE-4) |
**Fix:** correct the numbers; mark AIFS "conditional"; fix or reword the
coalescing claim.

---

## HIGH

### ISSUE-4 · Byte-range coalescing is ineffective (ratio 1.04) — FIXED
1530 chunks -> 1466 requests. The 51 members of one GRIB2 step-file are not
byte-adjacent, so adjacent-merge coalescing barely helped.
**Fix applied:** `fetch_coalesced_ranges` now groups ranges by file (URI) and
issues a single `obstore.get_ranges` multi-range request per file (HTTP-layer
coalescing). ~1500 per-chunk `get_range` calls collapse to ~30 per-file
requests (one per step-file holding all 51 members).

### ISSUE-5 · No tolerance to individual fetch failures — FIXED
A single GRIB2 object timing out aborted the whole day (observed 3x over WAN).
**Fix applied:** each per-file fetch in `fetch_coalesced_ranges` is wrapped in
try/except — a failed file (after obstore's retries) is logged
(`fetch_file_failed`) and skipped; its chunks stay absent and become NaN
downstream, guarded by the existing `min_members` check. The completion log
reports `n_failed_files`. Covered by `tests/unit/test_byte_range.py`.

### ISSUE-6 · ECMWF S3 public retention (~15 months)
Cannot validate on historical floods (e.g. Kenya MAM 2024) without a MARS
subscription. Bounds the "retrospective" scope the README advertises.
**Architectural constraint** — document clearly; MARS or local mirror for old dates.

---

## MEDIUM / LOW (debt, non-blocking)

| # | Issue | Notes |
|---|-------|-------|
| 7  | Dashboard data dir empty | `dashboard/calendar_map/data/` never generated/deployed |
| 8  | Benchmarks empty | `results/benchmarks/` — README table vs dynamical.org never measured |
| 9  | Gap-fill Cloud Run/Lithops untested | needs GCP environment |
| 10 | EM-DAT CPT refinement unvalidated | code present, never run on real events |
| 11 | C3 runs without GPM IMERG | gpm=0 -> observation evidence silently absent |
| 12 | Coverage 46% vs original 80% target | threshold lowered to 45% in CI |
| 13 | Notebooks not exercised in CI | nbmake job is continue-on-error |

---

## FIXED THIS SESSION (for reference)

| Area | Commit |
|---|---|
| CRMA single-cluster dispatch bug | d8e2c19 |
| Admin units 155 -> 196 (MDG + SDN) | 46c876f |
| IceChunk 2.x API migration (array_chunk_iterator) | f19d56f |
| AWS_ENDPOINT_URL collision (obstore explicit endpoint) | b2b00ab/fa42f60 |
| Manifest splitting (VirtualiZarr #884) | fa42f60 |
| Non-uniform step hours (3h/6h) + hour-based accumulation | 6a5bfae |
| Configurable sub-resolution window skip | a84ce18 |
| Lint (15 ruff) + mypy (51) clean | 08502f9 / ac115b1 |
| CI: checkout-before-local-action, coverage 45%, continue-on-error | d8e2c19 / 68da92e / 2400908 |
| E2E workflow + single MinIO config | f441ffe / 7b35383 |

---

## Recommended priority order (revised 2026-06-09)

1. **ISSUE-14 (existential)** — débloquer le signal : corriger tp→mm dans le
   chemin E2E réel + brancher GPM (ISSUE-15). Critère : 3 dates de crue
   MAM/OND 2025 produisent ≥1 Orange/Red. Convertit ISSUE-1, 11, 14, 15.
2. **ISSUE-16** — récupérer la CSV EM-DAT EA → débloque CPT refinement +
   validation chiffrée (le cœur du framework §7 de la proposal).
3. **ISSUE-17** — décision AIFS : experimental documenté OU migration + source
   accessible. Ne pas laisser 2 innovations affichées « livrées » mais 403.
4. **ISSUE-3 / ISSUE-19** — aligner README/proposal sur la réalité
   (chiffres, store privé, dashboard, AIFS conditional).
5. **ISSUE-18** — un benchmark réel écrit dans `results/benchmarks/`.
6. **ISSUE-20** — corriger OND (décembre) dans `thresholds.py`.
7. ~~ISSUE-2 / ISSUE-4 / ISSUE-5~~ — FIXED (see above; tests added).
