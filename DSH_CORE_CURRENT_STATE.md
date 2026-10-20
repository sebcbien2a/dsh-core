# DSH-Core — état technique réellement observé

**Date de l’inventaire : 20 octobre 2026**  
**Périmètre :** dépôt local `A:\dsh-core`, code, documentation, chemins de stockage
et services Docker visibles en lecture seule.  
**Nature :** photographie factuelle; elle ne décrit pas les cibles comme des
fonctionnalités existantes.

## Sommaire

1. [Méthode, portée et statuts](#1-méthode-portée-et-statuts)
2. [État Git au début de l’inventaire](#2-état-git-au-début-de-linventaire)
3. [Arborescence utile et stockages](#3-arborescence-utile-et-stockages)
4. [API, points d’entrée et raccordements](#4-api-points-dentrée-et-raccordements)
5. [Pipeline facture observé](#5-pipeline-facture-observé)
6. [Contrats de facture et InvoiceSchema](#6-contrats-de-facture-et-invoiceschema)
7. [Vérification des trois corrections](#7-vérification-des-trois-corrections)
8. [Pipeline bancaire](#8-pipeline-bancaire)
9. [Qdrant](#9-qdrant)
10. [Neo4j](#10-neo4j)
11. [Skills et apprentissage](#11-skills-et-apprentissage)
12. [Mémoire, orchestration et observabilité](#12-mémoire-orchestration-et-observabilité)
13. [MasterData](#13-masterdata)
14. [Hermes, Oracle et Telegram](#14-hermes-oracle-et-telegram)
15. [Documentation canonique et statut Git](#15-documentation-canonique-et-statut-git)
16. [Tableau des écarts](#16-tableau-des-écarts)
17. [Prochaines actions issues de la Roadmap V2](#17-prochaines-actions-issues-de-la-roadmap-v2)
18. [Limites et incertitudes](#18-limites-et-incertitudes)

## 1. Méthode, portée et statuts

L’inventaire combine :

- lecture ciblée des sources Python et des configurations non secrètes;
- recherche statique des routes, classes, appels et déclarations de dépendance;
- consultation en lecture seule de `git status`;
- consultation de l’état des conteneurs Docker, de leurs ports et montages;
- lecture seule des métadonnées de collections Qdrant accessibles;
- rapprochement avec les rapports d’audit canoniques, en conservant leurs dates
  et divergences.

Aucun test n’a été exécuté pendant cet inventaire. Aucun secret ni contenu de
fichier de secret n’a été consulté ou reproduit. Aucun service n’a été démarré,
arrêté ou reconfiguré. Aucune écriture, migration ou opération de base n’a été
effectuée.

Les statuts ci-dessous sont exclusivement :

- **EXISTANT ET ACTIF**
- **EXISTANT MAIS PARTIEL**
- **EXISTANT MAIS NON RACCORDE**
- **CIBLE NON IMPLÉMENTÉE**
- **BLOQUÉ**
- **ÉTAT INCONNU**

Pour le code, **EXISTANT ET ACTIF** signifie qu’un chemin est relié à une entrée
applicative selon le code et les audits; cela ne prouve pas que son serveur est
démarré ni que tous les cas métier fonctionnent. Une cible décrite dans un
document reste **CIBLE NON IMPLÉMENTÉE** tant que le code et le raccordement ne
sont pas observés.

## 2. État Git au début de l’inventaire

### 2.1 Résumé

Le relevé `git status --short --untracked-files=normal`, capturé avant la
réécriture de ce document, affichait **68 entrées** :

- 24 fichiers suivis modifiés;
- 16 fichiers suivis supprimés;
- 28 entrées non suivies (les répertoires sont condensés par le format normal
  de Git; ce nombre ne signifie donc pas 28 fichiers individuels).

Ces modifications et fichiers non suivis étaient présents avant la présente
réécriture. Ils n’ont pas été nettoyés, réinitialisés ni restaurés. Ce document
était lui-même non suivi avant son remplacement.

**Avertissement :** un chemin `??` est un élément non suivi par Git; cela ne
signifie ni qu’il est supprimé, ni qu’il n’existe pas sur le disque.

### 2.2 Fichiers suivis modifiés (`M`)

```text
.vscode/settings.json
agents/research_agent.py
api.py
docs/02_ROADMAP.md
docs/03_JOURNAL.md
langfuse_db/base/16384/pg_internal.init
langfuse_db/global/pg_control
langfuse_db/global/pg_internal.init
langfuse_db/pg_wal/000000010000000000000001
langfuse_db/postmaster.pid
n8n_data/database.sqlite-shm
n8n_data/database.sqlite-wal
n8n_data/n8nEventLog-1.log
n8n_data/n8nEventLog-2.log
n8n_data/n8nEventLog.log
open-webui_data/vector_db/chroma.sqlite3
open-webui_data/webui.db
open-webui_data/webui.db-shm
open-webui_data/webui.db-wal
services/ingestion_manager.py
services/invoice_skill_applier.py
services/invoice_skill_creator.py
test_skill_creator.py
workspace/skills/invoice_smtrt_sas.json
```

### 2.3 Fichiers suivis supprimés (`D`)

Git rapportait 16 suppressions antérieures à cette tâche, toutes sous
`n8n_data/storage/workflows/QQEuTgJUGnFtTi4c/executions/.../binary_data/` :

- exécution `10` : un fichier de données binaires et son fichier `.metadata`;
- exécution `27` : deux fichiers de données binaires et leurs fichiers
  `.metadata`;
- exécution `3` : deux fichiers de données binaires et leurs fichiers
  `.metadata`;
- exécution `35` : un fichier de données binaires et son fichier `.metadata`;
- exécution `36` : un fichier de données binaires et son fichier `.metadata`;
- exécution `38` : un fichier de données binaires et son fichier `.metadata`.

Cette photographie ne détermine pas la cause des suppressions et ne constitue
pas une demande de restauration.

### 2.4 Entrées non suivies (`??`)

```text
03_JOURNAL.md
04_AUDIT_ARCHITECTURE.md
05_DECISIONS_ARCHITECTURE.md
06_GEMINI_HANDOVER.md
BANKING_ARCHITECTURE.md
BANKING_RECORD_SPEC.md
BANKING_RELATIONS_REVIEW.md
BANKING_SCHEMA_REVIEW.md
DATABASE_STORAGE_MAP.md
DSH_CORE_CURRENT_STATE.md
IMPORTS_BROKEN_REPORT.md
INGESTION_MANAGER_PATCH.md
INVOICE_SCHEMA_ORIGIN.md
INVOICE_SCHEMA_RECONSTRUCTION.md
NEO4J_INVENTORY.md
PATCH_PLAN.md
PIPELINE_OFFICIEL.md
QDRANT_INVENTORY.md
SKILLS_DUPLICATES_INVENTORY.md
TODO_CONVERGENCE.md
backup/v8_6_5_stable/
docs/fichier dans dsh core.png
dsh core complet.md
dsh_core_master_specification_2026_2027.md
generate_current_state_audit.ps1
n8n_data/n8nEventLog-3.log
services/supplier_repository.py
workspace/suppliers/
```

## 3. Arborescence utile et stockages

### 3.1 Vue utile du dépôt

Les répertoires de dépendances et de données volumineuses exclus de la vue
récursive sont `.git`, `.venv-v3`, `node_modules`, `qdrant_storage`,
`neo4j_data`, `langfuse_db`, `open-webui_data` et `n8n_data`. Leurs fichiers
internes ne sont pas inventoriés individuellement.

```text
A:\dsh-core
├── agents/
├── backend/
├── backup/
├── docker/
├── docs/
├── knowledge/
├── memory/
├── models/
├── repositories/
├── schemas/
├── services/
├── skills/
├── tests/
├── validators/
├── workspace/
├── api.py
├── n8n_api.py
├── dsh_harness.py
├── orchestrator.py
├── memory_manager.py
└── documents d’audit et de spécification
```

### 3.2 Services et tailles globales des stockages

Les services ci-dessous étaient démarrés lors de la vérification Docker. Les
tailles sont des totaux mesurés par répertoire depuis l’hôte; elles ne décrivent
pas la quantité de données métier utilisables.

| Service ou chemin | État observé | Montage / taille mesurée |
|---|---|---|
| Qdrant `dsh-qdrant` | **EXISTANT ET ACTIF** | Port `6333`; stockage racine `A:\dsh-core\qdrant_storage`, 4 366 859 237 octets (1 574 fichiers). |
| Stockage Qdrant secondaire | **ÉTAT INCONNU** quant à son contenu logique | `A:\dsh-core\docker\qdrant_storage`, 3 553 976 763 octets (1 652 fichiers); aucune seconde instance active associée. |
| Neo4j `dsh-neo4j` | **EXISTANT ET ACTIF** | Ports `7474` HTTP et `7687` Bolt; `A:\dsh-core\neo4j_data`, 541 130 919 octets (85 fichiers). |
| Langfuse web et base | **EXISTANT ET ACTIF** au niveau des conteneurs | Web `3001→3000`; base PostgreSQL `5432`; répertoire `langfuse_db`, 68 148 891 octets. |
| Open WebUI | **EXISTANT ET ACTIF** au niveau du conteneur | Port `3000→8080`; `open-webui_data`, 932 511 306 octets. |
| n8n | **EXISTANT ET ACTIF** au niveau du conteneur | Port `5678`; `n8n_data`, 11 262 646 octets. |

Les deux répertoires Qdrant ont des tailles et nombres de fichiers différents.
Leur relation est plausible, mais leur équivalence n’est pas prouvée. Aucune
fusion, comparaison destructive ou suppression n’a été effectuée.

## 4. API, points d’entrée et raccordements

### 4.1 `api.py`

Les déclarations observées sont :

| Route | État et raccordement observé |
|---|---|
| `GET /` | Route présente. |
| `POST /api/research` | Route présente, associée au service/agent de recherche. |
| `POST /api/code` | Route présente, associée à l’agent code. |
| `POST /api/execute` | Route présente pour exécution; environnement et effets d’exécution non testés pendant l’inventaire. |
| `POST /api/autocode` | Route présente; comportement de bout en bout non vérifié. |
| `POST /api/router` | La version actuelle importe et appelle `router.route_prompt`; la correction de symbole est présente. La route retourne une décision de routage, pas l’exécution d’un skill complet. |
| `POST /api/scrape` | Route présente; comportement de bout en bout non vérifié. |
| `POST /api/skills/register` | **EXISTANT ET ACTIF** dans le code : appelle le gestionnaire racine et son registre JSON. |
| `GET /api/skills/find` | **EXISTANT ET ACTIF** dans le code : recherche dans le registre racine. |
| `GET /api/skills` | **EXISTANT ET ACTIF** dans le code : liste le registre racine. |
| `POST /api/orchestrator` | Route présente; s’appuie sur le graphe simplifié décrit à la section 12. |

La présence de ces routes dans le code ne confirme pas que le processus FastAPI
correspondant soit actuellement lancé ou exposé en production.

### 4.2 Webhook n8n facture

`n8n_api.py` définit `POST /api/v1/ingest`, instancie
`agents.invoice_agent.InvoiceAgent`, reçoit un fichier, le place sous
`workspace/invoices/input`, appelle `process_invoice` et retourne le JSON. Ce
chemin est **EXISTANT ET ACTIF** au sens du raccordement de code rapporté par
l’audit. L’exécution d’une requête HTTP réelle n’a pas été testée lors de cet
inventaire. Le conteneur n8n actif ne prouve pas que l’API Python soit elle-même
démarrée.

### 4.3 Routes backend alternatives

`backend/app/n8n_api.py` et sa copie
`backend/app/graphs/n8n_api.py` déclarent toutes deux
`POST /api/v1/process-invoice`. Elles invoquent une graph facture dont les
nœuds d’extraction et de persistance contiennent des simulations et des
données de démonstration. Ce chemin est **EXISTANT MAIS PARTIEL**; il n’est pas
une seconde voie de traitement facture réel.

## 5. Pipeline facture observé

### 5.1 Chemin HTTP le plus raccordé

```text
n8n_api.py — POST /api/v1/ingest
→ agents.invoice_agent.InvoiceAgent.process_invoice(...)
→ traitement Vision via Ollama
→ validation avec InvoiceData définie dans agents/invoice_agent.py
→ réponse JSON à n8n
```

Ce chemin reçoit et extrait une pièce. Il n’appelle pas, selon le code et les
rapports examinés, les contrôles métier et les writers de facture listés
ci-dessous.

### 5.2 Composants et niveaux de raccordement

| Composant | Statut | Constat vérifié |
|---|---|---|
| `n8n_api.py` — `POST /api/v1/ingest` | **EXISTANT ET ACTIF** | Route raccordée à l’agent de facture sous `agents/`; serveur Python non vérifié comme processus en cours. |
| `agents/invoice_agent.py::InvoiceAgent` | **EXISTANT ET ACTIF** | Extraction Ollama Vision et validation par sa classe locale `InvoiceData`; pas de persistance métier Qdrant/Neo4j dans ce flux. |
| `services/ingestion_manager.py::IngestionManager` | **EXISTANT MAIS PARTIEL** | Code présent et exercé par `test_ingestion.py`; non appelé par le webhook actif. La correction du message d’erreur est présente. |
| `services/document_profiler.py::DocumentProfiler` | **EXISTANT MAIS PARTIEL** | Code et test présents; pas d’appel par le webhook facture. |
| `services/routing_engine.py::RoutingEngine` | **EXISTANT MAIS PARTIEL** | Code et test présents; pas d’appel par le webhook facture. |
| `services/business_extraction_manager.py::BusinessExtractionManager` | **EXISTANT MAIS PARTIEL** | Exercice dans les tests de profilage; pas de raccordement au webhook. |
| `services/invoice_reconciliation_engine.py::InvoiceReconciliationEngine` | **EXISTANT MAIS PARTIEL** | Code présent et appelé par un script de test; non raccordé au webhook. |
| `services/tax_classification_engine.py::TaxClassificationEngine` | **EXISTANT MAIS PARTIEL** | Code présent et appelé par un script de test; non raccordé au webhook. |
| `services/invoice_skill_creator.py::InvoiceSkillCreator` | **EXISTANT MAIS PARTIEL** | Code présent et exercé hors webhook; ne prouve pas une création automatique depuis le pipeline HTTP. |
| `services/invoice_skill_applier.py::InvoiceSkillApplier` | **EXISTANT MAIS PARTIEL** | Code présent et exercé hors webhook; pas d’appel depuis le chemin HTTP actif. |
| `services/extraction_manager.py::ExtractionManager` | **EXISTANT MAIS NON RACCORDE** | Pas d’appel par le webhook; dépend du symbole `InvoiceSchema` absent. |
| `InvoiceIndexer`, services d’embedding/similarité facture | **EXISTANT MAIS NON RACCORDE** | Code disponible; aucun appel par le webhook actif n’est établi. |
| Writers facture Neo4j | **EXISTANT MAIS NON RACCORDE** | Repositories présents mais sans appelant applicatif établi; schéma différent des données runtime relevées. |
| Graphe facture backend | **EXISTANT MAIS PARTIEL** | Route alternative présente; plusieurs nœuds renvoient des exemples simulés plutôt que des intégrations métier. |

La présence d’un composant, son exécution par un script ou un test et son
intégration au webhook sont trois faits distincts. Seul le premier chemin
décrit ci-dessus est qualifié d’actif dans les audits; aucun pipeline facture
complet de bout en bout n’a été démontré ici.

## 6. Contrats de facture et InvoiceSchema

- Aucune déclaration de `class InvoiceSchema` n’a été trouvée dans les sources
  applicatives actuelles inspectées.
- `schemas/invoice_schema.py` définit une classe `InvoiceData`.
- `agents/invoice_agent.py` définit une autre classe locale `InvoiceData`.
  Elles sont distinctes et ne doivent pas être considérées comme compatibles
  en raison de leur nom.
- Le fichier `invoice_agent.py` à la racine et
  `services/extraction_manager.py` importent le symbole absent `InvoiceSchema`.
  Ces voies sont **BLOQUÉ** par le contrat manquant; elles ne constituent pas
  le webhook actif.
- Les prompts, exemples et commentaires ne suffisent pas à reconstruire les
  types, contraintes, champs requis, validateurs ou valeurs par défaut de ce
  contrat.
- La décision documentaire est de maintenir `InvoiceSchema` gelé jusqu’à
  arbitrage. Aucune reconstruction ni substitution par `InvoiceData` n’est
  effectuée dans cet état des lieux.

## 7. Vérification des trois corrections

La présence effective dans les fichiers actuels a été vérifiée par inspection
ciblée; aucun test n’a été lancé pendant cet inventaire.

| Correction attendue | Preuve actuelle | Résultat |
|---|---|---|
| `api.py` utilise `route_prompt` | `api.py::run_router` importe `route_prompt` et lui transmet `prompt` ainsi que `image_provided`. | Présente. La route `/api/router` n’importe plus le symbole absent `router_intelligent`. |
| `ResearchAgent` utilise `retrieve_skill_for_task` | `agents/research_agent.py` instancie `SkillRouter` et appelle `retrieve_skill_for_task(task=query)`; le résultat optionnel est utilisé comme texte. | Présente dans le code actuel. |
| L’erreur d’IngestionManager vient de `message` plutôt que `last_error` | La branche d’erreur dans `services/ingestion_manager.py` utilise `result.get("message")`. | Présente dans le code actuel. |

Leur comportement à l’exécution demeure à confirmer par les tests ciblés
priorisés dans la Roadmap V2.

## 8. Pipeline bancaire

| Élément | Statut | Observation |
|---|---|---|
| Détection `AssetType.BANK_STATEMENT` par `IngestionManager` | **EXISTANT MAIS PARTIEL** | Le type est reconnu; cela ne constitue pas un parsing. |
| Branche bancaire | **BLOQUÉ** | Retourne `pending_implementation`, cible déclarée `banking_bus`. |
| `schemas/bank_statement_schema.py` | **BLOQUÉ** | Fichier présent de 0 octet au relevé. |
| `BankingAgent` | **CIBLE NON IMPLÉMENTÉE** | Aucune classe/fichier applicatif trouvée; seul un import commenté apparaît dans `IngestionManager`. |
| Parseur OFX `ofxparse` | **CIBLE NON IMPLÉMENTÉE** | Aucun import ni parseur trouvé dans le code Python actuel. |
| Parseur MT940 `mt940` | **CIBLE NON IMPLÉMENTÉE** | Aucun import ni parseur trouvé dans le code Python actuel. |
| Parseurs CSV et PDF bancaires | **CIBLE NON IMPLÉMENTÉE** | Aucune voie bancaire correspondante raccordée; ces formats doivent rester distincts. |
| Ledger bancaire applicatif | **CIBLE NON IMPLÉMENTÉE** | Aucun ledger opérationnel raccordé. `banking_bus` est une destination logique déclarée dans la branche en attente. |
| Allocations de rapprochement N↔N | **CIBLE NON IMPLÉMENTÉE** | Aucun modèle/service de rapprochement bancaire N↔N raccordé. |

`BankingRecord`, les paiements partiels/groupés, chèques, remises, virements,
prélèvements, LCR et retours sont des éléments de la cible documentaire. Ils ne
sont pas des fonctionnalités bancaires observées comme implémentées. Les
décisions métier nécessaires restent **BLOQUÉ**.

## 9. Qdrant

### 9.1 Service, montages et tailles

Le conteneur `dsh-qdrant` était **EXISTANT ET ACTIF** lors de l’inspection
Docker; son API locale sur `6333` répondait. Le bind mount actif est
`A:\dsh-core\qdrant_storage`, d’une taille totale observée de 4 366 859 237
octets. Le répertoire secondaire `A:\dsh-core\docker\qdrant_storage` mesure
3 553 976 763 octets et n’est lié à aucun second conteneur actif observé.

La liste des huit collections a répondu à une requête de lecture. Les comptes
actuels directement récupérés dans cette vérification étaient :

| Collection | Points vérifiés lors de la lecture du 20 octobre | Dernier relevé documenté exact | Remarque |
|---|---:|---:|---|
| `agent_skills` | 4 | 4 | Quatre points distincts. |
| `document_bus` | Non récupéré dans cette lecture | 0 le 19 octobre | La collection est listée; compte du 20 octobre non certifié. |
| `invoice_documents` | Non récupéré dans cette lecture | 3 le 19 octobre | Le writer applicatif actuel n’est pas attribué. |
| `mem0` | 0 | 0 | Collection de mémoire observée vide. |
| `mem0migrations` | 0 | 0 | Collection auxiliaire; rôle détaillé incertain. |
| `research_cache` | Non récupéré dans cette lecture | 0 le 19 octobre | La collection est raccordée au code de cache, mais était vide au relevé du 19 octobre. |
| `skills_catalog` | 0 selon la cartographie du 20 octobre | 0 le 19 octobre | Collection vide. |
| `teacher_student_metrics` | 16 selon la cartographie du 20 octobre | 16 le 19 octobre | Points de métriques, pas des définitions de skills. |

L’inventaire du 19 octobre rapporte huit collections et 23 points au total,
avec des comptages exacts à cette date. Le relevé du 20 octobre confirme le
nombre des collections et les collections de skills/métriques indiquées, mais
ne permet pas de certifier au même moment les comptes de toutes les autres
collections. Les observations ne sont pas fusionnées en un faux instantané
unique.

### 9.2 Skills dans Qdrant et interdiction de fusion

- `agent_skills` contient quatre points visant la même tâche d’extraction de
  TVA avec inversion HT/TTC.
- Leurs contenus ont des empreintes distinctes; **aucun doublon strict** entre
  ces quatre points n’est établi.
- Leur recouvrement fonctionnel est jugé **probable**, pas prouvé comme
  équivalence exacte.
- `skills_catalog` est vide.
- `teacher_student_metrics` contient 16 métriques; les inventaires ne
  confirment aucun événement `skill_created=true` ou `skill_reused=true`.
- Aucun stockage n’a été fusionné ou supprimé. La similarité des noms ou
  finalités ne suffit pas à autoriser une opération destructive.

## 10. Neo4j

### 10.1 Service et accès

Le conteneur `dsh-neo4j` est **EXISTANT ET ACTIF**, avec ports `7474` HTTP et
`7687` Bolt publiés et montage de données
`A:\dsh-core\neo4j_data` (541 130 919 octets au relevé). La page HTTP a répondu
`200` dans la cartographie du 20 octobre; une lecture de la base a alors
renvoyé `401 Unauthorized`. L’accès authentifié en lecture reste **BLOQUÉ**.

### 10.2 Données certifiées et divergence des relevés

- `NEO4J_INVENTORY.md`, relevé read-only du 19 octobre, rapporte 3 nœuds
  (`Fournisseur`, `Facture`, `MouvementBancaire`) et 2 relations
  (`A_EMIS`, `REGLE`).
- `DATABASE_STORAGE_MAP.md`, relevé du 20 octobre, indique que la requête de
  lecture a reçu `401` et que le contenu n’a pas pu être certifié.
- En conséquence, les 3 nœuds et 2 relations sont **certifiés par le rapport
  du 19 octobre**, mais leur persistance au relevé du 20 octobre n’a pas pu être
  vérifiée. La provenance métier de ces données n’est pas attribuée.

Le graphe runtime du relevé du 19 octobre utilise des labels français. Les
repositories/services du dépôt ciblent plutôt `Supplier`, `Invoice`, `Document`
et les relations `BELONGS_TO`/`CONTAINS`. Cette divergence de schéma est
observée; l’origine du graphe présent n’est pas démontrée.

`GraphRepository` et `Neo4jService` sont **EXISTANT MAIS NON RACCORDE** au
pipeline applicatif selon les recherches d’appelants. La présence du service
ou du code repository ne prouve pas que le dépôt ait écrit les données runtime.

## 11. Skills et apprentissage

| Stockage/composant | État réel |
|---|---|
| `skills/registry.json` | **EXISTANT ET ACTIF** via les routes skills de `api.py`; une entrée `ping_qdrant` au relevé. |
| `skills/ping_qdrant.py` | Fichier présent; le contenu observé est un affichage fixe, pas une sonde réseau démontrée. |
| `workspace/skills/invoice_smtrt_sas.json` | **EXISTANT MAIS PARTIEL**; JSON présent, version `1.0.0`, `usage_count=2` selon l’inventaire. |
| Qdrant `agent_skills` | **EXISTANT MAIS PARTIEL**; quatre points, recherche exercée par test; les variantes n’ont ni version ni état de cycle de vie attesté. |
| Qdrant `skills_catalog` | Présent et vide. Aucun catalogue canonique opérationnel n’est démontré. |
| Qdrant `teacher_student_metrics` | 16 métriques; aucune preuve de création/réutilisation de skills selon les indicateurs recensés. |
| `SkillOpt` | **CIBLE NON IMPLÉMENTÉE**; aucun module/configuration trouvé dans le dépôt audité. |
| `SKILL.md` métier DSH-Core | **CIBLE NON IMPLÉMENTÉE** dans le dépôt projet. Cinq fichiers `SKILL.md` repérés appartiennent à des packages sous `.venv-v3`, pas au registre métier du projet. |

Les empreintes des quatre points Qdrant `agent_skills` sont distinctes :
aucun doublon strict n’est établi. Le doublon fonctionnel est probable du fait
de leur tâche déclarée commune. L’entrée JSON `invoice_smtrt_sas` traite aussi
des factures, mais le rapport d’inventaire n’établit pas qu’elle soit
fonctionnellement identique aux règles des quatre points.

Les registres JSON et Qdrant sont des voies distinctes. Le cycle cible
`CANDIDATE`, `TESTING`, `APPROVED`, `PUBLISHED`, `REJECTED`, `RETIRED`,
l’approbation SkillOpt et la distribution Oracle après publication locale ne
sont pas constatés comme implémentés.

## 12. Mémoire, orchestration et observabilité

### 12.1 Mem0 et Qdrant mémoire

Le module racine `memory_manager.py` initialise un client Mem0; ses fonctions
`ajouter_souvenir` et `recuperer_contexte` sont présentes. `router.py::route_prompt`
utilise la récupération du contexte; l’enregistrement d’un skill peut ajouter
un souvenir. Cette voie est **EXISTANT MAIS PARTIEL** : le chemin facture ne
l’utilise pas comme persistance métier et la collection runtime `mem0` a été
mesurée vide.

Qdrant est un stockage actif. Son rôle comme backend du client Mem0 est déduit
de la configuration du client et de la collection observée; il n’en fait pas
une mémoire peuplée ou une source de vérité. La collection `mem0` vérifiée
était à zéro point.

`services/memory_manager.py`, `repositories/memory_repository.py` et
`memory/memory_manager.py::MultiLevelMemory` sont **EXISTANT MAIS NON RACCORDE**
selon l’absence d’instanciation/appelant applicatif établie par les audits.

### 12.2 LangGraph et nœuds simulés

Le code LangGraph est **EXISTANT MAIS PARTIEL** :

- `orchestrator.py` construit et compile un `StateGraph`, mais ses nœuds de
  triage/modèle retournent une logique élémentaire ou des réponses textuelles
  simulées; ce n’est pas l’orchestrateur métier complet.
- `backend/app/graphs/invoice_graph.py` construit et compile un graphe facture;
  les intégrations Vision/OCR et plusieurs opérations sont des placeholders ou
  des simulations avec données de démonstration.
- Les routes backend peuvent appeler ce graphe simulé. Le graphe ne constitue
  pas le pipeline facture actif décrit à la section 5.

LangGraph existe donc réellement dans le code et les dépendances inspectées;
les anciennes affirmations qui le décriraient comme absent ou à « 0 % » ne
décrivent pas l’état présent. Son usage en orchestration métier complète reste
**CIBLE NON IMPLÉMENTÉE**.

### 12.3 Langfuse

Les conteneurs `dsh-langfuse-web` et `dsh-langfuse-db` étaient en cours
d’exécution. Plusieurs composants Python importent `langfuse.observe` et
déclarent des décorateurs. Le service est **EXISTANT ET ACTIF** au niveau
infrastructure; l’instrumentation est **EXISTANT MAIS PARTIEL**. Le nombre,
la complétude et la qualité des traces applicatives n’ont pas été vérifiés.

## 13. MasterData

- Aucun classeur identifié comme référentiel MasterData n’a été trouvé.
- Un seul classeur Excel a été trouvé dans le périmètre utile :
  `workspace/invoices/iput effectués/test_excel.xlsx`; son emplacement et son
  nom le caractérisent comme fichier de test facture, pas comme MasterData.
- `services/supplier_repository.py` existe comme dépôt JSON de fournisseurs;
  `workspace/suppliers/suppliers.json` existe également. Le code
  `InvoiceSkillCreator`/`InvoiceSkillApplier` peut instancier ce repository,
  mais aucun raccordement de bout en bout vers une interface MasterData Excel
  n’est démontré.
- Aucune synchronisation Excel → référentiels ou pipeline MasterData complet
  n’a été trouvée. Les sociétés, comptes bancaires, clients, alias et
  catégories ne sont pas établis comme jeu de référentiels central raccordé.
- Le rôle Excel/MasterData validé dans les spécifications demeure
  **CIBLE NON IMPLÉMENTÉE**. Le repository fournisseurs isolé est
  **EXISTANT MAIS PARTIEL**; sa présence ne démontre pas une gouvernance de
  référentiels complète.

Les valeurs du classeur et du JSON fournisseurs n’ont pas été ouvertes ni
reproduites.

## 14. Hermes, Oracle et Telegram

La recherche de configurations, fichiers source et références non
documentaires du dépôt n’a pas trouvé de configuration Hermes Desktop, Hermes
Oracle ou Telegram.

| Capacité | Statut réel |
|---|---|
| Hermes Desktop | **CIBLE NON IMPLÉMENTÉE** dans le dépôt audité; aucune configuration trouvée. |
| Hermes Oracle | **CIBLE NON IMPLÉMENTÉE** dans le dépôt audité; aucune configuration trouvée. |
| Telegram et ses cinq canaux | **CIBLE NON IMPLÉMENTÉE** dans le dépôt audité; aucune intégration trouvée. |
| Staging Oracle et manifeste Oracle → local | **CIBLE NON IMPLÉMENTÉE**; règles documentées, mais code et transport non trouvés. |

La documentation canonique décrit des décisions de cible; elle ne constitue
pas la preuve d’une installation, d’un déploiement ni d’une intégration. Les
conteneurs observés (n8n, Qdrant, Neo4j, Langfuse et Open WebUI) ne sont pas des
preuves d’installation Hermes ou Telegram.

## 15. Documentation canonique et statut Git

Le statut Git ci-dessous correspond au relevé initial de la section 2.
`??` indique un fichier non suivi et ne signifie pas qu’il est absent ou
supprimé.

| Document | Présence | Git au relevé | Observation |
|---|---|---|---|
| `03_JOURNAL.md` (racine) | Présent | `??` | Journal canonique non suivi. |
| `dsh core complet.md` | Présent | `??` | Vision fonctionnelle canonique non suivie. |
| `dsh_core_master_specification_2026_2027.md` | Présent | `??` | Spécification maîtresse V2 non suivie. |
| `docs/02_ROADMAP.md` | Présent | `M` | Historique et Roadmap V2; modification déjà présente au début de l’inventaire. |
| `PIPELINE_OFFICIEL.md` | Présent | `??` | Rapport de raccordements. |
| `04_AUDIT_ARCHITECTURE.md` | Présent | `??` | Audit architecture. |
| `05_DECISIONS_ARCHITECTURE.md` | Présent | `??` | Décisions d’architecture. |
| `SKILLS_DUPLICATES_INVENTORY.md` | Présent | `??` | Inventaire des skills et variantes. |
| `DATABASE_STORAGE_MAP.md` | Présent | `??` | Cartographie des stockages. |
| `BANKING_ARCHITECTURE.md` | Présent | `??` | Audit bancaire. |
| `BANKING_RECORD_SPEC.md` | Présent | `??` | Spécification conceptuelle de BankingRecord. |
| `BANKING_SCHEMA_REVIEW.md` | Présent | `??` | Revue du schéma bancaire. |
| `BANKING_RELATIONS_REVIEW.md` | Présent | `??` | Revue des relations bancaires. |
| `QDRANT_INVENTORY.md` | Présent | `??` | Inventaire Qdrant daté. |
| `NEO4J_INVENTORY.md` | Présent | `??` | Inventaire Neo4j daté. |
| `DSH_CORE_CURRENT_STATE.md` | Présent | `??` | Ce document; il demeure non suivi. |

`docs/03_JOURNAL.md` apparaît également modifié (`M`) dans Git; il est distinct
du document racine `03_JOURNAL.md`.

## 16. Tableau des écarts

| Domaine | État réel | Cible validée | Écart constaté | Blocage | Prochaine preuve nécessaire |
|---|---|---|---|---|---|
| API et facture | Webhook raccordé à `InvoiceAgent`; contrôles facture non raccordés. | Pipeline documentaire orchestré et traçable. | Profilage, validations métier et persistance ne sont pas dans le webhook. | **BLOQUÉ** par `InvoiceSchema` pour les chemins qui l’importent. | Tests ciblés des corrections, contrat de schéma arbitré, test HTTP contrôlé. |
| `InvoiceSchema` | Symbole absent; deux `InvoiceData` distincts. | Contrat facture unique validé avant intégration. | Appelants dépendent d’un schéma absent. | **BLOQUÉ** jusqu’à l’arbitrage. | Décision de contrat et tests de validation JSON approuvés. |
| Banque | Détection de type présente; branche `pending_implementation`; schéma vide. | BankingRecord et allocations N↔N validés localement. | Aucun parseur, ledger ou rapprochement opérationnel. | **BLOQUÉ** sur les règles de comptes, allocations, devises, tolérances et remises. | Décisions métier puis relevés anonymisés et tests de parsers. |
| Qdrant | Service actif, 8 collections listées, stockages racine et secondaire présents. | Similarité/RAG et synchronisation logique contrôlée. | Second stockage inactif, identité non prouvée; certains comptes de points du 20 octobre non récupérés. | **ÉTAT INCONNU** pour l’équivalence des stockages et certains décomptes courants. | Comparaison read-only et reproductible; ne pas fusionner. |
| Neo4j | Conteneur actif; lecture authentifiée refusée le 20 octobre. Relevé du 19 octobre : 3 nœuds/2 relations. | Relations métier stables et validées. | Schéma runtime français divergent des repositories anglais; provenance incertaine. | **BLOQUÉ** par l’accès lecture actuel et la divergence des relevés. | Accès read-only autorisé et inventaire reproductible. |
| Skills | Registre JSON présent; 4 variantes Qdrant, catalogue vide, 16 métriques. | Registre canonique local et cycle de vie gouverné. | Stockages et versions/états non unifiés; redondance fonctionnelle probable. | **BLOQUÉ** par décision de registre et évaluation des variantes. | Audit des quatre contenus et décision métier/technique documentée. |
| Mem0 | Code de récupération présent; collection `mem0` vide au relevé. | Mémoire épisodique au service du contexte, jamais vérité métier. | Présence du code sans données persistées observées. | **ÉTAT INCONNU** pour activité récente et contenu ultérieur au relevé. | Lecture contrôlée de métadonnées et test sans écriture métier. |
| LangGraph | Deux graphes définis/compilés avec logique ou nœuds simulés. | Orchestrateur métier unique. | Aucun pipeline métier complet raccordé. | **CIBLE NON IMPLÉMENTÉE** pour l’orchestration complète. | Tests E2E de graphe avec intégrations réelles et preuve d’appel applicatif. |
| Langfuse | Conteneurs actifs; imports/décorateurs dans le code. | Observabilité des flux. | Traces effectives non vérifiées. | **ÉTAT INCONNU** pour les traces et leur couverture. | Vérifier une trace non sensible sur flux contrôlé. |
| MasterData | Un classeur de test, repository JSON fournisseurs et fichier JSON présents. | Interface Excel et référentiels locaux validés. | Pas d’interface/synchronisation Excel raccordée; référentiels centraux incomplets. | **CIBLE NON IMPLÉMENTÉE** pour le MasterData complet. | Contrat Excel, identifiants stables et validation locale. |
| Hermes/Oracle/Telegram | Aucune configuration/intégration trouvée. | Hermes local, Oracle en staging et Telegram avec cinq canaux. | Aucune implémentation observée. | **CIBLE NON IMPLÉMENTÉE**. | Preuve de configuration et tests de permissions avant qualification active. |
| Documentation et Git | Documents présents; plusieurs sont `??`, Roadmap modifiée; nombreux changements préexistants. | Documentation canonique cohérente et versions suivies. | Les sources canoniques ne sont pas toutes suivies par Git; worktree non propre. | **ÉTAT INCONNU** quant à l’intention de suivi de chaque `??`. | Décision propriétaire sur l’indexation Git; aucun nettoyage automatique. |

## 17. Prochaines actions issues de la Roadmap V2

Les actions sont reprises dans l’ordre du « Prochain sprint concret » de
`docs/02_ROADMAP.md`. Les éléments marqués **BLOQUÉ** exigent un arbitrage;
aucune hypothèse ne les contourne.

1. **Tester les trois corrections déjà présentes.** Tests ciblés pour
   `route_prompt`, `retrieve_skill_for_task` et `message`; utiliser des fixtures
   et doubles, sans écriture dans les bases actives.
2. **Obtenir un accès Neo4j en lecture authentifiée.** Confirmer le schéma,
   l’origine vérifiable des données et l’écart entre les relevés du 19 et du
   20 octobre.
3. **Comparer sans modification les deux stockages Qdrant.** Capturer des
   inventaires/empreintes compatibles; établir les éléments prouvés et les
   inconnues, sans démarrer une seconde instance.
4. **Évaluer les quatre variantes `agent_skills`.** Comparer leurs fonctions et
   cas de test; aucune fusion ou suppression avant décision.
5. **Décider le registre canonique des skills.** Définir stockage, versions,
   états, transitions et autorité locale de publication.
6. **Résoudre les décisions bancaires bloquantes.** Propriété des comptes,
   objets rapprochables, allocations, devises, tolérances, parties et remises
   de chèques.
7. **Résoudre `InvoiceSchema` avant tout raccordement dépendant.** Définir le
   contrat exact et sa relation aux deux `InvoiceData`.
8. **Figer les contrats des trois tableaux.** Pièces comptables, validation
   locale avant ventilation, et rapprochement bancaire avec audit et
   validation humaine.

L’implémentation de schémas et services vient seulement après ces preuves et
décisions, conformément à la Roadmap V2.

## 18. Limites et incertitudes

- Le relevé du contenu Neo4j diffère entre les rapports datés du 19 et du
  20 octobre; le relevé récent n’a pas pu authentifier la lecture.
- Les comptes exacts de toutes les collections Qdrant ne sont pas tous
  certifiés dans la lecture du 20 octobre; les anciens chiffres sont attribués
  explicitement à leur date de mesure.
- L’équivalence des deux stockages Qdrant n’est pas prouvée malgré leur rôle
  vraisemblablement apparenté.
- Les chemins applicatifs sont vérifiés statiquement; aucune requête HTTP
  facture ni test ciblé n’a été exécuté durant cette génération.
- La présence du conteneur Langfuse ne confirme pas que les traces soient
  écrites ou complètes.
- La présence du fichier `.env` a été observée indirectement dans la liste du
  dépôt, mais son contenu n’a pas été lu. La présence, validité et migration
  des secrets restent **ÉTAT INCONNU**.
- Les documents de vision, spécification et roadmap établissent des cibles et
  décisions; ils ne certifient pas une installation ni un raccordement.
- Les nombres de fichiers et tailles de stockage sont des mesures de
  l’inventaire du 20 octobre et peuvent évoluer.

