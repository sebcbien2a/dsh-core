# DSH-Core — vision fonctionnelle canonique

**État documentaire : 20 octobre 2026**  
**Portée :** vision complète, architecture cible validée et état réel des
raccordements. Ce document ne déclare pas une cible comme implémentée.

## Sommaire

1. [Statuts de lecture](#1-statuts-de-lecture)
2. [Vision générale](#2-vision-générale)
3. [Architecture locale et Oracle](#3-architecture-locale-et-oracle)
4. [Les trois tableaux de travail](#4-les-trois-tableaux-de-travail)
5. [Pipeline documentaire et facture](#5-pipeline-documentaire-et-facture)
6. [Architecture bancaire](#6-architecture-bancaire)
7. [Mémoire et connaissances](#7-mémoire-et-connaissances)
8. [Skills, apprentissage et SkillOpt](#8-skills-apprentissage-et-skillopt)
9. [Modèles d'intelligence artificielle](#9-modèles-dintelligence-artificielle)
10. [Indexation documentaire Oracle](#10-indexation-documentaire-oracle)
11. [Synchronisation logique Oracle vers local](#11-synchronisation-logique-oracle-vers-local)
12. [Interfaces et rôles](#12-interfaces-et-rôles)
13. [État réel au 20 octobre 2026](#13-état-réel-au-20-octobre-2026)
14. [Principes non négociables](#14-principes-non-négociables)
15. [Architecture finale — diagramme](#15-architecture-finale--diagramme)
16. [Points bloqués et travaux futurs](#16-points-bloqués-et-travaux-futurs)
17. [Sources de référence](#17-sources-de-référence)

## 1. Statuts de lecture

Les statuts décrivent le niveau de preuve, pas une promesse de disponibilité
en production.

- **EXISTANT ET ACTIF** : le chemin est relié à une entrée applicative ou
  fonctionne dans le flux décrit. Cela ne garantit pas qu'il couvre tous les
  cas.
- **EXISTANT MAIS PARTIEL** : le composant est appelé dans une partie du
  parcours ou exercé par des tests, mais le flux complet ou un raccordement
  important manque.
- **EXISTANT MAIS NON RACCORDE** : le code ou la ressource est présent sans
  appelant applicatif courant identifié.
- **VALIDÉ COMME CIBLE** : principe d'architecture retenu; son implémentation
  n'est pas présumée.
- **BLOQUÉ** : une décision métier ou un contrat doit être tranché avant de
  figer ou d'implémenter le comportement correspondant.
- **FUTUR** : capacité envisagée, non disponible comme flux applicatif actuel.

« Pipeline officiel » désigne le chemin actuel le plus raccordé, et non le
pipeline cible complet.

## 2. Vision générale

DSH-Core est une plateforme locale, souveraine, documentaire et comptable,
multi-entreprises. Elle doit pouvoir recevoir des pièces, les classifier,
extraire des faits traçables, assister les décisions métier, apprendre des
corrections validées et réutiliser les règles publiées. Son ambition est
d'observer, comprendre, mémoriser, réutiliser et s'améliorer sans transformer
une proposition de modèle en vérité métier.

La boucle fonctionnelle visée est :

```text
Pièce ou événement
→ observation et extraction
→ contrôles déterministes et recherche de contexte
→ proposition documentée
→ revue humaine si requise
→ décision validée et traçable
→ apprentissage candidat
→ test de non-régression
→ publication locale contrôlée
→ réutilisation
```

### Responsabilités structurantes

- **DSH-Core local est l'unique source de vérité métier.** Les référentiels,
  décisions publiées et données officielles sont gouvernés localement.
- **Hermes** est une couche d'interface et d'encapsulation de DSH-Core. Il ne
  remplace ni le moteur DSH-Core, ni ses services métier, ni LangGraph.
- **LangGraph** est l'orchestrateur métier unique de la cible : il coordonne
  les étapes, les transitions d'état et les interruptions humaines. Le graphe
  actuellement présent n'est pas encore ce chef d'orchestre opérationnel de
  bout en bout.
- **La validation humaine** est le mécanisme de résolution des ambiguïtés et
  le signal d'apprentissage. L'autonomie augmente par skill vérifié, sans
  supprimer les contrôles ni autoriser la promotion automatique d'une décision
  incertaine.

## 3. Architecture locale et Oracle

### Architecture validée comme cible

- **Hermes Desktop** est l'interface générale locale.
- **Telegram** est l'interface mobile et expose cinq canaux métier :
  `PRESSE`, `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL` et `DOCUMENTS`.
- **Hermes Oracle** est un satellite disponible 24/7, complémentaire au poste
  local.
- Oracle peut traiter les emails, les contenus OneDrive, l'indexation
  documentaire, les pièces comptables et proposer des rapprochements pendant
  la nuit.
- Oracle ne modifie pas les données officielles. Ses résultats sont déposés
  uniquement dans un **staging réversible**.
- La validation définitive et toute promotion vers les référentiels, ledgers
  ou mémoires officiels sont exécutées localement.

Hermes encapsule les outils et expériences utilisateur DSH-Core; il ne
réimplémente pas la logique métier et ne détourne pas les workflows LangGraph.
Une indisponibilité d'Oracle ne doit pas empêcher le fonctionnement local
prévu.

### État de réalisation

Cette architecture est **VALIDÉE COMME CIBLE**. L'inventaire du dépôt ne
trouve pas de configuration Hermes; le fonctionnement Telegram, Oracle,
staging distant et leurs connecteurs ne sont donc pas déclarés opérationnels.

## 4. Les trois tableaux de travail

Les trois tableaux constituent les vues métier de la cible; ils ne sont pas
présentés comme des écrans déjà disponibles.

### 4.1 Tableau général des pièces comptables

Vue transverse des pièces à traiter et de leur progression, couvrant :

- factures clients;
- avoirs clients;
- factures fournisseurs;
- avoirs fournisseurs;
- frais et justificatifs, y compris les frais payés personnellement.

Chaque ligne doit conserver l'identité de la pièce, la société concernée, le
tiers résolu ou le texte source, le type de pièce, les dates et montants
extraits, les contrôles, les références au fichier et son état de traitement.
Une pièce source et ses données normalisées restent distinguables.

### 4.2 Tableau de validation locale avant ventilation

Espace où l'utilisateur examine les champs extraits, les contrôles fiscaux,
l'identité du tiers, la catégorie proposée, les preuves et les incertitudes
avant toute ventilation ou écriture comptable. La validation, le rejet ou la
correction sont attribués à un acteur et horodatés. Une proposition Oracle
reste en attente dans le staging jusqu'à cette décision locale.

### 4.3 Tableau général de rapprochement bancaire

Vue des relevés, mouvements et allocations entre mouvements et documents. Elle
doit montrer les paiements non affectés, partiellement affectés, groupés,
contestés ou à revoir, avec la société résolue via le compte, la devise, les
références, les documents associés et le solde restant.

Un paiement partiel ou groupé se présente comme plusieurs **allocations
distinctes**; le tableau ne doit pas réduire une relation N↔N à un champ
unique « facture associée ».

### Conventions de signe et audit

- Les montants d'une facture ou d'un avoir sont conservés tels qu'extraits de
  la pièce, avec le type de document explicite. Dans les soldes métier, une
  facture augmente la créance ou la dette correspondante et un avoir la réduit;
  le signe économique de l'effet est dérivé du type de document, sans réécrire
  la valeur source.
- Pour un mouvement bancaire, le signe normalisé est celui du compte observé :
  **débit / sortie de fonds négatif**, **crédit / entrée de fonds positif**.
  Le statut ou signe source est conservé pour audit.
- Les allocations portent leur montant et devise propres; leur convention de
  signe, les frais, écarts, arrondis et conversions doivent rester cohérents
  avec les objets rapprochés. Les règles précises non décidées restent
  bloquées, pas déduites automatiquement.
- Les frais restent identifiables comme pièces et/ou mouvements selon leur
  source; ils ne sont pas inventés pour équilibrer un paiement.
- Les tableaux conservent les preuves, valeurs brutes et normalisées, hashes,
  scores éventuels, modèle, skill/version, décisions et historique de
  validation. Une donnée inconnue demeure inconnue jusqu'à résolution.

## 5. Pipeline documentaire et facture

### Flux réellement raccordé

Le seul flux de réception facture identifié qui relie une route HTTP à un
agent d'extraction réel est :

```text
n8n_api.py — POST /api/v1/ingest
→ agents.invoice_agent.InvoiceAgent
→ Ollama Vision
→ JSON validé par l'InvoiceData locale de cet agent
→ réponse JSON à n8n
```

Ce chemin est **EXISTANT ET ACTIF**, mais minimal : il ne raccorde pas le
profilage documentaire métier, les contrôles de rapprochement, la
classification fiscale, l'indexation Qdrant ni l'écriture Neo4j.

### Composants présents et niveau de raccordement

| Composant | Statut | Ce qui est effectivement établi |
|---|---|---|
| `n8n_api.py` — route `POST /api/v1/ingest` | **EXISTANT ET ACTIF** | Appelle `agents.invoice_agent.InvoiceAgent`. |
| `agents/invoice_agent.py::InvoiceAgent` | **EXISTANT ET ACTIF** | Extraction avec Ollama Vision et validation par son modèle `InvoiceData` local. |
| `services/ingestion_manager.py::IngestionManager` | **EXISTANT MAIS PARTIEL** | Exercé par test, non utilisé par le webhook facture actuel; la branche bancaire retourne `pending_implementation`. |
| `DocumentProfiler` et `RoutingEngine` | **EXISTANT MAIS PARTIEL** | Présents et testés, mais non appelés par le webhook actif. |
| `BusinessExtractionManager` | **EXISTANT MAIS PARTIEL** | Utilisé dans des tests, non raccordé au webhook. |
| `InvoiceReconciliationEngine` et `TaxClassificationEngine` | **EXISTANT MAIS PARTIEL** | Exercice rapporté par scripts/tests, pas par le webhook actif. |
| `schemas/invoice_schema.py::InvoiceData` | **EXISTANT MAIS PARTIEL** | Modèle plat distinct, importé par des composants/tests; ne valide pas le contrat imbriqué de `InvoiceSchema`. |
| `InvoiceSkillCreator` et `InvoiceSkillApplier` | **EXISTANT MAIS PARTIEL** | Exercés séparément; aucun raccordement depuis le webhook facture actif. |
| `services/extraction_manager.py::ExtractionManager` | **EXISTANT MAIS NON RACCORDE / BLOQUÉ** | Non appelé par le webhook; dépend de `InvoiceSchema`, symbole absent. |
| `invoice_agent.py` à la racine | **EXISTANT MAIS NON RACCORDE** | Voie distincte du webhook actif; importe le symbole absent `InvoiceSchema`. |
| Graphe facture backend | **EXISTANT MAIS PARTIEL** | Route alternative; les étapes d'extraction/validation/ingestion sont simulées. |
| `InvoiceIndexer`, `InvoiceEmbeddingService`, `InvoiceSimilarityService` | **EXISTANT MAIS NON RACCORDE** | Aucun raccordement au webhook actif repéré. |
| `GraphRepository` et `Neo4jService` de facture | **EXISTANT MAIS NON RACCORDE** | Aucun appelant applicatif identifié; schéma différent des labels/runtime rapportés. |

Le flux de traitement cible doit coordonner profilage, choix d'extracteur,
extraction OCR/Vision, contrôles fiscaux et arithmétiques, résolution
d'identité, skill applicable, validation humaine et persistance. Tant que ces
étapes ne sont pas raccordées et vérifiées, elles ne sont pas décrites comme
actives.

### `InvoiceSchema` est gelé et distinct d'`InvoiceData`

Les recherches dans le dépôt n'ont retrouvé aucune déclaration
`class InvoiceSchema`. Le fichier `schemas/invoice_schema.py` définit
`InvoiceData`, modèle plat. L'agent actif définit également sa propre classe
locale `InvoiceData`, distincte de celle du schéma.

Les prompts et exemples donnent des indices sur un ancien format de facture
imbriqué, mais ne prouvent pas les annotations Pydantic, les champs
obligatoires, les validateurs ni les valeurs par défaut de la classe absente.
Le format demandé par `ExtractionManager` comprend notamment `numero_facture`,
`date_emission`, objets `fournisseur` et `client`, `lignes`, `total_ht`,
`montant_tva`, `total_ttc` et `devise`; ces exemples ne constituent pas un
contrat exécutable vérifié.

**Décision :** contrat `InvoiceSchema` gelé. Ne pas le remplacer par
`InvoiceData`, ne pas renommer les classes et ne pas prétendre à leur
compatibilité tant que le contrat n'est pas arbitré. Aucun modèle manquant
n'est reconstruit dans ce document.

### Trois corrections de code déjà appliquées

1. `api.py` utilise `router.route_prompt` au lieu du symbole absent
   `router_intelligent`, avec le paramètre d'image attendu.
2. `agents/research_agent.py` utilise
   `SkillRouter.retrieve_skill_for_task` et traite le résultat comme une
   chaîne optionnelle.
3. `services/ingestion_manager.py` récupère le détail d'erreur depuis
   `message`, champ renvoyé par `InvoiceAgent`, au lieu de `last_error`.

Ces corrections réparent les raccordements indiqués; elles ne raccordent pas
à elles seules tous les pipelines cibles et ne débloquent pas `InvoiceSchema`.

## 6. Architecture bancaire

### Statut et séparation des formats

Le point d'entrée partagé `IngestionManager` reconnaît `AssetType.BANK_STATEMENT`,
mais sa branche est **EXISTANTE MAIS EN ATTENTE D'IMPLÉMENTATION** :
`pending_implementation`, destination logique `banking_bus`. Aucun parseur,
`BankingAgent`, ledger applicatif ou workflow bancaire LangGraph n'est
raccordé.

- **OFX — `ofxparse` :** lecteur prévu pour les relevés OFX et leurs
  transactions/métadonnées.
- **MT940 — `mt940` :** lecteur prévu séparément pour MT940.
- **CSV bancaire :** format distinct; aucun parseur ni mapping n'est validé
  par l'architecture OFX/MT940.
- **PDF bancaire :** format distinct; aucun pipeline de relevé bancaire PDF
  n'est actuellement spécifié par les parseurs ci-dessus. Le pipeline Vision
  de facture ne doit pas être présenté comme parseur bancaire.

Le format réel doit déterminer le parseur. Aucun fallback ne doit essayer
d'interpréter arbitrairement le même fichier comme tous les formats.

### Modèle conceptuel du mouvement

Le `BankingRecord` est une **cible conceptuelle**, et non un schéma exécutable
actuel (`schemas/bank_statement_schema.py` est vide). Son contrat cible
conserve notamment :

- une date transactionnelle et, si distincte, une date d'inscription;
- un `amount` décimal exact, un `currency` explicite et la convention débit
  négatif / crédit positif;
- libellé, informations de paiement et contenu brut utile à l'audit;
- un `account_id` métier résolu, distinct de la référence de compte source;
- le `document_id` du relevé, le `source_format`, et les références source
  disponibles;
- les champs optionnels de contrepartie, numéro de chèque, identifiant de
  transaction, statut source et métadonnées de relevé.

Valeur absente dans le relevé : la résoudre depuis un contexte fiable ou mettre
le mouvement en attente/revue. Ne pas inventer compte, devise, référence ou
identité.

### Comptes, sociétés et allocations

- Le compte bancaire possède son identifiant stable et son rattachement à une
  société. Le mouvement référence le compte; la société est déterminée à
  travers cette relation plutôt que dupliquée arbitrairement dans chaque
  mouvement.
- Le mouvement décrit une opération bancaire source; le document comptable
  décrit une facture, un avoir ou une autre pièce. Chaque objet conserve sa
  provenance.
- Une **allocation** relie exactement un mouvement à exactement un document,
  avec montant, devise, statut, méthode et audit. Un mouvement peut porter
  plusieurs allocations; un document peut être réglé par plusieurs mouvements.
  C'est le modèle conceptuel permettant paiements partiels, paiements groupés
  et rapprochements N↔N.
- `matched_document_id` et `matched_amount` singuliers ne suffisent donc pas à
  représenter le N↔N. L'état global de rapprochement est dérivé des
  allocations selon une règle métier à définir.
- Les chèques individuels restent identifiables lorsqu'ils sont exposés par
  la source. Une remise agrégée de chèques est distincte des allocations aux
  factures; son objet et sa relation de composition restent à arbitrer.
- Virements, prélèvements, LCR, retours/contre-passations, frais et autres
  opérations sont des cas métier à traiter en préservant références, signes
  source et traçabilité. La disponibilité de leurs champs détaillés dépend des
  relevés; aucune capacité de parsing n'est inférée sans preuve.

### Rapprochement nocturne

**VALIDÉ COMME CIBLE :** Oracle peut préparer des analyses et propositions
pendant la nuit, mais uniquement dans le staging réversible. La revue,
l'approbation et la publication d'allocations dans les données officielles
restent locales. Aucun graphe bancaire, score opérationnel ou rapprochement
automatique n'est actuellement établi.

**BLOQUÉ :** propriété simple ou partagée des comptes; objets rapprochables;
convention de signe et devise des allocations; frais, tolérances, écarts,
conversions et annulations; résolution des parties; détail ou exclusion des
remises de chèques. Ne pas figer le contrat de données en présumant ces
réponses.

## 7. Mémoire et connaissances

Les rôles sont séparés pour éviter de confondre référentiel, faits relationnels,
similarité et décisions passées.

| Couche | Rôle cible | État observé |
|---|---|---|
| Excel / MasterData | Interface humaine pour sociétés, tiers, comptes, alias et catégories; source d'administration des données de référence. | **VALIDÉ COMME CIBLE**; les classeurs/service de synchronisation décrits dans les spécifications ne sont pas tous présents/raccordés. |
| Neo4j | Relations métier déterministes : qui est relié à quoi, propriété des comptes, identités et relations validées. | Service local actif; accès de lecture à sécuriser. L'origine et le schéma des données ne sont pas attribués avec certitude aux repositories du dépôt. |
| Qdrant | Similarité sémantique, recherche vectorielle et RAG documentaire; aide à trouver des candidats, ne constitue pas à lui seul une identité métier. | Service actif et accessible au relevé; plusieurs collections, raccordements et writers restent partiels ou inconnus. |
| Mem0 | Mémoire épisodique des décisions et validations passées, consultée comme contexte et non comme référentiel maître. | **EXISTANT MAIS PARTIEL** dans la façade racine; collection `mem0` vide au relevé. |

Les principes fonctionnels sont :

1. le MasterData déclare les entités et valeurs de référence;
2. Neo4j porte les relations structurées validées;
3. Qdrant retrouve des contenus ou candidats similaires;
4. Mem0 peut rappeler des décisions antérieures pertinentes.

Une proximité vectorielle ou un souvenir ne valide jamais seul une relation
juridique, comptable ou bancaire.

## 8. Skills, apprentissage et SkillOpt

### Inventaire réel

- `skills/registry.json` contient un skill `ping_qdrant`; son script affiche
  actuellement un message fixe et ne prouve pas un test réseau.
- `workspace/skills/` contient `invoice_smtrt_sas`, version `1.0.0`, compteur
  d'utilisation `2`.
- Qdrant `agent_skills` contient **quatre variantes** visant la même tâche
  d'extraction de TVA avec HT/TTC inversés. Leurs contenus diffèrent : aucun
  doublon strict n'est prouvé, mais leur redondance fonctionnelle est probable.
- `skills_catalog` est vide.
- `teacher_student_metrics` contient **16 métriques**, pas des définitions de
  skills. Les relevés ne confirment aucune création ni réutilisation (`0` pour
  `skill_created=true` et `skill_reused=true`).
- Les skills locaux, les points Qdrant et les métriques n'ont pas tous versions,
  états de cycle de vie ou liens fiables entre création, validation et usage.
- Aucun `SKILL.md` métier de premier niveau ni configuration Hermes/SkillOpt
  n'a été identifié dans le dépôt.

La voie skill la plus raccordée aujourd'hui est le registre JSON racine utilisé
par les endpoints API. Le routage retourne une décision/contexte, mais
`dsh_harness.py` n'exécute pas le skill. La création Qdrant/Teacher–Student
reste distincte et partielle; elle ne constitue pas une boucle d'apprentissage
autonome confirmée.

### Gouvernance cible validée

- Le local conserve le registre canonique des skills **publiés**.
- Oracle peut suggérer et produire des candidats; il ne peut ni promouvoir un
  skill ni écrire directement dans le registre officiel.
- Cycle de vie : `CANDIDATE` → `TESTING` → `APPROVED` → `PUBLISHED`, avec
  transitions possibles vers `REJECTED` ou `RETIRED` selon la revue.
- SkillOpt teste et optimise les candidats; une validation de non-régression
  précède l'approbation et la publication locales.
- Seules les versions publiées localement sont synchronisées vers Oracle.
- Claude supervise systématiquement le bootstrap d'un nouveau skill. Selon
  sa maturité, la supervision devient partielle ou s'active sur exception.
- Toute correction utilisée pour l'apprentissage conserve sa source, son
  approbateur, les essais, le résultat du test de non-régression et la version
  publiée.

Cette gouvernance est **VALIDÉE COMME CIBLE**, mais sa mise en œuvre et la
convergence des formats de stockage restent à effectuer. Aucune fusion ou
suppression des quatre variantes n'est autorisée par le seul constat d'un
recouvrement.

## 9. Modèles d'intelligence artificielle

### Choix par rôle

Il n'existe pas de choix global unique de type `AI_MODE` pour tous les usages.
La politique cible sélectionne un modèle pour chaque rôle :

- **Vision** : compréhension d'images et pièces scannées;
- **raisonnement de base** : classification, extraction et décisions
  courantes;
- **expert** : cas complexes ou exceptions;
- **superviseur** : contrôle et apprentissage, en particulier bootstrap des
  skills.

Les modèles **Ollama locaux** sont disponibles comme voie souveraine. Des
modèles **OpenRouter gratuits** peuvent être utilisés pour les rôles vision,
base et expert, mais aucun modèle gratuit n'est considéré permanent ni garanti
disponible.

### Garde-fous de sélection et déploiement

Avant activation d'un modèle, vérifier au déploiement :

- disponibilité réelle, identifiant/version et limites de capacité;
- prise en charge de la vision pour le rôle qui l'exige;
- appels d'outils si le workflow en dépend;
- longueur de contexte et compatibilité avec la taille des documents;
- sortie JSON/structurée et comportement d'erreur;
- règles de confidentialité, destination des données et possibilité de
  fonctionnement local.

Un **fallback Ollama local est obligatoire** pour les rôles qui pourraient
utiliser OpenRouter. En absence d'Internet, le mode local doit continuer à
fonctionner selon les capacités installées; toute capacité manquante est
signalée au lieu d'être simulée.

Le rôle superviseur Claude applique le cycle de skills défini plus haut :
supervision systématique au bootstrap, puis partielle ou sur exception à
mesure que les validations démontrent la maturité. Il ne transforme pas à lui
seul un résultat candidat en donnée métier publiée.

Cette politique est **VALIDÉE COMME CIBLE**; elle ne prouve pas la configuration
ou la disponibilité actuelle de ces fournisseurs et modèles.

## 10. Indexation documentaire Oracle

### Corpus visé

Oracle peut préparer l'indexation de sources documentaires et comptables telles
que :

- documentation technique et procédures;
- fiches produits, catalogues et tarifs;
- contrats;
- anciennes factures;
- pièces comptables et documents provenant des emails ou de OneDrive.

Le traitement comprend l'analyse et l'extraction, la conservation du texte et
des métadonnées utiles, puis la préparation de chunks destinés à la recherche.
Il ne donne pas à Oracle le droit d'écrire dans les bases officielles locales.

### Données transmises et compatibilité

Le manifeste Oracle → local identifie le lot et sa version, son origine, les
hashes, le schéma, le modèle d'embedding et sa dimension. Il contient une
entrée par document, ainsi que les chunks, le texte extrait, les métadonnées
et les artefacts nécessaires à l'examen et à la reprise.

À l'import local, la compatibilité de schéma et d'embedding est vérifiée. Si
les vecteurs reçus ne sont pas compatibles avec le modèle/dimension locaux,
le document et ses chunks peuvent être ré-embeddés localement avant
publication. L'incompatibilité non résolue, le document ambigu ou un conflit
reste en revue; aucun vecteur incompatible n'est silencieusement accepté.

L'indexation Oracle et ce transfert sont **VALIDÉS COMME CIBLE**. Il n'existe
pas de configuration Hermes repérée dans le dépôt et le pipeline
d'indexation documentaire n'est pas attesté comme opérationnel de bout en
bout.

## 11. Synchronisation logique Oracle vers local

### Contrat et règles validés

La synchronisation est **logique et versionnée**; elle transporte des objets
documentaires et leurs métadonnées, et non une copie des fichiers internes des
bases.

Le manifeste comprend :

- `manifest_id`, `version` et `origine`;
- hashes des documents/artefacts;
- versions du schéma et du modèle d'embedding, avec dimension;
- une entrée par document, avec chunks, texte extrait, métadonnées et artefacts.

La validation locale d'une entrée peut aboutir aux statuts :

`PENDING`, `ACCEPTED`, `ACCEPTED_REEMBED`, `DUPLICATE_SKIPPED`,
`HUMAN_REVIEW_REQUIRED`, `REJECTED`, `PUBLISHED`.

Règles invariantes :

- les propositions Oracle demeurent en staging réversible jusqu'à leur
  validation locale;
- une version locale `PUBLISHED` est prioritaire sur une proposition Oracle
  `CANDIDATE`;
- les conflits sont bloqués sous `SYNC_CONFLICT` et résolus localement;
- aucun `DELETE` automatique;
- aucune copie directe de `qdrant_storage` ou `neo4j_data`;
- Neo4j est synchronisé par `MERGE` sur des identifiants métier stables;
- Qdrant est synchronisé par objets logiques; un snapshot n'est permis que
  pour une migration explicitement contrôlée.

La synchronisation logique ne doit pas être confondue avec une fusion physique
des répertoires ou des bases. La promotion vers MasterData, ledger, graphe,
index officiel ou skill publié est une opération locale explicitement validée.

### Ce qui reste à construire

Le manifeste et les règles de gouvernance sont décidés. Restent à implémenter :
le code du manifeste, le transport concret des lots, les tests de reprise et
d'idempotence et l'interface locale de résolution des conflits. Ces travaux
futurs ne remettent pas en question les règles déjà validées.

## 12. Interfaces et rôles

| Interface / outil | Rôle dans la vision | État |
|---|---|---|
| Hermes Desktop | Interface générale locale vers les fonctions DSH-Core. | Cible validée; configuration absente du dépôt audité. |
| Telegram | Interface mobile avec les cinq canaux métier. | Cible validée; intégration non attestée dans le dépôt. |
| VS Code + Copilot | Développement, revue de code, tests et audit technique. | Outil de développement; ne constitue pas une vérité métier d'exécution. |
| Gemini | Mémoire de sécurité du projet et sous-traitant pour les tâches qui lui sont confiées. | Rôle d'interface/outillage; n'a aucune autorité de publication métier. |
| Claude | Superviseur/Teacher, systématique au bootstrap des nouveaux skills selon la politique retenue. | Service externe dépendant de sa disponibilité et de règles de confidentialité. |
| AnythingLLM | Interface facultative pour le RAG documentaire intensif. | Optionnel; n'est ni une base de vérité ni un remplacement de Qdrant/Neo4j/DSH-Core. |

Les interfaces transmettent des demandes et affichent des résultats; les règles
de décision et les écritures officielles restent gouvernées par DSH-Core local
et ses workflows.

## 13. État réel au 20 octobre 2026

### Corrections et contrats

- Trois corrections ont été appliquées : `api.py` appelle `route_prompt`;
  `ResearchAgent` appelle `retrieve_skill_for_task` et consomme sa chaîne;
  `IngestionManager` lit `message` pour son détail d'erreur.
- `InvoiceSchema` reste **BLOQUÉ/GELÉ**. Aucune classe portant ce nom n'a été
  retrouvée. Les deux `InvoiceData` existants sont distincts et ne sont pas
  interchangeables sur la seule base de leur nom.
- La branche bancaire de `IngestionManager` répond `pending_implementation`;
  le schéma bancaire est vide; BankingAgent, ledger bancaire et workflow de
  rapprochement ne sont pas raccordés.

### Facture et orchestration

- Le webhook `n8n_api.py` vers `agents.invoice_agent.InvoiceAgent` est le
  chemin facture HTTP actif identifié; il extrait via Ollama Vision et
  retourne son JSON.
- `IngestionManager`, `DocumentProfiler`, `RoutingEngine`,
  `BusinessExtractionManager`, `InvoiceReconciliationEngine`,
  `TaxClassificationEngine`, les invoice-skills et les modèles de schéma
  partagés sont partiels, exercés par tests ou non intégrés au webhook selon
  leur statut détaillé plus haut.
- `ExtractionManager`, `InvoiceIndexer`, les services d'embedding/similarité
  et les writers facture Neo4j n'ont pas de raccordement applicatif vérifié.
- Le graphe LangGraph présent expose des nœuds simulés; l'architecture finale
  d'orchestration métier unique reste à réaliser.

### Stockages

- **Qdrant :** deux stockages sont présents. Le stockage racine est associé à
  l'unique instance active auditée; le stockage sous `docker/` n'a pas
  d'instance active. Leur lien est probable mais leur identité n'est pas
  démontrée; aucune fusion ni suppression n'est autorisée sans audit
  complémentaire.
- L'API Qdrant a listé huit collections et l'audit rapporte 23 points au
  total : `agent_skills` (4), `teacher_student_metrics` (16),
  `invoice_documents` (3), `skills_catalog` (0), `mem0` (0),
  `mem0migrations` (0), `document_bus` et `research_cache` (collections
  listées; nombre non certifié dans la cartographie). Les relevés ponctuels
  peuvent évoluer.
- **Neo4j :** conteneur/service actif, mais accès de lecture à sécuriser. Un
  rapport d'audit indique trois nœuds et deux relations observés, tandis que
  la cartographie de stockage n'a pas pu lire les données (réponse `401`);
  le contenu et sa provenance ne sont donc pas certifiés par l'ensemble des
  sources. Les repositories du dépôt ne sont pas identifiés comme auteurs de
  ces données.
- La présence de services ou de collections ne prouve pas que les pipelines
  applicatifs les alimentent.

### Skills, interfaces et cibles

- Les quatre points `agent_skills` sont fonctionnellement redondants selon
  l'inventaire; leur égalité stricte n'est pas démontrée. `skills_catalog` est
  vide et les 16 points de `teacher_student_metrics` ne confirment aucune
  création/réutilisation enregistrée.
- Les configurations Hermes et SkillOpt sont absentes du dépôt audité.
- Les architectures Oracle, staging, manifestes, synchronisation logique,
  trois tableaux, gouvernance des skills et politique des modèles sont
  **VALIDÉES COMME CIBLES**, pas implémentées comme flux de bout en bout.
- Aucune suppression, fusion de stockage ou promotion automatique de données
  n'est autorisée par les audits ni par ce document.

## 14. Principes non négociables

1. Pas de suppression ou de fusion de données sans audit, preuve de provenance
   et autorisation explicite.
2. Pas de donnée inventée : conserver les valeurs source, distinguer
   observations et normalisations, signaler les inconnues.
3. Oracle n'écrit jamais directement dans la vérité métier locale; ses
   propositions sont réversibles jusqu'à leur validation locale.
4. Pas de promotion automatique d'un skill; test de non-régression, validation
   et publication locale sont requis.
5. Pas de copie physique des répertoires `qdrant_storage` ou `neo4j_data` pour
   synchroniser les instances.
6. Traçabilité obligatoire : document, hash, origine, modèle, version de
   schéma/skill, résultat des contrôles et décision humaine ou système.
7. Toute incertitude métier significative conduit à une revue humaine ou à un
   statut bloqué, jamais à une validation silencieuse.
8. Le mode local reste possible sans Internet; les fonctions dépendantes d'un
   service distant doivent annoncer explicitement leur indisponibilité.
9. Les parseurs, contrôles fiscaux, recherches vectorielles et modèles
   contribuent chacun selon leur rôle; aucun ne remplace l'approbation métier.
10. Les composants futurs restent étiquetés **FUTUR** jusqu'à ce que leur
    implémentation et leurs tests prouvent le raccordement attendu.

## 15. Architecture finale — diagramme texte

```text
Utilisateur
   │
   ├── Hermes Desktop (interface générale locale)
   └── Telegram (PRESSE · RECHERCHE · COMPTABILITÉ · GÉNÉRAL · DOCUMENTS)
          │
          ▼
Outils et services DSH-Core
   │
   ▼
LangGraph — orchestrateur métier unique de la cible
   │
   ├── Pipeline documentaire / facture
   │      ingestion → profilage → OCR/Vision → extraction
   │      → contrôles fiscaux et arithmétiques → propositions
   │
   ├── Pipeline bancaire
   │      OFX / MT940 (CSV et PDF traités séparément lorsqu'ils seront définis)
   │      → BankingRecord → propositions d'allocations N↔N
   │
   └── Indexation documentaire et apprentissage de skills candidats
          │
          ▼
Staging réversible des propositions Oracle et des résultats à examiner
          │
          ▼
Validation locale — revue humaine, résolution des conflits et approbation
          │
          ├── Rejet / revue requise → staging conservé, aucune promotion
          │
          └── Acceptation explicite
                 │
                 ▼
            Publication locale versionnée
                 │
                 ├── MasterData / registres officiels
                 ├── ledger et allocations bancaires
                 ├── Neo4j — relations métier validées
                 ├── Qdrant — objets vectoriels compatibles
                 ├── Mem0 — décisions épisodiques autorisées
                 └── Skills — versions testées et publiées
                         │
                         ▼
             Ventilation et exports comptables
```

Oracle est un satellite de traitement : ses analyses d'emails, OneDrive,
documents et rapprochements nocturnes aboutissent au staging. Le flux de
publication ne contourne jamais la validation locale.

## 16. Points bloqués et travaux futurs

### Bloqués par une décision ou un contrat

- Reconstituer ou remplacer le contrat `InvoiceSchema` après comparaison
  explicite des formes demandées par les appelants; le maintenir gelé d'ici là.
- Choisir la convergence des formats de skills de travail et traiter les
  variantes Qdrant après évaluation, sans les fusionner sur la seule base de
  leur tâche commune.
- Définir les règles bancaires non arrêtées : propriété partagée des comptes,
  objets rapprochables, identité/rôle des parties, signe/devise/tolérances des
  allocations, annulations et périmètre/composition des remises de chèques.
- Obtenir un accès Neo4j en lecture et établir la provenance du contenu avant
  toute évolution du graphe ou de ses données.

### Futur à implémenter ou vérifier

- Raccorder le pipeline métier facture complet à l'orchestration LangGraph,
  avec contrats compatibles, contrôles, revue, persistance et tests de bout
  en bout.
- Implémenter les parseurs/contrats bancaires et le ledger; traiter OFX et
  MT940 séparément, puis définir les voies CSV et PDF sans les assimiler à ces
  parseurs.
- Construire les intégrations Hermes Desktop, Telegram et Oracle ainsi que le
  staging réversible et les trois tableaux de travail.
- Implémenter le code du manifeste, le transport des lots, les tests de reprise
  et d'idempotence, et l'interface locale de résolution `SYNC_CONFLICT`.
- Rendre opératoire la gouvernance des skills, SkillOpt, ses tests de
  non-régression et la distribution Oracle des seules versions publiées.
- Tester disponibilité, compatibilité, confidentialité et fallback local des
  modèles par rôle au déploiement.
- Réconcilier les inventaires Qdrant et Neo4j par accès autorisé et non
  destructif avant toute opération de convergence.

## 17. Sources de référence

Cette vision consolide les documents suivants, sans reprendre comme acquis
leurs composants décrits uniquement comme cibles :

- [03_JOURNAL.md](./03_JOURNAL.md)
- [dsh_core_master_specification_2026_2027.md](./dsh_core_master_specification_2026_2027.md)
- [02_ROADMAP.md](./docs/02_ROADMAP.md)
- [DSH_CORE_CURRENT_STATE.md](./DSH_CORE_CURRENT_STATE.md)
- [PIPELINE_OFFICIEL.md](./PIPELINE_OFFICIEL.md)
- [04_AUDIT_ARCHITECTURE.md](./04_AUDIT_ARCHITECTURE.md)
- [05_DECISIONS_ARCHITECTURE.md](./05_DECISIONS_ARCHITECTURE.md)
- [SKILLS_DUPLICATES_INVENTORY.md](./SKILLS_DUPLICATES_INVENTORY.md)
- [DATABASE_STORAGE_MAP.md](./DATABASE_STORAGE_MAP.md)
- [BANKING_ARCHITECTURE.md](./BANKING_ARCHITECTURE.md)
- [BANKING_RECORD_SPEC.md](./BANKING_RECORD_SPEC.md)
- [BANKING_SCHEMA_REVIEW.md](./BANKING_SCHEMA_REVIEW.md)
- [BANKING_RELATIONS_REVIEW.md](./BANKING_RELATIONS_REVIEW.md)
- [INVOICE_SCHEMA_ORIGIN.md](./INVOICE_SCHEMA_ORIGIN.md)
- [INVOICE_SCHEMA_RECONSTRUCTION.md](./INVOICE_SCHEMA_RECONSTRUCTION.md)
