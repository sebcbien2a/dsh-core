# DSH-Core

Pipeline comptable multi-agents : perception documentaire (PDF, images, tableurs, relevés bancaires), graphe de factures, règles métiers, mémoire fournisseurs et persistance Qdrant/Neo4j.

Point d'entrée maître : [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md).

## Statut

| Domaine | Statut |
|---|---|
| Socle Document & Vision Pipeline (P0→P8.1) | v1.0.1 FROZEN — [contrat](docs/DOCUMENT_PERCEPTION_CONTRACT.md) |
| Contrat de persistance | v1.0.2 FROZEN — identifiants isolés par tenant et SHA-256 — [contrat](docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md) |
| Phases P10→P13 | Terminées — isolation multi-tenant, rapprochement bancaire avec arbitrage HITL, exports comptables, persistance SQLite et ingestion bancaire |
| Phase active P14 | Architecture « Machine à états » OneDrive / Gmail (P14.1) |
| Tests | 2 656 passés hors-ligne, 0 échec, 2 conditionnés |
| Qdrant | purgé (0 point legacy) ; `docker/qdrant_storage` supprimé |
| `services/neo4j_service.py`, `repositories/graph_repository.py` | non branchés (legacy) |

Arête 5 : ontologie Neo4j unifiée `Tenant`/`Supplier`/`Invoice`/`InvoiceLine`/`RuleOutcome`, correction du bug E4, payload Qdrant strict `InvoiceVectorPayloadV1`. En v1.0.2, `invoice_id`, `line_id` et `point_id` sont dérivés de `tenant_id` + SHA-256 pour éviter les collisions inter-tenants.

P10.1 est couvert par [test_multitenant_isolation.py](tests/test_multitenant_isolation.py) ; P10.2 par [test_tenant_middleware_exhaustive.py](tests/test_tenant_middleware_exhaustive.py). Le point d'entrée FastAPI intégrant le middleware est [backend/app/main.py](backend/app/main.py).

P10 est achevée : P10.1 gèle le contrat de persistance Neo4j/Qdrant v1.0.2 avec `invoice_id`, `line_id` et `point_id` isolés par `tenant_id` + SHA-256 ; P10.2 fournit le middleware ASGI pur `TenantContextMiddleware`, qui contrôle `X-Tenant-ID` et renseigne le `ContextVar` ; P10.3 ajoute `RuleRegistryService` avec les règles communes (`knowledge/rules/common/`) et tenant (`knowledge/rules/tenants/{tenant_id}/`), protégées contre la traversée de répertoire.

P11 est achevée : `POST /reconciliation/arbitrate` gère `ATTACH_INVOICE`, `PERSONAL_EXPENSE` (comptes 108/455) et `VALIDATE_WITHOUT_INVOICE` ; `ObsidianCandidateRuleWriter` persiste les règles candidates en Markdown avec frontmatter YAML ; `GET /reconciliation/lines` offre pagination, filtres, recherche plein texte et mapping des statuts. L'interface React / Next.js comprend [reconciliation.ts](frontend/types/reconciliation.ts), [ArbitrationModal.tsx](frontend/components/reconciliation/ArbitrationModal.tsx) et [ReconciliationTable.tsx](frontend/components/reconciliation/ReconciliationTable.tsx).

P12 est achevée — **Exports comptables & fiscaux** :
- **P12.1 — FEC** : moteur officiel A.47 A-1 dans `services/fec_export_service.py`, validation des 18 colonnes et équilibre strict Débit = Crédit ; endpoint `GET /accounting/export/fec`.
- **P12.2 — Tableurs** : moteur CSV de révision comptable et de relevé des arbitrages dans `services/accounting_spreadsheet_export_service.py`, avec protection contre l'injection de formules ; endpoint `GET /accounting/export/spreadsheet`.
- **P12.3 — Interface** : barre d'actions d'export dans [ReconciliationTable.tsx](frontend/components/reconciliation/ReconciliationTable.tsx), filtres de dates et gestion du téléchargement blob.

P13 est achevée — **Persistance durable & ingestion bancaire** :
- **P13.1 — Store SQLite** : store multi-tenant hermétique dans `services/sqlite_accounting_store.py`, unifiant `FecEntryStore` et `ArbitrationStore` ; couverture dans [test_sqlite_accounting_store_exhaustive.py](tests/test_sqlite_accounting_store_exhaustive.py).
- **P13.2 — Parseur bancaire** : parseur multi-formats dans `services/bank_statement_parser_service.py`, déduplication par hash SHA-256 déterministe ; endpoint `POST /reconciliation/import` (multipart ou JSON) ; couverture dans [test_bank_statement_import_exhaustive.py](tests/test_bank_statement_import_exhaustive.py).
- **P13.3 — Upload interface** : bouton « Importer un relevé (.csv) » dans [ReconciliationTable.tsx](frontend/components/reconciliation/ReconciliationTable.tsx), état d'import et rapport `{imported, skipped}`.

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

- **P10** — Terminée : persistance v1.0.2 FROZEN, middleware de contexte tenant et registre de règles hiérarchique.
- **P11** — Terminée : arbitrage HITL, persistance des règles candidates, consultation paginée/recherchable et interface de rapprochement.
- **P12** — Terminée : exports FEC A.47 A-1 et CSV de révision/arbitrages, avec contrôles comptables et actions d'export dans l'interface.
- **P13** — Terminée : store SQLite multi-tenant unifié, parseur bancaire avec déduplication SHA-256 et endpoint d'import, upload CSV dans l'interface React.
- **P14** — Phase active : P14.1 architecture « Machine à états » OneDrive / Gmail — dossiers `00_` à `05_`, 8 catégories, fichier `_A_COMPLETER.xlsx` et synchronisation bidirectionnelle.

Détails : [docs/PROJECT_HANDOVER.md](docs/PROJECT_HANDOVER.md), [docs/NEXT_ACTIONS.md](docs/NEXT_ACTIONS.md), [docs/collections_qdrant.md](docs/collections_qdrant.md), [QDRANT_INVENTORY.md](QDRANT_INVENTORY.md), [NEO4J_INVENTORY.md](NEO4J_INVENTORY.md).
