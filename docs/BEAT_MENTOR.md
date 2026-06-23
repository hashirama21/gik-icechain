# Beat-mentor — stratégie, état et feuille de route

> Objectif : rattraper l'`bn-ibf` du mentor (Nishadh Kalladath, ICPAC) sur tous
> les axes méthodologiques où il nous dépassait, **puis** le dépasser sur les
> axes que son stack ne peut pas suivre grâce à notre socle data C1/C2.
>
> Contexte : ECMWF Code for Earth 2026 — Challenge 41 « Missed Opportunities in
> Flood Disaster Risk Management », Africa Stream (ArcX). Ce document consolide
> ce qui est éparpillé dans les audits de [`docs/ISSUES.md`](ISSUES.md).

Dernière mise à jour : 2026-06-23.

---

## 1. Dépôts de référence et leur utilité

| Dépôt | Nature | Utilité pour nous |
|---|---|---|
| [`icpac-igad/crma`](https://github.com/icpac-igad/crma) | Plateforme de **simulation/formation** à la décision (3 actes : Comprendre / Évaluer l'évidence / Décider). Taxonomie evidence **hard/soft/virtual**, cadre couleur Monitor·Evaluate·Assess·Actionable. | Référentiel sémantique du CRMA que notre C3 prétend « intégrer ». Donne le cadre de validation par simulation d'événements. |
| [`nishadhka/bn-ibf @ jua-bnet`](https://github.com/nishadhka/bn-ibf/tree/jua-bnet) | BN pour Impact-Based Forecasting (le **mentor**). `flood_ibf` + `drought_ibf` + white-paper. | **Le benchmark méthodologique direct** que la Phase A/B poursuit. |
| [`…/jua-bnet/flood_ibf`](https://github.com/nishadhka/bn-ibf/tree/jua-bnet/flood_ibf) | BN flood : 5 parents (antecedent_rainfall, exceedance_prob, spatial_coverage, **rainfall_trend**, **tail_risk**) → `risk_level` ; soft-binning gaussien ; DBN (blend α=0.6) ; **per-member storylines** (51 membres) ; cost-loss γ=0.20 hors BN. Moteur RxInfer.jl + contrôle pgmpy. | **Le plan détaillé** de ce qu'on reconstruit — à copier, pas à deviner. |
| [`icpac-igad/DevOps-hazard-modeling`](https://github.com/icpac-igad/DevOps-hazard-modeling) | Couche **ops/prod** : Prefect, k8s CronJob, ArgoCD. Modèles **hydro** : RIM2D, wflow.jl, GEOSFM, sync FTP→GCS de riverdepth/streamflow. | Réponse à nos deux trous : (1) routage hydrologique fluvial (ISSUE-22), (2) blueprint de déploiement opérationnel. |

---

## 2. Tableau de score (où on mène / suit / parité)

| Axe | Mentor | Nous | Statut |
|---|---|---|---|
| Virtual store PB / time-travel as-of-date (C1 IceChunk) | ❌ | ✅ | **On mène** |
| Seuils GEV adaptatifs ENSO/IOD/saison | ❌ (2 ans fixe) | ✅ | **On mène** |
| Multi return-period (6 RP) | ❌ (2 ans seul) | ✅ | **On mène** |
| Apprentissage empirique des CPT (EM-DAT) | ❌ (règles expert) | ✅ | **On mène** |
| AIFS vs IFS (AI-NWP) | ❌ (GEFS = « futur ») | ✅ | **On mène** |
| Poids régionalisés (4 clusters climat) | ❌ | ✅ | **On mène** |
| Dashboard / storymap / EAHW | ❌ | ✅ | **On mène** |
| Validation à grande échelle | ~11 événements | ✅ corpus ~1000 j | **On peut mener** |
| `tail_risk` (A1) | ✅ | ✅ | **Parité** |
| Soft-evidence gaussien (A2) | ✅ | ✅ | **Parité** |
| Cost-loss déclencheur (A3) | ✅ γ=0.20 | ✅ (câblé, default OFF) | **Parité** |
| `rainfall_trend` (node + feed) | ✅ | ✅ | **Parité** |
| Per-member storylines | ✅ 51 membres | ✅ échelle quantiles worst/median | **Parité** |
| Routage hydro / streamflow | ~RIM2D (réf.) | ❌ | **Personne ne le fusionne au BN** |

**Positionnement** : on a rattrapé **tout** ce que le mentor faisait de plus, sans
combattre sur son moteur (RxInfer/Julia). La victoire vient de brancher son BN
sur notre étage C1/C2, qu'aucun autre dépôt ne possède.

---

## 3. Ce qui est livré

### Phase A — combler les écarts méthodologiques · commit `8553c1b`
- **A1 · Tail-risk (possible-worlds)** — `exceedance.compute_tail_ratio` (p95 membre / GEV),
  persisté en `tail_ratio` ; `Forecast_Hazard = max(fraction_state, tail_state)`.
  Config-gated, **default ON**. Escalade le hazard même quand la fraction moyenne ≈ 0
  (cas wet-tail convectif type Nairobi).
- **A2 · Soft-evidence (soft-binning gaussien)** — `SoftEvidenceConfig` (σ par node) +
  `_gaussian_soft_bin` / `_dist_of_max` ; `_infer_soft` marginalise la lookup table.
  σ→0 == hard exactement. **Default OFF** (net-conservateur sur la fenêtre Avr-2024).
- **A3 · Cost-loss decision trigger** — `CostLossConfig` câblé (était orphelin) →
  `_decide_risk_state` ; label = tier le plus sévère dont P(≥tier) ≥ τ (Murphy 1977 ;
  Coughlan de Perez 2015). **Default OFF** (argmax préservé). τ par défaut 0.15/0.25/0.35.

### Phase B — node de parité `Rainfall_Trend`
- **B1 · Node** · commit `16315bc` — 8ᵉ parent de `Compound_Risk`
  (Decreasing / Stable / Increasing) depuis la pente IMERG 7 j. Contribution
  **centrée** `(state-1)·poids` ⇒ **Stable strictement neutre** (calibration legacy
  préservée). Intégré : lookup hard, soft, DBN, refinement EM-DAT. Table 1296→3888.
- **B2 · Feed live** · commit `9aa289a` — `DynamicBNState` porte un buffer glissant
  7 j (`gpm_history`) ; `step` calcule `_trend_slope` (moindres carrés) et l'injecte
  avant inférence. **Node vivant en prod**. Checkpoint v3 (rétro-compatible).
- **B3 · Per-member storylines (échelle de quantiles)** · commit `9adb54d` —
  C2 émet `median_ratio` (p50 = monde médian) via `compute_member_ratio(quantile)`,
  à côté de `tail_ratio` (p95 = pire monde). Le risk engine garde `risk_state` = pire
  monde, rejoue le BN sur le monde médian, et reporte `storyline_median_state` +
  `storyline_spread = max(0, pire − médian)`. Storyline 2-points worst/median
  (cadrage opératoire du mentor), pas un balayage littéral des 51 membres.

**Qualité** (cumulée) : suite unit+intégration verte (217 passed hors fichiers
eccodes), ruff + mypy clean. Commits sur `develop`, auteur `hashirama21`.

---

## 4. Feuille de route — passer devant (Phase C)

Les axes que le stack du mentor ne peut pas suivre, par rendement décroissant :

### C1 · Calibration REV du cost-loss — SHIPPED (outil, default OFF)
Le mentor **devine** γ=0.20. Nous, avec ~1000 jours, on **apprend** les τ qui
maximisent la **Relative Economic Value** (Richardson 2000 ; Wilks) contre EM-DAT.
C'est la métrique exacte du Challenge 41 (« missed opportunities ») et ça attaque
directement le recall@actionable (~0.025). **Avantage asymétrique** : seul notre
corpus C1 permet de calibrer.

Module `risk/cost_loss_calibration.py` : `relative_economic_value` (pure,
REV=1 parfait / 0 sans skill), `calibrate_tier` (balaye τ, maximise REV à la C/L
du palier), `calibrate_cost_loss` → `CostLossConfig` + rapport (τ non-décroissants
imposés pour la contrainte d'ordre), `calibrate_from_risk_dir` (charge les
`*_risk_scores.json` + labels EM-DAT). CLI : `tools.py calibrate-cost-loss`
(advisory — on revoit puis on met `cost_loss.enabled=true` en config). L'inférence
live est inchangée. Reste à faire : tourner sur un corpus réel en rétention pour
publier les τ et l'uplift recall.

### C2 · Fusion hydrologique (streamflow)
Consommer le streamflow/riverdepth de `DevOps-hazard-modeling` (GEOSFM/wflow) comme
node `River_Hazard`. **Seul coup qui relève le recall sur les crues fluviales** — le
plafond structurel (ISSUE-22) qui bride *les deux* stacks.

### C3 · As-of-date skill curves
Productiser le time-travel C1 : « qu'aurait-on su à J-3 ? » → courbes de skill par
lead-time. **Impossible pour le mentor** (pas d'historique versionné). C'est le thème
littéral du challenge.

### C4 · CPT hiérarchique (Dirichlet)
Étendre le refinement EM-DAT avec priors Dirichlet + pooling par cluster — exactement
la « future upgrade » que le mentor annonce. Le battre sur sa propre roadmap.

---

## 5. Limites assumées (honnêteté méthodologique)

- **A2 soft-evidence** : net-conservateur sur la fenêtre no-signal Avr-2024 ; valeur
  attendue sur fenêtres convectives à signal partiel — default OFF jusqu'à preuve.
- **A3 cost-loss** : default OFF tant que les τ ne sont pas calibrés (→ C1).
- **B2 trend** : un jour GPM manquant entre dans le buffer comme 0 mm (cohérent avec
  l'API), léger biais baissier sur fenêtres à obs clairsemées.
- **B3 storylines** : 2-points worst/median, pas un balayage 51-membres (C2 ne
  persiste pas les membres) ; rung p90 trivialement ajoutable.
- **Plafond structurel (ISSUE-22)** : déclencheur pluie-locale → manque les crues
  fluviales non-locales. Levée seulement par C2 (streamflow).
