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

**Contrainte** : l'append refuse d'introduire une variable absente du store
(garde-fou anti-corruption, cf. `writer._align_append_schema` et
`test_introducing_lead_on_legacy_store_raises`). Activer `emit_lead_dimension: true`
sur un store existant qui n'a pas encore `exceedance_prob_by_lead` casserait donc
le job quotidien à l'étape append.

**Chemin retenu — wipe + recompute complet.** Les stores par ère
(`exceedance-zarr`, `exceedance-zarr-0p4`) sont **vidés puis entièrement
recalculés avec toutes les variables**. Comme le premier write d'un store vide est
un `mode="w"` (cf. `write_exceedance_store` : `FileNotFoundError` → création), chaque
store est créé en portant `exceedance_prob_by_lead` dès l'origine ; les merges/append
suivants trouvent la variable et s'alignent. Le flag est donc activé globalement :

- `configs/default.yaml` : `emit_lead_dimension: true` (hérité par `configs/realtime.yaml`,
  utilisé à la fois par `daily_update.yaml` et `backfill.yaml`).
- Procédure : (1) supprimer les zarrs de prod ; (2) redispatcher `backfill.yaml` sur
  toute la plage (shards → `merge_exceedance_shards.py`, déjà lead-aware) ; (3) le daily
  reprend l'append en portant la variable.

Le dashboard s'auto-active : dès que `dependency.json` porte `gev_by_lead`, le sélecteur
d'échéance apparaît (aucune bascule web à faire).

## Sémantique & calibration (à trancher avant remplacement)

`exceedance_prob` (max-horizon) répond à *« P(un pas quelconque de l'horizon
dépasse) »*, pas *« P(dépassement à un jour valide donné) »* : l'exceedance
gonfle mécaniquement avec la longueur de l'horizon (biais de sélection sur les
~40–85 pas). La vue `by_lead` ne souffre pas de ce biais **par jour**. Tant que
les deux coexistent, aucune re-calibration GEV n'est requise ; un éventuel
remplacement de la vue par défaut imposerait de re-valider AUC/precision/recall
(cf. `docs/C3_VALIDATION_FINDINGS.md`).

## Backfill

Recompute **complet** : les deux stores par ère sont vidés puis recalculés sur
toute la plage (2023-01-18 →), chaque date portant `exceedance_prob_by_lead`.
Couvre donc aussi les storyboards historiques (lead par carte disponible). RP=50 de
l'échelle de Jessica reste hors périmètre (refit GEV, bloqué sur les secrets
Earthdata, cf. `docs/LEAD_TIME_10D.md`).
