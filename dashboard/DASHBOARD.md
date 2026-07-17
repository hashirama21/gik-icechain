# GIK-IceChain - Dashboard Component Architecture

> Design de référence du composant *dashboard*, aligné sur les livrables
> exigés par la proposition Code-for-Earth (calendar-map + **VEDA-UI MDX
> storymaps + TiTiler**) et sur le template visuel `GIK-IceChain-Dashboard-v4.html`.

## Modèle mental : 2 surfaces, 1 contrat de données

Le projet exige **deux** surfaces, branchées sur les **mêmes** sorties pipeline :

| Surface | Quoi | Tech (exigée) | Hébergement |
|---|---|---|---|
| **A. Dashboard shell** | Calendrier + carte admin-1 interactive + chaîne de dépendance 5 étapes | **= template v4** (Leaflet, statique) | GitHub Pages (gratuit) |
| **B. Storymap par jour/événement** | Récit scrollytelling + couches raster (risque, exceedance, GPM, EM-DAT) | **VEDA-UI + MDX + TiTiler + STAC + COG** | Pages (UI) + Lambda (TiTiler, ~$0 idle) |

Le bouton `📖 Storymap` du v4 (`openSM(...)`) est le **pont** A → B. A et B ne sont
pas concurrents : ce sont les deux livrables, alimentés par le même pipeline.

## Architecture cible

```
                 PIPELINE (C1→C2→C3)  - déjà fonctionnel
   IceChunk store ──► C2 exceedance Zarr ──► C3 risk GeoJSON + risk_scores.json
                                 │                        │
                                 ▼                        ▼
                    ┌─────────────────────────────────────────────┐
                    │  build_dashboard_data.py  (exporteur unique) │
                    │  = generate_storymaps.py étendu              │
                    └───────────────┬───────────────┬─────────────┘
            STATIQUE (GitHub Pages) │               │ RASTER (S3 public + TiTiler)
        ┌───────────────────────────┘               └───────────────────────────┐
        ▼                                                                         ▼
  data/geojson/{code}.json      data/{date}/region_risks.json        cogs/risk_{date}.tif
  data/{date}/dependency.json   data/index.json                      cogs/exceedance_{date}_{win}_{rp}.tif
        │                                                            stac/catalog → items
        ▼                                                                         ▼
  ┌──────────────────────────┐                              ┌───────────────────────────────┐
  │  A. DASHBOARD SHELL (v4)  │ ── 📖 Storymap ───────────► │  B. VEDA-UI MDX STORYMAP       │
  │  Leaflet · vecteur · Pages│                              │  <Map> ◄── TiTiler ◄── COG/STAC │
  └──────────────────────────┘                              └───────────────────────────────┘
```

**Préservation du « zéro-coût » :** la couche risque admin-1 est **vectorielle
(GeoJSON)** → le shell A ne touche jamais TiTiler. TiTiler/COG ne servent que les
**rasters** (exceedance gridé, GPM, extent EM-DAT) dans la storymap B ; Lambda est
facturé à l'invocation (≈ 0 € au repos).

## Échelle du calendrier : étendue d'alerte, pas max

Le calendrier de l'archive était coloré par `worst_risk` (max des 238
unités). À l'échelle de l'archive complète (1 270 jours), ce max sature :
le fond chronique riverain (Sudd, Shabelle, Nil Blanc) maintient une
médiane de **25 unités Orange+/jour**, donc au moins une unité Red existe
presque chaque jour — 79 % de jours « Red », 0 % « Green », aucun pouvoir
discriminant. Le risque par unité, lui, est sain (4-12 % d'unités Red un
jour ordinaire, précision 0,96 à Orange+ sur la validation satellite).

Correctif (2026-07-17) : `index.json` porte `n_orange` / `n_red` par jour
(`update_index`), et le calendrier classe par **étendue** = unités Orange+
du jour, seuils ancrés sur les quartiles de l'archive (médiane 25, p95 51) :

| Classe | Unités Orange+ | Part de l'archive |
|---|---|---|
| vert | < 15 | 29 % |
| jaune | 15-29 | 31 % |
| orange | 30-49 | 33 % |
| rouge | ≥ 50 | 7 % |

Seuils dans `web/src/lib/risk.ts` (`EXTENT_THRESHOLDS`). Les entrées
d'index sans compteurs (anciens contrats) retombent sur `worst_risk`.
Le `worst_risk` reste exposé (tooltip, chip « PEAK » des storymaps).

## Contrat de données : sortie pipeline → couche

| Couche (template.mdx) | Source pipeline | Producteur | Statut |
|---|---|---|---|
| `risk-state` (COG 0-3, colormap `risk_levels`) | `risk_scores.json` → `rasterise_risk_geojson` | `generate_storymaps.py` | existe, à brancher |
| `exceedance-24h` (COG 0-1, `ylorrd`) | C2 Zarr `exceedance_prob[win,rp]` | export Zarr→COG par win×rp | **à ajouter** |
| GPM IMERG observé | `data/gpm_imerg/{date}` → COG | export → COG | **à ajouter** |
| EM-DAT overlays | `data/emdat/east_africa_floods.csv` → GeoJSON | léger | facile |
| Admin-1 vecteur (shell A) | `admin1_boundaries.geojson` | découpe par `country` | facile |
| STAC items | `generate_stac_item` | étendre aux 2 collections | partiel |

### Schémas réels (vérifiés)

`results/admin1_risk/{date}_risk_scores.json` :
```json
{ "date": "2025-11-20",
  "units": { "KEN_Turkana": {
      "risk_state": 0, "risk_label": "Green",
      "p_green": 0.87, "p_yellow": 0.09, "p_orange": 0.03, "p_red": 0.01,
      "exceedance_24h": 0.0, "exceedance_72h": 0.0,
      "api_mm": 20.0, "spatial_coverage": 0.0, "emdat_flood_match": false } } }
```

`admin1_boundaries.geojson` (source `data/admin_boundaries/east_africa_admin1.geojson`) :
FeatureCollection, **238 unités, 16 pays** - KEN(47), TZA(30), SYC(26), MDG(22),
SDN(19), BDI(18), SOM(18), ETH(11), SSD(10), ZMB(10), ERI(6), DJI(6), RWA(5),
UGA(4), COM(3), MWI(3). *(Les anciens `results/` à 155 unités/10 pays sont périmés.)*

Props **source** : `shapeName` (`Turkana`), `shapeGroup` (`KEN`), `admin1_pcode`
(`KEN_Turkana`). Props **sortie finale C3** (après `geojson_writer`) : `admin1_name`,
`country`, `admin1_pcode`.

Contrat attendu par le template v4 (aujourd'hui synthétique via `Math.random()`) :
- `GEOJSON[code]` (code minuscule `ken`), features avec `properties.name`.
- `REGION_RISKS[name]` → classe de risque.
- `simData(risk)` fabrique : sévérité par 7 fenêtres, exceedance fenêtre×6 RP,
  confiance ensemble m/51, KPIs POD/FAR/lead/missed → **à remplacer par données réelles**.

## Déjà scaffoldé (réutiliser) vs à construire

**Déjà là :** `generate_storymaps.py` (calendar JSON + COG + STAC), `template.mdx`
(2 couches, blocs Prose/Figure/Map), `titiler_config.yaml` (Lambda + colormaps),
`deploy-dashboard.yaml`, policy bucket public, template v4 (UI complète).

**À construire :**
1. Exporteur **Zarr→COG** pour `exceedance_prob` par fenêtre×RP + GPM→COG.
2. **Zonal-stats C2 → admin-1** pour `dependency.json` (remplace `simData()`).
3. **Câblage v4** : `fetch()` des fichiers `data/` au lieu des constantes hardcodées.
4. **STAC catalog** 2 collections (`gik-icechain-risk`, `gik-icechain-exceedance`)
   + déploiement TiTiler Lambda (ISSUE-19).
5. **Générateur MDX par événement** (date/centre/bbox paramétrés).

## Décisions par défaut

- **Risque = 4 classes canoniques** (Green/Yellow/Orange/Red) - c'est ce qu'exigent
  les artefacts VEDA déjà écrits (`risk_levels` 0-3, MDX `rescale "0,3"`). L'affichage
  7-tons du v4 est dérivé de `p_red/p_orange` ; la vérité COG/storymap reste 4.
- **Couverture pays** : le pipeline couvre **16 pays / 238 unités** (dont SDN,
  MDG, COM, SYC, MWI, ZMB). Le template v4 n'en liste que **11** → **étendre le
  template à 16** (ajouter `mdg, com, syc, mwi, zmb` dans `CNAMES`/`CFLAGS`,
  retirer le besoin de boundaries synthétiques).
- **Onglet AIFS & KPIs** : badge « preview / illustratif » tant qu'AIFS et la
  validation EM-DAT ne sont pas livrés.

## Séquence

1. **Phase 1 (statique)** : exporteur `data/` + câblage v4 → calendrier + carte
   admin-1 réels sur Pages. *(Aucune dépendance TiTiler.)*
2. **Phase 2 (raster)** : Zarr→COG exceedance + GPM→COG + STAC + TiTiler Lambda.
3. **Phase 3 (storymap)** : générateur MDX par événement, branché sur `openSM` du v4,
   rendu par VEDA-UI/TiTiler.

## Note de dépendance VEDA-UI ↔ TiTiler

VEDA-UI et TiTiler sont **découplés** (voir section dédiée du design) :
- **MDX storymaps n'imposent pas TiTiler** : une couche vectorielle (GeoJSON) ou un
  raster pré-rendu (PNG/COG statique) s'affiche sans serveur de tuiles.
- **TiTiler n'impose pas VEDA-UI** : n'importe quel client MapLibre/Leaflet consomme
  ses tuiles XYZ.
- TiTiler n'est **obligatoire que pour servir dynamiquement de gros rasters**
  (exceedance gridé). Notre risque admin-1 étant vectoriel, le shell A n'en a pas besoin.

## Blocage connu

Run E2E 2025-11-19 : C1 ✅ + C2-compute ✅, échec à la **persistance C2** -
`latitude 149 ≠ 159` (store S3 `exceedance-zarr` écrit avec une bbox différente,
résidu stale). Correctif : pointer `outputs.exceedance_store_uri` sur un chemin neuf
ou vider l'ancien store. N'empêche pas la Phase 1 (basée sur `results/` existant).
