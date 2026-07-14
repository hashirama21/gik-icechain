# GIK-IceChain — Issues non résolues (état vérifié au 2026-07-13)

> Liste de suivi consolidée : reprend `docs/ISSUES.md` (auto-audit interne,
> dernière mise à jour 2026-06-18) + les constats indépendants de
> `docs/AUDIT_EXTERNE_2026-07-13.md`, avec chaque ligne **revérifiée
> directement dans le code** au commit `ac3ac07` — pas de recopie aveugle.
> Les points déjà résolus (calibration cost-loss, gap-fill retargeté AWS, etc.)
> ont été retirés d'ici ; voir l'audit complet pour le détail de ce qui a
> changé et pourquoi.
>
> Format : `- [ ]` = ouvert, `- [x]` = résolu depuis mais gardé pour mémoire.

---

## 🟥 Critique

- [ ] **`cli.py` (1076 lignes, point d'entrée de production) exclu de la couverture de test**
  `pyproject.toml` → `[tool.coverage.run] omit` liste `*/cli.py` avec 8 autres
  modules d'I/O/CLI (`gap_filler.py`, `benchmark.py`, `aifs_discovery.py`,
  `aifs_track.py`, `icechunk_output.py`, `storage.py`, `logging.py`,
  `validation.py`). C'est le fichier le plus gros du dépôt et il n'est pas
  mesuré du tout.

- [x] ~~`lucide-react: "^1.24.0"` — version probablement inexistante~~ **[CORRIGÉ - c'était un faux positif]**
  Vérifié le 2026-07-13 : `npm install` résout réellement `lucide-react@1.24.0`
  sans erreur. L'hypothèse de l'audit initial (Lucide n'aurait jamais publié
  de 1.x) était fausse. Aucune action nécessaire.

- [ ] **Dashboard (`dashboard/web/`) : 0% de couverture de test, aucun gate CI**
  Pas de fichier `*.test.*`/`*.spec.*`, pas de script `test` dans
  `package.json`, et `.github/workflows/deploy-web.yaml` ne fait qu'un
  `npm run build` avant de publier — pas de lint ni de test avant mise en prod.

---

## 🟧 Élevé

- [ ] **Seuil d'exceedance (RP) calibré sur pluie rare, pas sur production de crue réelle** *(ISSUE-23)*
  Le déclencheur demande « la pluie prévue est-elle un extrême 5 ans ? »
  alors que les crues réelles viennent de pluie modérée sur sol saturé.
  Fix léger (`soil_conditioned_rp`) livré mais mesuré **inerte** sur
  avril 2024 (0 changement) ; le vrai fix (#1-full, recalcul C2 avec un seuil
  sub-RP2) reste à faire.

- [ ] **[PARTIEL]** ~~Benchmark vs dynamical.org jamais mesuré réellement~~
  Toujours pas de run réel (aucun accès aux identifiants MinIO/S3 de prod
  depuis cet environnement), mais corrigé au niveau documentation/honnêteté :
  le README ne présente plus le tableau comme un run empirique ("Preliminary
  benchmarks" → "Order-of-magnitude reference figures, not yet an empirical
  run"), le lien mort vers `notebooks/04_benchmark_report.ipynb` (fichier
  supprimé depuis) est corrigé vers `gik_icechain_walkthrough.ipynb`, et
  `_DYNAMICAL_STORE_FULL_GB` est maintenant commenté avec sa source (chiffre
  avancé par dynamical.org, non mesuré indépendamment). **Reste à faire** :
  quelqu'un avec les identifiants de prod doit lancer
  `python scripts/tools.py benchmark` pour de vrai.

- [ ] **Saturation du réseau bayésien : `p_red` constant (≈0.39) sur toute alerte Rouge**
  `Risk_State` n'a qu'un seul parent (`Compound_Risk`, 4 états) — une
  inondation de 13 km² et une de 3016 km² reçoivent le même score. Le
  `severity_score` continu compense en affichage mais la structure du réseau
  n'a pas changé. *(Non traité dans cette passe — refonte du réseau bayésien,
  hors périmètre d'une correction ponctuelle.)*

- [ ] **[PARTIEL]** ~~Rappel plafonné hors topologie fluviale couverte~~
  `SOM_Bakool` et `SOM_Bay` ajoutés à `data/river_basins/upstream_admin1.yaml`
  (routage géographique plausible via les hauts-plateaux éthiopiens/Hiiraan/
  Gedo, cohérent avec le style de curation existant du fichier), marqués
  **placeholder non vérifié contre GloFAS/HydroBASINS** dans un commentaire
  inline. Banadir (Mogadiscio) volontairement **non** raccroché au mécanisme
  fluvial : sa faille est documentée comme coastal/urbaine
  (`C3_VALIDATION_FINDINGS.md` §5), pas fluviale — le forcer via cette YAML
  aurait été un correctif de façade. **Reste à faire** : validation
  hydrologique de Bakool/Bay par quelqu'un du domaine avant usage opérationnel ;
  un vrai nœud de risque côtier pour Banadir reste à construire.

- [ ] **Deux stacks cartographiques complètes dans le dashboard**
  `leaflet` et `maplibre-gl` sont tous deux en dépendance directe de
  `dashboard/web/package.json` — doublon de poids de bundle, sauf retrait
  prévu de l'un des deux.

---

## 🟨 Moyen

- [ ] **Couverture de test backend abaissée (45%), pas remontée**
  `pyproject.toml`: `fail_under = 45` (cible initiale documentée : 80%).
  Seuil CI aligné (`--cov-fail-under=45`).

- [x] ~~**Saison `OND` exclut décembre**~~ **[CORRIGÉ]**
  `SEASON_MONTHS` (`exceedance/thresholds.py`) corrigé : `OND: [10, 11, 12]`,
  `DJF: [1, 2]`. Au passage, la définition était **dupliquée à 3 endroits**
  (`exceedance/thresholds.py`, `thresholds/gpm_seasonal.py` — bug identique
  copié-collé —, et `scripts/tools.py` — qui, lui, avait déjà la bonne
  définition `[10, 11, 12]` en dur) ; consolidé en une seule source de
  vérité (`SEASON_MONTHS`, désormais publique) que les deux autres modules
  importent. Test de régression `test_december_is_djf_not_ond` inversé en
  `test_december_is_ond_not_djf`. **Reste à faire** : les NetCDF
  pré-calculés dans `data/cmorph_thresholds/` (non présents dans ce dépôt)
  ont été ajustés sur l'ancienne définition de saison et devront être
  régénérés (`build-thresholds-gpm`) pour refléter la frontière corrigée —
  ce n'est pas un correctif de code, c'est un recalcul de climatologie.

- [ ] **[PARTIEL]** ~~Raffinement des CPT via EM-DAT non branché en production~~
  Le branchement production (`cli.py:_run_risk`, `use_refined_cpts`/
  `cpt_path`) était déjà correctement écrit et défensif — vérifié, pas de bug
  là. Le vrai trou : `save_cpts`/`load_cpts` (le mécanisme dont dépend ce
  flag) n'avait **aucun test**. Ajouté dans `test_crma_model.py` :
  round-trip save→load identique, chargement depuis un dict, invalidation du
  cache DBN, garde-fou avant `build()`. **Reste à faire** (hors de portée
  ici) : `data/gpm_imerg/` est vide dans cet environnement — impossible d'
  exécuter le vrai calibrage EM-DAT et de produire un
  `results/validation/refined_cpts.json` réel. Activer `use_refined_cpts` en
  production sans ce run réel serait irresponsable pour un système d'alerte
  humanitaire ; le flag reste donc `false` intentionnellement.

- [ ] **mypy partiellement strict**
  `check_untyped_defs = true` mais pas `disallow_untyped_defs` ni
  `warn_return_any` — des corps de fonction non typés peuvent cacher de
  vraies erreurs.

- [ ] **Fallback `http://localhost:8000` en dur pour `TITILER_BASE`**
  `dashboard/web/src/lib/config.ts` — la CI surcharge bien la variable
  aujourd'hui, mais aucun échec explicite ne protège un build de prod si
  elle est un jour oubliée (contrairement au contrat de données, qui lui
  échoue volontairement s'il est vide).

- [ ] **Gap-fill à grande échelle : infra prête, exécution réelle non confirmée**
  Le chemin GCP a été abandonné début juin au profit d'AWS (Batch,
  Lithops-sur-Lambda, CLI resume-safe EC2/local — `deploy/aws/README.md`).
  Le code est prêt et documenté mais aucune preuve d'un run réel à pleine
  échelle du corpus n'a été trouvée dans le dépôt.

- [ ] **Catalogue ingéré (~730 j) très en deçà du corpus disponible (~1200 j)**
  Le bucket ECMWF remonte à **2023-01-18** — la cible « ~1200 jours » du dossier
  est bien atteignable (ICPAC fournit même un store IceChunk prêt, demo6). Le
  frein n'est pas la rétention mais le **changement de grille** : avant ~2024-02
  les données sont en **0,4°** (`0p4-beta/`, 451×900), après en **0,25°**
  (721×1440). Le découpage bbox de C2 est corrigé (dérivé du GRIB), mais la
  cohérence physique d'une série mixte 0,4°/0,25° (seuils GEV, agrégation
  admin-1) reste à valider avant d'ingérer 2023.

- [ ] **Bug de grille en amont : `ISSUES.md` (L3) affirme une rétention ~15 mois**
  Faux. Il n'y a pas de fenêtre glissante ; il y a deux générations de grille.
  À corriger dans `ISSUES.md`.

---

## 🟩 Mineur / dette légère

- [x] ~~`src/utils/__init__.py` — fichier mort~~ **[SUPPRIMÉ]**
  Fichier vide, hors du package installé, retiré.
- [x] ~~`puppeteer-core` (devDependency du dashboard) — aucun usage trouvé~~ **[SUPPRIMÉ]**
  Confirmé inutilisé, retiré de `package.json` ; `package-lock.json`
  régénéré (`npm install`).
- [ ] Livrables de déploiement partiels (ISSUE-19) : store public AWS Open
  Data et TiTiler Lambda documentés (`deploy/aws/README.md`) mais pas
  confirmés déployés à ce jour.

---

## ⚪ Contraintes architecturales (non « corrigeables », à documenter)

- [ ] **Rétention publique ECMWF S3 (~15 mois)** — bloque la validation sur
  crues historiques anciennes sans abonnement MARS ou miroir local.
- [ ] **Frontière sud à −14.5°** — sud de Madagascar (MDG seulement 3/22
  unités in-domain), une partie du Mozambique/Zimbabwe en `No_Data` par
  construction ; pas de climatologie CHIRPS GEV sud construite.
- [ ] **API de partitionnement IceChunk épinglée à 2.0.5**
  (`ManifestSplittingConfig`) — cassera probablement à la prochaine montée
  de version majeure d'IceChunk.
- [ ] **Données partielles silencieuses au-delà de la rétention ECMWF** —
  membres manquants → `NaN`, gardé seulement par `min_members`, pas
  d'erreur dure.

---

*Méthode : chaque ligne a été vérifiée par lecture directe du fichier source
cité (pas une recopie de `docs/ISSUES.md`). Items retirés parce que résolus
depuis la dernière revue : calibration cost-loss τ (commit `79b554c`,
activée par défaut), correction du préfixe S3 AIFS, coalescing byte-range,
tolérance aux échecs de fetch, publication du dashboard depuis S3 — détail
dans `docs/ISSUES.md` (§ Solved) et `docs/AUDIT_EXTERNE_2026-07-13.md`.*

---

## Corrections apportées le 2026-07-13 (cette session)

Aucun des tests ci-dessous n'a pu être exécuté dans cet environnement
(pas de venv Python 3.12 avec les dépendances du projet installées) — chaque
changement a été vérifié par compilation syntaxique (`py_compile`) et par
relecture manuelle de la logique. **À faire côté utilisateur avant de
merger** : `pytest tests/unit/test_thresholds.py tests/unit/test_crma_model.py
tests/unit/test_gpm_seasonal.py -v`.

| Fichier | Changement |
|---|---|
| `src/gik_icechain/exceedance/thresholds.py` | `SEASON_MONTHS` public, corrigé (OND inclut décembre), source unique |
| `src/gik_icechain/thresholds/gpm_seasonal.py` | Suppression du dict dupliqué, import depuis `exceedance.thresholds` |
| `scripts/tools.py` | `_classify_years_enso_iod` réutilise `SEASON_MONTHS[Season.OND]` au lieu d'un `[10, 11, 12]` en dur |
| `tests/unit/test_thresholds.py` | Assertions corrigées (`get_season(12) == Season.OND`) |
| `tests/unit/test_crma_model.py` | + `TestCPTPersistence` (4 tests : round-trip, dict-load, invalidation cache, garde-fou) |
| `data/river_basins/upstream_admin1.yaml` | + `SOM_Bakool`, `SOM_Bay` (placeholder signalé, non vérifié) |
| `src/gik_icechain/conversion/benchmark.py` | `_DYNAMICAL_STORE_FULL_GB` sourcé en commentaire |
| `README.md` | Tableau de benchmark honnête (estimation, pas mesure) ; lien notebook mort corrigé |
| `src/utils/__init__.py` | Supprimé (mort) |
| `dashboard/web/package.json` | `puppeteer-core` retiré ; lockfile régénéré |

**Correction d'un faux positif de l'audit précédent** : `lucide-react@^1.24.0`
a été revérifié par un `npm install` réel — la version résout correctement.
L'affirmation initiale (« Lucide n'a jamais publié de 1.x ») était fausse.
