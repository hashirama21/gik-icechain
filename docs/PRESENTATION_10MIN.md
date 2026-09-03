# GIK-IceChain — présentation 10 minutes (webinaire du 2026-07-22)

*Script minuté, prêt à lire. Chiffres tous vérifiés dans le repo
(`README.md`, `docs/C3_VALIDATION_FINDINGS.md`, `docs/HUMAN_IMPACT_METRICS.md`).*

---

## Retours mentors reçus ce jour — à traiter avant le passage

**Jessica** :
1. **Slide 3** : trop vague sur la donnée et le problème - préciser
   **quelle prévision, quelles variables**, et formuler le problème plus
   concrètement. → traité ci-dessous, § « Le problème ».
2. **Slide 4/8** : ajouter des **captures d'écran ou une vidéo du
   dashboard**, en montrant certaines de ses fonctionnalités en action.
   → nouveau bloc dédié, § « Démo dashboard ».
3. **Budget réel : 10 minutes**, pas 7 - prévoir de raccourcir ailleurs
   si le bloc dashboard est ajouté.
4. **Le webinaire a lieu aujourd'hui**, pas demain/jeudi.

**Second mentor** :
5. **Cohérence visuelle** : les slides 3 à 10 doivent reprendre **le même
   thème/gabarit que les slides 1, 2 et 11** (pas de rupture de style au
   milieu du deck). → action sur le fichier de slides lui-même, pas sur
   ce script ; checklist en bas de ce document.

---

## 0:00–0:30 — Ouverture (30 s)

> Bonjour à tous. GIK-IceChain : un système d'alerte précoce aux
> inondations pour l'Afrique de l'Est, construit sur données ouvertes,
> pour un coût quasi nul.

## 0:30–1:30 — Le problème, précisément (1 min) *(slide 3 — retour Jessica)*

> Ce qu'on prévoit, exactement : le run **00Z de l'ensemble ECMWF IFS
> ENS**, **51 membres** (1 contrôle + 50 perturbés), publié en accès
> libre sur `s3://ecmwf-forecasts` depuis le **18 janvier 2023**, avec un
> horizon de prévision jusqu'à **360 heures (15 jours)**. Les variables
> qu'on exploite : **`tp`** (précipitation totale, la variable
> hydrologique clé), **`2t`** (température 2 m), **`10u`/`10v`** (vent
> 10 m) et **`ro`** (ruissellement) - les quatre entrées du réseau de
> risque en aval.

> **Le problème n'est pas l'accès brut, c'est l'accès exploitable.**
> Chaque run pèse une grille 0,25° (721×1440 points, 0,4° avant
> février 2024) × 51 membres × 85 pas de temps × 5 variables - plus
> d'1 pétaoctet cumulé sur l'archive complète. Pour en tirer une
> probabilité de dépassement de pluie sur une fenêtre glissante, un
> service météo national doit télécharger, décoder et agréger des
> téraoctets de GRIB2 **par jour**. C'est cette étape - pas la
> publication des données - qui bloque l'écosystème xarray/Dask
> aujourd'hui pour la plupart des équipes en Afrique de l'Est.

## 1:30–2:45 — L'approche : coût quasi nul, trois étages (1 min 15)

> GIK-IceChain répond en trois composants, chacun résolvant une brique du
> problème sans jamais dupliquer la donnée source.

- **C1 — Conversion** : les 51 membres × 85 pas de prévision deviennent un
  store virtuel IceChunk. Métadonnées seules (quelques dizaines de Go),
  zéro copie du pétaoctet source. Historique complet interrogeable comme
  une base versionnée (audit « qu'aurait-on su à telle date ? »).
- **C2 — Exceedance** : probabilités de dépassement de pluie par
  fenêtre (3h → 7 jours) et période de retour, calibrées de façon
  adaptative par saison et phase ENSO/IOD — pas des seuils statiques.
- **C3 — Risque (CRMA)** : réseau bayésien dynamique (ICPAC) qui combine
  prévision, pluie observée, mémoire du sol et signal fluvial amont pour
  produire un score de risque par unité administrative, chaque jour.

**Coût marginal : ~0 €.** Données ouvertes, calcul sur GitHub Actions
(minutes illimitées, repo public).

## 2:45–4:00 — Est-ce que ça marche vraiment ? Les chiffres de validation (1 min 15)

> On ne s'est pas contentés de le construire — on l'a confronté à la
> réalité, deux fois, sur des données satellite et humanitaires
> indépendantes.

- **Validation satellite admin-1** (FAO/VIIRS + UNOSAT, novembre 2024,
  105 unités) : après le levier « bassin fluvial amont »,
  **recall 0,29 → 0,51** à précision inchangée (**0,96**) — le système
  détecte désormais une crue confirmée sur deux, sans alerte
  supplémentaire pour rien.
- **Soudan du Sud (Sentinel-1)** : **100 % de recall** Jaune-ou-plus sur
  les 10 États — jamais un État en crue affiché comme « clair ».
- **Validation IFRC** (novembre 2024, échelle pays) : 76 % des alertes
  tombent dans un pays réellement en crise ; Somalie/Deyr et Soudan du
  Sud/Sudd, ses deux plus gros foyers, confirmés par la vérité terrain.

**La limite honnête** : recall encore ~50 % — les crues côtières/urbaines
(Mogadiscio, 562 000 personnes exposées) échappent encore au modèle
fluvial actuel. C'est le prochain chantier, pas un secret.

## 4:00–5:30 — Démo dashboard (1 min 30) *(slide 4/8 — retour Jessica : captures/vidéo)*

**À préparer avant le passage** (pas du texte à lire - des visuels à insérer) :

- [ ] **Capture 1 - vue calendrier** : `https://hashirama21.github.io/gik-icechain/`,
      onglet Archive. Montrer le calendrier type heatmap GitHub, coloré
      par **étendue d'alerte** (pas juste le pire cas), 1 270+ jours
      navigables 2023 → aujourd'hui.
- [ ] **Capture 2 - storymap d'un jour** : cliquer une date à forte
      étendue (ex. un jour d'août 2024, Soudan du Sud). Montrer le hero
      satellite NASA du jour, la puce « PEAK RED · N UNITS ALERTED »,
      et la carte de risque CRMA par unité.
- [ ] **Capture 3 - couches de données** : sur la même storymap, montrer
      la bascule risque / exceedance / confiance d'ensemble - trois
      lectures différentes de la même journée.
- [ ] Si vidéo possible (10-15 s) : un clic calendrier → storymap → survol
      d'une unité admin-1 avec popup, en direct. Plus parlant qu'une
      capture statique pour montrer que c'est **interactif et à jour**.

> Script pendant la démo : « Voici le dashboard public, régénéré chaque
> jour automatiquement. Chaque cellule du calendrier est une journée
> réelle, cliquable, avec sa propre storymap - image satellite du jour,
> carte de risque, carte de probabilité de dépassement. Rien n'est
> statique : c'est le même pipeline qui tourne en production depuis
> mi-juillet qui alimente ce que vous voyez à l'écran. »

## 5:30–6:30 — Ce que ça veut dire pour des gens réels (1 min)

> Traduire l'AUC et le recall en langage humain, sans survendre.

- Sur les inondations réelles de 2025 en Afrique de l'Est
  (**151 morts, ~1,33 million de personnes touchées**, EM-DAT), un calcul
  **prudent et doublement borné** — statistique ONU/OMM 2025 (mortalité
  6× plus faible avec un système d'alerte complet), pondérée par notre
  propre recall mesuré — situe l'ordre de grandeur atteignable
  **aujourd'hui** à environ **60-65 vies et ~200 000 personnes** moins
  touchées. **Ce n'est pas une prédiction** — c'est un plafond illustratif,
  documenté avec toutes ses limites dans `docs/HUMAN_IMPACT_METRICS.md`.
- Deuxième ordre : les crues laissent une eau stagnante qui élève le
  risque paludisme de +30 à +35 % pendant des semaines (études Ouganda et
  Soudan) — un horizon de préavis plus long sert autant la lutte
  antivectorielle que l'évacuation d'urgence.

## 6:30–7:45 — Où on en est aujourd'hui : en production (1 min 15)

> Ce n'est plus un prototype — ça tourne, chaque jour, tout seul.

- **Temps réel quotidien** : chaque jour à 8h UTC, le pipeline lit
  directement les prévisions ECMWF de la veille (fichiers `.index`
  publics, zéro store intermédiaire) et republie le dashboard.
- **Archive complète rétro-calculée** : 1 270+ jours, du 18 janvier 2023
  à aujourd'hui, calculés en parallèle sur GitHub Actions en une seule
  campagne (~10 h, 0 €).
- **Extension en cours** : passage de 7 à **10 jours d'horizon de
  prévision**, pour donner plus de délai à la réponse (évacuation *et*
  lutte antivectorielle). Calibration en cours sur 27 ans de
  climatologie CMORPH (ICPAC) — on réutilise et étend le travail déjà
  publié par le mentor, pas de donnée réinventée.

## 7:45–9:15 — Ce qui reste à faire (honnêtement) (1 min 30)

- Étendre la couverture fluviale (topologie de bassins) aux zones
  côtières/urbaines encore aveugles.
- Boucler la calibration 10 jours et l'activer en production.
- Étendre la validation satellite au Kenya et à l'Éthiopie (non couverts
  par FAO/VIIRS aujourd'hui).
- Reconstituer, événement par événement, l'alerte réelle qu'aurait donnée
  le système sur les crues 2025 — remplacer l'estimation illustrative du
  § précédent par un chiffre mesuré.

## 9:15–10:00 — Message de clôture (45 s)

> **Un système d'alerte précoce aux inondations pour 16 pays d'Afrique de
> l'Est, validé sur données indépendantes, tournant chaque jour en
> production, pour un coût marginal quasi nul — construit sur des
> données ouvertes, dans l'esprit même de ce programme. Merci.**

---

## Checklist avant le passage

- [ ] **Thème visuel** : slides 3 à 10 alignées sur le gabarit des
      slides 1, 2 et 11 (retour second mentor - vérifier polices,
      couleurs, mise en page, pas seulement le contenu).
- [ ] Captures/vidéo dashboard insérées (slide 4 ou 8, § dédié ci-dessus).
- [ ] Répétition chronométrée à **10 minutes**, pas 7 - le bloc dashboard
      ajoute ~1 min 30 ; les autres sections ont été légèrement resserrées
      en conséquence pour tenir le budget.
- [ ] Vérifier la connexion / le dashboard en ligne juste avant le
      webinaire (`https://hashirama21.github.io/gik-icechain/`).

### Filet de sécurité — si une question arrive sur…

| Question probable | Réponse courte |
|---|---|
| « Combien ça coûte vraiment ? » | ~0 € marginal : données ECMWF/CMORPH publiques, calcul GitHub Actions (repo public = minutes illimitées), stockage S3 minime. |
| « Pourquoi le recall n'est qu'à 51 % ? » | Le modèle ne voit que ce qui passe par le réseau fluvial modélisé ; les crues côtières/urbaines (Banadir) sont hors topologie — limite connue, documentée, prochain chantier. |
| « Le chiffre de vies sauvées est-il fiable ? » | Non, et on le dit explicitement : c'est une extrapolation d'une statistique ONU externe, pas une mesure. Le vrai chiffre demande une reconstitution événement par événement, pas encore faite pour 2025. |
| « Est-ce que ça marche vraiment tout seul ? » | Oui — daily production depuis mi-juillet 2026, zéro intervention manuelle, auto-guéri (dates manquantes rattrapables, ré-essais idempotents). |
| « Quelle est la résolution de la grille ? » | 0,25° (721×1440) depuis fin février 2024 ; 0,4° (451×900) avant - les deux grilles sont gérées et calibrées séparément dans le pipeline. |
