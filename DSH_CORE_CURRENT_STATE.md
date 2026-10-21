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
19. [Web, scraping et Tor](#19-web-scraping-et-tor)

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
| Stockage Qdrant secondaire | **DIFFÉRENT** du stockage actif | `A:\dsh-core\docker\qdrant_storage`, 3 553 976 763 octets (1 652 fichiers, 775 répertoires); aucune seconde instance active associée. |
| Neo4j `dsh-neo4j` | **EXISTANT ET ACTIF** | Ports `7474` HTTP et `7687` Bolt; `A:\dsh-core\neo4j_data`, 541 130 919 octets (85 fichiers). |
| Langfuse web et base | **EXISTANT ET ACTIF** au niveau des conteneurs | Web `3001→3000`; base PostgreSQL `5432`; répertoire `langfuse_db`, 68 148 891 octets. |
| Open WebUI | **EXISTANT ET ACTIF** au niveau du conteneur | Port `3000→8080`; `open-webui_data`, 932 511 306 octets. |
| n8n | **EXISTANT ET ACTIF** au niveau du conteneur | Port `5678`; `n8n_data`, 11 262 646 octets. |

La comparaison non destructive P1-2 les a déclarés **DIFFÉRENTS** : les
collections, les chemins, les tailles et les nombres de fichiers divergent.
Seul le stockage racine est relié à l'instance active observée. Aucune
équivalence ni autorité du stockage secondaire n'en est déduite; aucune fusion
ou suppression n'a été effectuée.

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

`n8n_api.py` définit `POST /api/v1/ingest` et reçoit le fichier en multipart.
`response_mode` vaut `legacy` par défaut : le webhook conserve alors l’appel
historique à `agents.invoice_agent.InvoiceAgent.process_invoice()` et son
enveloppe de réponse. Une demande explicite `response_mode=canonical` passe
par `IngestionManager.ingest_canonical_invoice()` et renvoie l’InvoiceSchema
sérialisé en JSON. Aucun workflow n8n existant n’a été modifié. Ces routes et
leurs deux modes ont été vérifiés par tests ciblés avec doubles; aucune requête
à un service réel n’a été exécutée.

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
├─ mode par défaut (legacy)
│  → agents.invoice_agent.InvoiceAgent.process_invoice(...)
│  → enveloppe JSON historique à n8n
└─ mode canonical demandé explicitement
   → IngestionManager.ingest_canonical_invoice(...)
   → InvoiceAgent.process_invoice(...) une seule fois
   → validation/adaptation vers InvoiceSchema
   → réponse JSON canonique à n8n
```

Le mode legacy reste le comportement par défaut. Le mode canonical expose le
contrat et les données legacy sans remplacer l’enveloppe historique par défaut.
Les tests ne démontrent pas un pipeline comptable complet de bout en bout :
comptabilisation, persistance métier, interface humaine et rapprochement
bancaire restent hors périmètre.

### 5.2 Composants et niveaux de raccordement

| Composant | Statut | Constat vérifié |
|---|---|---|
| `n8n_api.py` — `POST /api/v1/ingest` | **RACCORDEMENT CIBLÉ VALIDÉ** | Le mode legacy conserve la réponse historique par défaut; le mode canonical est disponible sur demande explicite. Aucun workflow n8n n’a été modifié. |
| `agents/invoice_agent.py::InvoiceAgent` | **RACCORDEMENT CANONIQUE VALIDÉ** | Conserve `InvoiceData` et l’interface legacy; expose l’adaptation et la validation vers `InvoiceSchema`. |
| `invoice_agent.py::InvoiceAgent` racine | **RACCORDEMENT CANONIQUE VALIDÉ** | Convertit les données legacy avec le contrat `InvoiceSchema` sans supprimer l’interface historique. |
| `services/ingestion_manager.py::IngestionManager` | **RACCORDEMENT CANONIQUE VALIDÉ** | Fournit `CanonicalInvoiceIngestionResult`; le chemin canonique appelle l’agent une fois et conserve l’enveloppe legacy séparément. |
| `services/extraction_manager.py::ExtractionManager` | **RACCORDEMENT CANONIQUE VALIDÉ** | Valide les payloads d’extraction avec `InvoiceSchema`; la méthode historique conserve son enveloppe. |
| `services/document_profiler.py::DocumentProfiler` | **EXISTANT MAIS PARTIEL** | Code et test présents; pas d’appel par le webhook facture. |
| `services/routing_engine.py::RoutingEngine` | **EXISTANT MAIS PARTIEL** | Code et test présents; pas d’appel par le webhook facture. |
| `services/business_extraction_manager.py::BusinessExtractionManager` | **EXISTANT MAIS PARTIEL** | Exercice dans les tests de profilage; pas de raccordement au webhook. |
| `services/invoice_reconciliation_engine.py::InvoiceReconciliationEngine` | **EXISTANT MAIS PARTIEL** | Code présent et appelé par un script de test; non raccordé au webhook. |
| `services/tax_classification_engine.py::TaxClassificationEngine` | **EXISTANT MAIS PARTIEL** | Code présent et appelé par un script de test; non raccordé au webhook. |
| `services/invoice_skill_creator.py::InvoiceSkillCreator` | **EXISTANT MAIS PARTIEL** | Code présent et exercé hors webhook; ne prouve pas une création automatique depuis le pipeline HTTP. |
| `services/invoice_skill_applier.py::InvoiceSkillApplier` | **EXISTANT MAIS PARTIEL** | Code présent et exercé hors webhook; pas d’appel depuis le chemin HTTP actif. |
| `InvoiceIndexer`, services d’embedding/similarité facture | **EXISTANT MAIS NON RACCORDE** | Code disponible; aucun appel par le webhook actif n’est établi. |
| Writers facture Neo4j | **EXISTANT MAIS NON RACCORDE** | Repositories présents mais sans appelant applicatif établi; schéma différent des données runtime relevées. |
| Graphe facture backend | **EXISTANT MAIS PARTIEL** | Route alternative présente; plusieurs nœuds renvoient des exemples simulés plutôt que des intégrations métier. |

Les raccordements canoniques ciblés sont vérifiés par tests et ne constituent
pas une preuve de pipeline comptable complet. Aucun test n’a appelé un modèle,
un réseau, un webhook réel ou un stockage métier.

## 6. Contrats de facture et InvoiceSchema

`schemas/invoice_schema.py` définit le contrat Pydantic canonique
`InvoiceSchema`, ses validateurs et ses adaptateurs legacy. Des tests ciblés
valident sa compatibilité avec les payloads réels utilisés par
`ExtractionManager` et les deux `InvoiceAgent`; les classes legacy
`InvoiceData` sont conservées.

Les raccordements ciblés validés sont :

- `services/extraction_manager.py::ExtractionManager` ;
- `invoice_agent.py::InvoiceAgent` à la racine ;
- `agents/invoice_agent.py::InvoiceAgent` ;
- `services/ingestion_manager.py::IngestionManager`, qui retourne
  `CanonicalInvoiceIngestionResult` ;
- `n8n_api.py`, avec réponse legacy par défaut et mode canonical uniquement
  sur demande explicite.

Les tests ciblés ont réussi avec des doubles et sans appel réel à un modèle,
au réseau ou à un stockage. Les commits de preuve pertinents sont :
`9a80288` (contrat canonique des factures), `5c04e98` (raccordement
ExtractionManager), `3a24038` (InvoiceAgent racine), `e8dc69b` (InvoiceAgent
ingestion), `0eb6baa` (IngestionManager) et `4cac8cf` (mode canonique
optionnel du webhook n8n).

Cette clôture est technique et limitée au contrat et aux raccordements ciblés.
La comptabilisation, la persistance métier, l’interface humaine et le
rapprochement bancaire restent hors périmètre; le pipeline comptable complet
n’est pas déclaré terminé.

## 7. Vérification des trois corrections

Les trois corrections ont réussi la compilation ciblée et les tests isolés
avec doubles. Aucun test E2E global n'a encore été réalisé.

| Correction attendue | Preuve actuelle | Résultat |
|---|---|---|
| `api.py` utilise `route_prompt` | `api.py::run_router` importe `route_prompt` et lui transmet `prompt` ainsi que `image_provided`. | Compilation ciblée et test isolé réussis; aucune validation E2E globale. |
| `ResearchAgent` utilise `retrieve_skill_for_task` | `agents/research_agent.py` instancie `SkillRouter` et appelle `retrieve_skill_for_task(task=query)`; le résultat optionnel est utilisé comme texte. | Compilation ciblée et test isolé réussis; aucune validation E2E globale. |
| L’erreur d’IngestionManager vient de `message` plutôt que `last_error` | La branche d’erreur dans `services/ingestion_manager.py` utilise `result.get("message")`. | Compilation ciblée et test isolé réussis; aucune validation E2E globale. |

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
octets (1 574 fichiers, 738 répertoires). Le répertoire secondaire
`A:\dsh-core\docker\qdrant_storage` mesure 3 553 976 763 octets (1 652
fichiers, 775 répertoires) et n’est lié à aucun second conteneur actif observé.

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

- P1-2 a comparé les deux stockages sans modification et a conclu
  **DIFFÉRENTS**. Le stockage actif contient `agent_skills`, `document_bus`,
  `invoice_documents`, `mem0`, `mem0migrations`, `research_cache`,
  `skills_catalog` et `teacher_student_metrics`. Le stockage secondaire
  contient `document_bus`, `invoices`, `mem0`, `mem0_memories` et
  `mem0migrations`; seules `document_bus`, `mem0` et `mem0migrations` sont
  communes. Cinq collections sont exclusives à l'actif et deux au secondaire.
- Seul `A:\dsh-core\qdrant_storage` est relié à l'instance active observée.
  L'équivalence ou l'autorité du stockage secondaire ne peut pas être déduite.
- P1-3 a examiné en lecture seule les quatre points de `agent_skills`, qui
  visent la même tâche d'extraction de TVA avec inversion visuelle HT/TTC.
- Les points ont des identifiants, contenus, dates et compteurs d'usage
  différents, et leurs vecteurs de 768 dimensions ont des similarités cosinus
  entre 94,90 % et 99,04 %. Aucun doublon strict ni aucune contradiction de
  règle n'est établi; il s'agit de doublons fonctionnels **probables**, avec
  des formulations partiellement complémentaires.
- Aucun champ de version ou de cycle de vie exploitable n'est présent dans
  leurs payloads. Un seul point a `usage_count=1`; les trois autres ont
  `usage_count=0`. Aucun skill canonique ne peut être choisi immédiatement
  sans risque de perte d'information.
- La décision P1-4 établit désormais que ces quatre points restent inchangés
  et sont classés `LEGACY_CANDIDATE_UNREVIEWED`; aucun n'est désigné comme
  canonique. Le registre canonique local décidé n'est pas implémenté.
- `skills_catalog` est vide.
- La cible est une projection Qdrant de recherche sémantique, reconstructible
  intégralement depuis le registre canonique local; Qdrant ne décide pas d'une
  publication. Aucune collection n'est migrée ou supprimée.
- `teacher_student_metrics` contient 16 métriques; les inventaires ne
  confirment aucun événement `skill_created=true` ou `skill_reused=true`.
- Aucune fusion ou suppression de stockage ni de skill n'a été effectuée ou
  n'est autorisée par ces audits.

## 10. Neo4j

### 10.1 Service et accès

Le conteneur `dsh-neo4j` est **EXISTANT ET ACTIF**, avec ports `7474` HTTP et
`7687` Bolt publiés et montage de données
`A:\dsh-core\neo4j_data` (541 130 919 octets au relevé). L'audit authentifié
du 15 octobre 2026 a réussi avec `cypher-shell --access-mode read`, sur Neo4j
Community `2026.09.0`. Les bases `neo4j` et `system` étaient en ligne; la base
auditée était `neo4j`. Statut : **PARTIAL_READ_ONLY**. Ce mode a protégé
l'audit, mais l'édition Community n'a pas permis de certifier un rôle
exclusivement read-only.
Les dates des 19 et 20 octobre mentionnées dans les relevés antérieurs restent
des dates documentaires non vérifiées; elles ne remplacent pas la date réelle
de cet audit, le 15 octobre 2026.

### 10.2 Données certifiées et divergence des relevés

- L'audit authentifié du 15 octobre rapporte **3 nœuds** (`Facture` : 1,
  `Fournisseur` : 1, `MouvementBancaire` : 1) et **2 relations**
  (`Fournisseur` — `A_EMIS` → `Facture` : 1;
  `MouvementBancaire` — `REGLE` → `Facture` : 1).
- Aucune contrainte n'a été retournée; deux index techniques de lookup/token
  étaient en ligne.
- La provenance métier des nœuds reste inconnue et aucun writer applicatif
  n'est attribué.

Le graphe runtime utilise des labels français. Les repositories/services du
dépôt ciblent principalement `Document`, `Invoice`, `Supplier`, `Skill` et
`Task`, avec notamment les relations `BELONGS_TO`/`CONTAINS`. Cette divergence
de schéma est observée; l'origine du graphe présent n'est pas démontrée.

`GraphRepository` et `Neo4jService` sont **EXISTANT MAIS NON RACCORDE** au
pipeline applicatif selon les recherches d'appelants. La présence du service
ou du code repository ne prouve pas que le dépôt ait écrit les données runtime.
N'effectuer aucune écriture Neo4j tant qu'un mécanisme permanent de lecture
seule n'est pas défini.

## 11. Skills et apprentissage

| Stockage/composant | État réel |
|---|---|
| `skills/registry.json` | `LEGACY_ACTIVE`; fichier utilisé par les routes skills de `api.py`, avec une entrée `ping_qdrant` au relevé. |
| `skills/*.py` | `LEGACY_ARTIFACT`; fichiers conservés, sans statut de version canonique établi. |
| `workspace/skills/invoice_smtrt_sas.json` | `LEGACY_DOMAIN_SKILL`; JSON présent, version `1.0.0`, `usage_count=2` selon l’inventaire. |
| Qdrant `agent_skills` | `LEGACY_VECTOR_PROJECTION`; quatre points conservés, recherche exercée par test; aucun versionnage ou cycle de vie exploitable attesté. |
| Qdrant `skills_catalog` | `UNUSED_EMPTY`; présent et vide, pas le registre canonique. |
| Qdrant `teacher_student_metrics` | 16 métriques; aucune preuve de création/réutilisation de skills selon les indicateurs recensés. |
| Mem0 | `CONTEXT_ONLY`; souvenirs contextuels et événements d'usage, pas autorité ni publication de skills. |
| Oracle | Staging uniquement pour produire des candidats et preuves de tests; aucune approbation ou publication directe. |
| `SkillOpt` | **CIBLE NON IMPLÉMENTÉE**; aucun module/configuration trouvé dans le dépôt audité. |
| `SKILL.md` métier DSH-Core | **CIBLE NON IMPLÉMENTÉE** dans le dépôt projet. Cinq fichiers `SKILL.md` repérés appartiennent à des packages sous `.venv-v3`, pas au registre métier du projet. |

Les empreintes des quatre points Qdrant `agent_skills` sont distinctes :
aucun doublon strict n’est établi. Le doublon fonctionnel est probable du fait
de leur tâche déclarée commune. L’entrée JSON `invoice_smtrt_sas` traite aussi
des factures, mais le rapport d’inventaire n’établit pas qu’elle soit
fonctionnellement identique aux règles des quatre points.

L'architecture du registre est décidée mais **non implémentée**. La cible
documentaire est décrite dans `SKILLS_REGISTRY_ARCHITECTURE.md`; elle prévoit
`skills/catalog/<skill_id>/manifest.json` et des artefacts versionnés. Cette
cible n'a pas été créée. Les voies existantes restent legacy et aucune n'est
migrée ou supprimée. Les skills facture suivent le contrat général avec des
champs de domaine supplémentaires, sans second registre.

Le cycle décidé est `CANDIDATE` → `TESTING` → `APPROVED` → `PUBLISHED`, avec
`REJECTED` et `RETIRED` comme sorties. Seuls les skills publiés sont utilisables
automatiquement en production; les skills `TESTING` sont limités aux tests
isolés. La publication demeure l'autorité de DSH-Core local. Qdrant est une
projection sémantique reconstructible depuis le registre; Mem0 reste
contextuel et Oracle écrit uniquement en staging.

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
| API et facture | Contrat `InvoiceSchema` et raccordements ciblés validés; webhook legacy par défaut et mode canonical optionnel. | Pipeline documentaire orchestré et traçable. | Comptabilisation, persistance métier, interface humaine et rapprochement bancaire restent hors périmètre. | Pipeline comptable complet non terminé. | Décisions P1-7 puis étapes métier et E2E contrôlé. |
| `InvoiceSchema` | Symbole absent; deux `InvoiceData` distincts. | Contrat facture unique validé avant intégration. | Appelants dépendent d’un schéma absent. | **BLOQUÉ** jusqu’à l’arbitrage. | Décision de contrat et tests de validation JSON approuvés. |
| Banque | Détection de type présente; branche `pending_implementation`; schéma vide. | BankingRecord et allocations N↔N validés localement. | Aucun parseur, ledger ou rapprochement opérationnel. | **BLOQUÉ** sur les règles de comptes, allocations, devises, tolérances et remises. | Décisions métier puis relevés anonymisés et tests de parsers. |
| Web / scraping / Tor | DDGS, Playwright, `/api/scrape` et wrapper MCP présents. | Recherche Web sûre et Tor explicitement configuré et validé. | Scraping partiel/non validé; wrapper non raccordé; Tor reconnu mais non fonctionnel; SearXNG documenté seulement. | Web **EXISTANT MAIS PARTIEL**; Tor **BLOQUÉ** jusqu'au traitement SSRF et au contrôle réseau. | Chantier reporté; sécuriser accès, destinations et redirections avant réactivation. |
| Qdrant | Service actif; P1-2 comparatif exécuté, P1-3 des quatre skills exécuté techniquement. | Projection sémantique reconstructible depuis le registre canonique local. | Stockages **DIFFÉRENTS**; la projection canonique cible et son registre ne sont pas implémentés. | P1-4 **DÉCISION D'ARCHITECTURE TERMINÉE**; implémentation non commencée. | Garder les stockages existants sans migration; définir le schéma JSON canonique et ses tests en P1-5. |
| Neo4j | Conteneur actif; authentification réussie en session `read`; `PARTIAL_READ_ONLY`; 3 nœuds, 2 relations, aucune contrainte et deux index techniques. | Relations métier stables et validées. | Schéma runtime français divergent des repositories anglais; provenance inconnue; rôle exclusivement read-only non certifié. | **BLOQUÉ** pour toute écriture tant qu'un mécanisme permanent de lecture seule n'est pas défini. | Établir la provenance quand elle est vérifiable et définir une protection permanente avant toute écriture. |
| Skills | Plusieurs voies legacy présentes; 4 points `agent_skills`, catalogue vide, 16 métriques. | Registre canonique local versionné, publication gouvernée; Qdrant en projection. | L'architecture est décidée; schéma et implémentation restent à faire. Les quatre points restent inchangés `LEGACY_CANDIDATE_UNREVIEWED`. | P1-4 **DÉCISION D'ARCHITECTURE TERMINÉE**; implémentation non commencée. | P1-5 : définir le schéma JSON canonique et les tests; aucune migration ni suppression. |
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

1. **Corrections ciblées des trois appels : exécutées et validées.** La
   compilation ciblée et les tests isolés avec doubles ont réussi pour
   `route_prompt`, `retrieve_skill_for_task` et `message`. Aucun test E2E global
   n'a encore été réalisé.
2. **P1-1 — Audit Neo4j en lecture authentifiée exécuté.** L'accès et
   l'inventaire runtime ont été obtenus en session `read`; la provenance reste
   inconnue et le rôle exclusivement read-only n'est pas certifié. Aucune
   écriture avant définition d'un mécanisme permanent de lecture seule.
3. **P1-2 — Comparaison non destructive des stockages Qdrant exécutée :
   `DIFFÉRENTS`.** Seul le stockage racine est relié à l'instance active
   observée; aucune équivalence n'est déduite.
4. **P1-3 — Audit technique des quatre `agent_skills` exécuté.** Ils sont des
   doublons fonctionnels probables, mais pas stricts; aucune fusion ou
   suppression n'a eu lieu.
5. **P1-4 — Décision d'architecture du registre canonique : terminée.**
   L'architecture est documentée dans `SKILLS_REGISTRY_ARCHITECTURE.md`;
   l'implémentation n'a pas commencé. Les quatre points restent inchangés et
   `LEGACY_CANDIDATE_UNREVIEWED`.
6. **P1-5 — Définir le schéma JSON canonique et les tests du registre.**
   Aucun schéma ni test d'implémentation n'est créé par la décision P1-4.
7. **P1-6 — InvoiceSchema : TECHNIQUEMENT TERMINÉ.** Le contrat canonique et
   les raccordements ciblés sont validés par tests; le pipeline comptable
   complet reste hors périmètre.
8. **P1-7 — Résoudre les décisions bancaires bloquantes.** Propriété des comptes,
   objets rapprochables, allocations, devises, tolérances, parties et remises
   de chèques.
9. **Figer les contrats des trois tableaux.** Pièces comptables, validation
   locale avant ventilation, et rapprochement bancaire avec audit et
   validation humaine.

L’implémentation de schémas et services vient seulement après ces preuves et
décisions, conformément à la Roadmap V2.

## 18. Limites et incertitudes

- Les mentions des 19 et 20 octobre dans les rapports Neo4j et de stockage
  restent des dates documentaires non vérifiées. L'audit authentifié daté du
  15 octobre fournit un relevé structurel, sans établir la provenance des
  données ni un privilège permanent exclusivement read-only.
- Les deux stockages Qdrant sont **DIFFÉRENTS** selon la comparaison P1-2;
  les nombres de fichiers et tailles sont des mesures d'inventaire et peuvent
  évoluer.
- Les quatre points `agent_skills` sont des doublons fonctionnels probables,
  mais leurs différences de contenu excluent de les traiter comme doublons
  stricts; le choix canonique reste bloqué.
- Aucun test E2E comptable n'a été exécuté. Les raccordements InvoiceSchema
  ont passé leurs tests ciblés avec doubles; aucun modèle, réseau, webhook
  réel ou stockage métier n'a été appelé.
- La présence du conteneur Langfuse ne confirme pas que les traces soient
  écrites ou complètes.
- La présence du fichier `.env` a été observée indirectement dans la liste du
  dépôt, mais son contenu n’a pas été lu. La présence, validité et migration
  des secrets restent **ÉTAT INCONNU**.
- Les documents de vision, spécification et roadmap établissent des cibles et
  décisions; ils ne certifient pas une installation ni un raccordement.
- Les nombres de fichiers et tailles de stockage sont des mesures de
  l’inventaire du 20 octobre et peuvent évoluer.

## 19. Web, scraping et Tor

- **Web : EXISTANT MAIS PARTIEL.** La recherche DDGS / DuckDuckGo et la
  stratégie DDGS puis Playwright sont présentes. La route `/api/scrape` existe,
  mais n'a pas été validée; le wrapper `mcp_scrape.py` existe sans raccordement
  MCP actif démontré.
- **Tor : BLOQUÉ.** Les chaînes `.onion` sont reconnues et la route de scraping
  tente un proxy SOCKS, mais la disponibilité du proxy n'est pas réellement
  vérifiée. `ResearchAgent._strategy_tor` est un stub et aucune configuration
  Tor active n'a été trouvée dans Docker Compose.
- SearXNG est documenté seulement et n'est pas implémenté.
- Les risques SSRF, les redirections non contrôlées, l'accès possible aux
  adresses locales ou internes et l'absence de contrôle d'accès visible doivent
  être traités avant toute réactivation.
- Ce chantier est **REPORTÉ** et ne bloque pas le sprint actuel, Qdrant, les
  skills, la banque ni `InvoiceSchema`. Aucun service, test réseau,
  installation ou correction n'est lancé dans cet état.
