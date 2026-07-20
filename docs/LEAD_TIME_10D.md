# Extension de l'horizon à 10 jours (240h)

## Le constat (2026-07-20)

ECMWF publie chaque run IFS ENS jusqu'à 360h (15 jours), et
`ecmwf_direct.py` connaît déjà tout cet horizon (`INDEX_STEP_HOURS` va de
3h à 360h). Mais le daily n'exploite que ce qu'il faut pour sa plus longue
fenêtre d'accumulation configurée - `windows_h` s'arrête à `168h` (7 jours)
dans `configs/default.yaml`, donc seuls ~29 des 85 pas d'un run sont
récupérés. Aucune donnée n'est ignorée au niveau du bucket ECMWF ; c'est la
fenêtre de calcul qui est plus courte que l'horizon publié.

Les seuils GEV calibrés (`data/cmorph_thresholds/thresholds_*.nc`) suivent
le même plafond : rien au-delà de `168h`.

## Ce qui est prêt (cette session)

- **Profil opt-in `extended_10d`** dans `configs/default.yaml` :
  `windows_h: [3, 6, 12, 24, 48, 72, 168, 240]`, `max_forecast_h: 240`.
  N'affecte rien tant qu'il n'est pas sélectionné via `--profile extended_10d`
  (le profil par défaut, `null`, reste inchangé - aucune régression).
- **`scripts/tools.py build-thresholds-gpm --windows-h ...`** : la
  calibration accepte maintenant une liste de fenêtres explicite (avant :
  fixée en dur à `[3,6,12,24,48,72,168]`).
- **`.github/workflows/build-thresholds.yaml`** : nouvel input `windows_h`
  (défaut inchangé), transmis à la commande ci-dessus.
- **Fix latent corrigé** : `dashboard/data_pipeline/pipeline.py::_zonal`
  indexait les fenêtres **par position** (`enumerate(WINDOWS_H)`) plutôt
  que par sélection nommée - ajouter `240h` à `WINDOW_LABELS` sans ce fix
  aurait décalé silencieusement toutes les fenêtres existantes dès que le
  store ne les avait pas encore. Corrigé en `.sel(window=wh)` avec saut
  propre si la fenêtre est absente du store (testé : `test_dashboard_zonal.py`).

## Blocage identifié : pas de credentials NASA Earthdata

`EARTHDATA_USER` / `EARTHDATA_PASSWORD` ne sont pas dans les secrets du
repo - `build-thresholds.yaml` échoue à l'étape de téléchargement GPM
IMERG tant qu'ils n'existent pas. Décision utilisateur (2026-07-20) :
GPM IMERG reste la source (cohérence avec les seuils existants), à
calibrer une fois les secrets ajoutés - pas de repli sur CHIRPS.

## Checklist d'activation (quand vous êtes prêt)

1. **Ajouter les secrets** : Settings → Secrets and variables → Actions →
   `EARTHDATA_USER`, `EARTHDATA_PASSWORD` (compte Earthdata autorisant
   l'application "NASA GESDISC DATA ARCHIVE").
2. **Dispatcher `build-thresholds.yaml`** avec
   `windows_h: 3,6,12,24,48,72,168,240`. Le runner est plafonné à 6h et ne
   persiste rien entre les runs : pour tout l'historique (2001-2023),
   plusieurs dispatches par sous-plage (une année à la fois, comme déjà
   documenté dans le workflow). Chaque run produit un artefact zip
   `gpm-thresholds-<start>_<end>` - **pas de dépôt/upload automatique**.
3. **Fusionner et publier** : rassembler les `.nc` des artefacts dans
   `data/cmorph_thresholds/`, vérifier la couverture (fichiers `*_240h.nc`
   pour chaque combinaison saison × ENSO × IOD), puis republier sur le
   dataset HuggingFace `E4DRR/virtualizarr-stores` (nécessite un token HF
   en écriture - absent aujourd'hui, cf. `docs/BACKFILL_RUN_2026-07-16.md`
   pour un blocage similaire déjà rencontré).
4. **Valider** avant bascule : comparer `AUC`/`Precision`/`Recall` du
   nouveau signal 10 jours contre l'existant sur une fenêtre connue
   (ex. Deyr 2024), comme pour `docs/C3_VALIDATION_FINDINGS.md`.
5. **Activer** : `--profile extended_10d` dans `daily_update.yaml` (ou
   promouvoir `windows_h`/`max_forecast_h` dans le profil par défaut si le
   comité valide un remplacement plutôt qu'un opt-in).

## Coût attendu une fois activé

Le fetch quotidien passe de ~29 à ~85 pas GRIB par jour (le run entier,
déjà couvert par `INDEX_STEP_HOURS`) : C2 quotidien passerait de ~5 min à
~15 min sur un runner GitHub Actions - large marge sous le plafond de
30 min du job `update-exceedance`.
