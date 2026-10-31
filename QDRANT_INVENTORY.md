# Inventaire Qdrant — DSH-Core

**État de référence : 31 octobre 2026 (phase P9.4).**

Ce document remplace l'inventaire détaillé du 19 octobre 2026. L'état courant
est issu du serveur local `http://localhost:6333`, Qdrant **1.19.1**, après
l'assainissement P9.4. Le contrat de payload est
[`docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md`](docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md)
et l'annexe opérationnelle est
[`docs/collections_qdrant.md`](docs/collections_qdrant.md).

## 1. Méthode

- Relevé par l'API Qdrant et par
  `tools/audit_qdrant_collections.py` avec l'interpréteur
  `A:\dsh-core\.venv-v3\Scripts\python.exe`.
- Les comptes de points sont exacts (`count(exact=true)`), les dimensions et
  les distances proviennent de la configuration de chaque collection.
- Aucun contenu de point, aucune valeur métier et aucun secret ne sont
  reproduits ici.
- Les writers/readers ont été recherchés dans les sources Python du dépôt.
  Une classe présente mais non instanciée n'est pas considérée comme raccordée.

## 2. État réel du serveur

| Indicateur | Valeur |
|---|---|
| Collections présentes | **9** |
| Collections absentes | **2** (`invoices`, `episodic_memory`) |
| Points stockés | **0** |
| Vecteurs de 768 dimensions | oui, pour toutes les collections présentes |
| Distance | `Cosine` |
| Points non conformes au contrat facture | **0** |
| Candidats de purge | **0** |

### 2.1 Collections présentes

| Collection | Points | Dimension | Distance | Index de payload | Rôle | Tenant |
|---|---:|---:|---|---|---|---|
| `agent_skills` | 0 | 768 | Cosine | — | Skills distillés (legacy) | Aucun |
| `document_bus` | 0 | 768 | Cosine | — | Bus documentaire RAG (legacy) | Aucun |
| `invoice_documents` | 0 | 768 | Cosine | — | Ancienne similarité facture (legacy) | Aucun |
| `mem0` | 0 | 768 | Cosine | `actor_id`, `agent_id`, `run_id`, `user_id` | Mémoire Mem0 | Aucun `tenant_id` |
| `mem0_memories` | 0 | 768 | Cosine | `actor_id`, `agent_id`, `run_id`, `user_id` | Ancienne collection de test Mem0 — **blacklistée** | Aucun |
| `mem0migrations` | 0 | 768 | Cosine | `actor_id`, `agent_id`, `run_id`, `user_id` | Migrations Mem0 — **blacklistée** | Aucun |
| `research_cache` | 0 | 768 | Cosine | — | Cache de recherche (legacy) | Aucun |
| `skills_catalog` | 0 | 768 | Cosine | — | Ancienne collection planifiée — **blacklistée** | Aucun |
| `teacher_student_metrics` | 0 | 768 | Cosine | — | Métriques teacher/student (legacy) | Aucun |

### 2.2 Collections absentes

| Collection | Points | Statut |
|---|---:|---|
| `invoices` | 0 | **Canonique** ; créée paresseusement par `QdrantVectorStore.ensure_collection()` au premier commit |
| `episodic_memory` | 0 | Ancienne collection `repositories/memory_repository.py`, non raccordée |

### 2.3 Collection canonique `invoices`

- Écriture : `services/persistence_manager.py::PersistenceManager` via
  `QdrantVectorStore`.
- Payload strict : `schemas/vector_payload_schema.py::InvoiceVectorPayloadV1`.
- Champs structurants : `contract_version="1.0.1"`, `tenant_id`,
  `source_sha256`, `invoice_id`, `point_id`, `text`.
- Vecteur : 768 dimensions, distance `Cosine`.
- Clé de point : `uuid5(NAMESPACE_URL, "qdrant:<invoice_id>")`.
- Isolation : **toute lecture métier doit filtrer `tenant_id`** ; aucune
  recherche multi-tenant sans filtre explicite.

## 3. Purge P9.4

Commande exécutée :

```powershell
A:\dsh-core\.venv-v3\Scripts\python.exe tools\audit_qdrant_collections.py --sanitize --apply
```

Points supprimés :

| Collection | Points |
|---|---:|
| `agent_skills` | 4 |
| `document_bus` | 4 |
| `invoice_documents` | 3 |
| `mem0_memories` | 1 |
| `teacher_student_metrics` | 16 |
| **Total** | **28** |

Après suppression, l'audit final ne signale :

- aucun point non conforme ;
- aucun candidat de purge ;
- aucun point dans une collection blacklistée.

Rapports :

- planification : `workspace/qdrant/audit/qdrant-audit-20261031T165442Z.json` ;
- contrôle final : `workspace/qdrant/audit/qdrant-audit-20261031T165449Z.json`.

## 4. Writers / readers identifiés

| Composant | Collection | Écriture | Lecture |
|---|---|---|---|
| `services/persistence_manager.py::PersistenceManager` | `invoices` (canonique) | `QdrantVectorStore.upsert` | — (writer du pipeline) |
| `repositories/skills_repository.py` | `agent_skills` | `save_skill` | `find_relevant_skills`, `increment_usage` |
| `agents/teacher_student_agent.py` | `teacher_student_metrics` | `_record_metric` | `_should_create_skill` |
| `repositories/teacher_student_repository.py` | `teacher_student_metrics` | — | `get_global_metrics` |
| `agents/research_agent.py` | `research_cache`, `document_bus` | `_save_cache` | `_check_cache`, retriever documentaire |
| `memory_manager.py` (racine) | `document_bus`, `mem0` | `indexer_document`, `ajouter_souvenir` | `interroger_documents`, `recuperer_contexte` |
| `services/invoice_similarity_service.py` | `invoice_documents` | Aucun writer canonique | Recherche vectorielle et par fournisseur |
| `repositories/memory_repository.py` | `episodic_memory` | `save_memory` | `search_semantic_memory`, `get_session_history` |
| `test_mem0_qdrant.py` | `mem0_memories` | `Memory.add` | `Memory.search` |

Les collections `agent_skills`, `document_bus`, `research_cache`,
`invoice_documents`, `teacher_student_metrics`, `mem0`, `mem0_memories`,
`mem0migrations`, `skills_catalog` et `episodic_memory` ne portent pas de
`tenant_id`. Elles ne doivent pas servir de source d'isolation multi-tenant
pour le pipeline facture.

## 5. Historique

- **2026-10-19** : relevé initial, 8 collections présentes et 23 points
  (dont des skills, des documents legacy et des métriques). Il incluait
  `research_cache` vide et ne contenait pas encore `mem0_memories`.
- **2026-10-31 (P9.4)** : audit et purge. Le serveur comptait 9 collections
  présentes, 28 points et 0 point conforme au contrat facture. Les 28 points
  ont été supprimés ; l'état courant est de 0 point.
- Les anciens audits P1-2/P1-3 restent des pièces historiques ; leurs chiffres
  ne décrivent plus l'état courant.

## 6. Vérification

```powershell
A:\dsh-core\.venv-v3\Scripts\python.exe tools\audit_qdrant_collections.py
```

Attendu en mode courant : `Collections=11 present=9 points=0 conforming=0
purge_candidates=0`.
