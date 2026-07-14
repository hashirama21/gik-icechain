# GIK-IceChain — Proposition Code for Earth 2026 vs code réellement livré

> Date : 2026-07-13 · Commit : `ac3ac07`
> Confrontation point par point du dossier de soumission (avril 2026) au dépôt
> réel, 3 mois avant la deadline du 31 août 2026.
> Sources : inspection directe du code + `docs/AUDIT_EXTERNE_2026-07-13.md` +
> `docs/ISSUES_NON_RESOLUES_2026-07-13.md`.

---

## Verdict en une phrase

**Le projet a livré nettement plus de science que promis, et nettement moins
d'infrastructure que promis.** Les trois composants C1/C2/C3 existent et
fonctionnent, mais aucun n'a été exécuté à l'échelle annoncée (~1000-1200 jours) ;
en revanche, la qualité du modèle de risque dépasse largement le cahier des
charges initial grâce à des travaux **non promis** (nœud fluvial, validation
satellite, calibration coût-perte, courbes de skill par lead-time). Le risque
principal n'est pas scientifique, il est **contractuel** : plusieurs livrables
formels (D1 public, benchmark, piste AIFS) sont des coquilles prêtes mais
jamais exécutées.

---

## 1. Livrables (D1 → D5)

| Livrable promis | État réel | Verdict |
|---|---|---|
| **D1** — Store IceChunk public sur AWS Open Data, ~1200 jours, `zarr.open()` unique, time-travel, **rapport de benchmark** | Store IceChunk fonctionnel et time-travel **réellement testé** (`checkout_as_of`, tests d'intégration). Mais : store sur **MinIO privé**, pas AWS Open Data ; catalogue réel **720–737 jours**, pas 1200 (gap-fill jamais exécuté à l'échelle) ; **benchmark jamais lancé** (`results/benchmarks/` = `.gitkeep`) | 🟧 **Partiel** |
| **D2** — Zarr d'exceedance `date × lat × lon × window × RP × member` + GEV adaptatif + **piste AIFS parallèle** | Zarr d'exceedance ✅ (7 fenêtres × 6 RP), GEV adaptatif saison×ENSO×IOD ✅ **réellement câblé**. Mais : les **membres ne sont pas persistés** (dimension `ensemble_member` absente — remplacée par des quantiles p50/p95) ; **AIFS `enabled: false`** dans la config, `results/aifs_comparison/` inexistant | 🟧 **Partiel** |
| **D3** — Calendar-map + storymaps **VEDA-UI** + TiTiler Lambda, ~1000 jours cliquables | Dashboard **Next.js** déployé sur GitHub Pages ✅, cal-heatmap ✅, storymaps MDX ✅. Mais : **VEDA-UI remplacé par une implémentation maison** (substitution technique non annoncée) ; TiTiler = docker-compose **local seulement**, Lambda non déployé ; nombre de jours cliquables ≪ 1000 | 🟨 **Livré, substitué** |
| **D4** — Risque admin-1 quotidien ~1000 jours, ~300 unités, DBN + API, **raffinement CPT EM-DAT**, validation vs EM-DAT, export EAHW | DBN + nœud API ✅ réel et testé ; export EAHW ✅ ; validation ✅ **et bien au-delà du promis** (satellite FAO/VIIRS + UNOSAT). Mais : **raffinement CPT EM-DAT pas en production** (`use_refined_cpts: false`) ; risque produit sur **des fenêtres ciblées**, pas ~1000 jours ; **238 unités** et non ~300 | 🟧 **Partiel** |
| **D5** — Dépôt Apache 2.0, notebook Colab end-to-end, doc | Apache 2.0 ✅, 2 notebooks Colab ✅, README + CONTRIBUTING + docs d'audit ✅ | 🟩 **Tenu** |

---

## 2. Les 4 « innovations » mises en avant

| Innovation | Promesse | Réalité |
|---|---|---|
| **1 — IceChunk Time-Travel Audit** | Commit par jour de prévision, requêtes « as-of date X » | 🟩 **Tenue.** Le mécanisme existe, est testé, et a même été poussé plus loin que promis : `lead_time_skill.py` produit des **courbes de skill par lead-time** — c'est-à-dire exactement la question du Challenge 41 (« missed opportunities »), que la proposition ne promettait pas explicitement |
| **2 — Seuils GEV adaptatifs (saison + ENSO/IOD)** | Remplace les percentiles CMORPH statiques | 🟩 **Tenue** (câblée dans le E2E). ⚠️ **Mais** : le bug OND/décembre (corrigé aujourd'hui seulement) tronquait d'un tiers la saison des *short rains* — l'innovation tournait sur une stratification saisonnière fausse. Et **l'ablation promise « GEV adaptatif vs percentiles statiques » (AUC-ROC comparé) n'a jamais été faite** — le bénéfice reste donc non démontré |
| **3 — CRMA-Live : DBN + API + raffinement CPT EM-DAT** | 3 volets | 🟨 **2 volets sur 3.** DBN + API : ✅ réels. Raffinement CPT EM-DAT : code présent et enrichi (pooling Dirichlet), mais **jamais activé en production**. Le **nœud « Climate Mode » ENSO/IOD promis dans le réseau bayésien n'existe pas** — l'ENSO/IOD n'agit qu'en amont, sur les seuils GEV. L'**ablation « bénéfice du nœud API » (§7.3) n'a jamais été mesurée** |
| **4 — AIFS vs IFS parallèle** | « Première comparaison systématique IA-NWP vs physique en Afrique de l'Est » | 🟥 **Non livrée.** Le code existe (`aifs_discovery.py`, `aifs_track.py`, la découverte S3 est réparée et fonctionne), mais `aifs_track.enabled: false`, aucun run, aucun résultat. **C'est l'écart le plus visible entre la promesse et le livré** : c'était présenté comme une première mondiale régionale |

---

## 3. Cibles chiffrées du §7 (cadre d'évaluation auto-imposé)

C'est la section la plus sévère : **la proposition s'est engagée sur des
métriques précises, et la majorité n'a jamais été mesurée.**

| Cible promise | Valeur cible | Mesuré ? |
|---|---|---|
| Ratio de stockage (store virtuel vs copie complète) | < 0,005 % (< 12 GB / 1000 j) | ❌ **Jamais mesuré** (chiffre 18,5 GB hérité de GIK, pas d'un run) |
| Time-to-first-byte | < 3 s | ❌ **Jamais mesuré** |
| Scan complet 1000 j × 51 membres | < 8 h sur 32 vCPU | ❌ **Jamais mesuré.** ⚠️ L'extrapolation interne (`RESULT.md`) donne **~50 h** pour 1200 jours — le chiffre disponible **rate la cible**, même s'il n'est pas mesuré dans les mêmes conditions (1 worker vs 32 vCPU) |
| Correction du time-travel (snapshot as-of) | Vérifiée | ✅ **Tenue** (tests d'intégration) |
| Hit rate / False alarm ratio vs EM-DAT | — | 🟨 Fait, mais via **satellite** (meilleur qu'EM-DAT) plutôt que par la métrique exacte promise |
| **AUC-ROC : GEV adaptatif vs seuils statiques** | Comparaison | ❌ **Jamais faite** — le cœur de la justification de l'Innovation 2 |
| **Delta AIFS vs IFS par saison / phase ENSO** | — | ❌ **Jamais fait** (piste désactivée) |
| **Bénéfice du nœud API (test d'ablation)** | precision/recall | ❌ **Jamais fait** |
| Précision spatiale admin-1 | — | ✅ Fait, et mieux que promis (panel satellite admin-1) |

---

## 4. Ce qui a été livré **sans avoir été promis** (la vraie force du projet)

Ces travaux viennent de la feuille de route « beat-mentor » (`docs/BEAT_MENTOR.md`),
postérieure à la proposition. **Aucun n'était dans le dossier de soumission.**

- **Nœud de danger fluvial + pooling amont** — le seul levier qui ait réellement
  fait bouger la performance : **rappel 0,29 → 0,51 à précision constante (0,96)**.
  La proposition ne prévoyait rien pour les crues fluviales ; c'était pourtant
  le trou structurel principal.
- **Harnais de validation satellite admin-1** (FAO/VIIRS + UNOSAT Sentinel-1) —
  vérité terrain indépendante, publique, **plus rigoureuse que l'EM-DAT promis**
  (fournit de vrais négatifs, donc AUC/précision/rappel bien posés).
- **Tail-risk (mondes possibles, p95)** — capte les queues humides d'ensemble
  invisibles à la moyenne.
- **Soft-evidence (binning gaussien)** — et, fait notable, **désactivé par
  défaut après avoir mesuré qu'il n'aidait pas** sur la fenêtre testée. C'est de
  l'honnêteté méthodologique rare.
- **Déclencheur coût-perte + calibration REV** (Murphy 1977 / Coughlan de Perez) —
  la métrique économique qui correspond littéralement au titre du Challenge 41.
- **Nœud Rainfall_Trend**, **storylines worst/median**, **pooling Dirichlet
  hiérarchique des CPT**, **risque en double période de retour (2 ans / 5 ans)**.
- **Courbes de skill par lead-time** (`lead_time_skill.py`) — « qu'aurait-on su
  à J-3 ? ». C'est *le* thème du challenge, adressé plus frontalement que dans
  la proposition elle-même.

---

## 5. Lecture stratégique

**Le projet a fait un arbitrage implicite : profondeur scientifique plutôt que
largeur d'infrastructure.** C'est défendable — et probablement le bon choix
scientifique, puisque livrer 1200 jours d'un modèle au rappel de 29 % aurait eu
moins de valeur que 1 mois d'un modèle au rappel de 51 %. Mais cet arbitrage
n'a jamais été **explicité** ni renégocié avec les mentors, et le dossier de
soumission reste la référence contractuelle.

Trois écarts sont **récupérables d'ici le 31 août** avec un effort modeste, et
ce sont ceux qui coûteraient le plus cher à laisser ouverts, car ils sont
**visibles** (un jury peut les vérifier en 5 minutes) :

1. **Lancer le benchmark** — le code est écrit et fonctionne. Il manque un run
   et un CSV. C'est quelques heures, et ça ferme la promesse chiffrée la plus
   facilement vérifiable du dossier (§7.1).
2. **Activer et faire tourner la piste AIFS** — la découverte S3 est réparée et
   validée (10/10 URIs). Il faut basculer `enabled: true` et produire la
   comparaison. C'était annoncé comme une première régionale ; le laisser à
   `false` est le seul endroit où le dossier pourrait être lu comme
   sur-promesse.
3. **Faire l'ablation « GEV adaptatif vs statique »** — c'est la seule preuve du
   bénéfice de l'Innovation 2, et elle n'existe pas. À faire **après** avoir
   régénéré les seuils avec la saison OND corrigée (sinon on valide une
   stratification fausse).

Deux écarts sont **structurels** et doivent être **assumés explicitement** dans
le rapport final plutôt que masqués :

- **Le store n'est pas public sur AWS Open Data** (MinIO privé) et le catalogue
  fait ~730 jours, pas ~1200. La rétention publique ECMWF (~15 mois) rend
  d'ailleurs une partie de l'ambition initiale physiquement impossible sans
  abonnement MARS — ce qui n'était pas anticipé dans le dossier.
- **VEDA-UI a été remplacé par du Next.js maison.** Ce n'est pas un problème en
  soi (le résultat est déployé et fonctionne), mais c'est une substitution
  technique par rapport à un livrable nommément promis.

---

## 6. Recommandation de présentation finale

La position la plus solide n'est pas de prétendre que tout a été livré, mais de
**revendiquer l'arbitrage** :

> « Nous avons livré les trois composants et le time-travel comme promis. En
> cours de route, la validation contre vérité terrain satellite a révélé que le
> modèle promis avait un angle mort structurel (crues fluviales, rappel 29 %).
> Nous avons choisi de corriger la science avant d'industrialiser l'échelle :
> rappel porté à 51 % à précision constante, plus une calibration
> économique coût-perte et des courbes de skill par lead-time qui répondent
> directement à la question du Challenge 41. Le passage à l'échelle
> (~1000 jours, AIFS, benchmark) est du travail d'exécution sur du code déjà
> écrit et testé. »

Cette formulation est **vraie**, vérifiable dans le dépôt, et transforme les
écarts en séquencement plutôt qu'en manquements — à condition de fermer les
trois trous récupérables ci-dessus avant la deadline.
