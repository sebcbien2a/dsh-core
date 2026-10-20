# Audit de l'architecture réellement présente — DSH-Core

## Sources et périmètre

Cette synthèse s'appuie uniquement sur les quatre rapports suivants :

- `IMPORTS_BROKEN_REPORT.md`
- `QDRANT_INVENTORY.md`
- `NEO4J_INVENTORY.md`
- `PIPELINE_OFFICIEL.md`

Les statuts décrivent les raccordements constatés dans ces rapports. Ils ne
constituent ni une proposition d'évolution ni une garantie de disponibilité en
production. Pour les collections Qdrant et Neo4j, les nombres et données
indiqués correspondent aux relevés runtime rapportés dans les inventaires.

## 1. Architecture réellement présente

Le dépôt comporte plusieurs voies applicatives et plusieurs implémentations
parallèles de facture, skills et mémoire. Elles ne forment pas un pipeline
unique de bout en bout.

- **Factures :** la route `n8n_api.py` (`POST /api/v1/ingest`) appelle
  `agents.invoice_agent.InvoiceAgent`. L'agent extrait un JSON avec Ollama
  Vision, le valide avec son `InvoiceData` local et renvoie le résultat.
  Le pipeline décrit ne relie pas cet agent aux moteurs de profilage,
  d'extraction métier, de rapprochement, de classification fiscale ou à une
  persistance Qdrant/Neo4j. Une autre route backend invoque un graphe dont les
  étapes sont simulées.
- **Skills :** l'API de skills utilise le registre JSON de
  `memory_manager.py` à la racine. `router.py::route_prompt`, appelé par
  `dsh_harness.py`, recherche un skill et du contexte, puis retourne une
  décision sans exécuter le skill. Une voie distincte de skills Qdrant existe
  via `SkillsRepository`, `SkillRouter` et `SkillCreatorAgent`; la tentative
  d'accès depuis `ResearchAgent` utilise une méthode inexistante.
- **Mémoire :** le gestionnaire racine regroupe Mem0 et LlamaIndex/Qdrant
  `document_bus`; le routeur utilise la récupération de contexte Mem0 et
  `ResearchAgent` peut lire le retriever documentaire. Les collections `mem0`
  et `document_bus` étaient vides au relevé. Un second gestionnaire dans
  `services/` combine Qdrant et Neo4j, mais n'a pas d'instanciation repérée.
  `memory/memory_manager.py` définit encore une autre mémoire chaud/froid,
  sans appelant courant identifié.
- **Stockages :** Qdrant était accessible avec 8 collections présentes et
  23 vecteurs au total. Certaines collections sont reliées au code sans
  contenir de points au relevé; d'autres contiennent des points sans writer
  actuel identifié. Neo4j était accessible avec 3 nœuds et 2 relations. Les
  labels et relations observés dans la base diffèrent du schéma déclaré par
  `GraphRepository` et `Neo4jService`; l'origine des données n'est pas établie.
- **Contrats et imports :** `InvoiceSchema` est importé par des appelants, mais
  n'est pas défini dans `schemas/invoice_schema.py`. Le schéma expose
  `InvoiceData`; l'agent facture dans `agents/` possède aussi une définition
  locale homonyme. L'API routeur demande `router_intelligent`, absent de
  `router.py`, qui expose `route_prompt`. `ResearchAgent` demande
  `find_relevant_skill`, absent de `SkillRouter`, dont la méthode réelle est
  `retrieve_skill_for_task` et renvoie une chaîne ou `None`.

## 2. Composants ACTIFS

Ici, **ACTIF** désigne un chemin que les rapports identifient comme effectivement
appelé dans une entrée applicative ou comme opérationnel dans le flux décrit.
Cela ne signifie pas que le composant traite tous les cas ni que son stockage
contient des données.

| Composant | Rôle observé |
|---|---|
| `n8n_api.py` — `POST /api/v1/ingest` | Point d'entrée HTTP qui appelle l'agent de facture sous `agents/`. |
| `agents/invoice_agent.py::InvoiceAgent` | Extraction facture par Ollama Vision et validation avec son modèle local. |
| `api.py` — endpoints `/api/skills/register`, `/api/skills/find`, `/api/skills` | Entrées API raccordées au gestionnaire racine de skills. |
| `memory_manager.py` racine — registre JSON | Enregistrement et recherche des skills du chemin API. |
| `agents/research_agent.py` — cache `research_cache` | Lecture et écriture du cache au cours des recherches concernées. |
| Qdrant — service runtime | Service accessible au relevé; 8 collections présentes, 23 vecteurs au total. |
| Neo4j — service runtime | Service accessible au relevé; 3 nœuds et 2 relations présents. Ces données ne prouvent pas l'utilisation du repository par l'application. |

## 3. Composants PARTIELLEMENT ACTIFS

| Composant | Limite constatée |
|---|---|
| `services/ingestion_manager.py::IngestionManager` | Exécuté par un test; pas par le webhook facture. Son chemin d'erreur attend `last_error` alors que l'agent renvoie `message`. |
| `DocumentProfiler`, `RoutingEngine`, `BusinessExtractionManager` | Employés dans des scripts/tests, mais non raccordés au webhook facture. |
| `InvoiceReconciliationEngine`, `TaxClassificationEngine` | Appelés par un script de test, pas par le webhook. |
| `schemas/invoice_schema.py::InvoiceData` | Importé par le gestionnaire d'extraction et des tests; modèle distinct de la classe locale de l'agent. |
| `InvoiceSkillCreator`, `InvoiceSkillApplier` | Parcours invoice-skill exercé par des scripts/tests, sans liaison au webhook facture. |
| `backend/app/n8n_api.py` et `backend/app/graphs/invoice_graph.py` | Route alternative; les opérations du graphe facture sont simulées. |
| `router.py::route_prompt` | Appelé par `dsh_harness.py`; retrouve un skill/contexte et retourne une décision, mais le harnais ne lance pas le skill. |
| `api.py::run_router` | Route présente, mais import de `router_intelligent` absent; elle échoue au lieu d'utiliser `route_prompt`. |
| `agents/skill_creator_agent.py`, `repositories/skills_repository.py` et `agent_skills` | Chemin de création/recherche Qdrant exercé par le test d'apprentissage; non intégré au registre JSON de l'API. 4 vecteurs étaient présents. |
| `services/skill_router.py::retrieve_skill_for_task` | Méthode réelle utilisée par le test d'apprentissage; l'appel correspondant de `ResearchAgent` vise un nom absent. |
| `agents/research_agent.py` — accès aux skills | Flux de recherche présent, mais le lookup de skill échoue sur `find_relevant_skill`; l'exception est interceptée. |
| `agents/teacher_student_agent.py` | Raccordé à l'enregistrement de métriques de recherche; la création de skill dépend d'une branche d'escalade qui n'est normalement pas atteinte. |
| `memory_manager.py` racine — Mem0 | Appelé par le routeur pour le contexte et potentiellement à l'enregistrement d'un skill; collection `mem0` vide au relevé. |
| `memory_manager.py` racine — LlamaIndex / `document_bus` | `ResearchAgent` a un lecteur documentaire; aucun appelant applicatif d'`indexer_document` n'a été repéré et la collection était vide. |
| `agents/research_agent.py` — lecteur `document_bus` | Lecture prévue dans le chemin de recherche, sur une collection sans vecteur au relevé. |
| Qdrant `teacher_student_metrics` | 16 vecteurs observés; écriture reliée à l'agent de recherche, sans appelant repéré pour le repository de lecture global. |
| Qdrant `research_cache` | Collection reliée au code de cache, mais vide au relevé. |
| Qdrant `document_bus` | Collection cible de lecture/écriture documentaire, mais vide au relevé; l'écriture applicative n'a pas d'appelant repéré. |
| Qdrant `mem0` | Collection sous-jacente observée vide; la correspondance au client Mem0 est déduite dans l'inventaire. |
| Tests `test_mem0_qdrant.py`, `test_llamaindex_qdrant.py` | Scripts autonomes; ils n'établissent pas un raccordement applicatif des collections configurées pour les tests. |

## 4. Composants NON RACCORDE

| Composant | État constaté |
|---|---|
| `services/extraction_manager.py::ExtractionManager` | Non appelé par le chemin facture HTTP; importe `InvoiceSchema`, symbole absent. |
| `invoice_indexer.py::InvoiceIndexer` et collection `invoices` | Indexation/recherche disponibles en lancement autonome; aucun appelant applicatif repéré et collection absente au relevé. |
| `services/invoice_embedding_service.py::InvoiceEmbeddingService` | Aucun appelant repéré. |
| `services/invoice_similarity_service.py::InvoiceSimilarityService` | Aucun appelant repéré; la collection `invoice_documents` contient 3 vecteurs sans writer actuel attribué. |
| `services/memory_manager.py::MemoryManager` | Composition Qdrant/Neo4j définie, mais aucune instanciation applicative repérée. |
| `repositories/memory_repository.py::MemoryRepository` et `episodic_memory` | Aucun manager raccordé à ce repository; collection absente de Qdrant au relevé. |
| `repositories/graph_repository.py::GraphRepository` | Appels internes au `MemoryManager` de service, mais aucun instanciateur applicatif repéré. |
| `services/neo4j_service.py::Neo4jService` | Aucun appelant/instanciation repéré; requêtes sur un schéma différent des données observées. |
| `memory/memory_manager.py::MultiLevelMemory` | Gestion mémoire chaud/froid définie, sans appelant courant identifié. |
| Qdrant `mem0migrations` | Collection runtime sans accès applicatif direct identifié; rôle exact non confirmé. |
| Qdrant `invoice_documents` | Données présentes, mais aucun writer courant ni usage effectif du service lecteur repéré. |
| Qdrant `episodic_memory` | Collection déclarée par le repository, absente du serveur; repository non raccordé. |
| Qdrant `mem0_memories` | Collection déclarée dans un script de test, absente de l'inventaire runtime. |
| Qdrant `skills_catalog` | Collection vide sans référence opérationnelle au code courant; ancien nom documentaire. |

## 5. Composants OBSOLÈTES

Dans cette section, **OBSOLETE** reprend cette classification uniquement là où
elle est explicitement portée par l'audit des pipelines. Un composant simplement
non raccordé n'est pas reclassé comme obsolète.

| Composant | Motif consigné |
|---|---|
| `invoice_agent.py` à la racine | Doublon de l'agent utilisé sous `agents/`; importe `InvoiceSchema`, symbole absent, et n'est pas appelé par le webhook retenu. |

## 6. Diagramme texte des flux réels

```text
FACTURE — voie HTTP avec agent réel
┌─────────────────────────────────────────────────────────────────┐
│ n8n_api.py : POST /api/v1/ingest                                │
└──────────────────────────────┬──────────────────────────────────┘
                               v
                 agents.invoice_agent.InvoiceAgent
                               |
                               v
                  Ollama Vision → JSON extrait
                               |
                               v
          InvoiceData locale à agents/invoice_agent.py
                               |
                               v
                     réponse JSON à n8n

FACTURE — voie backend alternative simulée
backend/app/n8n_api.py → invoice_graph → étapes simulées → réponse HTTP

SKILLS — registre exposé par l'API
api.py : register/find/list
               |
               v
memory_manager.py racine → scripts Python + skills/registry.json
               |
               +── enregistrement de skill → ajout Mem0 facultatif

SKILLS / CONTEXTE — harnais
dsh_harness.py → router.py::route_prompt
                     ├── registre JSON : recherche de skill
                     └── mémoire racine : recherche de contexte Mem0
                               |
                               v
                    décision avec active_skill/contexte
                    (le skill n'est pas exécuté par le harnais)

RECHERCHE — voies Qdrant et point de raccordement cassé
ResearchAgent
   ├── research_cache : lecture/écriture au cours des recherches
   ├── document_bus : lecteur documentaire (collection vide au relevé)
   ├── SkillRouter.find_relevant_skill : méthode absente → exception interceptée
   └── TeacherStudentAgent → teacher_student_metrics
                              └── création de skill conditionnelle

PERSISTANCE FACTURE / GRAPHE
InvoiceAgent ──X──> invoice_indexer / invoice_documents / GraphRepository
services.MemoryManager ──> MemoryRepository (Qdrant) + GraphRepository (Neo4j)
                           mais aucun appelant applicatif repéré

NEO4J — état runtime, origine non attribuée
Fournisseur ──A_EMIS──> Facture <──REGLE── MouvementBancaire
  1 nœud                 1 nœud                 1 nœud

Le code GraphRepository/Neo4jService vise plutôt :
Supplier / Invoice / Document
BELONGS_TO / CONTAINS
```

`X` indique une liaison absente dans les rapports; il ne représente pas une
erreur d'exécution constatée sur une invocation effective.

## 7. État des données de stockage au relevé

### Qdrant

Les collections présentes totalisaient **23 vecteurs dans 8 collections** :

| Collection | Points | Classement de l'inventaire |
|---|---:|---|
| `agent_skills` | 4 | TEST |
| `document_bus` | 0 | ACTIF |
| `invoice_documents` | 3 | INCONNU |
| `mem0` | 0 | ACTIF |
| `mem0migrations` | 0 | INCONNU |
| `research_cache` | 0 | ACTIF |
| `skills_catalog` | 0 | ABANDONNÉ |
| `teacher_student_metrics` | 16 | ACTIF |

Les collections `invoices`, `episodic_memory` et `mem0_memories` étaient
absentes du serveur au relevé. Les classifications ci-dessus sont celles de
l'inventaire Qdrant; elles ne remplacent pas les statuts de raccordement utilisés
dans les sections précédentes.

### Neo4j

Le relevé runtime indiquait **3 nœuds**, `Fournisseur`, `Facture` et
`MouvementBancaire`, et **2 relations**, `A_EMIS` et `REGLE`. Aucun élément du
relevé ne permettait d'attribuer avec certitude ces écritures aux classes
`GraphRepository`, `Neo4jService` ou `MemoryManager` du dépôt.
