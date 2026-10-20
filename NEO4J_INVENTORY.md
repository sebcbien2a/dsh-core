# Inventaire Neo4j — DSH-Core

## Méthode et limites

- Le serveur local `dsh-neo4j` était démarré et répondait au relevé du
  **2026-10-19**.
- Les données ont été comptées par des requêtes Cypher en lecture seule dans la
  base `neo4j`. Seuls labels, types de relations, noms de propriétés, comptes et
  dates déjà présentes ont été examinés; les valeurs métier ne sont pas
  reproduites ici.
- Les usages et appels applicatifs ont été recherchés dans les sources Python
  du dépôt. Aucun appel n'a été exécuté pour cet audit.
- « ABANDONNÉ » décrit ici un composant sans raccordement appelant trouvé dans
  le dépôt courant; ce constat ne prouve pas qu'il n'est utilisé par un système
  externe.

## Résultat runtime — données réellement enregistrées

Le serveur contient **3 nœuds** et **2 relations** :

| Label de nœud réellement observé | Nombre | Propriétés présentes | Date métier observée |
|---|---:|---|---|
| `Fournisseur` | 1 | `nom`, `region` | Aucune propriété de date observée |
| `Facture` | 1 | `numero`, `date`, `montant` | `2026-09-20` |
| `MouvementBancaire` | 1 | `id`, `date`, `montant` | `2026-09-27` |

Relations présentes :

| Source | Relation | Cible | Nombre |
|---|---|---|---:|
| `Fournisseur` | `A_EMIS` | `Facture` | 1 |
| `MouvementBancaire` | `REGLE` | `Facture` | 1 |

Aucune contrainte Neo4j n'a été retournée. Les seuls index relevés sont les
index natifs de lookup par label de nœud et type de relation.

### Écart avec le modèle de graphe du code

`GraphRepository.create_invoice_graph` écrit des labels anglais `Supplier`,
`Invoice` et `Document`, avec des relations `BELONGS_TO` et `CONTAINS`.
`Neo4jService` recherche les mêmes labels anglais et relations.
Les nœuds présents utilisent au contraire `Fournisseur`, `Facture` et
`MouvementBancaire`, et les relations `A_EMIS` et `REGLE`.

Par conséquent, **les requêtes actuelles de `Neo4jService` ne correspondent pas
au graphe observé** et ne trouveraient pas les données présentes sous les
labels/relation indiqués. Le relevé runtime ne permet pas d'attribuer avec
certitude qui a créé ces données.

## ACTIF

### Service Neo4j

- Le conteneur Neo4j est actif et accessible.
- Le graphe comporte les trois nœuds et deux relations décrits ci-dessus.
- Ce statut est celui de l'infrastructure et des données présentes; il ne
  signifie pas que le code `GraphRepository` est utilisé par l'application.

## TEST

### `GraphRepository` — code d'accès/écriture présent

Fichier : `repositories/graph_repository.py`.

Les recherches d'appelants ne trouvent **aucune instanciation directe**
(`GraphRepository(...)`) dans les sources du dépôt. Les seuls appels aux
méthodes de ce repository proviennent de `services/memory_manager.py`, dont la
classe `MemoryManager` n'a elle-même aucun appelant/instanciation repéré.
L'accès applicatif à ce repository n'est donc pas raccordé dans le code courant.

Méthodes et Cypher définis :

1. `document_exists(document_hash)` — **lecture**
   ```cypher
   MATCH (d:Document {hash: $hash})
   RETURN count(d) > 0 AS exists
   ```
2. `create_entity(label, properties, unique_key)` — **écriture**;
   `MERGE`/mise à jour quand la clé existe, sinon `CREATE`
   ```cypher
   MERGE (n:<label> {<unique_key>: $unique_value})
   SET n += $props
   RETURN elementId(n) AS node_id
   ```
   ou
   ```cypher
   CREATE (n:<label> $props)
   RETURN elementId(n) AS node_id
   ```
3. `create_relationship(...)` — **écriture**
   ```cypher
   MATCH (a:<label_a> {<key_a>: $val_a})
   MATCH (b:<label_b> {<key_b>: $val_b})
   MERGE (a)-[r:<rel_type>]->(b)
   RETURN type(r)
   ```
4. `create_invoice_graph(invoice_data, document_hash)` — **écriture**, au sein
   de la méthode :
   ```cypher
   MERGE (s:Supplier {name: $fournisseur})
   ```
   ```cypher
   MERGE (i:Invoice {id: $invoice_id})
   SET i += $props
   WITH i
   MATCH (s:Supplier {name: $fournisseur})
   MERGE (i)-[:BELONGS_TO]->(s)
   ```
   Si `document_hash` est fourni :
   ```cypher
   MERGE (d:Document {hash: $hash})
   WITH d
   MATCH (i:Invoice {id: $invoice_id})
   MERGE (d)-[:CONTAINS]->(i)
   ```
5. `execute_query(query, parameters)` — exécute le Cypher reçu en argument;
   aucune requête arbitraire appelante n'a été trouvée dans le dépôt.

### Appels `GraphRepository` trouvés

Tous les sites sont dans `services/memory_manager.py` :

- `MemoryManager.is_document_known` → `graph_repo.document_exists`.
- `MemoryManager.save_invoice_context` → `graph_repo.create_invoice_graph`.
- `MemoryManager.save_skill` → `create_entity` pour `Skill` et `Task`, puis
  `create_relationship` avec `SOLVES`.
- `MemoryManager.save_episodic_memory` → `create_entity` pour `Session` et
  `Message`, puis `create_relationship` avec `HAS_MESSAGE`.

Ces sites sont des appels de méthode présents, mais aucun code trouvé
n'instancie `MemoryManager` ni n'injecte un `GraphRepository` dans ce manager.
Ils ne démontrent donc pas que ces écritures ont été faites sur le serveur.

**Classement : TEST** — code d'accès implémenté, mais sans chemin d'appel
applicatif courant trouvé; son schéma ne correspond pas au graphe actuellement
présent.

## ABANDONNÉ

### `Neo4jService` — lectures métier non raccordées

Fichier : `services/neo4j_service.py`.

Aucun appel à `Neo4jService(...)` ni à ses méthodes n'a été trouvé dans les
sources du dépôt. Le service délègue à `GraphRepository.execute_query` ou
`document_exists`; il ne constitue donc pas à lui seul une connexion Neo4j.

Requêtes déclarées :

1. `get_supplier_history(supplier_name)` — lit factures et fournisseur :
   ```cypher
   MATCH (i:Invoice)-[:BELONGS_TO]->(s:Supplier {name: $supplier_name})
   RETURN i.id AS invoice_id, i.montant_ttc AS montant_ttc,
          i.date_facture AS date_facture, i.qdrant_vector_id AS qdrant_id
   ORDER BY i.date_facture DESC
   ```
2. `get_invoice_lineage(invoice_num, supplier_name)` — lit la chaîne document,
   facture, fournisseur :
   ```cypher
   MATCH (d:Document)-[:CONTAINS]->(i:Invoice {id: $invoice_id})-[:BELONGS_TO]->(s:Supplier)
   RETURN d.hash AS document_hash, i.id AS invoice_id, s.name AS supplier_name
   ```
3. `get_skills_for_task(task_name)` — lit les skills liés aux tâches :
   ```cypher
   MATCH (s:Skill)-[:SOLVES]->(t:Task {name: $task_name})
   RETURN s.id AS skill_id, s.snippet AS skill_snippet
   ```
4. `verify_document_ingestion(document_hash)` — délègue à
   `GraphRepository.document_exists`, qui cherche un nœud `Document`.

Ces requêtes utilisent `Invoice`, `Supplier` et `Document`, labels qui ne
correspondent pas à `Facture` et `Fournisseur` présents dans la base. Aucun nœud
`Document`, `Skill`, `Task`, `Session` ou `Message` n'a été observé dans le
relevé runtime.

**Classement : ABANDONNÉ (au sens non raccordé dans les sources examinées)** —
service défini, mais non instancié ni appelé, et requêtes alignées sur un schéma
différent de celui des données présentes.

## Conclusion

- La base Neo4j est en fonctionnement et contient un petit graphe non vide :
  **1 fournisseur, 1 facture et 1 mouvement bancaire**, reliés par deux
  relations.
- Les classes `GraphRepository` et `Neo4jService` ainsi que les méthodes du
  `MemoryManager` fournissent un autre schéma de graphe, majoritairement en
  anglais.
- Aucun branchement de ces composants depuis l'API ou le pipeline facture
  courant n'a été trouvé. Les données runtime ne peuvent pas être attribuées
  avec certitude à ces méthodes et ne suivent pas leur schéma.
