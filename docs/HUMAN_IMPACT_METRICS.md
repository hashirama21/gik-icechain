# Ce que les métriques techniques veulent dire pour des gens réels

Ce document traduit les métriques déjà mesurées et publiées ailleurs dans le
repo (`README.md`, `docs/C3_VALIDATION_FINDINGS.md`, `configs/default.yaml`)
en lecture humaine : à quoi ressemble ce chiffre pour un opérateur
humanitaire, un décideur local, un village qui reçoit ou ne reçoit pas
d'alerte. Aucun nouveau calcul de validation n'est fait ici. Chaque chiffre
humain est une extrapolation directe et explicite d'une métrique déjà
mesurée sur les mêmes données réelles (ensemble ECMWF, observations GPM
IMERG, vérité terrain satellite FAO/VIIRS + UNOSAT, appels IFRC GO).

**Ce que ce document n'est pas** : une étude d'impact. On n'a pas mesuré
« combien de vies sauvées » - personne ne le peut sans essai contrôlé sur le
terrain. Ce qui suit est une lecture honnête de ce que la précision, le
recall et l'AUC mesurés impliquent concrètement, avec les limites
explicites à chaque section.

---

## 1. Fiabilité : quand le système alerte, peut-on le croire ?

| Métrique technique | Valeur mesurée | Lecture humaine |
|---|---|---|
| Précision @ Orange+ (post-remédiation, panel VIIRS) | **0,96** (46/48) | Sur 100 alertes Orange ou Rouge émises, **96 correspondent à une inondation réellement confirmée par satellite**. Un intervenant qui agit à chaque alerte se trompe environ 1 fois sur 25 - la « fatigue d'alerte » (ignorer le système parce qu'il crie au loup) n'est pas un risque significatif ici. |
| Recall Vert-ou-plus, Soudan du Sud (UNOSAT) | **100 %** (10/10 États) | Sur les 10 États sud-soudanais réellement inondés (confirmés par radar Sentinel-1), **aucun n'a jamais été affiché comme « clair » (Vert)**. Pour un décideur qui surveille le tableau de bord, ça veut dire : si un État est en crue, le système ne le lui cachera jamais - au pire il sous-estime la sévérité, il ne fait jamais disparaître le signal. |
| AUC (p_red vs inondation VIIRS) | 0,715 → **0,734** | Si on prend au hasard un jour-unité inondé et un jour-unité sec, le système classe correctement celui qui est inondé comme le plus risqué **73 % du temps** (contre 50 % pour un tirage au sort). C'est un système qui discrimine réellement le danger, pas parfait, mais loin du hasard. |

## 2. Couverture : combien de crues réelles sont vues ?

| Métrique technique | Avant | Après | Lecture humaine |
|---|---|---|---|
| Recall @ Orange+ (panel VIIRS, 91 unités-jours inondées) | 0,29 (26/91) | **0,51 (46/91)** | Avant : sur 10 crues réelles, le système en repérait environ 3. Après le levier rivière : **il en repère environ 5 sur 10** - le recall a quasiment doublé, à précision inchangée (toujours 96 % de fiabilité). Concrètement : **20 unités-jours de crue supplémentaires** sont désormais vues, sans une seule fausse alerte de plus. |
| Bassins somaliens passés de 0 à couverts | 0 jour Orange+ | **29-30 jours Orange+** sur la fenêtre Deyr (31 jours) | Middle Shabelle (245 149 personnes exposées), Lower Juba (126 773), Lower Shabelle (157 215), Middle Juba (51 603) : ces quatre bassins étaient **totalement invisibles** au système avant le levier rivière - une inondation de 3 016 km² à Middle Shabelle ne déclenchait aucune alerte. Après : alertes quasi tout le mois. **Ordre de grandeur illustratif : ~580 000 personnes** dans ces quatre bassins précis passent d'une couverture nulle à une couverture quasi continue pendant l'épisode. *(Ce nombre vient des 4 unités précisément documentées où le avant/après est mesuré - pas d'une extrapolation aux 238 unités.)* |
| Zones encore invisibles | - | - | Banadir/Mogadiscio (**561 738 personnes exposées**), Bakool, Bay : restent à 0 jour d'alerte - hors de la topologie fluviale actuelle (inondation côtière/urbaine, pas un mécanisme de bassin versant). C'est la limite honnête du système aujourd'hui : **une grande ville densément peuplée peut inonder sans qu'aucune alerte ne se déclenche**, si l'inondation n'est pas de nature fluviale. |

## 3. Densité du signal à l'échelle pays (validation IFRC, novembre 2024)

| Métrique technique | Valeur | Lecture humaine |
|---|---|---|
| Alertes C3 tombant dans un pays réellement inondé | 76 % | Sur 100 alertes Orange/Rouge émises en novembre 2024, 76 sont tombées dans un pays où l'IFRC confirmait une crise d'inondation active. |
| Taux d'alerte, pays inondé vs calme | 9,0 % vs 5,0 % | Dans un pays en crue, environ **1 unité admin-1 sur 11** est signalée un jour donné ; dans un pays calme, environ **1 sur 20**. Le signal est réel mais pas outrancier - le système ne « crie au feu » pas partout, il concentre le signal là où c'est chaud. |
| Somalie / Deyr | **32 %** des jours-unités signalés | Sur la saison des pluies courtes, environ 1 jour sur 3 dans les unités somaliennes concernées portait une alerte Orange+ - cohérent avec une crue qui dure des semaines, pas un pic isolé. |
| Soudan du Sud / Sudd | **56 %** des jours-unités signalés | Plus d'un jour sur deux alerté dans la région du Sudd pendant l'épisode confirmé (~300 000 personnes affectées) - le système « voit » la persistance réelle de la crue du Nil Blanc. |

## 4. Valeur de la décision : l'alerte vaut-elle la peine d'agir ?

Le seuil de décision (`cost_loss`, calibré par Relative Economic Value,
Richardson 2000) répond à une question différente de la précision : *étant
donné le coût d'une action préventive face au coût de ne rien faire, à
partir de quelle probabilité agir est rentable ?* REV = 1 est un décideur
parfait (agit exactement quand il faut) ; REV = 0 n'apporte rien de plus que
la climatologie (agir au hasard selon la fréquence historique).

| Palier | REV mesurée | Lecture humaine |
|---|---|---|
| Jaune (action légère : pré-positionner, alerter les autorités locales) | **0,243** | Pour les actions **peu coûteuses et réversibles**, le système capture environ **un quart de la valeur d'un décideur parfait** - un gain réel et exploitable pour les décisions de type « surveillance renforcée » ou « préparer le matériel ». |
| Orange (action moyenne) | 0,076 | Pour des actions plus coûteuses (évacuation partielle, mobilisation logistique), la valeur ajoutée tombe à **~8 % du maximum théorique** - le signal existe mais est plus incertain à ce niveau, cohérent avec la théorie : plus l'action coûte cher, plus il faut de certitude, et l'incertitude à 5-10 jours d'échéance grandit. |
| Rouge (action lourde : évacuation majeure) | 0,030 | ~3 % du maximum théorique - à ce niveau de décision, le système donne un signal d'alerte utile mais **ne doit pas remplacer le jugement humain et les observations de terrain** pour déclencher une évacuation massive. |

Lecture d'ensemble : **le système est aujourd'hui le plus utile pour les
décisions réversibles et peu coûteuses prises tôt** (pré-positionnement,
vigilance), et doit rester un signal d'appui - pas l'unique déclencheur -
pour les décisions lourdes et coûteuses.

## 5. Empreinte opérationnelle : à quelle échelle et à quel coût ?

- **238 unités administratives (admin-1), 16 pays d'Afrique de l'Est** -
  évaluées quotidiennement.
- **Archive complète 2023-01-18 → aujourd'hui** (1 270+ jours) : sur cette
  période, **7 % des jours** ont une étendue d'alerte « exceptionnelle »
  (≥ 50 unités en Orange+ simultanément - ex. crues d'août 2024 au Soudan du
  Sud, juillet 2025), et un fond chronique médian de **25 unités/jour** en
  Orange+ reflète les bassins fluviaux en crue quasi permanente (Sudd,
  Shabelle, Nil Bleu) - un signal cohérent avec la réalité hydrologique de
  la région, pas du bruit (précision 0,96 déjà validée sur ce même signal).
- **Coût marginal ~0 €** : pipeline entièrement sur données ouvertes
  (ensemble ECMWF public, observations satellite publiques), infrastructure
  GitHub Actions (repo public = minutes illimitées) + stockage S3 minime.
  Ce que d'autres systèmes opérationnels financent en abonnements de données
  et calcul dédié, celui-ci le fait à coût quasi nul - ce qui compte pour
  des services météo nationaux à budget contraint.

## 6. Ce qu'on ne peut pas dire à partir de ces chiffres

- **Aucune vie sauvée mesurée.** Précision et recall mesurent la qualité du
  signal, pas l'effet d'une action qui en découle - ça demanderait un essai
  de terrain avec des populations qui reçoivent l'alerte et d'autres non.
- **Recall encore ~1 sur 2.** La moitié des crues confirmées par satellite
  ne déclenchent toujours aucune alerte Orange+ - principalement les crues
  côtières/urbaines hors du réseau fluvial modélisé (Banadir en est
  l'exemple le plus lourd : 561 738 personnes exposées, 0 jour d'alerte).
- **`p_red` sature à 0,39 par construction** du réseau bayésien : une
  petite crue confirmée et une catastrophe majeure peuvent recevoir le même
  score brut Rouge ; c'est le `severity_score` additif (§ remédiation) qui
  porte la gradation fine, pas la probabilité affichée.
- **La couverture ETH/KEN manque** au panel satellite de validation (FAO
  EVE ne les couvre pas) - la qualité du signal dans ces deux pays n'est
  pas mesurée aussi rigoureusement que pour la Somalie et le Soudan du Sud.
- Tous les chiffres « personnes exposées » de ce document viennent des
  jeux de données FAO/VIIRS et UNOSAT cités - ce sont des estimations
  satellite de population dans la zone inondée, pas des dénombrements
  vérifiés au sol.

---

*Sources : `README.md` (validation IFRC + admin-1 satellite),
`docs/C3_VALIDATION_FINDINGS.md` (audit complet, avant/après par levier),
`configs/default.yaml` (seuils REV calibrés,
`risk/cost_loss_calibration.py`). Reproductible via
`python scripts/satellite_validation.py`.*
