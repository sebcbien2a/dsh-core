---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 5 — Persistance & Mémoire Métier
contract_version: 1.0.2
status: FROZEN
upstream_dependencies: [schemas/invoice_schema.py, schemas/vector_payload_schema.py, services/persistence_manager.py]
downstream_consumers: [backend/app/graphs/invoice_graph.py, tools/audit_qdrant_collections.py]
verification_tests: [tests/test_persistence_manager.py, tests/test_multitenant_isolation.py, tests/test_vector_payload_schema.py, tests/test_invoice_graph.py, tests/test_qdrant_audit_service.py]
---

# Contrat de persistance unifiée Qdrant / Neo4j — v1.0.2

**Statut : FROZEN — v1.0.1 scellée en phase P9.4 (31 octobre 2026) ; v1.0.2 (P10.1) : isolation multi-tenant des identifiants.**

## 0. Évolution v1.0.2 (P10.1) — étanchéité inter-tenants

Quatre défauts d'isolation de la v1.0.1 sont corrigés dans
`services/persistence_manager.py`. Le schéma `InvoiceVectorPayloadV1`
(`contract_version` du payload = `"1.0.1"`) est **inchangé**.

| Défaut v1.0.1 | Correction v1.0.2 |
|---|---|
| `tenant_id` vide ou mal formé accepté (repli silencieux sur `company_id`, écriture Neo4j avant rejet Qdrant) | `tenant_id` résolu (explicite, constructeur, `DSH_TENANT_ID`, `company_id`) puis validé par `^[A-Za-z0-9._:-]{1,64}$` **avant toute écriture** ; sinon `graph_status="FAILED"`, `vector_status="FAILED"`, erreur `tenant: …`. Une chaîne vide explicite n'est plus remplacée. |
| `invoice_id`/`point_id` identiques pour deux tenants important le même document | `invoice_id = uuid5(NAMESPACE_URL, "<tenant_id>:<source_sha256>")` ; `point_id = uuid5(NAMESPACE_URL, "qdrant:<invoice_id>")` |
| `line_id` dépendant du seul `source_sha256` | `line_id = uuid5(NAMESPACE_URL, "<tenant_id>:<source_sha256>:<line_index>")` |
| `MERGE (i:Invoice {source_sha256})` et contrainte d'unicité globale | `MERGE (i:Invoice {invoice_id: $invoice_id})` ; contrainte `dsh_invoice_invoice_id` (`i.invoice_id IS UNIQUE`) ; `source_sha256` reste une propriété non unique |

Sans `source_sha256` (rien n'est écrit), `invoice_id` retombe sur l'ancien
calcul métier, à titre informatif.

**Migration** : `ensure_constraints()` exécute `DROP CONSTRAINT
dsh_invoice_source_sha256 IF EXISTS` avant de créer les contraintes. Les
nœuds `Invoice`/`InvoiceLine` et points Qdrant écrits en v1.0.1 portent
d'anciens identifiants : ils ne sont pas réécrits automatiquement et doivent
être ré-ingérés (la base Qdrant est vide depuis P9.4). Le harnais
`tests/test_multitenant_isolation.py` verrouille ces garanties.

Ce document formalise le contrat de persistance de l'Arête 5. Le contrat de
perception de l'Arête 1 (`docs/DOCUMENT_PERCEPTION_CONTRACT.md`) reste
inchangé et scellé en v1.0.1. Toute évolution incompatible du présent contrat
exige une nouvelle phase, une note de migration et un incrément de version.

## 1. Portée et flux canonique

Le seul chemin d'écriture raccordé au pipeline facture est
`backend/app/graphs/invoice_graph.py::commit_node` → `PersistenceManager` :

```text
InvoiceSchema validé + RuleDecision
        │
        ▼
PersistenceManager.build_graph_record()
        ├── Neo4jGraphStore.upsert_invoice()  → graphe comptable
        └── InvoiceVectorPayloadV1            → QdrantVectorStore.upsert()
```

Les deux écritures sont indépendantes : une panne Neo4j ne bloque pas
l'indexation Qdrant, et inversement. Il n'existe pas de transaction distribuée
entre les deux bases.

## 2. Contrat Qdrant — `InvoiceVectorPayloadV1`

### 2.1 Collection et vecteur

| Élément | Valeur |
|---|---|
| Collection canonique | `invoices` |
| Dimension | `768` |
| Distance | `Cosine` |
| Création | Paresseuse par `QdrantVectorStore.ensure_collection()` au premier write |
| Écriture | `QdrantVectorStore.upsert()` |
| Clé de point | `uuid5(NAMESPACE_URL, "qdrant:<invoice_id>")` |
| Texte vectorisé | `PersistenceManager.build_vector_text()` |

La collection `invoices` est absente d'un serveur neuf ou purgé ; elle est
créée au premier commit. Les autres collections présentes sur le serveur ne
sont **pas** couvertes par ce contrat (voir
`docs/collections_qdrant.md`).

### 2.2 Payload strict

Le payload est validé par `schemas/vector_payload_schema.py::InvoiceVectorPayloadV1`
(`extra="forbid"`). Toute clé inconnue est refusée.

| Champ | Type | Obligatoire | Règle |
|---|---|:---:|---|
| `contract_version` | littéral | oui | exactement `"1.0.1"` |
| `tenant_id` | chaîne | oui | `[A-Za-z0-9_.:-]{1,64}` |
| `source_sha256` | chaîne | oui | 64 caractères hexadécimaux minuscules |
| `doc_type` | chaîne | oui | non vide |
| `invoice_id` | chaîne | oui | non vide |
| `point_id` | chaîne | non | identifiant Qdrant du point |
| `company_id` | chaîne | oui | non vide |
| `supplier_id` | chaîne | non | identifiant déterministe du fournisseur |
| `invoice_number` | chaîne | non | numéro métier |
| `party_name` | chaîne | non | nom de la partie concernée |
| `supplier_name` | chaîne | non | nom du fournisseur |
| `customer_name` | chaîne | non | nom du client |
| `issue_date` | chaîne | non | date ISO |
| `net_amount` | chaîne | non | montant HT sérialisé |
| `tax_amount` | chaîne | non | montant TVA sérialisé |
| `gross_amount` | chaîne | non | montant TTC sérialisé |
| `currency` | chaîne | oui | non vide |
| `direction` | chaîne | oui | `PURCHASE` ou `SALE` |
| `document_type` | chaîne | oui | type documentaire |
| `validation_status` | chaîne | oui | statut de validation |
| `category` | chaîne | non | catégorie métier |
| `rule` | objet | non | décision de règle sérialisée |
| `text` | chaîne | oui | texte source de l'embedding |

Les champs optionnels de type chaîne refusent une valeur vide ou composée
uniquement d'espaces.

### 2.3 Isolation multi-tenant

`tenant_id` est porté par chaque point et fait partie du contrat strict. Toute
lecture métier doit inclure un filtre `tenant_id`; une recherche sans ce filtre
est interdite pour les données de production.

Exemple de filtre Qdrant canonique :

```python
from qdrant_client.models import FieldCondition, Filter, MatchValue

tenant_filter = Filter(
    must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
    ]
)
```

Pour retrouver un document précis dans un tenant :

```python
source_filter = Filter(
    must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
        FieldCondition(key="source_sha256", match=MatchValue(value=source_sha256)),
    ]
)
```

`source_sha256` identifie le document source *au sein d'un tenant* : le même
fichier peut être indexé par plusieurs tenants, chacun obtenant son propre
point (`point_id` dérivé de `tenant_id` + `source_sha256`, voir §0).

> État opérationnel : `invoices` n'existe pas encore sur le serveur local et
> aucun index de payload n'est créé par le code. Un index `tenant_id` (et, si
> nécessaire, `source_sha256`) doit être ajouté côté exploitation avant une
> recherche filtrée à grande échelle.

## 3. Contrat Neo4j — ontologie unifiée

### 3.1 Nœuds et relations

| Élément | Propriétés principales | Relations |
|---|---|---|
| `(:Tenant)` | `tenant_id` (unique), `contract_version` | `OWNS` → `(:Invoice)` |
| `(:Supplier)` | `supplier_id` (unique), `name`, `siren`, `siret`, `vat_number`, `country` | `ISSUED` → `(:Invoice)` |
| `(:Invoice)` | `invoice_id` (unique, clé de fusion), `source_sha256` (non unique), `document_number`, `issue_date`, `gross_amount`, `net_amount`, `tax_amount`, `currency`, `direction`, `validation_status`, `contract_version` | reçue par `OWNS`, `ISSUED`; `HAS_LINE` → `(:InvoiceLine)`; `EVALUATED_BY` → `(:RuleOutcome)` |
| `(:InvoiceLine)` | `line_id` (clé de fusion), `line_index`, `description`, `quantity`, `unit_price`, `total_amount`, `tax_rate` | `HAS_LINE` depuis `(:Invoice)` |
| `(:RuleOutcome)` | `rule_id`, `outcome`, `reason` | `EVALUATED_BY` depuis `(:Invoice)` |

Le fournisseur et la règle sont optionnels : leurs clauses Cypher ne sont
émises que lorsque la donnée correspondante existe. Un tenant est toujours
créé ou fusionné.

### 3.2 Contraintes d'unicité

`Neo4jGraphStore.ensure_constraints()` garantit, une fois par instance :

```cypher
CREATE CONSTRAINT dsh_tenant_tenant_id IF NOT EXISTS
FOR (t:Tenant) REQUIRE t.tenant_id IS UNIQUE

CREATE CONSTRAINT dsh_supplier_supplier_id IF NOT EXISTS
FOR (s:Supplier) REQUIRE s.supplier_id IS UNIQUE

DROP CONSTRAINT dsh_invoice_source_sha256 IF EXISTS   -- migration v1.0.2

CREATE CONSTRAINT dsh_invoice_invoice_id IF NOT EXISTS
FOR (i:Invoice) REQUIRE i.invoice_id IS UNIQUE
```

### 3.3 Sémantique d'upsert

L'upsert est idempotent et s'appuie sur `invoice_id` (tenant + hash) pour la facture :

1. `MERGE` du `Tenant`, puis `SET t.contract_version = $contract_version`.
2. `MERGE` de l'`Invoice` sur `invoice_id`, puis mise à jour de ses
   propriétés et de `contract_version`.
3. `MERGE (t)-[:OWNS]->(i)`.
4. Si un fournisseur existe : `MERGE` du `Supplier` sur `supplier_id`,
   mise à jour de ses propriétés et `MERGE (s)-[:ISSUED]->(i)`.
5. Si une règle existe : `MERGE` du `RuleOutcome` sur
   `(rule_id, outcome, reason)` et `MERGE (i)-[:EVALUATED_BY]->(r)`.
6. Suppression des lignes obsolètes :
   `OPTIONAL MATCH (i)-[:HAS_LINE]->(stale:InvoiceLine)
   WHERE NOT stale.line_id IN $line_ids DETACH DELETE stale`.
7. `UNWIND $line_items`, `MERGE` de chaque `InvoiceLine` sur `line_id`, mise à
   jour des champs, puis `MERGE (i)-[:HAS_LINE]->(l)`.

L'identifiant de ligne est déterministe :

```text
line_id = uuid5(NAMESPACE_URL, "<tenant_id>:<source_sha256>:<line_index>")
```

Deux lignes de même description restent donc deux nœuds distincts si leurs
index diffèrent. L'identifiant de facture est également déterministe :
`uuid5(NAMESPACE_URL, "<tenant_id>:<source_sha256>")`.

### 3.4 Correction E4 — suppression de l'écrasement `Product`

La correction E4 est actée dans le contrat v1.0.1 :

- l'upsert unifié n'écrit **plus** de nœud `Product` ;
- les descriptions de lignes ne sont plus fusionnées dans une entité partagée
  susceptible d'être écrasée ;
- la donnée de ligne vit exclusivement dans `(:InvoiceLine)` et reste
  rattachée à sa facture par `HAS_LINE`.

Les tests `tests/test_persistence_manager.py` vérifient explicitement
l'absence de `Product` dans le Cypher généré, y compris lorsque deux lignes
partagent la même description.

### 3.5 Erreurs et statuts

| Statut | Signification |
|---|---|
| `PERSISTED` | Écriture Neo4j réussie |
| `INDEXED` | Écriture Qdrant réussie |
| `FAILED` | Écriture tentée mais en échec ; l'erreur est remontée dans `PersistenceResult.errors` |
| `UNAVAILABLE` | Backend non configuré ou driver absent |

`PersistenceManager.persist_invoice()` capture les erreurs de chaque backend
et continue vers l'autre. Une erreur Neo4j ne doit jamais empêcher
l'indexation Qdrant, ni l'inverse. La disponibilité Neo4j peut être sondée
sans exception via `Neo4jGraphStore.verify_connectivity()`.

`source_sha256` est obligatoire. Il provient, dans l'ordre, de l'argument
explicite, de `invoice.metadata["source_sha256"]`, ou du hachage du fichier
source lisible. Sans cette valeur, les deux statuts sont `FAILED` et aucune
écriture n'est effectuée.

## 4. Fichiers legacy — état non raccordé

Les deux fichiers suivants ne font pas partie du chemin de persistance
canonique :

| Fichier | État | Justification |
|---|---|---|
| `services/neo4j_service.py` | **NON RACCORDÉ / ABANDONNÉ** | Aucune instanciation ni appel trouvés dans le dépôt. Lecture d'un schéma legacy `Invoice`/`Supplier`/`Document`/`Skill`/`Task`. |
| `repositories/graph_repository.py` | **NON RACCORDÉ / LEGACY** | Aucune instanciation directe. Seul `services/memory_manager.py` le référence, mais `MemoryManager` n'est lui-même instancié par aucun appelant repéré. Écrit `Supplier`/`Invoice`/`Document` avec `BELONGS_TO`/`CONTAINS`, hors contrat unifié. |

Ces fichiers ne doivent pas être branchés au pipeline facture sans migration
explicite vers `services/persistence_manager.py`. Ils ne créent pas les
contraintes d'unicité du contrat v1.0.1.

## 5. Assainissement Qdrant P9.4

L'assainissement a été exécuté le **31 octobre 2026** sur
`http://localhost:6333` avec l'interpréteur du projet :

```powershell
A:\dsh-core\.venv-v3\Scripts\python.exe tools\audit_qdrant_collections.py --sanitize --apply
```

Résultat :

- **28 points supprimés** dans 5 collections non conformes ou blacklistées :
  `agent_skills` (4), `document_bus` (4), `invoice_documents` (3),
  `mem0_memories` (1), `teacher_student_metrics` (16).
- Audit post-nettoyage : **0 point**, **0 candidat de purge**, aucun point non
  conforme ou blacklisté restant.
- 9 collections restent présentes, toutes vides ; `invoices` et
  `episodic_memory` sont absentes. Les collections blacklistées
  (`skills_catalog`, `mem0migrations`, `mem0_memories`) restent présentes mais
  vides et ne contiennent plus aucun point.

Rapports générés :

- planification : `workspace/qdrant/audit/qdrant-audit-20261031T165442Z.json` ;
- contrôle final : `workspace/qdrant/audit/qdrant-audit-20261031T165449Z.json`.

La commande d'audit sans `--sanitize` reste strictement en lecture seule.

## 6. Vérification

```powershell
A:\dsh-core\.venv-v3\Scripts\python.exe -m pytest tests\test_persistence_manager.py -q
A:\dsh-core\.venv-v3\Scripts\python.exe -m pytest tests -q
$env:NEO4J_IT='1'
A:\dsh-core\.venv-v3\Scripts\python.exe -m pytest tests -q
```

Le test d'intégration Neo4j est opt-in : `NEO4J_IT=1`. Sans cette variable, il
est ignoré et la suite est entièrement hors-ligne.
