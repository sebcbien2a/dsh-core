# DSH-Core

Pipeline comptable multi-agents : perception documentaire (PDF, images, tableurs, relevés bancaires), graphe de factures, règles métiers, mémoire fournisseurs et persistance Qdrant/Neo4j.

Point d'entrée maître : [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md).

## Statut

| Domaine | Statut |
|---|---|
| Socle Document & Vision Pipeline (P0→P8.1) | v1.0.1 FROZEN — [contrat](docs/DOCUMENT_PERCEPTION_CONTRACT.md) |
| Contrat de persistance | v1.0.2 FROZEN — identifiants isolés par tenant et SHA-256 — [contrat](docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md) |
| Phases P10.1 / P10.2 | Terminées — isolation de la persistance (61 tests) et middleware multi-tenant (39 tests) |
| Tests | 2 409 passés hors-ligne (0 échec) |
| Qdrant | purgé (0 point legacy) ; `docker/qdrant_storage` supprimé |
| `services/neo4j_service.py`, `repositories/graph_repository.py` | non branchés (legacy) |

Arête 5 : ontologie Neo4j unifiée `Tenant`/`Supplier`/`Invoice`/`InvoiceLine`/`RuleOutcome`, correction du bug E4, payload Qdrant strict `InvoiceVectorPayloadV1`. En v1.0.2, `invoice_id`, `line_id` et `point_id` sont dérivés de `tenant_id` + SHA-256 pour éviter les collisions inter-tenants.

P10.1 est couvert par [test_multitenant_isolation.py](tests/test_multitenant_isolation.py) ; P10.2 par [test_tenant_middleware_exhaustive.py](tests/test_tenant_middleware_exhaustive.py). Le point d'entrée FastAPI intégrant le middleware est [backend/app/main.py](backend/app/main.py).

## Validation

Toujours utiliser l'interpréteur du projet :

```powershell
& 'A:\dsh-core\.venv-v3\Scripts\python.exe' -m pytest tests -q
```

Suites exhaustives (hors-ligne) :

```powershell
& 'A:\dsh-core\.venv-v3\Scripts\python.exe' -m pytest tests\test_banking_producer_exhaustive.py tests\test_spreadsheet_producer_exhaustive.py tests\test_rule_registry_exhaustive.py tests\test_vision_budget_supervisor_exhaustive.py tests\test_document_router_exhaustive.py -q
```

## Feuille de route

- **P10** — Isolation stricte multi-tenant (Qdrant, Neo4j, Obsidian) pour les 20 comptes clients professionnels : authentification, middleware `tenant_id` obligatoire, tests d'isolation.
- **P11** — Portail web & recherche à facettes (dates, montants, catégories comptables, statuts).
- **P12** — Export comptable (FEC, intégration ERP / logiciels comptables).

Détails : [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md), [docs/NEXT_ACTIONS.md](docs/NEXT_ACTIONS.md), [docs/collections_qdrant.md](docs/collections_qdrant.md), [QDRANT_INVENTORY.md](QDRANT_INVENTORY.md), [NEO4J_INVENTORY.md](NEO4J_INVENTORY.md).
