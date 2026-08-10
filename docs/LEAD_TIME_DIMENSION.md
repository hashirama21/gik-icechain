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
- **Read-side dashboard** : `data_pipeline.pipeline._zonal(..., lead=L)` extrait
  l'échéance demandée, avec repli propre sur `exceedance_prob` si la variable ou
  l'échéance est absente (défaut `lead=None` = comportement actuel).

## Reste à faire (activation)

Ces étapes se font au moment de la bascule (forward-only), quand un store
lead-enabled existe à servir :

1. **Sélecteur d'échéance web** (Step 1) : état `lead` dans `DashboardApp.tsx`,
   contrôle dans `MapTab.tsx`, `LEADS` dans `config.ts`, avec « horizon (max) »
   comme option par défaut ; émettre un `region_risks` par lead dans le contrat.
2. **COG par lead** : `exceedance_cogs` prend un paramètre `lead` (même repli).
3. **Workflow** : activer `emit_lead_dimension: true` dans `daily_update.yaml`.
4. **Scripts** : rendre `merge_exceedance_shards.py` / `repair_exceedance_store.py`
   lead-aware avant tout backfill historique.

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
