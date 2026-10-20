# Pipelines officiels — DSH-Core

## Périmètre et lecture des statuts

Ce document consolide les audits des imports, points d'entrée, Qdrant et Neo4j,
à partir du code présent et des relevés runtime effectués pendant ces audits.
Il ne décrit pas une architecture cible nouvelle : il distingue les chemins déjà
appelés des composants seulement présents ou testés.

- **ACTIF** : chemin appelé depuis une entrée applicative observée dans le code.
- **PARTIELLEMENT ACTIF** : une partie du chemin est appelée ou testée, mais le
  parcours complet ou une dépendance importante manque.
- **NON RACCORDE** : code présent, sans appelant relié au pipeline applicatif.
- **OBSOLETE** : ancienne implémentation, doublon remplacé ou chemin avec
  dépendance/symbole absent qui ne peut pas servir de chemin officiel en l'état.

« Officiel » désigne ici le parcours existant le plus raccordé, pas une garantie
qu'il soit déployé ni qu'il réussisse à chaque exécution.

## 1. Pipeline Facture Officiel

### Parcours retenu dans le code courant

```text
n8n_api.py — POST /api/v1/ingest
→ agents.invoice_agent.InvoiceAgent.process_invoice(file_path)
→ agents.invoice_agent.InvoiceAgent.traiter_facture(...)
→ ollama.chat avec le document image
→ JSON extrait
→ InvoiceData définie localement dans agents/invoice_agent.py
→ réponse JSON à n8n
```

C'est le seul chemin de réception facture du dépôt qui relie une route HTTP à
l'agent de facture réel, sans passer par les nœuds d'extraction simulés du graphe
backend. Il ne faut pas le confondre avec `invoice_agent.py` à la racine, qui
importe le symbole absent `InvoiceSchema`.

| Composant existant | Statut | Motif |
|---|---|---|
| `n8n_api.py`, `POST /api/v1/ingest` | **ACTIF** | Instancie `agents.invoice_agent.InvoiceAgent` et appelle `process_invoice`. |
| `agents/invoice_agent.py::InvoiceAgent` | **ACTIF** | Extrait par Ollama Vision et valide avec sa classe `InvoiceData` locale. Ne persiste pas la facture dans Qdrant/Neo4j. |
| `services/ingestion_manager.py::IngestionManager` | **PARTIELLEMENT ACTIF** | Appelé par `test_ingestion.py`, mais pas par le webhook n8n. Il délègue à `agents.invoice_agent`; son chemin d'erreur cherche `last_error`, alors que l'agent renvoie `message` en cas d'erreur. |
| `services/document_profiler.py::DocumentProfiler` | **PARTIELLEMENT ACTIF** | Testé avec la chaîne d'extraction, mais pas appelé par le webhook ni par `InvoiceAgent`. |
| `services/routing_engine.py::RoutingEngine` | **PARTIELLEMENT ACTIF** | Testé avec `DocumentProfiler`; non raccordé au webhook courant. |
| `services/business_extraction_manager.py::BusinessExtractionManager` | **PARTIELLEMENT ACTIF** | Utilisé dans `test_profiler.py`, pas dans le webhook facture. |
| `services/invoice_reconciliation_engine.py::InvoiceReconciliationEngine` | **PARTIELLEMENT ACTIF** | Appelé par `test_nuextract.py`; non raccordé au webhook. |
| `services/tax_classification_engine.py::TaxClassificationEngine` | **PARTIELLEMENT ACTIF** | Appelé par `test_nuextract.py`; non raccordé au webhook. |
| `schemas/invoice_schema.py::InvoiceData` | **PARTIELLEMENT ACTIF** | Importé par `BusinessExtractionManager` et des tests; l'agent actif définit une classe locale homonyme distincte. |
| `services/invoice_skill_creator.py::InvoiceSkillCreator` et `services/invoice_skill_applier.py::InvoiceSkillApplier` | **PARTIELLEMENT ACTIF** | Essayés dans des scripts/tests facture; pas de liaison depuis le webhook de facture courant. |
| `invoice_agent.py` à la racine | **OBSOLETE** | Doublon de l'agent dans `agents/`; importe `InvoiceSchema`, qui n'existe pas, et n'est pas utilisé par `n8n_api.py`. |
| `services/extraction_manager.py::ExtractionManager` | **NON RACCORDE** | Importe `InvoiceSchema`, absent du schéma, et n'est pas appelé par le chemin facture HTTP. |
| `backend/app/n8n_api.py` et sa copie `backend/app/graphs/n8n_api.py` | **PARTIELLEMENT ACTIF** | Routes alternatives vers `invoice_graph`; l'extraction, la validation et l'ingestion du graphe sont simulées. Les deux fichiers sont identiques. |
| `backend/app/graphs/invoice_graph.py` | **PARTIELLEMENT ACTIF** | Le webhook backend peut le construire et l'invoquer, mais ses nœuds Vision/OCR/ingestion n'effectuent pas les intégrations annoncées. |
| `invoice_indexer.py::InvoiceIndexer` | **NON RACCORDE** | Peut indexer et rechercher dans `invoices` lorsqu'il est lancé manuellement; aucun appel depuis le webhook ou l'agent actif n'a été trouvé. La collection `invoices` était absente du Qdrant relevé. |
| `services/invoice_embedding_service.py::InvoiceEmbeddingService` | **NON RACCORDE** | Aucun appelant trouvé. |
| `services/invoice_similarity_service.py::InvoiceSimilarityService` | **NON RACCORDE** | Aucun appelant trouvé; lit `invoice_documents`, dont les 3 points présents n'ont pas de writer actuel attribué. |
| `repositories/graph_repository.py::GraphRepository` pour factures | **NON RACCORDE** | Aucun instanciateur applicatif trouvé; son écriture utilise `Invoice`/`Supplier`/`Document`, contrairement aux labels français observés dans Neo4j. |
| `services/neo4j_service.py::Neo4jService` | **NON RACCORDE** | Aucune instanciation/appel trouvé; ses lectures ciblent le schéma anglais absent des données observées. |

### Ce que le pipeline officiel fait et ne fait pas

- Il reçoit le fichier, demande au modèle Vision un JSON et renvoie le résultat.
- La validation de l'agent actif est locale à `agents/invoice_agent.py`; ce n'est
  pas `schemas.invoice_schema.InvoiceData`.
- Il ne fait pas appel au profiler/routing OCR, à la réconciliation ou à la
  classification fiscale des services.
- Il ne crée pas de skill facture, n'indexe pas la facture dans Qdrant et ne
  l'enregistre pas dans Neo4j.
- `backend/app/n8n_api.py` est une voie alternative et simulée : ne pas la traiter
  comme une seconde implémentation équivalente du pipeline réel.

## 2. Pipeline Skills Officiel

Le chemin le plus directement exposé par l'API est le **registre JSON du module
racine `memory_manager.py`**. Il constitue le chemin de gestion des skills
actuellement raccordé, mais n'est pas un cycle complet d'apprentissage et
d'exécution.

```text
POST /api/skills/register
→ memory_manager.py::register_skill
→ fichier Python sous skills/ + skills/registry.json
→ ajout facultatif d'un souvenir Mem0

GET /api/skills/find ou /api/skills
→ find_skill / list_all_skills

dsh_harness.py
→ router.py::route_prompt
→ find_skill + recuperer_contexte
→ active_skill et contexte retournés dans la décision
```

| Composant existant | Statut | Motif |
|---|---|---|
| `api.py` : `/api/skills/register`, `/api/skills/find`, `/api/skills` | **ACTIF** | Appelle directement les fonctions du gestionnaire racine. |
| `memory_manager.py` racine : registre JSON (`register_skill`, `find_skill`, `list_all_skills`) | **ACTIF** | Écrit/lit `skills/registry.json` et les scripts de skill; c'est la voie API en place. |
| `router.py::route_prompt` | **PARTIELLEMENT ACTIF** | Appelé par `dsh_harness.py`; cherche le skill et retourne `active_skill`, mais le harnais ne lance pas ce skill. |
| `api.py::run_router` | **PARTIELLEMENT ACTIF** | La route `/api/router` existe, mais importe `router_intelligent`, symbole absent de `router.py`, et échoue au lieu d'utiliser `route_prompt`. |
| `agents/skill_creator_agent.py` + `repositories/skills_repository.py` (`agent_skills`) | **PARTIELLEMENT ACTIF** | Création Qdrant utilisée par le test `test_learning_loop.py` et appelable par Teacher–Student; pas intégrée au registre API JSON. 4 vecteurs `agent_skills` ont été observés. |
| `services/skill_router.py::retrieve_skill_for_task` | **PARTIELLEMENT ACTIF** | Fonction de recherche Qdrant utilisée par le test d'apprentissage; son appel depuis `ResearchAgent` utilise le nom absent `find_relevant_skill`. |
| `agents/research_agent.py` → `SkillRouter` | **PARTIELLEMENT ACTIF** | Le chemin de recherche existe, mais l'appel `find_relevant_skill` est incompatible avec `retrieve_skill_for_task`; l'exception est interceptée et le contexte de skill n'est pas injecté comme prévu. |
| `agents/teacher_student_agent.py` | **PARTIELLEMENT ACTIF** | Utilisé par les stratégies de recherche pour enregistrer des métriques. La création de skill dépend d'une branche superviseur que le chemin d'escalade actuel n'atteint pas normalement. |
| `services/invoice_skill_creator.py` + `services/invoice_skill_applier.py` | **PARTIELLEMENT ACTIF** | Cycle invoice-skill persistant en JSON et référentiel fournisseur, exercé par des scripts/tests; non raccordé au pipeline HTTP facture. |
| `services/memory_manager.py::MemoryManager.save_skill` | **NON RACCORDE** | Offre une double écriture Qdrant/Neo4j, mais `MemoryManager` n'est instancié par aucun appelant repéré. |

**Décision officielle à partir des raccordements existants :** garder le registre
JSON racine comme voie actuelle de gestion/API des skills. Traiter le loop
Qdrant Teacher–Student comme voie expérimentale/partielle, pas comme une voie
automatique de production. Le skill enregistré ou retourné n'est pas exécuté
par le code API/harness observé.

Ne pas confondre les trois stockages/catalogues actuels : le registre de fichiers
`skills/registry.json`, la collection Qdrant `agent_skills` et les fichiers
invoice-skills JSON sous `workspace/skills`.

## 3. Pipeline Mémoire Officiel

Le module racine `memory_manager.py` est la voie mémoire la plus raccordée au
code applicatif : l'API l'utilise pour le registre des skills, et `route_prompt`
l'utilise pour retrouver du contexte Mem0. L'accès reste partiel; le pipeline
facture ne lui envoie pas le contexte de la facture.

```text
api.py / memory_manager.py racine
→ Mem0 : ajouter_souvenir / recuperer_contexte
→ Qdrant sous-jacent pour la mémoire épisodique

router.py::route_prompt
→ recuperer_contexte(user_id, prompt)

memory_manager.py racine / LlamaIndex
→ document_bus : indexer_document / interroger_documents
→ lecture de document_bus également depuis ResearchAgent
```

| Composant existant | Statut | Motif |
|---|---|---|
| `memory_manager.py` racine — Mem0 (`ajouter_souvenir`, `recuperer_contexte`) | **PARTIELLEMENT ACTIF** | Le routeur appelle `recuperer_contexte`; `register_skill` peut appeler `ajouter_souvenir`. La collection runtime `mem0` était vide au relevé. |
| `memory_manager.py` racine — LlamaIndex `document_bus` | **PARTIELLEMENT ACTIF** | `ResearchAgent` crée un retriever pour la recherche documentaire; aucun appelant applicatif de `indexer_document` n'a été trouvé et la collection était vide. |
| `agents/research_agent.py` — `research_cache` | **ACTIF** | La recherche lit et écrit la collection de cache lorsque son chemin s'exécute; la collection était vide au relevé. |
| `services/memory_manager.py::MemoryManager` | **NON RACCORDE** | Composition Qdrant/Neo4j définie, mais aucun appelant/instanciation trouvée. |
| `repositories/memory_repository.py::MemoryRepository` | **NON RACCORDE** | Collection par défaut `episodic_memory`, absente de Qdrant; méthode disponible mais repository non injecté depuis un manager instancié. |
| `repositories/graph_repository.py::GraphRepository` et `services/neo4j_service.py::Neo4jService` | **NON RACCORDE** | Pas d'instanciation applicative repérée. Le Neo4j runtime contient 3 nœuds et 2 relations, mais sous un schéma différent du code; leur origine n'est pas établie. |
| `memory/memory_manager.py::MultiLevelMemory` | **NON RACCORDE** | Gestion chaud/froid par liste et fichiers JSON; aucun appelant courant repéré, distincte des deux gestionnaires précédents. |
| `test_mem0_qdrant.py`, `test_llamaindex_qdrant.py` | **PARTIELLEMENT ACTIF** | Scripts de test autonomes; `mem0_memories` n'existait pas dans l'inventaire runtime et `document_bus` ne contenait aucun vecteur. |

**Décision officielle à partir des raccordements existants :** retenir le
module racine `memory_manager.py` comme façade mémoire déjà utilisée par l'API
et le routeur. Ne pas présenter `services/memory_manager.py`, Neo4j, ou
`MultiLevelMemory` comme des voies déjà raccordées au traitement courant.

## ORDRE DE PRIORITE DES CORRECTIONS

Ordre proposé pour corriger les flux existants, sans ajouter de classes,
services, bases ou nouvelles architectures :

1. **Fixer le contrat facture partagé.** `InvoiceSchema` est importé dans
   `invoice_agent.py` racine et `services/extraction_manager.py`, mais absent de
   `schemas/invoice_schema.py`. Choisir le modèle existant `InvoiceData` ou
   aligner le contrat des appelants; ne pas changer uniquement le nom si le JSON
   reste incompatible. Clarifier aussi la classe locale homonyme dans
   `agents/invoice_agent.py`.
2. **Stabiliser le seul webhook facture à retenir.** Conserver
   `n8n_api.py` → `agents.invoice_agent` comme chemin HTTP réellement
   implémenté; éviter de présenter le graphe backend simulé et sa copie comme
   traitement facture équivalent. Propager correctement le statut/erreur
   `critical_error` que l'agent renvoie au lieu de le traiter comme succès HTTP.
3. **Rendre cohérente la chaîne d'extraction existante.** Si le traitement doit
   utiliser le flux OCR/routé déjà écrit et testé, aligner les contrats puis
   raccorder dans l'ordre les modules existants
   `DocumentProfiler` → `RoutingEngine` → `BusinessExtractionManager` →
   `InvoiceReconciliationEngine` → `TaxClassificationEngine` → `InvoiceData`.
   En l'état, cette séquence n'est que partiellement exercée par des scripts de
   test; elle n'est pas appelée par le webhook officiel.
4. **Réparer la route API du routeur.** Remplacer l'import inexistant
   `router_intelligent` par l'API réellement définie `route_prompt` et adapter
   l'appel à ses paramètres `prompt`, `user_id`, `image_provided`.
5. **Choisir une seule voie de skills effectivement officielle.** À court terme,
   garder le registre JSON racine, déjà utilisé par les endpoints de skills.
   Ne pas déclarer la boucle Qdrant automatique tant que
   `ResearchAgent.find_relevant_skill` n'est pas aligné sur
   `SkillRouter.retrieve_skill_for_task` et que son retour `str | None` n'est pas
   traité comme tel. Corriger aussi les erreurs de branchement qui empêchent
   `TeacherStudentAgent` d'atteindre la création superviseur si cette voie est
   conservée.
6. **Décider le devenir des invoice-skills existants.** `InvoiceSkillCreator`
   et `InvoiceSkillApplier` sont testés séparément; les raccorder aux méthodes
   facture existantes si leur application est requise, sinon les laisser hors
   du pipeline officiel. Ne pas les confondre avec le registre générique ou
   `agent_skills`.
7. **Ne déclarer aucune persistance facture avant raccordement vérifié.**
   L'indexeur Qdrant `invoices`, le service `invoice_documents` et les écritures
   Neo4j sont séparés du webhook actuel. Avant de les traiter comme une étape
   officielle, corriger les références aux schémas et relier les writers
   existants aux données validées; vérifier ensuite les collections/labels
   effectivement visés.
8. **Garder un chemin mémoire unique pour l'application courante.** Conserver
   le module racine comme voie API/contexte existante. Ne pas considérer la
   mémoire Qdrant/Neo4j de `services/memory_manager.py` comme active tant qu'elle
   n'a aucun appelant; la gestion mémoire chaud/froid de
   `memory/memory_manager.py` reste une implémentation non raccordée.

## État final en bref

- **Facture :** webhook racine → agent Vision local, actif mais minimal; le
  pipeline d'extraction métier et la persistance ne sont pas raccordés.
- **Skills :** registre JSON via API est la voie actuelle; l'exécution par le
  routeur/harness est partielle; la boucle Qdrant auto-apprenante est
  expérimentale et comporte un appel de méthode invalide.
- **Mémoire :** façade racine Mem0/LlamaIndex partiellement utilisée; le manager
  de service Qdrant/Neo4j et le stockage chaud/froid restent non raccordés.
