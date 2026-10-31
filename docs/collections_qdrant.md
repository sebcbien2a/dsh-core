---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe — Collections Qdrant
contract_version: 1.0.1
status: ACTIVE
upstream_dependencies: [docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md, schemas/vector_payload_schema.py]
downstream_consumers: [services/persistence_manager.py, services/invoice_similarity_service.py, agents/research_agent.py, repositories/skills_repository.py, repositories/memory_repository.py, repositories/teacher_student_repository.py]
verification_tests: [tests/test_persistence_manager.py, tests/test_qdrant_audit_service.py, tests/test_vector_payload_schema.py]
---

# Collections Qdrant — état et règles d'usage

**Relevé de référence : 31 octobre 2026, Qdrant 1.19.1, `http://localhost:6333`.**

Le contrat de persistance est décrit dans
[`PERSISTENCE_QDRANT_NEO4J_CONTRACT.md`](PERSISTENCE_QDRANT_NEO4J_CONTRACT.md).
Ce document est l'annexe opérationnelle des collections : il décrit ce qui
existe réellement, ce qui est canonique et quels filtres de tenant appliquer.

## 1. Synthèse du serveur

| Indicateur | Valeur au 2026-10-31 |
|---|---|
| Collections présentes | **9** |
| Collections absentes | **2** (`invoices`, `episodic_memory`) |
| Points stockés | **0** |
| Dimension des vecteurs | **768** |
| Distance | **Cosine** |
| Points non conformes au contrat facture | **0** |
| Candidats de purge | **0** |
| Collections blacklistées non vides | **0** |

La purge P9.4 a supprimé les **28** derniers points non conformes au contrat
facture ou appartenant à une collection blacklistée. Le détail des rapports
est en section 5.

## 2. Collection canonique `invoices`

C'est la seule collection couverte par le contrat de persistance v1.0.1.

| Élément | Valeur |
|---|---|
| Nom | `invoices` |
| Présence actuelle | **absente** — création paresseuse au premier commit |
| Vecteur | 768 dimensions, Cosine |
| Écriture | `services/persistence_manager.py::QdrantVectorStore.upsert` |
| Payload | `schemas/vector_payload_schema.py::InvoiceVectorPayloadV1` |
| Clé de point | `uuid5(NAMESPACE_URL, "qdrant:<invoice_id>")` |
| Champs de tenant | `tenant_id` (obligatoire), `source_sha256` (identité document) |

Le payload est strict : `contract_version="1.0.1"`, `tenant_id` conforme à
`[A-Za-z0-9_.:-]{1,64}`, `source_sha256` sur 64 caractères hexadécimaux
minuscules, et aucune clé inconnue (`extra="forbid"`).

### 2.1 Filtres tenant_id

Toute recherche métier sur `invoices` doit être filtrée par `tenant_id` :

```python
from qdrant_client.models import FieldCondition, Filter, MatchValue

tenant_filter = Filter(
    must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    ]
)

points = client.query_points(
    collection_name="invoices",
    query=query_vector,
    query_filter=tenant_filter,
    limit=10,
    with_payload=True,
).points
```

Recherche d'un document précis dans un tenant :

```python
document_filter = Filter(
    must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        FieldCondition(key="source_sha256", match=MatchValue(value=source_sha256)),
    ]
)
```

Le `tenant_id` résolu par `PersistenceManager` suit cet ordre :

1. argument explicite `tenant_id` ;
2. `PersistenceManager.tenant_id` ;
3. variable d'environnement `DSH_TENANT_ID` ;
4. `invoice.company_id` ;
5. `DEFAULT_COMPANY_ID` (`DSH_CORE`).

Le code ne crée pas encore d'index de payload `tenant_id` ou
`source_sha256` sur `invoices`. Un index de payload doit être ajouté côté
exploitation avant une recherche filtrée à grande échelle.

## 3. Collections présentes hors contrat canonique

Toutes les collections ci-dessous sont **vides** au 2026-10-31. Elles ne
possèdent pas de champ `tenant_id` et ne doivent pas être utilisées pour une
lecture multi-tenant du pipeline facture.

| Collection | Points | Rôle / raccordement | Tenant |
|---|---:|---|---|
| `agent_skills` | 0 | Skills distillés ; `repositories/skills_repository.py`, `services/skill_router.py` | Aucun `tenant_id` |
| `document_bus` | 0 | Bus documentaire RAG ; `memory_manager.py` (racine), `agents/research_agent.py` via LlamaIndex | Aucun `tenant_id` |
| `invoice_documents` | 0 | Ancien service de similarité facture ; `services/invoice_similarity_service.py`, sans writer canonique | Aucun `tenant_id` |
| `research_cache` | 0 | Cache de recherche ; `agents/research_agent.py` (`_check_cache` / `_save_cache`) | Aucun `tenant_id` |
| `teacher_student_metrics` | 0 | Métriques teacher/student ; `agents/teacher_student_agent.py`, `repositories/teacher_student_repository.py` | Aucun `tenant_id` |
| `mem0` | 0 | Mémoire Mem0 ; `memory_manager.py` (racine) | Index `user_id`, `agent_id`, `actor_id`, `run_id` — pas de `tenant_id` |
| `mem0migrations` | 0 | Collection interne de migrations Mem0 | **Blacklistée**, aucun `tenant_id` |
| `mem0_memories` | 0 | Ancienne collection de test Mem0 (`test_mem0_qdrant.py`) | **Blacklistée**, aucun `tenant_id` |
| `skills_catalog` | 0 | Ancienne collection planifiée, sans writer/reader courant | **Blacklistée**, aucun `tenant_id` |

### 3.1 Collections définies par le code mais absentes

| Collection | Points | Statut |
|---|---:|---|
| `invoices` | 0 | Canonique, créée au premier write |
| `episodic_memory` | 0 | Ancienne collection `repositories/memory_repository.py`, non raccordée |

## 4. Règles d'isolation

1. **Ne jamais interroger `invoices` sans `tenant_id`** hors audit
   administratif explicitement autorisé.
2. **Ne pas déduire un tenant** depuis `agent_skills`, `document_bus`,
   `research_cache`, `invoice_documents`, `teacher_student_metrics`,
   `mem0`, `mem0migrations`, `mem0_memories` ou `skills_catalog` : ces
   collections n'ont pas de champ tenant.
3. **Ne pas écrire dans `invoice_documents`** depuis le pipeline courant :
   le writer canonique est `PersistenceManager` vers `invoices`.
4. **Ne pas réutiliser `mem0` comme stockage facture** : ses index portent sur
   l'utilisateur et la session Mem0, pas sur le tenant comptable.
5. Une recherche transverse (plusieurs tenants) doit être réservée à un
   outil d'administration et tracée ; elle ne fait pas partie du contrat.

## 5. Audit et assainissement P9.4

Commande exécutée avec l'interpréteur du projet :

```powershell
A:\dsh-core\.venv-v3\Scripts\python.exe tools\audit_qdrant_collections.py --sanitize --apply
```

Résultat de la purge :

| Collection | Points supprimés |
|---|---:|
| `agent_skills` | 4 |
| `document_bus` | 4 |
| `invoice_documents` | 3 |
| `mem0_memories` | 1 |
| `teacher_student_metrics` | 16 |
| **Total** | **28** |

Audit final :

- 11 collections inspectées (9 présentes, 2 absentes) ;
- **0 point** ;
- **0 point non conforme** ;
- **0 candidat de purge** ;
- 3 collections blacklistées restent présentes mais **vides** :
  `skills_catalog`, `mem0migrations`, `mem0_memories`.

Rapports JSON :

- planification : `workspace/qdrant/audit/qdrant-audit-20261031T165442Z.json` ;
- contrôle final : `workspace/qdrant/audit/qdrant-audit-20261031T165449Z.json`.

La commande sans `--sanitize` est strictement en lecture seule. Le détail du
contrat de payload et des règles multi-tenant se trouve dans
[`PERSISTENCE_QDRANT_NEO4J_CONTRACT.md`](PERSISTENCE_QDRANT_NEO4J_CONTRACT.md).
