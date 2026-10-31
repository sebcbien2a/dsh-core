---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe — Feuille de route historique (9 phases)
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: []
downstream_consumers: []
verification_tests: []
---

# 02 — Roadmap DSH-Core : 9 phases et 5 Sources de Vérité

**Version :** 2.0
**Date de reconstruction :** 28/10/2026
**Statut :** Phase 0 en cours ; Phase 1 priorité n°1 ; Phases 2 et 3 priorités suivantes.

Ce document remplace la roadmap historique. Il fige la séquence de convergence de DSH-Core et la décision d'architecture des 5 Sources de Vérité (SSOT). Il est la référence pour l'ordre des chantiers ; les détails techniques restent dans les documents spécialisés.

## 1. Décision architecturale majeure — 5 Sources de Vérité (SSOT)

Axiome :

> **Donnée ≠ Règle ≠ Relation ≠ Recherche ≠ Procédure**

**Excel = Données**, **Obsidian = Règles**, **Neo4j = Relations**, **Qdrant = Recherche**, **LangGraph = Procédure**.

Chaque catégorie possède un seul référentiel maître. Aucune donnée n'est dupliquée d'un référentiel à l'autre ; les autres systèmes sont des projections ou des index reconstructibles.

| Catégorie | Source de vérité unique | Contenu exclusif | Interdits | Clés de liaison |
|---|---|---|---|---|
| **Données** | **Excel (`.xlsb` / `.xlsx`) et tables maîtres JSON** | Grilles tarifaires, catalogues articles, écritures comptables validées, montants figés | Règles logiques complexes, code, graphe de dépendances | `supplier_id` (UUID), `article_ref`, `invoice_id` |
| **Règles** | **Obsidian (`knowledge/rules/*.md`)** | Contrats YAML + Markdown (`status: approved`), règles TVA/territorialité, conditions fournisseurs, arbitrages humains | Grilles de prix, listes de factures, transactions runtime | `rule_id` + `sha256` |
| **Relations** | **Neo4j** | Nœuds et arêtes (`Supplier`, `Rule`, `Invoice`, `DeliveryNote`), lettrage, dépendances | Texte intégral des règles, embeddings, grilles Excel | `supplier_id`, `rule_id`, `invoice_id` |
| **Recherche** | **Qdrant** | Embeddings (`nomic-embed-text`) des articles, clauses de règles et historiques d'extraction | Statut d'approbation maître, vérité métier | `payload.source_id` + `source_hash` |
| **Procédure** | **LangGraph (Python)** | Graphe d'états, schémas Pydantic, appels d'outils, transitions | Constantes métier en dur (TVA, SIRET, fournisseurs, prix) | `state` ne contient que des IDs |

### 1.1 Hiérarchie de la connaissance

`Obsidian → RuleRegistryService → (Neo4j + Qdrant) → LangGraph`

LangGraph consomme les services de connaissance ; il ne les domine pas. Le flux est unidirectionnel.

### 1.2 Règles de cohérence

- **Obsidian est la SSOT des règles** : toute règle est créée, modifiée et approuvée dans `knowledge/rules/`, jamais directement dans Neo4j, Qdrant ou le code.
- **Projections unidirectionnelles** : `RuleRegistryService` calcule le `sha256` de chaque fichier Markdown, met à jour le nœud `(:Rule {rule_id, version, status, sha256})` dans Neo4j et réindexe le vecteur dans Qdrant.
- **Reconstruction** : vider Neo4j ou Qdrant ne perd aucune règle ; `rebuild_projections()` reconstruit les index depuis Obsidian.
- **Passage par référence** : LangGraph reçoit des `rule_id`, `supplier_id`, `article_ref` et `invoice_id`, jamais des copies de règles ou de données.
- **Traçabilité** : chaque écriture validée enregistre `rule_id`, `rule_version` et `rule_sha256`.
- **Qdrant et Neo4j ne sont jamais autoritaires** : en cas de divergence, Obsidian gagne pour les règles, Excel pour les données.

## 2. Vue d'ensemble des 9 phases

| Phase | Nom | Priorité / statut | Dépendance principale |
|---|---|---|---|
| 0 | Assainissement — doublons, circularité, hygiène Git | **En cours** | — |
| 1 | Vision / OCR — corpus 10 docs, NuExtract/LightOnOCR | **Priorité n°1** | Phase 0 |
| 2 | Rule Registry Obsidian — `knowledge/rules/` → `RuleRegistryService` → LangGraph | **Priorité n°2** | Phase 0 ; intégration après Phase 1 |
| 3 | Mémoire Métier — Qdrant (recherche), Neo4j (relations) | **Priorité n°3** | Phase 2 |
| 4 | Orchestration LangGraph — source de vérité procédurale, workflows complets | Planifiée | Phase 3 |
| 5 | SkillOpt & Distillation — MiMo → Qwen 27B → Qwen 9B | Planifiée | Phase 4 |
| 6 | DeepSeek Harness — harnais autonome spécialisé | Planifiée | Phase 5 |
| 7 | Mémoire épisodique | Planifiée | Phase 6 |
| 8 | Déploiement Oracle Cloud | Planifiée | Phase 7 |

## 3. Détail des phases

### Phase 0 — Assainissement (Doublons, circularité, hygiène Git) — En cours
**Objectif :** supprimer les doublons, casser les dépendances circulaires et assainir l'hygiène Git avant d'ajouter des briques.

**Périmètre :**
- Doublons de code, repositories et services.
- Dépendances circulaires (dont le chantier `supplier_registration`).
- Indexation Git : `langfuse_db/`, `n8n_data/`, fichiers générés et artefacts locaux.
- Working tree et règles de non-régression.

**Livrables :**
- Audit des doublons et décisions de fusion/suppression.
- Dépendances stabilisées et imports nettoyés.
- `.gitignore` à jour, index Git propre.
- Tests ciblés verts.

**Critère de sortie :** aucun doublon critique, aucune circularité, index Git sain, tests de non-régression validés.

### Phase 1 — Vision / OCR (Corpus 10 docs, NuExtract/LightOnOCR) — Priorité n°1
**Objectif :** fiabiliser la lecture documentaire avant toute automatisation métier.

**Périmètre :**
- Corpus gelé de 10 documents et manifeste associé.
- Validation humaine des orientations contestées (`facture_emaux.pdf`, `facture_transport.PDF`).
- OCR natif PyMuPDF et OCR GLM-OCR pour les scans/images.
- Extraction structurée NuExtract3 ; LightOnOCR évalué comme OCR, pas comme classifieur d'orientation.
- Vérité terrain des champs fournisseur, date, HT, TVA, TTC et SIRET.

**Livrables :**
- Corpus et annotations validés.
- Décision documentée sur NuExtract3 et LightOnOCR.
- Mesures d'exactitude sur les 10 documents.

**Critère de sortie :** précision documentée et reproductible, aucune décision fondée sur un benchmark artificiel, validation humaine traçable.

### Phase 2 — Rule Registry Obsidian (`knowledge/rules/` → `RuleRegistryService` → LangGraph) — Priorité n°2
**Objectif :** faire d'Obsidian la source unique des règles métier et brancher `RuleRegistryService` sur LangGraph.

**Périmètre :**
- `knowledge/rules/*.md` : contrats YAML + Markdown, `status: approved`, `rule_id`, version, `sha256`.
- `RuleRegistryService` : chargement, validation, versionnement, projections.
- Projections Neo4j (`(:Rule)`) et Qdrant (embeddings des clauses).
- `deterministic_actions` injectées dans les nœuds LangGraph.

**Livrables :**
- Service et schémas Pydantic.
- Règles pilotes (TVA, territorialité, conditions fournisseurs).
- Tests de chargement, de versionnement et de reconstruction.

**Critère de sortie :** aucune règle en dur dans le code ; toute règle consommée provient d'Obsidian avec `status: approved` ; projections reconstructibles.

### Phase 3 — Mémoire Métier (Qdrant pour la recherche, Neo4j pour les relations) — Priorité n°3
**Objectif :** séparer clairement la recherche sémantique (Qdrant) des relations métier (Neo4j).

**Périmètre :**
- Qdrant : embeddings `nomic-embed-text`, collections articles/règles/historique, `payload.source_id` et `source_hash`.
- Neo4j : nœuds et arêtes fournisseurs, règles, factures, bons de livraison, lettrage.
- Requêtes de résolution relationnelle et sémantique.
- Reconstruction des index depuis les sources maîtres.

**Livrables :**
- Schémas Qdrant et Neo4j.
- Requêtes de résolution et tests.
- Documentation des clés de liaison.

**Critère de sortie :** Qdrant ne porte aucun statut d'approbation ; Neo4j ne contient aucun texte intégral de règle ; les deux index sont reconstructibles.

### Phase 4 — Orchestration LangGraph (Source de vérité procédurale, workflows complets)
**Objectif :** faire de LangGraph la source de vérité procédurale et exécuter des workflows complets.

**Périmètre :**
- Graphe d'états : `Ingest → ResolveRules → Validate → Persist`.
- Nœuds d'appel à `RuleRegistryService`, Neo4j, Qdrant et Excel.
- Gestion des validations humaines (HITL) et des reprises.
- Workflows facture, tarif et fournisseur.

**Livrables :**
- Graphe et schémas d'état.
- Workflows de bout en bout.
- Tests d'intégration et de reprise.

**Critère de sortie :** zéro constante métier en dur ; l'état ne transporte que des IDs ; workflows complets et testés.

### Phase 5 — SkillOpt & Distillation (MiMo → Qwen 27B → Qwen 9B)
**Objectif :** capitaliser les corrections et transférer les compétences des modèles experts vers les modèles locaux.

**Périmètre :**
- Chaîne MiMo → Qwen 27B → Qwen 9B.
- Skills versionnés et catalogue de compétences.
- Détection des échecs, escalade et non-régression.
- Métriques d'autonomie de Qwen 9B.

**Livrables :**
- Pipeline de distillation.
- Skills approuvés et tests de non-régression.
- Tableau de bord d'autonomie.

**Critère de sortie :** Qwen 9B exécute les tâches validées ; l'escalade vers Qwen 27B/MiMo reste explicite ; aucune régression.

### Phase 6 — DeepSeek Harness (Harnais autonome spécialisé)
**Objectif :** disposer d'un harnais autonome spécialisé, contrôlé et reproductible.

**Périmètre :**
- Installation et scripts npm.
- Rôles, garde-fous et permissions.
- Un seul exécutant par worktree.
- Procédures de reprise et d'audit.

**Livrables :**
- Harnais opérationnel.
- Documentation des rôles et des limites.
- Validation en situation réelle.

**Critère de sortie :** exécution autonome contrôlée, aucun fallback silencieux, traçabilité des actions.

### Phase 7 — Mémoire épisodique
**Objectif :** conserver les trajectoires, décisions et corrections pour l'apprentissage et l'audit.

**Périmètre :**
- Épisodes d'exécution et événements.
- Liens vers `rule_id`, versions et `sha256`.
- Rétention, recherche et restitution.
- Articulation avec SkillOpt.

**Livrables :**
- Stockage et schéma des épisodes.
- API de lecture/écriture.
- Tests et politique de rétention.

**Critère de sortie :** épisodes traçables et exploitables, sans duplication des 5 SSOT.

### Phase 8 — Déploiement Oracle Cloud
**Objectif :** déployer la cible DSH-Core sur Oracle Cloud dans des conditions contrôlées.

**Périmètre :**
- Oracle Linux, runtime Python, dépendances et GPU si nécessaire.
- Services applicatifs, Neo4j, Qdrant et stockage.
- Synchronisation contrôlée PC principal → Oracle.
- Sécurité, sauvegardes, supervision et runbook.

**Livrables :**
- Infrastructure et déploiement reproductibles.
- Monitoring, sauvegardes et procédures d'incident.
- Validation de la parité avec l'environnement local.

**Critère de sortie :** déploiement contrôlé et documenté ; la source de vérité métier reste locale/Git ; aucun déplacement d'autorité vers Oracle ou Hermes.

## 4. Séquencement et gouvernance

- **Phase 0 d'abord** : ne pas empiler de nouvelles briques sur un socle non assaini.
- **Phase 1 reste la priorité n°1** : la qualité Vision/OCR conditionne toutes les phases suivantes.
- **Phases 2 et 3** : construire la connaissance (règles, relations, recherche) avant de figer les procédures.
- **Phase 4** : LangGraph assemble les briques ; il ne remplace ni Obsidian, ni Excel, ni Neo4j, ni Qdrant.
- **Phases 5 à 8** : optimisation, autonomie, mémoire épisodique puis déploiement.
- **Aucun benchmark artificiel** : les évaluations se font sur des tâches réelles et des corpus validés.
- **Une seule source d'autorité par catégorie** : toute divergence se résout par la source maître (Excel pour les données, Obsidian pour les règles).
- **Pas de commit ni de push sans demande explicite** ; préserver les changements préexistants.

## 5. Références

- `docs/PROJECT_HANDOVER.md` : point d'entrée et état exécutif.
- `docs/PROJECT_DECISIONS.md` : registre des décisions durables.
- `docs/DSH_CORE_AI_ARCHITECTURE_V2.md` : architecture cible détaillée.
- `docs/AI_MODEL_ROUTING_AND_HARNESSES.md` : matrice des modèles, rôles et routage.
