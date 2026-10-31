# TODO de convergence — DSH-Core

## Source et portée

Ce suivi reprend uniquement les décisions d'action de
[`05_DECISIONS_ARCHITECTURE.md`](./05_DECISIONS_ARCHITECTURE.md). Les statuts
ont été établis en vérifiant l'état courant des fichiers concernés.

- **FAIT** : modification correspondante visible dans le code.
- **EN ATTENTE** : décision à exécuter; aucune modification correspondante
  constatée.
- **BLOQUÉ** : impossible à exécuter sans lever le blocage ou obtenir une
  décision supplémentaire.

Aucun code, fichier applicatif, collection ou donnée runtime n'est modifié par
ce suivi.

## Corrections déjà réalisées

| Composant | Décision | Statut | Constat |
|---|---|---|---|
| `api.py::run_router` — appel `router_intelligent` | FIX | **FAIT** | Appelle maintenant `router.route_prompt` avec `image_provided`. |
| `agents/research_agent.py` — lookup `find_relevant_skill` | FIX | **FAIT** | Appelle maintenant `retrieve_skill_for_task` et consomme son résultat `str \| None`. |

## Corrections et décisions de convergence restantes

| Composant | Décision | Statut | Priorité | Raison / blocage |
|---|---|---|---|---|
| `services/extraction_manager.py` — import et validation `InvoiceSchema` | FIX | **BLOQUÉ** | P1 | Le symbole est absent. Le contrat imbriqué attendu doit être conservé; la substitution par `InvoiceData` est explicitement refusée et le modèle Pydantic original n'a pas été retrouvé. Aucun changement à effectuer tant que le contrat n'est pas reconstruit et validé. |
| `agents/invoice_agent.py` — renommage de son `InvoiceData` local | RENAME | **BLOQUÉ** | P1 | Décision P1 de 05; toutefois, une modification de code sur le sujet facture est suspendue. Le nom local ne sera pas changé sans levée explicite de ce blocage. |
| `services/ingestion_manager.py::IngestionManager` | FIX | **EN ATTENTE** | P2 | Son chemin d'erreur attend `last_error`, tandis que l'agent retourne `message`; ce correctif n'apparaît pas dans les changements réalisés. |
| `backend/app/n8n_api.py` et `backend/app/graphs/n8n_api.py` | MERGE | **EN ATTENTE** | P3 | Les copies sont déclarées identiques dans 05; la fusion et la vérification des points d'import restent à faire. |
| Voie backend facture — `backend/app/n8n_api.py` et `backend/app/graphs/invoice_graph.py` | ARCHIVE | **EN ATTENTE** | P3 | La voie alternative est simulée et non équivalente au webhook facture retenu; son classement hors chemin applicatif courant n'est pas réalisé. |
| `services/document_profiler.py::DocumentProfiler` | ARCHIVE | **EN ATTENTE** | P3 | Présent dans des scripts/tests, non raccordé au webhook facture courant. |
| `services/routing_engine.py::RoutingEngine` | ARCHIVE | **EN ATTENTE** | P3 | Présent dans des scripts/tests, non raccordé au webhook facture courant. |
| `services/business_extraction_manager.py::BusinessExtractionManager` | ARCHIVE | **EN ATTENTE** | P3 | Utilisé dans un test, non raccordé au webhook facture courant. |
| `services/invoice_reconciliation_engine.py::InvoiceReconciliationEngine` | ARCHIVE | **EN ATTENTE** | P3 | Appelé par un script de test, non raccordé au webhook facture courant. |
| `services/tax_classification_engine.py::TaxClassificationEngine` | ARCHIVE | **EN ATTENTE** | P3 | Appelé par un script de test, non raccordé au webhook facture courant. |
| `services/invoice_skill_creator.py::InvoiceSkillCreator` | ARCHIVE | **EN ATTENTE** | P3 | Parcours exercé par scripts/tests, non lié au webhook facture courant. |
| `services/invoice_skill_applier.py::InvoiceSkillApplier` | ARCHIVE | **EN ATTENTE** | P3 | Parcours exercé par scripts/tests, non lié au webhook facture courant. |
| `invoice_indexer.py::InvoiceIndexer` et collection `invoices` déclarée | ARCHIVE | **EN ATTENTE** | P3 | Voie autonome sans appelant applicatif repéré; collection absente au relevé. |
| `services/invoice_embedding_service.py::InvoiceEmbeddingService` | ARCHIVE | **EN ATTENTE** | P3 | Aucun appelant repéré dans l'audit. |
| `services/invoice_similarity_service.py::InvoiceSimilarityService` | ARCHIVE | **EN ATTENTE** | P3 | Aucun usage effectif repéré; les 3 points `invoice_documents` ne doivent pas être attribués à ce service. |
| `repositories/graph_repository.py::GraphRepository` | ARCHIVE | **EN ATTENTE** | P3 | Aucun instanciateur applicatif repéré; schéma différent du graphe Neo4j runtime. |
| `services/neo4j_service.py::Neo4jService` | ARCHIVE | **EN ATTENTE** | P3 | Aucun appelant repéré; requêtes sur un schéma différent du graphe observé. |
| `repositories/teacher_student_repository.py::TeacherStudentRepository` | ARCHIVE | **EN ATTENTE** | P3 | Lecture globale des métriques sans appelant repéré. |
| `memory_manager.py` racine — fonction `indexer_document` | ARCHIVE | **EN ATTENTE** | P3 | Aucun appelant applicatif de cette écriture documentaire repéré. |
| `services/memory_manager.py::MemoryManager` | ARCHIVE | **EN ATTENTE** | P3 | Composition Qdrant/Neo4j sans instanciation applicative repérée. |
| `repositories/memory_repository.py::MemoryRepository` et collection `episodic_memory` | ARCHIVE | **EN ATTENTE** | P3 | Repository non raccordé; collection déclarée absente du serveur au relevé. |
| `memory/memory_manager.py::MultiLevelMemory` | ARCHIVE | **EN ATTENTE** | P3 | Mémoire chaud/froid sans appelant courant identifié. |
| `test_mem0_qdrant.py` et `test_llamaindex_qdrant.py` | ARCHIVE | **EN ATTENTE** | P3 | Scripts de test autonomes, hors des voies mémoire applicatives. |
| `test_learning_loop.py` | ARCHIVE | **EN ATTENTE** | P3 | Test de la voie Qdrant skills, pas un chemin applicatif. |
| Collection/configuration `episodic_memory` | ARCHIVE | **EN ATTENTE** | P3 | Absente du serveur et liée à un repository non raccordé. |
| Collection/configuration de test `mem0_memories` | ARCHIVE | **EN ATTENTE** | P3 | Absente du runtime et configurée dans un test. |
| Collection runtime `mem0migrations` | ARCHIVE | **EN ATTENTE** | P3 | Collection vide de rôle non confirmé; conserver les données/collection tant que son cycle de vie n'est pas établi. |
| `invoice_agent.py` à la racine | REMOVE | **EN ATTENTE** | P3 | Doublon obsolète selon 05; vérifier les dépendances externes avant tout retrait physique. |
| Collection Qdrant `skills_catalog` | REMOVE | **EN ATTENTE** | P3 | Vide et classée abandonnée dans 05; vérifier qu'aucun consommateur externe ne l'utilise avant retrait. |

## Conditions de suivi

- Les deux lignes **FAIT** correspondent aux seuls correctifs du groupe A
  vérifiés dans le code.
- **EN ATTENTE** signifie que la décision existe mais n'a pas été exécutée; ce
  statut n'autorise pas une suppression automatique.
- Les décisions concernant Qdrant/Neo4j sont des décisions de classement. Ne
  supprimer aucune donnée runtime d'origine inconnue sur la seule base de ce
  suivi.
- Le groupe B `InvoiceSchema` reste **BLOQUÉ**; aucun remplacement par
  `InvoiceData` n'est prévu.
