# GIK-IceChain v2.0 — Audit externe complet

> Date : 2026-07-13
> Commit audité : `ac3ac07` (228 fichiers, 2018 nœuds de graphe)
> Portée : architecture, validité scientifique, qualité du code (backend + dashboard),
> CI/CD, sécurité, dette technique.
> Méthode : croisement du graphe de connaissances (`graphify-out/GRAPH_REPORT.md`),
> des audits internes déjà existants (`ISSUES.md`, `C3_VALIDATION_FINDINGS.md`,
> `RESULT.md`) et d'une inspection indépendante du code, des tests, des dépendances
> et des workflows CI.

> **Note de méthode.** Le projet note lui-même ses risques sur une échelle
> Vert / Jaune / Orange / Rouge (son propre système d'alerte crue). Par cohérence,
> cet audit reprend la même échelle pour classer ses constats — un clin d'œil
> volontaire, pas un hasard de palette. Légende : 🟩 acquis/mineur · 🟨 à
> surveiller · 🟧 notable · 🟥 critique.

> **Suivi (2026-07-13, même jour).** Voir `docs/ISSUES_NON_RESOLUES_2026-07-13.md`
> pour le détail des corrections apportées depuis : saison OND (corrigé, DRY
> consolidé), topologie fluviale Bakool/Bay (étendue, placeholder signalé),
> tests de persistance des CPT (ajoutés), honnêteté du tableau de benchmark
> (corrigée), code mort (`src/utils/`, `puppeteer-core`, supprimés). Un point
> de cet audit était un faux positif : `lucide-react ^1.24.0` résout
> correctement à l'installation — corrigé ci-dessous.

---

## Chiffres clés

| Indicateur | Valeur |
|---|---|
| Couverture tests backend | **45%** (seuil abaissé, cible initiale 80%) |
| Couverture tests frontend (dashboard) | **0%** |
| Rappel crues Orange+ (post-remédiation, panel FAO/VIIRS) | **51%** (46/91) |
| p_red sur toute alerte Rouge | constant à **0.39** (saturation du réseau bayésien) |
| Issues internes classées « non résolues » (`ISSUES.md`) | **10** |

---

## Verdict en une phrase

GIK-IceChain est un projet scientifique inhabituellement honnête avec lui-même —
`ISSUES.md` et `C3_VALIDATION_FINDINGS.md` documentent déjà la plupart des
faiblesses structurelles avec plus de rigueur qu'un audit externe n'en
produirait — mais cette honnêteté documentaire ne s'étend pas encore au code :
la couverture de test a été abaissée plutôt que le code corrigé, le fichier le
plus gros et le plus risqué (`cli.py`) est explicitement exclu de la
couverture, et le dashboard livré en production n'a aucun test ni gate CI.

---

## Ce qui est solide

🟩 **Auto-critique scientifique rare.** Peu de projets publient un document qui
dit noir sur blanc « le rappel de 29% n'est pas réglable par seuil, c'est une
limite structurelle » (`C3_VALIDATION_FINDINGS.md`) puis mesure l'effet réel
d'un correctif (29%→51% après le levier rivière). C'est le niveau de rigueur
qu'on cherche à obtenir d'un audit, déjà fait en interne.

🟩 **Hygiène de code backend propre.** Aucun `TODO`/`FIXME`/`except:` nu /
`print()` / `eval` / `pickle.load` dans `src/`. Zéro cycle d'import détecté par
le graphe. C'est net pour une base scientifique de 11 000 lignes.

🟩 **Secrets bien tenus.** Aucun secret en clair trouvé dans `.github/` ni
`dashboard/` ; tout passe par `secrets.*`/`vars.*`. `.gitignore` exclut
correctement `.env` et les identifiants Earthdata, avec un modèle d'exemple
documenté.

🟩 **Frontend discipliné malgré l'absence de tests.** 0 `console.log`, 0
`: any`, 0 `dangerouslySetInnerHTML`, `strict: true` réellement respecté dans
le dashboard Next.js. Le déploiement échoue volontairement (`exit 1`) plutôt
que de publier un site vide si le contrat de données est manquant.

---

## Validité scientifique du modèle de risque

Le cœur du projet — le réseau bayésien CRMA (Component 3) — a été validé
contre des données satellite indépendantes (FAO/VIIRS, UNOSAT), pas seulement
contre lui-même. Le tableau ci-dessous vient de `docs/C3_VALIDATION_FINDINGS.md`
et du README ; il montre un vrai avant/après mesurable, pas une promesse.

| Métrique (105 unités admin-1, FAO/VIIRS) | Avant remédiation | Après remédiation |
|---|---:|---:|
| AUC (p_red vs inondation VIIRS) | 0.715 | 0.734 |
| Rappel @ Orange+ | 0.29 (26/91) | 0.51 (46/91) |
| Précision @ Orange+ | 0.96 (26/27) | 0.96 (46/48) |

🟧 **Limite connue — Saturation du réseau bayésien (p_red constant).**
`Risk_State` n'a qu'un seul parent (`Compound_Risk`, 4 états) : dès que ce
parent sature à « High », `p_red` converge vers ≈0.39 pour **toute** alerte
Rouge — une inondation de 13 km² et une de 3 016 km² reçoivent le même score.
Un `severity_score` continu additif a été ajouté en compensation, mais la
structure du réseau reste inchangée « by design ».

🟧 **Limite connue — Plafond de rappel hors zones fluviales couvertes.**
Banadir (Mogadiscio, 562k exposés), Bakool et Bay restent à 0 jour d'alerte :
ils ne sont pas sur la topologie fluviale actuelle
(`data/river_basins/upstream_admin1.yaml`). Le rappel est désormais borné par
la couverture de cette topologie, pas par la structure du modèle — un
problème d'extension de données, pas de méthode.

🟨 **À surveiller — README encore en avance sur la réalité chiffrée.**
`ISSUES.md` (ISSUE-3) documente lui-même l'écart : « ~300 unités admin-1 »
réel = 196–238 selon la version ; « ~1200 jours » réel = 720–737 ; le ratio de
coalescence de bytes annoncé « ~10× » mesuré à 1.04× avant correctif. Le
README a depuis été partiellement corrigé (238 unités, chiffres de rappel
sourcés) mais certains tableaux (benchmark dynamical.org) restent des
constantes codées en dur, jamais mesurées (`results/benchmarks/` vide).

---

## Qualité du code backend (Python, ~11 100 lignes)

Architecture en couches propre (`conversion/` → `exceedance/` → `risk/` →
`shared/`), confirmée par le graphe de connaissances (aucun cycle d'import).
Mais la rigueur scientifique documentée plus haut ne se retrouve pas dans la
politique de test : le seuil de couverture a été **baissé** de 80% à 45%
plutôt que le code testé davantage, et les fichiers les plus complexes sont
ceux qui échappent le plus à la mesure.

| Fichier | Lignes | Constat |
|---|---:|---|
| `cli.py` | 1076 | Le plus gros fichier du projet — **explicitement exclu** de la couverture de test |
| `risk/crma_model.py` | 1067 | God-node confirmé par le graphe : 77 arêtes, cœur de couplage du projet |
| `shared/config.py` | 796 | Config monolithique, tous les composants en dépendent |
| `risk/cpt_refinement.py` | 739 | Non exclu de coverage mais peu exercé (méthode « preuve de concept » selon ISSUES.md) |
| `risk/risk_engine.py` | 628 | Orchestrateur C3, bien testé (agent de test dédié récent) |

🟥 **Critique — Le fichier le plus risqué est hors du filet de sécurité.**
`pyproject.toml` exclut de la couverture `cli.py` (1076 lignes, point d'entrée
de toute exécution en production) ainsi que `gap_filler.py`, `benchmark.py`,
`aifs_discovery.py`, `storage.py`, `validation.py`. Ce sont précisément les
modules d'I/O et de frontière système où une régression silencieuse coûte le
plus cher.

🟨 **À surveiller — mypy partiellement strict.** `check_untyped_defs = true`
mais pas `disallow_untyped_defs` ni `warn_return_any` — des corps de fonction
non typés peuvent cacher de vraies erreurs de type sans que mypy ne les
signale.

🟩 **Mineur — Fichier mort probable.** `src/utils/__init__.py` (vide) vit hors
du package installé `src/gik_icechain/` — scaffolding oublié, sans risque mais
à supprimer.

---

## Dashboard Next.js et pipeline de données associé

Le code est propre (voir « ce qui est solide ») mais entièrement hors du
filet CI : aucun test, aucun script `test` dans `package.json`, et
`deploy-web.yaml` ne fait qu'un *build* avant de publier — pas de lint, pas de
test. L'historique git récent (`c62a373 self-healing prerequisites for the
dashboard deploy chain`, `f62280f fix(ci): repair the dashboard publish and
deploy chain`, deux merges du 2026-07 rien que sur ce sujet) confirme que
cette chaîne a été fragile en pratique, pas seulement en théorie — cohérent
avec L7 du watch-list interne (« dashboard déployé mais vide si les 3 étapes
manuelles sont sautées »).

🟩 **[CORRIGÉ] Faux positif — `lucide-react ^1.24.0` est en fait valide.**
Vérifié le 2026-07-13 par un `npm install` réel : la version résout sans
erreur vers `lucide-react@1.24.0`. L'hypothèse initiale de cet audit
(« Lucide n'a jamais publié de 1.x ») était fausse. Aucune action requise.

🟧 **Notable — Deux stacks cartographiques complètes embarquées.** `leaflet`
et `maplibre-gl` sont tous deux en dépendance directe — doublon de poids de
bundle et de maintenance, sauf si l'un des deux est résiduel et prévu pour
retrait.

🟨 **À surveiller — Fallback `localhost` en dur.** `TITILER_BASE` retombe sur
`http://localhost:8000` si `NEXT_PUBLIC_TITILER_BASE` n'est pas fourni au
build. La CI le surcharge bien via `vars.*` aujourd'hui, mais rien n'empêche
un build de prod silencieusement cassé si cette variable est un jour oubliée
— pas d'échec explicite comme celui déjà en place pour le contrat de données
vide.

🟨 **À surveiller — Dépendance morte probable.** `puppeteer-core` en
devDependency sans usage trouvé dans le code source scanné — à confirmer
avant de le retirer (peut servir à un outil de capture d'écran hors du
périmètre exploré).

---

## CI/CD, sécurité et exploitation

- **7 workflows** (CI, seuils GPM, compaction IceChunk mensuelle, mise à jour
  quotidienne, déploiement dashboard, run E2E, release) — un seul
  `continue-on-error` (job notebooks, documenté et volontaire).
- Le `Dockerfile` ajoute un utilisateur non-root et `eccodes` — bonne
  pratique. Le `docker-compose` local TiTiler utilise l'image
  `titiler:latest` non épinglée (reproductibilité locale seulement, pas un
  risque de prod).
- Politique de coverage lue comme un compromis assumé et documenté (45% au
  lieu de 80%, intégration/notebooks en `continue-on-error` car nécessitant
  MinIO/AWS en direct) — transparent, mais reste une dette réelle tant que le
  chiffre n'est pas remonté.

---

## Dette déjà trackée par l'équipe (`ISSUES.md`) — non résolue à ce jour

Le projet maintient sa propre liste d'issues non résolues ; les reprendre ici
évite de les re-découvrir à tort comme des trouvailles neuves. Point commun :
la plupart sont des contraintes d'infrastructure ou de données (GCP,
subscription ECMWF), pas des bugs de logique.

> **Correction post-audit (2026-07-13).** Une repasse de vérification contre
> l'état actuel du code (et `docs/BEAT_MENTOR.md`, plus récent qu'`ISSUES.md`
> sur ces points précis) a montré que 2 lignes ci-dessous étaient obsolètes
> dans la version précédente de ce document — corrigées et signalées
> **[MIS À JOUR]**.

| Issue | Statut vérifié |
|---|---|
| Benchmark vs dynamical.org jamais mesuré | Constante codée en dur dans le README ; `results/benchmarks/` ne contient qu'un `.gitkeep` — **toujours ouvert** |
| Saison OND exclut décembre (`thresholds.py`) | `Season.OND: [10, 11]` inchangé, le commentaire du code reconnaît lui-même le compromis — **toujours ouvert** |
| Seuil d'exceedance (RP) calibré sur pluie rare, pas sur production de crue (ISSUE-23) | Le fix léger (`soil_conditioned_rp`) est livré mais mesuré inerte sur avril 2024 ; le vrai fix (#1-full, recalcul C2) reste reporté — **toujours ouvert** |
| **[MIS À JOUR]** Calibration du seuil de décision cost-loss (τ Yellow/Orange/Red) | Contrairement à ce qui était écrit précédemment : la calibration REV a été livrée (`risk/cost_loss_calibration.py`, commit `79b554c`, Phase C1) et est **activée par défaut** dans `configs/default.yaml` (τ = 0.14 / 0.19 / 0.19, avec une contrainte non-décroissante documentée en commentaire) — **résolu**, sous réserve que le corpus de calibration inclue encore avril 2024 (partiellement in-sample, noté dans le code) |
| **[MIS À JOUR]** Gap-fill à grande échelle (backfill de l'archive) | Contrairement à ce qui était écrit précédemment (« nécessite un environnement GCP ») : le chemin GCP Cloud Run/Lithops a été abandonné début juin au profit d'AWS (Batch, Lithops-sur-Lambda, ou CLI resume-safe EC2/local — voir `deploy/aws/README.md`, ISSUE-9). Le code est prêt et documenté ; aucune preuve d'une exécution réelle à l'échelle du corpus complet n'a été trouvée dans le dépôt — **infrastructure prête, exécution à confirmer**, pas bloqué par un environnement manquant |
| Raffinement des CPT via EM-DAT | Le mécanisme s'est étoffé depuis (pooling hiérarchique Dirichlet par cluster, jeu d'entraînement GPM×EM-DAT passé de 5 à 966 positifs) mais reste **non branché en production** : `configs/default.yaml` a `use_refined_cpts: false` et aucun `results/validation/refined_cpts.json` n'existe dans le dépôt — **partiellement avancé, toujours pas en prod** |
| Couverture 46% vs cible initiale 80% | `fail_under = 45` inchangé dans `pyproject.toml` — **toujours ouvert** |

### Risques latents non trackés, remontés du watch-list interne

- **Frontière sud à −14.5°** : le sud de Madagascar, une partie du
  Mozambique/Zimbabwe sont `No_Data` par construction — le README avance une
  couverture large alors qu'une partie en est structurellement absente.
- **API de partitionnement IceChunk épinglée à 2.0.5** — cassera
  probablement à la prochaine montée de version majeure.
- **Données partielles silencieuses au-delà de la rétention ECMWF
  (~15 mois)** — membres manquants deviennent `NaN` sans erreur dure,
  seulement gardés par `min_members`.

---

## Priorités recommandées

Classées par effet levier réel, pas par facilité — cohérent avec la méthode
que le projet applique déjà à lui-même dans `C3_VALIDATION_FINDINGS.md`.

1. ~~Vérifier / corriger `lucide-react ^1.24.0`.~~ **Fait — faux positif, rien à corriger.**
2. **Sortir `cli.py` de la liste d'exclusion de couverture.** C'est le plus
   gros fichier et le point d'entrée de production — le seul du top 5
   explicitement non mesuré.
3. **Ajouter un socle de tests + gate CI pour `dashboard/`.** Aujourd'hui :
   0% de couverture, aucun script `test`, déploiement sans lint ni test
   préalable.
4. ~~Étendre la topologie fluviale (`upstream_admin1.yaml`).~~ **Fait
   partiellement (2026-07-13)** — `SOM_Bakool`/`SOM_Bay` ajoutés en
   placeholder ; reste à valider contre GloFAS/HydroBASINS avant usage
   opérationnel (Banadir volontairement laissé de côté, cf.
   `ISSUES_NON_RESOLUES_2026-07-13.md`).
5. **[PARTIEL, 2026-07-13]** Le README ne présente plus le tableau comme un
   run empirique et la constante est sourcée en commentaire ; **reste à
   faire** : quelqu'un avec les identifiants de prod doit réellement lancer
   `scripts/tools.py benchmark`.
6. ~~Corriger `Season.OND` pour inclure décembre.~~ **Fait (2026-07-13)** —
   voir `ISSUES_NON_RESOLUES_2026-07-13.md` pour le détail (dict dupliqué à
   3 endroits, consolidé en une seule source). Les NetCDF pré-calculés
   restent à régénérer.

---

*Sources : `graphify-out/GRAPH_REPORT.md`, `docs/ISSUES.md`,
`docs/C3_VALIDATION_FINDINGS.md`, `RESULT.md`, `README.md`, inspection directe
de `pyproject.toml`, `.github/workflows/`, `dashboard/web/package.json` et du
code source aux commits jusqu'à `ac3ac07` (2026-07-13).*
