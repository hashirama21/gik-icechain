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

## 6. Étude de cas : les inondations réelles de 2025 en Afrique de l'Est

**Question posée : si ce système avait existé et alerté à temps en 2025,
combien de vies aurait-on pu épargner ?** Voici comment y répondre
honnêtement - avec le vrai bilan 2025, une statistique externe citée (pas
inventée), et une lecture doublement prudente.

### 6.1 Le bilan réel 2025 (données EM-DAT, `data/emdat/east_africa_floods.csv`)

| Événement | Pays | Période | Morts | Personnes affectées |
|---|---|---|---:|---:|
| 2025-0109-MDG | Madagascar | 15-16 fév | - | 3 688 |
| 2025-0128-MDG | Madagascar | 15-28 fév | 11 | - |
| 2025-0226-MWI | Malawi | jan-mars | 39 | 180 801 |
| 2025-0306-SOM | Somalie | 15-30 avr | 4 | 45 000 |
| 2025-0346-SOM | Somalie | 9-10 mai | 14 | 84 000 |
| 2025-0373-TZA | Tanzanie | 14-15 mai | 26 | 3 150 |
| 2025-0719-UGA | Ouganda | 17-19 août | 5 | 15 000 |
| 2025-0735-SDN | Soudan | 27-28 août | 32 | 10 000 |
| 2025-0764-SSD | Soudan du Sud | juin-sept | 20 | 960 000 |
| 2025-1081-ZMB | Zambie | déc 2025-jan 2026 | - | 25 000 |
| **Total (champs renseignés)** | **10 événements** | | **151 morts** | **~1 326 600 personnes** |

*Provenance : extrait EM-DAT au format utilisé en interne par
`risk/cpt_refinement.py` pour calibrer/valider le modèle - le même jeu de
données que le système utilise déjà pour se juger lui-même. Le fichier
source se documente lui-même comme un extrait **curé, potentiellement
approximatif**, pas l'export EM-DAT officiel en temps réel - certains
champs morts/affectés sont vides quand la donnée n'était pas disponible ;
les vrais totaux sont donc probablement **supérieurs** à ceux ci-dessus.*

### 6.2 La statistique externe utilisée (citée, pas inventée)

> « La mortalité liée aux catastrophes est au moins **6 fois plus faible**
> dans les pays dotés d'un système d'alerte précoce de qualité, et **24h de
> préavis** avant un aléa peuvent réduire les dégâts de **jusqu'à 30 %**. »
> - António Guterres, Secrétaire général de l'ONU, 22 octobre 2025,
> initiative *Early Warnings for All* (OMM, UNDRR, UIT, FICR).
> [wmo.int](https://wmo.int/news/media-centre/early-warnings-all-initiative-scaled-action-ground) ·
> [undrr.org](https://www.undrr.org/implementing-sendai-framework/sendai-framework-action/early-warnings-for-all)

**Ce que cette statistique mesure et ce qu'elle ne mesure pas** : c'est une
association mondiale, cross-pays, entre la présence d'un **système
d'alerte précoce complet** (prévision **+ diffusion dernier kilomètre +
préparation communautaire + capacité d'évacuation**) et la mortalité liée
aux catastrophes - toutes catastrophes, tous pays confondus. **Ce n'est ni
un essai contrôlé, ni une mesure spécifique aux inondations d'Afrique de
l'Est, ni une évaluation de notre système en particulier.** Notre pipeline
ne fournit que **la couche prévision/détection** - une pièce nécessaire
mais pas suffisante de ce système complet.

### 6.3 Estimation illustrative - deux lectures

| Lecture | Méthode | Morts évitées (illustratif) | Personnes moins affectées (illustratif) |
|---|---|---:|---:|
| **Plafond théorique** | Si un système d'alerte *complet* et de qualité avait couvert ces 10 événements (mortalité ÷ 6, dégâts × 0,70) | **~126** (151 → ~25) | **~398 000** (30 % de 1 326 600) |
| **Réalisable aujourd'hui** | Le plafond ci-dessus, pondéré par le **recall réellement mesuré de notre couche prévision** (0,51 @ Orange+, § 2) - la moitié des crues comparables ne déclenchent pas encore d'alerte à temps | **~64** | **~203 000** |

**Comment lire ce tableau** : la colonne « réalisable aujourd'hui » n'est
**pas une prédiction** - c'est le plafond théorique multiplié par notre
propre métrique de couverture déjà mesurée (0,51), pour éviter de
s'attribuer un bénéfice qui suppose un système parfait. Elle suppose aussi
que, **quand** le système alerte à temps, la chaîne complète (diffusion,
préparation, évacuation) existe et fonctionne en aval - une hypothèse que
ce pipeline seul ne garantit pas.

### 6.4 Pourquoi ce chiffre doit être manié avec précaution

- **151 morts et ~1,33 M de personnes affectées sont des faits mesurés**
  (sous réserve des limites de la source EM-DAT curée, § 6.1). **~64 vies**
  et **~203 000 personnes** ne sont **pas** des faits - ce sont des
  extrapolations à deux niveaux (statistique mondiale externe × recall
  mesuré localement), à traiter comme un ordre de grandeur de plaidoyer,
  pas comme un résultat scientifique.
- Le facteur 6× vient d'une comparaison **entre pays** (bons systèmes vs
  pas de système), pas d'une expérience avant/après sur les mêmes
  événements - l'appliquer événement par événement suppose que la relation
  se transpose, ce qui n'est pas démontré ici.
- Le Soudan du Sud (960 000 affectés, 20 morts rapportés) illustre la
  limite : c'est une crue **fluviale et durable** (Sudd/Nil Blanc), le
  profil que notre levier rivière détecte le mieux (§ 2) - le potentiel de
  bénéfice y est plausiblement plus proche du plafond que la moyenne.
  Madagascar (cyclogénique, côtière) est plus proche du profil « Banadir »
  que le système rate encore aujourd'hui (§ 2).
- Aucun de ces dix événements n'a fait l'objet d'une reconstitution
  rétrospective événement par événement avec les alertes que notre système
  aurait réellement émises (contrairement à la Somalie/Deyr et au Soudan du
  Sud, novembre 2024, § 2-3, qui sont des validations réelles, pas des
  extrapolations). C'est un travail faisable - `risk/lead_time_skill.py`
  existe déjà pour ça - mais qui n'a pas encore été fait pour 2025.

## 7. Deuxième ordre : maladies hydriques et paludisme, même bilan 2025

Une inondation ne s'arrête pas le jour où l'eau se retire : elle laisse une
eau stagnante qui couve le choléra pendant des semaines et des gîtes
larvaires à moustiques pendant des mois. C'est un effet **de deuxième
ordre** - trois maillons causaux après ce que le système mesure
(prévision → alerte → action en amont de la crue → moins de cas de
maladie) - donc traité ici avec encore plus de prudence qu'au § 6.

### 7.1 Choléra 2025 : un fait rapporté, un lien partiel avec les crues

| Pays | Cas (2025) | Décès | Lien documenté avec les inondations |
|---|---:|---:|---|
| Soudan du Sud | **77 388** (1er jan-28 sept) | **1 249** | Crues ayant endommagé 63 établissements de santé et déplacé ~230 000 personnes (OMS/OCHA) |
| Soudan | **71 728** | **2 012** | Les autorités sanitaires pointent le mélange eaux de crue / eaux usées comme voie de contamination (OCHA) |

**Ce lien n'est pas causal ni exclusif.** Ces deux épidémies sont
dominées par le **conflit armé et l'effondrement des systèmes
eau-assainissement** - les inondations sont **une voie de contamination
documentée parmi d'autres**, pas la cause unique. L'Afrique CDC qualifie
2025 de pire année pour le choléra sur le continent depuis 25 ans, un
phénomène bien plus large que l'Afrique de l'Est. **Je ne calcule
délibérément aucun « nombre de cas de choléra évités par notre
système »** - la chaîne causale est trop confondue (conflit, déplacement,
capacité WASH) pour qu'un chiffre soit autre chose qu'une invention.

### 7.2 Paludisme : un mécanisme mieux quantifié dans la littérature

Contrairement au choléra, le lien inondation → paludisme est établi par
des études épidémiologiques dédiées, avec un ordre de grandeur répété :

| Étude | Contexte | Effet mesuré |
|---|---|---|
| Kasese, Ouganda (crue de mai 2013) | Villages riverains vs éloignés | **+30 %** de risque de test paludisme positif après la crue, dans les villages riverains |
| Gezira, Soudan (crue de 2013) | Sites sentinelles | Incidence passée de 6,09 à 8,24 pour 100 000 personnes-jours (**+35 %** relatif) |

Mécanisme : l'eau stagnante et les flaques de décrue créent de nouveaux
gîtes larvaires pour les moustiques *Anopheles*, sur une fenêtre de
plusieurs semaines à plusieurs mois après la crue - bien après que le
signal hydrologique soit retombé.

**Le lien avec ce qui a été construit aujourd'hui (§ extension à 10
jours) :** la lutte antivectorielle (pulvérisation intra-domiciliaire,
distribution de moustiquaires, démoustication des nouveaux gîtes) demande
un délai d'organisation - souvent **plus long** que celui nécessaire à
une évacuation d'urgence. C'est précisément ce que l'extension d'horizon
à 240h (10 jours), calibrée aujourd'hui même sur 9 ans de données GPM
IMERG (voir le journal de run), vise à fournir : plus de jours de préavis
pour organiser une réponse qui ne se limite pas à l'évacuation immédiate.

**Estimation, prudemment bornée** : les ~1,33 million de personnes déjà
comptées au § 6.1 comme affectées par les crues 2025 sont, par ce
mécanisme, la population dont le risque paludisme se trouve élevé
pendant les semaines/mois suivant la crue - **pas un nombre de cas**,
faute d'un décompte 2025 spécifique au paludisme post-crue pour ces
événements précis. Il est notable (mais ne constitue pas une preuve) que
l'ordre de grandeur « +30 % » revienne indépendamment dans la littérature
épidémiologique du paludisme (Ouganda, Soudan) et dans la statistique
généraliste ONU/OMM déjà citée au § 6.2 (« 24h de préavis réduisent les
dégâts de jusqu'à 30 % ») - une coïncidence de grandeur, pas une preuve
que le même mécanisme est à l'œuvre.

### 7.3 Ce que ce paragraphe ne dit pas

- **Aucun cas de choléra ou de paludisme évité n'est chiffré.** La
  distance causale (prévision → alerte → action → moins de crue subie →
  moins de maladie) est trop longue pour un nombre défendable avec les
  sources disponibles.
- Le choléra 2025 Soudan/Soudan du Sud est **majoritairement** un
  phénomène conflit + WASH, pas un phénomène météo - ne pas lire le
  tableau du § 7.1 comme « notre système aurait pu éviter 77 388 cas ».
  Ce serait faux et trompeur.
- Les effets « +30 %/+35 % » du paludisme viennent d'études sur des
  crues de **2013** en Ouganda et au Soudan - des contextes précis, pas
  une loi universelle transposable telle quelle à chaque crue 2025.

## 8. Ce qu'on ne peut pas dire à partir de ces chiffres

- **Le §6 est un ordre de grandeur de plaidoyer, pas un résultat de
  recherche.** Les ~64 vies et ~203 000 personnes sont une statistique
  mondiale externe appliquée à un bilan réel - une reconstitution
  événement par événement (comme celle déjà faite pour le Deyr somalien et
  le Soudan du Sud, novembre 2024) donnerait un chiffre bien plus solide,
  et reste à faire pour 2025.
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
- **§ 7 (choléra/paludisme) ne chiffre aucun cas ou décès évité.** Le
  choléra 2025 au Soudan/Soudan du Sud est majoritairement un phénomène
  conflit + WASH ; le paludisme n'a qu'un ordre de grandeur d'effet
  (études 2013), pas un décompte 2025 pour ces événements précis.

---

*Sources : `README.md` (validation IFRC + admin-1 satellite),
`docs/C3_VALIDATION_FINDINGS.md` (audit complet, avant/après par levier),
`configs/default.yaml` (seuils REV calibrés,
`risk/cost_loss_calibration.py`), `data/emdat/east_africa_floods.csv`
(bilan 2025). Statistique externe § 6.2 : ONU/OMM/UNDRR/UIT/FICR,
*Early Warnings for All*, 22 octobre 2025
([wmo.int](https://wmo.int/news/media-centre/early-warnings-all-initiative-scaled-action-ground),
[undrr.org](https://www.undrr.org/implementing-sendai-framework/sendai-framework-action/early-warnings-for-all)).
Choléra 2025 (§ 7.1) : OMS *Cholera – Multi-country* DON
([who.int](https://www.who.int/emergencies/disease-outbreak-news/item/2025-DON579)),
OMS AFRO South Sudan
([afro.who.int](https://www.afro.who.int/countries/south-sudan/news/sustained-response-curbing-cholera-outbreak-south-sudan)),
OCHA Sudan Cholera Operational Update, 3 juillet 2025
([unocha.org](https://www.unocha.org/publications/report/sudan/sudan-cholera-operational-update-3-july-2025)).
Paludisme post-crue (§ 7.2) : Uganda highlands 2013,
*J Infect Dis* 214(9):1403
([PMC5079365](https://pmc.ncbi.nlm.nih.gov/articles/PMC5079365/)) ;
Gezira, Soudan, 2013
([PMC6209411](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6209411/)).
Reproductible via `python scripts/satellite_validation.py`.*
