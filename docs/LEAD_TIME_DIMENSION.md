# Dimension lead time (init date vs jour valide)

Réponse à la revue de Jessica : une prévision a deux axes de temps — la **date
d'initialisation** (base date) et le **lead time / jour valide**. Ce document
décrit la sémantique retenue et le chemin d'activation forward-only.

## Deux vues, coexistantes (0 régression)

- **`exceedance_prob`** — vue **max sur l'horizon** (historique, inchangée) :
  `tp.max(dim=step)`, le pire pas n'importe où dans l'horizon de prévision.
  Une prévision = une valeur par (date d'init, window, RP).
- **`exceedance_prob_by_lead`** — vue **par jour valide** (nouvelle, opt-in) :
  ajoute une dimension `lead`. Lead 0 = les 24 premières heures après
  l'initialisation, lead 1 = 24–48 h, etc. Au sein de chaque jour valide on
  garde le pire pas (même esprit « pire cumul », borné au jour).

Les deux variables coexistent dans le store ; `exceedance_prob` n'est jamais
remplacée. La réduction est centralisée dans
`exceedance.reduce_over_horizon(da, mode="max_horizon" | "by_lead")`.

Mapping pas → lead : `lead_day = floor((forecast_hour - 1) / 24)`.

## Ce qui est livré (Phase 2 — backend + read-side)

- **Calcul** : `compute_exceedance_probabilities(..., by_lead=True)` produit la
  vue par lead ; `reduce_over_horizon` est réutilisé par les 3 sites
  (exceedance, tail ratio, ensemble confidence — ces deux derniers restent
  `max_horizon`).
- **Store** : `writer.write_exceedance_store(..., lead_dict=...)` écrit
  `exceedance_prob_by_lead` (dim `lead`) avec réconciliation append lead-aware
  (comme window/return_period). Schéma : voir le docstring de `writer.py`.
- **Config** : `component2.emit_lead_dimension` (défaut `false`) et la propriété
  `effective_max_lead_days`. Flag exposé dans `configs/default.yaml`.
- **Pipeline quotidien** : `cli._process_exceedance_day` calcule aussi la vue par
  lead quand le flag est actif, et `_run_exceedance` la persiste.
- **Read-side dashboard** :
  - `_zonal(..., lead=L)` extrait une échéance ; `_zonal(..., with_leads=True)` émet
    en un seul passage un bloc `gev_by_lead` par unité (réutilise les masques
    polygonaux). `dependency()` l'attache à `dependency.json` **quand le store
    porte la variable** (0 coût / 0 sortie sinon).
  - `exceedance_cogs(..., lead=L)` écrit `exceedance_{date}_{w}_{rp}_L{lead}.tif`
    (repli propre sur le max-horizon si la variable/échéance est absente).
- **Sélecteur d'échéance web** : `DependencyPanel` affiche une sous-sélection
  d'échéance (étape ① : « Max » + L0…Ln) **seulement** quand `gev_by_lead` est
  présent ; « Max » (max-horizon) est le défaut. La sévérité par fenêtre (③) et
  le panneau GEV (④) suivent l'échéance choisie (`sevFromGev` réplique `_sev`).
  `LEAD_MAX`/`LeadChoice` dans `config.ts`, `gev_by_lead?` dans `api.ts`.
- **Scripts** : `merge_exceedance_shards.py` propage `exceedance_prob_by_lead`
  (sinon la variable serait droppée au merge d'un shard).
  `repair_exceedance_store.py` est déjà générique (il pade toute variable ayant un
  axe `date`) — aucun changement nécessaire.

## Activation en production (⚠ pas un simple flip de flag)

Le store de prod (`exceedance-zarr`, deux ères) contient déjà ~1264 dates **sans**
la variable `exceedance_prob_by_lead`. Or l'append refuse d'introduire une variable
absente du store (garde-fou anti-corruption, cf. `writer._align_append_schema` et
`test_introducing_lead_on_legacy_store_raises`). **Activer `emit_lead_dimension: true`
tel quel casserait le job quotidien** à l'étape append.

Chemin sûr (forward-only), au choix :

1. **Nouveau store lead-enabled** : écrire les dates à venir dans un store neuf
   (`exceedance-zarr-lead`), servir les deux le temps de la transition, basculer le
   dashboard quand la couverture est suffisante. Le plus sûr, zéro risque sur
   l'existant.
2. **Réécriture ponctuelle** : recalculer/réécrire le store (`append=False`) avec
   `emit_lead_dimension: true` pour intégrer la nouvelle variable, puis reprendre
   l'append quotidien. Implique un backfill (coût runner, par sous-plages).

Une fois un store lead-enabled disponible, activer `emit_lead_dimension: true` dans
`configs/realtime.yaml` (daily) et brancher le dashboard (dependency/COG lead déjà
prêts). Tant que ce store n'existe pas, le flag reste `false` : toute la plomberie
est gracieuse et sans effet.

## Sémantique & calibration (à trancher avant remplacement)

`exceedance_prob` (max-horizon) répond à *« P(un pas quelconque de l'horizon
dépasse) »*, pas *« P(dépassement à un jour valide donné) »* : l'exceedance
gonfle mécaniquement avec la longueur de l'horizon (biais de sélection sur les
~40–85 pas). La vue `by_lead` ne souffre pas de ce biais **par jour**. Tant que
les deux coexistent, aucune re-calibration GEV n'est requise ; un éventuel
remplacement de la vue par défaut imposerait de re-valider AUC/precision/recall
(cf. `docs/C3_VALIDATION_FINDINGS.md`).

## Backfill

Bascule **forward-only** : le daily écrit la dimension lead à J+0. L'historique
(1264 dates) n'est pas recalculé par défaut — un store écrit sans la variable
doit être **réécrit** (append refuse d'introduire une variable absente, par
sécurité). À planifier seulement si les storyboards **historiques** doivent
afficher le lead par carte. RP=50 de l'échelle de Jessica dépend d'un refit GEV
(bloqué sur les secrets Earthdata, cf. `docs/LEAD_TIME_10D.md`).
