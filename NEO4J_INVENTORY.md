# Inventaire Neo4j — DSH-Core

**État de référence : 31 octobre 2026 (phase P9.4).**

Ce document décrit l'état réel du serveur local `dsh-neo4j` et distingue
explicitement le chemin de persistance canonique des fichiers legacy non
raccordés. Le contrat applicable est
[`docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md`](docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md).

## 1. Méthode

- Instance locale Neo4j **Community 2026.09.0**, base `neo4j`, accessible via
  le driver du projet.
- Relevé en lecture par requêtes Cypher avec l'interpréteur
  `A:\dsh-core\.venv-v3\Scripts\python.exe`.
- Seuls les labels, types de relations, noms de propriétés, comptes et
  contraintes sont reproduits. Aucune valeur métier et aucun secret ne sont
  affichés.
- « Non raccordé » signifie qu'aucune instanciation ou appel applicatif n'a
  été trouvé dans les sources du dépôt. Cela ne prouve pas l'absence d'un
  usage externe.

## 2. État réel

Le serveur contient **3 nœuds** et **2 relations** :

| Label observé | Nœuds | Propriétés observées |
|---|---:|---|
| `Facture` | 1 | `numero`, `date`, `montant` |
| `Fournisseur` | 1 | `nom`, `region` |
| `MouvementBancaire` | 1 | `id`, `date`, `montant` |

| Source | Relation | Cible | Relations |
|---|---|---|---:|
| `Fournisseur` | `A_EMIS` | `Facture` | 1 |
| `MouvementBancaire` | `REGLE` | `Facture` | 1 |

Contraintes d'unicité présentes :

| Nom | Type | Label / propriété |
|---|---|---|
| `dsh_tenant_tenant_id` | `NODE_PROPERTY_UNIQUENESS` | `Tenant.tenant_id` |
| `dsh_supplier_supplier_id` | `NODE_PROPERTY_UNIQUENESS` | `Supplier.supplier_id` |
| `dsh_invoice_source_sha256` | `NODE_PROPERTY_UNIQUENESS` | `Invoice.source_sha256` |

Index : les trois index `RANGE` associés à ces contraintes, plus les deux index
techniques `LOOKUP` de Neo4j.

### 2.1 Ontologie unifiée du contrat v1.0.1

L'ontologie canonique est celle de `services/persistence_manager.py` :

| Élément | Propriétés principales | Relations |
|---|---|---|
| `(:Tenant)` | `tenant_id`, `contract_version` | `OWNS` → `(:Invoice)` |
| `(:Supplier)` | `supplier_id`, `name`, `siren`, `siret`, `vat_number`, `country` | `ISSUED` → `(:Invoice)` |
| `(:Invoice)` | `source_sha256`, `invoice_id`, `document_number`, `issue_date`, `gross_amount`, `net_amount`, `tax_amount`, `currency`, `direction`, `validation_status`, `contract_version` | `OWNS`, `ISSUED`, `HAS_LINE`, `EVALUATED_BY` |
| `(:InvoiceLine)` | `line_id`, `line_index`, `description`, `quantity`, `unit_price`, `total_amount`, `tax_rate` | `HAS_LINE` depuis `(:Invoice)` |
| `(:RuleOutcome)` | `rule_id`, `outcome`, `reason` | `EVALUATED_BY` depuis `(:Invoice)` |

**Comptes actuels :** `Tenant` = 0, `Supplier` = 0, `Invoice` = 0,
`InvoiceLine` = 0, `RuleOutcome` = 0. L'ontologie unifiée est donc vide mais
prête : les contraintes existent et le premier commit de
`PersistenceManager` créera les nœuds. Les labels `Tenant`, `Supplier`,
`Invoice` et `RuleOutcome` figurent encore dans le catalogue de labels du
serveur parce qu'ils ont déjà été utilisés, mais ils ne portent aucun nœud.

### 2.2 Correction E4

La correction E4 est actée dans le contrat v1.0.1 :

- aucun nœud `Product` n'est écrit par l'upsert unifié ;
- aucune donnée de ligne ne peut écraser une entité partagée ;
- chaque ligne est un `(:InvoiceLine)` identifié par
  `uuid5(NAMESPACE_URL, "invoice-line:<source_sha256>:<line_index>")` et
  rattaché à sa facture par `HAS_LINE`.

Les tests `tests/test_persistence_manager.py` vérifient l'absence de `Product`
dans le Cypher, y compris pour deux lignes de même description.

### 2.3 Nettoyage P9.4

Un nœud `RuleOutcome` orphelin, résidu de l'ancien test d'intégration (règle
`RULE-TVA-ZERO-001` sans relation `EVALUATED_BY`), a été supprimé le
31 octobre 2026. Après nettoyage :

- aucun nœud `Tenant`, `Supplier`, `Invoice`, `InvoiceLine` ou `RuleOutcome` ;
- aucune donnée de test résiduelle ;
- les 3 nœuds legacy et 2 relations historiques sont conservés ;
- total : **3 nœuds, 2 relations**.

## 3. Chemin canonique raccordé

Le seul writer unifié est `services/persistence_manager.py` :

- `PersistenceManager.persist_invoice()` construit un `InvoiceGraphRecord` ;
- `Neo4jGraphStore.upsert_invoice()` exécute l'upsert idempotent ;
- `ensure_constraints()` crée les trois contraintes `IF NOT EXISTS` une fois
  par instance ;
- `verify_connectivity()` permet une sonde non bloquante.

L'appel applicatif est `backend/app/graphs/invoice_graph.py::commit_node`.
Aucun autre service n'écrit dans l'ontologie unifiée.

## 4. Fichiers legacy non raccordés

### 4.1 `services/neo4j_service.py`

- **Statut : NON RACCORDÉ / ABANDONNÉ.**
- Aucune instanciation de `Neo4jService(...)` ni appel de ses méthodes trouvé
  dans le dépôt.
- Le service délègue à `GraphRepository` et lit un schéma legacy
  `Invoice` / `Supplier` / `Document` / `Skill` / `Task`.
- Ses requêtes ne créent pas les contraintes du contrat v1.0.1.

### 4.2 `repositories/graph_repository.py`

- **Statut : NON RACCORDÉ / LEGACY.**
- Aucune instanciation directe de `GraphRepository(...)` trouvée.
- Seul `services/memory_manager.py` référence ce repository ; or
  `MemoryManager` n'est lui-même instancié par aucun appelant repéré.
- Le writer `create_invoice_graph()` écrit `Supplier` / `Invoice` / `Document`
  avec `BELONGS_TO` et `CONTAINS`, hors contrat unifié.
- Il imprime des erreurs sur la sortie standard et ne garantit pas les
  contraintes d'unicité du contrat.

Ces deux fichiers ne doivent pas être branchés au pipeline facture sans
migration explicite vers `services/persistence_manager.py`.

## 5. Vérification

```powershell
@'
from services.persistence_manager import Neo4jGraphStore
store = Neo4jGraphStore.from_env()
print("connectivité:", store.verify_connectivity())
with store.driver.session(database=store.database) as session:
    print("nœuds:", session.run("MATCH (n) RETURN count(n) AS c").single()["c"])
    print("relations:", session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"])
    print("contraintes:", session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names").single()["names"])
store.close()
'@ | A:\dsh-core\.venv-v3\Scripts\python.exe -
```

Attendu : connectivité `True`, 3 nœuds, 2 relations, trois contraintes
`dsh_*`.

## 6. Historique

- **2026-10-19** : relevé initial de 3 nœuds et 2 relations, sans contrainte
  applicative ; le schéma des fichiers legacy ne correspondait pas aux données
  présentes.
- **P9.1 / P9.2** : mise en place de l'ontologie unifiée et des contraintes.
- **2026-10-31 (P9.4)** : suppression du `RuleOutcome` orphelin de test ;
  inventaire consolidé. Les données legacy `Facture` / `Fournisseur` /
  `MouvementBancaire` restent inchangées et non attribuées.
