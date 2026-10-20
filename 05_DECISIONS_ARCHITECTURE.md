# Décisions d'architecture — DSH-Core

## Base et portée

Source unique : [`04_AUDIT_ARCHITECTURE.md`](./04_AUDIT_ARCHITECTURE.md).
Les décisions portent sur les composants qui y sont recensés, y compris les
collections et données runtime. Elles préparent la convergence des éléments
existants sans définir de nouvelle architecture, fonctionnalité, classe ou
service.

Ce document est une décision de devenir et d'ordre, pas une autorisation de
supprimer des données runtime. Aucune modification de code ou de stockage n'est
effectuée ici.

### Décisions

- **KEEP** : conserver dans son rôle actuel.
- **MERGE** : réunir des doublons existants.
- **RENAME** : distinguer un nom ambigu sans changer le rôle recensé.
- **FIX** : corriger un raccordement ou un contrat explicitement signalé.
- **ARCHIVE** : conserver comme voie de test, alternative ou non raccordée, hors
  du chemin applicatif courant.
- **REMOVE** : retirer un élément explicitement abandonné ou obsolète.

### Priorités

- **P1** : point d'entrée actif ou contrat bloquant à traiter en premier.
- **P2** : composant relié partiellement ou nécessaire à la cohérence des flux.
- **P3** : voie non raccordée, archive, doublon ou élément à retirer après
  traitement des priorités précédentes.

Chaque ligne ci-dessous porte une seule décision et une priorité.

## 1. Décisions par composant

| Composant | Décision | Raison | Priorité |
|---|---|---|---|
| `n8n_api.py` — route `POST /api/v1/ingest` | **KEEP** | Point d'entrée HTTP qui appelle l'agent facture décrit comme actif. | P1 |
| `agents/invoice_agent.py::InvoiceAgent` | **KEEP** | Agent facture appelé par le webhook courant; il extrait par Ollama Vision et valide avec son modèle local. | P1 |
| Ollama Vision utilisé par `InvoiceAgent` | **KEEP** | Dépendance du chemin d'extraction facture réellement décrit. | P1 |
| Modèle `InvoiceData` local de `agents/invoice_agent.py` | **RENAME** | Homonyme du modèle du schéma, mais contrat distinct selon l'audit. | P1 |
| `schemas/invoice_schema.py::InvoiceData` | **KEEP** | Modèle plat importé par le gestionnaire d'extraction et des tests; ne pas le confondre avec le modèle local de l'agent. | P2 |
| `InvoiceSchema` référencé par `services/extraction_manager.py` | **FIX** | Le symbole n'existe pas dans le schéma; le gestionnaire attend un contrat différent de `InvoiceData`. | P1 |
| `invoice_agent.py` à la racine | **REMOVE** | Doublon non utilisé par le webhook et bloqué par l'import de `InvoiceSchema`, absent. | P3 |
| `services/ingestion_manager.py::IngestionManager` | **FIX** | Utilisé par un test; son traitement d'erreur attend `last_error` alors que l'agent renvoie `message`. | P2 |
| `services/document_profiler.py::DocumentProfiler` | **ARCHIVE** | Employé dans les scripts/tests, non raccordé au webhook facture décrit. | P3 |
| `services/routing_engine.py::RoutingEngine` | **ARCHIVE** | Employé dans les scripts/tests, non raccordé au webhook facture décrit. | P3 |
| `services/business_extraction_manager.py::BusinessExtractionManager` | **ARCHIVE** | Utilisé dans un test, non raccordé au webhook facture décrit. | P3 |
| `services/invoice_reconciliation_engine.py::InvoiceReconciliationEngine` | **ARCHIVE** | Appelé par un script de test, non raccordé au webhook facture décrit. | P3 |
| `services/tax_classification_engine.py::TaxClassificationEngine` | **ARCHIVE** | Appelé par un script de test, non raccordé au webhook facture décrit. | P3 |
| `services/invoice_skill_creator.py::InvoiceSkillCreator` | **ARCHIVE** | Exercice par scripts/tests; aucune liaison au webhook facture n'est recensée. | P3 |
| `services/invoice_skill_applier.py::InvoiceSkillApplier` | **ARCHIVE** | Exercice par scripts/tests; aucune liaison au webhook facture n'est recensée. | P3 |
| `backend/app/n8n_api.py` et `backend/app/graphs/n8n_api.py` | **MERGE** | Les fichiers sont décrits comme copies identiques; leur route backend alternative appelle un graphe dont les étapes facture sont simulées. | P3 |
| `backend/app/graphs/invoice_graph.py` | **ARCHIVE** | Graphe de la voie backend alternative, avec opérations simulées plutôt qu'intégrations facture réelles. | P3 |
| `services/extraction_manager.py::ExtractionManager` | **FIX** | Non raccordé au webhook et dépend de `InvoiceSchema`, symbole absent; son contrat de facture est signalé comme incompatible avec le modèle existant. | P2 |
| `invoice_indexer.py::InvoiceIndexer` | **ARCHIVE** | Voie autonome sans appelant applicatif repéré; la collection `invoices` est absente au relevé. | P3 |
| Qdrant — collection `invoices` (configuration du `InvoiceIndexer`) | **ARCHIVE** | Collection seulement déclarée par une voie autonome et absente du serveur relevé. | P3 |
| `services/invoice_embedding_service.py::InvoiceEmbeddingService` | **ARCHIVE** | Aucun appelant n'est repéré dans l'audit. | P3 |
| `services/invoice_similarity_service.py::InvoiceSimilarityService` | **ARCHIVE** | Aucun usage effectif n'est repéré; ses 3 points Qdrant lus ne possèdent pas de writer actuel attribué. | P3 |
| `repositories/graph_repository.py::GraphRepository` | **ARCHIVE** | Aucun instanciateur applicatif repéré; son schéma ne correspond pas aux données Neo4j observées. | P3 |
| `services/neo4j_service.py::Neo4jService` | **ARCHIVE** | Aucun appelant/instanciation repéré; ses requêtes ciblent un schéma différent des données runtime. | P3 |
| API `api.py` — `POST /api/skills/register` | **KEEP** | Endpoint relié au gestionnaire racine de skills. | P1 |
| API `api.py` — `GET /api/skills/find` | **KEEP** | Endpoint relié à la recherche dans le registre racine. | P1 |
| API `api.py` — `GET /api/skills` | **KEEP** | Endpoint relié à la liste du registre racine. | P1 |
| `memory_manager.py` racine — registre JSON et scripts de skills | **KEEP** | Voie d'enregistrement et de recherche effectivement utilisée par les endpoints skills. | P1 |
| `api.py::run_router` / `POST /api/router` | **FIX** | La route importe `router_intelligent`, symbole absent; le routeur existant expose `route_prompt`. | P1 |
| `router.py::route_prompt` | **KEEP** | Fonction réellement appelée par `dsh_harness.py`; elle retourne la décision avec skill/contexte. | P1 |
| `dsh_harness.py` | **KEEP** | Appelle `route_prompt` sur le chemin de routage observé. | P1 |
| `agents/skill_creator_agent.py::SkillCreatorAgent` | **KEEP** | Partie du chemin Qdrant de création de skills exercé par le test d'apprentissage. | P2 |
| `repositories/skills_repository.py::SkillsRepository` | **KEEP** | Repository utilisé par la voie `agent_skills` et par le routeur de skills; distinct du registre JSON. | P2 |
| `services/skill_router.py::SkillRouter.retrieve_skill_for_task` | **KEEP** | Méthode effectivement définie et appelée par le test d'apprentissage. | P2 |
| `agents/research_agent.py` — accès à `SkillRouter` | **FIX** | L'appel utilise `find_relevant_skill`, absent, et attend un dictionnaire alors que la méthode réelle renvoie `str | None`. | P1 |
| `agents/teacher_student_agent.py::TeacherStudentAgent` | **KEEP** | Raccordé à l'enregistrement de métriques; la branche de création de skill est conditionnelle et partielle. | P2 |
| `repositories/teacher_student_repository.py::TeacherStudentRepository` | **ARCHIVE** | Le repository expose la lecture globale des métriques, mais aucun appelant n'est repéré dans l'audit. | P3 |
| `agents/research_agent.py::ResearchAgent` — recherche et cache | **KEEP** | Le chemin de recherche est raccordé; le cache Qdrant est lu et écrit par les stratégies concernées. | P1 |
| `memory_manager.py` racine — Mem0 (`ajouter_souvenir`, `recuperer_contexte`) | **KEEP** | Appelé pour le contexte du routeur et potentiellement lors de l'enregistrement d'un skill. | P2 |
| `memory_manager.py` racine — LlamaIndex / lecteur `document_bus` | **KEEP** | Le lecteur documentaire est présent dans le chemin ResearchAgent; la collection était vide au relevé. | P2 |
| `memory_manager.py` racine — `indexer_document` | **ARCHIVE** | Aucun appelant applicatif de cette fonction d'écriture n'est repéré dans l'audit. | P3 |
| `agents/research_agent.py` — lecteur documentaire `document_bus` | **KEEP** | Lecteur présent dans le chemin de recherche, même si la collection était vide au relevé. | P2 |
| `services/memory_manager.py::MemoryManager` | **ARCHIVE** | Composition Qdrant/Neo4j sans instanciation applicative repérée. | P3 |
| `repositories/memory_repository.py::MemoryRepository` | **ARCHIVE** | Repository non raccordé à un manager instancié; sa collection par défaut est absente du serveur. | P3 |
| `memory/memory_manager.py::MultiLevelMemory` | **ARCHIVE** | Gestion mémoire chaud/froid sans appelant courant identifié. | P3 |
| Tests `test_mem0_qdrant.py` et `test_llamaindex_qdrant.py` | **ARCHIVE** | Scripts autonomes qui ne constituent pas des voies mémoire applicatives. | P3 |
| Test `test_learning_loop.py` | **ARCHIVE** | Exerce la voie Qdrant skills, sans constituer un chemin applicatif en lui-même. | P3 |
| Qdrant runtime | **KEEP** | Service accessible au relevé; la décision ne prétend pas que chaque collection est utilisée par l'application. | P1 |
| Qdrant — `agent_skills` | **KEEP** | Quatre vecteurs observés et voie de repository/test existante; son lookup depuis ResearchAgent est traité séparément. | P2 |
| Qdrant — `document_bus` | **KEEP** | Collection liée à la lecture documentaire, vide au relevé. | P2 |
| Qdrant — `invoice_documents` | **KEEP** | Trois vecteurs présents; leur origine n'est pas attribuée. Conserver les données observées. | P2 |
| Qdrant — `mem0` | **KEEP** | Collection liée par l'audit au client Mem0; vide au relevé. | P2 |
| Qdrant — `mem0migrations` | **ARCHIVE** | Collection runtime vide, de rôle non confirmé; aucun accès applicatif direct n'est identifié. | P3 |
| Qdrant — `research_cache` | **KEEP** | Collection lue/écrite par le chemin ResearchAgent, bien que vide au relevé. | P1 |
| Qdrant — `skills_catalog` | **REMOVE** | Collection vide, sans référence opérationnelle au code courant et classée abandonnée dans l'inventaire. | P3 |
| Qdrant — `teacher_student_metrics` | **KEEP** | Seize vecteurs observés; écriture reliée à TeacherStudentAgent et ResearchAgent. | P2 |
| Qdrant — `episodic_memory` (collection déclarée) | **ARCHIVE** | Collection absente du serveur et repository correspondant non raccordé. | P3 |
| Qdrant — `mem0_memories` (configuration de test) | **ARCHIVE** | Collection configurée par un test, absente de l'inventaire runtime. | P3 |
| Neo4j runtime | **KEEP** | Service accessible au relevé; sa présence n'établit pas l'utilisation des repositories du dépôt. | P2 |
| Neo4j — nœud `Fournisseur` et relation `A_EMIS` | **KEEP** | Données observées dans le graphe runtime; origine non attribuée par l'audit. | P2 |
| Neo4j — nœud `Facture` | **KEEP** | Donnée observée dans le graphe runtime; origine non attribuée par l'audit. | P2 |
| Neo4j — nœud `MouvementBancaire` et relation `REGLE` | **KEEP** | Données observées dans le graphe runtime; origine non attribuée par l'audit. | P2 |

## 2. Ordre d'exécution

L'ordre suit les priorités ci-dessus. Il indique le séquencement des décisions,
pas des modifications réalisées.

1. **P1 — préserver les points d'entrée et clarifier les contrats actifs** :
   conserver le webhook facture et son agent; distinguer son `InvoiceData`;
   traiter la référence `InvoiceSchema` de l'ExtractionManager; maintenir les
   endpoints skills, le registre JSON et le couple `dsh_harness.py` /
   `route_prompt`; traiter les raccordements cassés de `/api/router` et du
   lookup skills de ResearchAgent; conserver le cache de recherche.
2. **P2 — stabiliser les composants partiels existants** : traiter le contrat
   d'erreur d'IngestionManager; conserver les modèles/services/stockages
   partiellement utilisés recensés sous KEEP; conserver l'accès existant à
   `document_bus`, Mem0, Teacher–Student et aux données runtime sans attribuer
   leur origine.
3. **P3 — mettre à l'écart les voies non courantes** : archiver les composants
   de test et non raccordés, puis fusionner les deux copies backend identiques.
   Enfin retirer les éléments explicitement abandonnés/obsolètes : l'agent
   facture racine et la collection `skills_catalog`.

## Limites

Les décisions sont fondées uniquement sur le contenu de
`04_AUDIT_ARCHITECTURE.md`. En particulier, cet audit précise que l'origine de
certaines données Neo4j et de `invoice_documents` n'est pas connue; **KEEP**
signifie ici conserver les données observées, pas attribuer leur production à
un composant. Les décisions **ARCHIVE**, **MERGE** et **REMOVE** sont des
classements pour la convergence; aucune suppression, fusion ou modification de
code n'est effectuée par ce document.
