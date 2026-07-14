# Runbook — fermer les 3 écarts prioritaires avant le 31 août 2026

> Date : 2026-07-13 · Contexte : `docs/PROMESSES_VS_LIVRE_2026-07-13.md`
> Objet : les 3 promesses du dossier de soumission qui sont **vérifiables en
> 5 minutes par un jury** et actuellement non tenues, avec le chemin exact pour
> les fermer.

---

## Résultat de l'exploration : aucun des 3 écarts n'est bloqué techniquement

J'ai sondé le bucket public ECMWF (`ecmwf-forecasts`) en HTTP direct pour
établir la disponibilité réelle des données. **Les trois chantiers sont
faisables ; ce qui manque, c'est de l'exécution, pas du code.** Une seule pièce
de code manquait réellement (la ligne de base statique de l'ablation) : elle est
désormais écrite (voir §3).

### Disponibilité réelle des données (mesurée, pas supposée)

> 🔴 **CORRECTION (2026-07-14).** Une version antérieure de ce document affirmait
> que « la promesse ~1200 jours depuis mai 2023 est physiquement morte, ECMWF a
> purgé 2023 ». **C'était faux.** Le mail de Nishadh Kalladath (09/07, demo6) m'a
> conduit à revérifier : je sondais le préfixe `0p25/`, qui n'existe pas pour 2023.
> Les données 2023 **sont bien là**, sous `0p4-beta/`. Ma conclusion était un
> artefact de mauvais chemin, pas un fait.

Le bucket **n'est pas homogène** — c'est le vrai enjeu :

| Période | Préfixe | Grille |
|---|---|---|
| 2023-01-18 → ~2024-02 | `{date}/00z/0p4-beta/enfo/` | **0,4°** (451 × 900) |
| ~2024-02 → aujourd'hui | `{date}/00z/ifs/0p25/enfo/` | **0,25°** (721 × 1440) |
| AIFS ENS, depuis ~2025-07-05 | `{date}/00z/aifs-ens/0p25/enfo/` | 0,25° |

**Conséquences :**

1. **Le corpus complet (~1200 j, 2023-01-18 → 2026-06-02) est atteignable**, et
   ICPAC en fournit déjà un store IceChunk prêt à l'emploi (demo6). L'objectif D1
   « archive complète + time-travel » est donc **à portée**, contrairement à ce que
   je concluais.

2. **Le coût réel n'est pas la rétention, c'est le changement de grille.** Et il
   cachait un **bug de corruption silencieuse** dans C2 : `_bbox_to_slices`
   calculait les indices sur une grille 0,25° codée en dur, puis les appliquait au
   tableau décodé. Sur du 0,4°, l'index latitude 268 ne désigne pas 23°N mais
   **−17,2°N** → on aurait découpé **la mauvaise région, sans aucune erreur levée**.
   *Corrigé aujourd'hui* : les slices sont dérivées du `Nj`/`Ni` de chaque message
   GRIB (C1 gérait déjà le 0,4°, C2 non). Couvert par
   `TestBboxToSlicesMixedResolution`.

3. **La fenêtre AIFS ∩ IFS = ~370 jours, couvrant les 4 saisons** — la comparaison
   « par saison et phase ENSO » du §7.2 est entièrement réalisable.

4. **`ISSUES.md` (L3) est faux** : il parle d'une rétention de ~15 mois. Il n'y a
   pas de fenêtre glissante qui nous ampute ; il y a deux générations de grille.

> ⚠️ **Avant d'ingérer 2023**, il faut aussi vérifier ce que la grille 0,4° implique
> en aval : seuils GEV (grille de la climatologie), agrégation admin-1, et
> `flood_floor_mm`. Le correctif de ce jour rend le **découpage** correct ; il ne
> garantit pas encore la **cohérence physique** d'un mélange 0,4°/0,25° dans une
> même série temporelle.

---

## Priorité 1 — Piste AIFS (l'écart le plus visible)

**Diagnostic.** La chaîne est **déjà entièrement câblée** dans `run-all`
(`cli.py:1023-1062`) : convert-AIFS → exceedance AIFS → `compute_aifs_ifs_delta`
→ `seasonal_comparison`, avec écriture dans `results/aifs_comparison/`. Une
commande `compare` autonome existe aussi (`cli.py:832`). **Rien à développer.**
Le seul verrou est `aifs_track.enabled: false` dans `configs/default.yaml`.

**Risque résiduel identifié** : `aifs_to_virtual_dataset` utilise encore
`kerchunk.combine.MultiZarrToZarr` au lieu de VirtualiZarr 2.x (résidu
ISSUE-17). Ça fonctionne, mais c'est le point le plus susceptible de casser au
premier run — à tester en premier sur **une seule date** avant de lancer une
fenêtre longue.

### Étapes

Aucun nouveau fichier de config : `run-all` accepte désormais `--aifs`.
`default.yaml` garde `aifs_track.enabled: false` pour que le run quotidien reste
mono-track ; le flag surcharge à l'exécution.

```bash
# 0. Smoke test sur UN jour, avant tout engagement de compute.
#    (2025-11-19 : dans la fenêtre AIFS ∩ IFS, saison humide OND)
python -m gik_icechain run-all --start 2025-11-19 --end 2025-11-19 \
  --aifs --output results/aifs_smoke/
# → si ça casse à l'étape convert(AIFS), c'est le résidu kerchunk : migrer
#   aifs_to_virtual_dataset vers VirtualiZarr avant d'aller plus loin.

# 1. Fenêtre courte, saison humide, les deux tracks (recommandé pour livrer vite)
python -m gik_icechain run-all --start 2025-10-15 --end 2025-12-15 \
  --aifs --output results/aifs_ond2025/

# 2. Si le budget compute le permet, étendre à MAM 2026 pour une comparaison
#    bi-saisonnière (ce que promet le §7.2 : "par saison et phase ENSO")
python -m gik_icechain run-all --start 2026-03-01 --end 2026-05-31 \
  --aifs --output results/aifs_mam2026/
```

**Livrable attendu** : `results/aifs_comparison/aifs_ifs_delta.zarr` +
`seasonal_{MAM,OND,...}.zarr`. C'est ce qui transforme l'Innovation 4 de
« coquille » en résultat.

**Ordre de grandeur du coût** : d'après `RESULT.md`, C1+C2 ≈ 2–10 min/jour selon
le nombre de workers. Une fenêtre de 60 jours × 2 tracks ≈ **quelques heures**,
pas des jours. C'est le meilleur rapport valeur/effort des trois chantiers.

---

## Priorité 2 — Benchmark (la promesse chiffrée la plus facile à vérifier)

**Diagnostic.** `conversion/benchmark.py` est **fonctionnel et honnête** :
`_measure_store_size_gb()` fait un vrai `s3fs.du()` sur le bucket, et
`_bbox_subset()` gère déjà le piège des latitudes décroissantes (qui faisait
silencieusement lire 0 octet). Il n'a simplement **jamais été lancé**.

### Étapes

```bash
python scripts/tools.py benchmark \
  --gik-store s3://<bucket>/gik-icechain-store \
  --n-days 30 \
  --workers 4 \
  --output-dir results/benchmarks/
# → écrit results/benchmarks/benchmark_east_africa_30days.csv
```

**Deux décisions à prendre, et il faut être honnête sur les deux :**

1. **La ligne `dynamical.org`.** Sans `--dynamical-store`, elle n'est pas
   mesurée et la constante `242 TB` reste **un chiffre annoncé par
   dynamical.org, pas une mesure**. Deux options :
   - *(propre)* trouver l'URI publique de leur store IceChunk IFS ENS et passer
     `--dynamical-store s3://...` → la ligne devient mesurée ;
   - *(honnête à défaut)* garder la constante et **l'étiqueter comme telle** dans
     le README — ce que j'ai déjà fait aujourd'hui.

2. **La cible « full-scan < 8 h sur 32 vCPU » (§7.1).** L'extrapolation interne
   de `RESULT.md` donne **~50 h pour 1200 jours** — c'est-à-dire que **le chiffre
   actuellement disponible rate la cible annoncée**. Il faut soit mesurer
   réellement sur 32 vCPU (le chiffre de 50 h vient de runs à 1–4 workers, donc
   il n'est pas comparable), soit corriger la cible dans le rapport final.
   **Ne pas laisser cette contradiction non traitée** : c'est le genre d'écart
   qu'un jury technique repère.

---

## Priorité 3 — Ablation « GEV adaptatif vs statique » (la preuve manquante)

**Diagnostic.** C'est la seule justification de l'Innovation 2, et elle n'existe
pas. Il manquait aussi **le code pour construire la ligne de base statique** :
je l'ai écrit aujourd'hui.

### Ce qui a été ajouté (code, aujourd'hui)

`build_seasonal_thresholds(..., pool_seasons: bool)` + le flag CLI
`--pool-seasons`. Les trois bras de l'ablation écrivent **les mêmes noms de
fichiers**, donc le pipeline C2 charge n'importe lequel **sans modification de
code** — il suffit de pointer `component2.thresholds` vers le bon dossier :

| Bras | Commande | Ce qui varie par cellule |
|---|---|---|
| **adaptatif** (livré) | défauts | saison × ENSO × IOD |
| **saison seule** | `--min-years 999` | saison (bins ENSO/IOD repliés) |
| **statique** (baseline) | `--min-years 999 --pool-seasons` | rien — un seul ajustement sur maxima annuels |

Bascule d'un bras à l'autre à l'exécution : `run-all --thresholds-dir <dossier>`.

Couvert par `TestStaticBaselineArm` dans `tests/unit/test_gpm_seasonal.py`
(le bras statique doit faire **disparaître** l'écart OND/MAM — c'est tout
l'intérêt de la baseline).

### ⚠️ Ordre impératif

**Régénérer les seuils APRÈS le correctif OND de ce jour.** Les NetCDF actuels
de `data/cmorph_thresholds/` ont été ajustés avec `OND = [10, 11]` (décembre
manquant). Valider l'Innovation 2 contre eux reviendrait à **prouver le bénéfice
d'une stratification saisonnière fausse.**

### Étapes

```bash
# 0. PRÉ-REQUIS : GPM IMERG (data/gpm_imerg est vide ici). Nécessite des
#    identifiants Earthdata (cf. docs/earthdata_credentials.py.example).
python scripts/tools.py download-gpm --source nasa --start 2001-01-01 --end 2023-12-31

# 1. Bras ADAPTATIF (avec la saison OND corrigée)
python scripts/tools.py build-thresholds-gpm \
  --start 2001-01-01 --end 2023-12-31 \
  --output data/thresholds_adaptive/

# 2. Bras STATIQUE (la baseline qui n'existait pas)
python scripts/tools.py build-thresholds-gpm \
  --start 2001-01-01 --end 2023-12-31 \
  --output data/thresholds_static/ \
  --min-years 999 --pool-seasons

# 3. Deux runs C2+C3 sur novembre 2024 (données IFS confirmées présentes).
#    --thresholds-dir bascule le bras ; les sorties sont séparées pour que les
#    deux bras ne s'écrasent pas. Aucun fichier de config supplémentaire.
python -m gik_icechain run-all --start 2024-10-31 --end 2024-11-30 \
  --thresholds-dir data/thresholds_adaptive/ \
  --exceedance-store s3://<bucket>/exc-abl-adaptive \
  --risk-output results/abl_adaptive/admin1_risk

python -m gik_icechain run-all --start 2024-10-31 --end 2024-11-30 \
  --thresholds-dir data/thresholds_static/ \
  --exceedance-store s3://<bucket>/exc-abl-static \
  --risk-output results/abl_static/admin1_risk

# 4. Scorer les deux bras contre le MÊME panel satellite
python scripts/satellite_validation.py --risk-dir results/abl_adaptive/admin1_risk
python scripts/satellite_validation.py --risk-dir results/abl_static/admin1_risk
```

**Note** : `satellite_validation.py` est **codé en dur sur novembre 2024**
(`days = [f"2024-11-{d:02d}" ...]`). C'est une contrainte, pas un bug — mais
elle impose que l'ablation se fasse sur cette fenêtre. C'est heureusement la
meilleure vérité terrain disponible (FAO/VIIRS, vrais négatifs).

**Livrable** : deux AUC-ROC comparables. C'est **la** preuve que le §7.2
promettait et qui manque.

> ⚖️ **Honnêteté requise sur le résultat.** Il est parfaitement possible que
> l'ablation montre un gain faible ou nul. Le projet a déjà fait ce choix une
> fois — le soft-evidence a été **désactivé par défaut après avoir mesuré qu'il
> n'aidait pas**. C'est cette rigueur qui donne du poids au reste ; il faut
> publier le résultat de l'ablation quel qu'il soit.

---

## Séquencement recommandé

Par rapport valeur/effort décroissant, et en tenant compte des dépendances :

1. **Benchmark** (heures) — aucune dépendance, ferme une promesse chiffrée,
   le code marche déjà.
2. **AIFS, smoke test 1 jour** (minutes) — révèle immédiatement si le résidu
   kerchunk casse. Puis fenêtre OND 2025 (heures).
3. **Ablation GEV** (jours) — dépend du téléchargement GPM (long) et **doit**
   venir après la régénération des seuils post-correctif OND.

En parallèle, deux corrections de récit à porter dans le rapport final, qui ne
coûtent rien et protègent la crédibilité :

- **Assumer que « ~1200 jours depuis mai 2023 » est impossible** (ECMWF a purgé
  2023) et donner le vrai plafond : ~865 jours. C'est une prémisse fausse du
  dossier, pas un manquement d'exécution.
- **Corriger `ISSUES.md` (L3)** : la rétention est de ~28 mois, pas ~15.
