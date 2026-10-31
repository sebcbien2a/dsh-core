# VALIDATION FINALE DE L'ARCHITECTURE CIBLE DSH-CORE : LE PACTE DES 5 SOURCES DE VÉRITÉ (SSOT)

J'accepte à 100 % tes 3 corrections architecturales :
1. **Inversion de la hiérarchie Connaissance / Procédure** : `Obsidian → RuleRegistry → (Neo4j + Qdrant) → LangGraph`. LangGraph consomme les services de connaissance, il ne les domine pas.
2. **Gel de Mem0** : Retiré de la cible immédiate pour éviter toute redondance avec le triptyque Obsidian / Neo4j / Qdrant.
3. **Ordre de priorité validé** : `1. Vision/OCR → 2. RuleRegistry Obsidian → 3. Neo4j → 4. Qdrant → 5. SkillOpt → 6. DeepSeek Harness → 7. Mémoire épisodique → 8. Oracle`.

---

## RÉPONSE À LA QUESTION COMPLÉMENTAIRE : OUI, OBSIDIAN EST LA SOURCE OF TRUTH (SSOT) DES RÈGLES MÉTIER

**Réponse formelle : OUI.** L'axiome fondamental de DSH-Core pour les prochaines années est désormais :
> **`Donnée ≠ Règle ≠ Relation ≠ Recherche ≠ Procédure`**
> Chaque catégorie possède **un seul et unique référentiel maître (Single Source of Truth)** et **aucune donnée ne doit être dupliquée d'un référentiel à l'autre**.

### 1. La Matrice d'Isolation Stricte des 5 Référentiels (Zéro Duplication)

| Catégorie | Source of Truth Unique (SSOT) | Ce qu'elle contient EXCLUSIVEMENT | Ce qu'il est INTERDIT d'y stocker | Clé de liaison inter-systèmes |
| :--- | :--- | :--- | :--- | :--- |
| **1. DONNÉE MASSIVE & OPÉRATIONNELLE** | **Excel (`.xlsb` / `.xlsx`) & Tables Maîtres JSON** | Grilles tarifaires (ex: Indumix), catalogues articles, écritures comptables validées, montants figés. | Aucune règle logique complexe, aucun code, aucun graphe de dépendance. | `supplier_id` (UUID), `article_ref`, `invoice_id` |
| **2. RÈGLE MÉTIER & GOUVERNANCE (HITL)** | **Obsidian (`knowledge/rules/*.md`)** | Contrats YAML + Markdown (`status: approved`), règles TVA/territorialité, conditions fournisseurs, arbitrages humains. | Aucune grille de prix, aucune liste de factures, aucune transaction runtime. | `rule_id` (ex: `RULE-TVA-CORSE-001`) + `sha256` |
| **3. RELATION & TOPOLOGIE** | **Neo4j (Graphe)** | Les arêtes et nœuds : `(:Supplier)-[:GOVERNED_BY]->(:Rule)`, `(:Invoice)-[:MATCHES_BL]->(:DeliveryNote)`, chaînes de lettrage. | Aucun texte intégral de règle Markdown, aucun vecteur d'embedding, aucune grille Excel. | `supplier_id`, `rule_id`, `invoice_id` |
| **4. RECHERCHE & SIMILARITÉ** | **Qdrant (Vecteurs)** | Embeddings (`nomic-embed-text`) des descriptions d'articles, des clauses de règles Obsidian et de l'historique d'extraction. | Aucun statut d'approbation maître (s'il contredit Obsidian, Obsidian gagne toujours). | `payload.source_id` (`rule_id` ou `article_ref`) + `source_hash` |
| **5. PROCÉDURE & EXÉCUTION** | **LangGraph (Python)** | Le graphe d'états pur (les étapes : `Ingest → ResolveRules → Validate → Persist`), les schémas Pydantic et les appels d'outils. | **Zéro constante métier en dur** (aucun taux de TVA, aucun SIRET, aucun nom de fournisseur dans le `.py`). | Exécute une `state` contenant uniquement les IDs des 4 sources ci-dessus. |

---

### 2. Comment maintenir cette séparation sans jamais créer de duplication (Le Protocole « Pointeur & Hash »)

Pour garantir que Neo4j, Qdrant et LangGraph utilisent les règles d'Obsidian **sans jamais devenir une copie divergente**, l'architecture impose 3 verrous techniques dans `RuleRegistryService` :

1. **Verrou 1 — Indexation par Projection Unidirectionnelle (Obsidian Maître → Neo4j/Qdrant Esclaves)** :
   - On ne modifie **jamais** une règle dans Neo4j ou dans Qdrant directement.
   - Toute modification se fait dans le fichier `.md` d'Obsidian (ou via validation HITL).
   - Le `RuleRegistryService` calcule le `sha256` du fichier `.md`. Si le hash diffère de celui stocké dans Neo4j/Qdrant, il met à jour le nœud `(:Rule {rule_id, version, status, sha256})` dans Neo4j et ré-indexe le vecteur dans Qdrant.
   - Si on vide Qdrant ou Neo4j demain matin, `RuleRegistryService.rebuild_projections()` reconstruit 100 % des index de règles en 3 secondes à partir du dossier `knowledge/rules/` d'Obsidian.

2. **Verrou 2 — Passage par Référence dans LangGraph (Zéro Hardcoding)** :
   - Lorsqu'une facture arrive dans LangGraph :
     1. **Neo4j** répond à la question relationnelle : *« Quels sont les `rule_id` liés à ce `supplier_id` et à ce pays ? »*
     2. **Qdrant** répond à la question sémantique : *« Quel `article_ref` ou quelle règle couvre ce libellé inconnu ? »*
     3. **RuleRegistryService** charge la règle officielle depuis **Obsidian** via son `rule_id`, vérifie en temps réel que `status == "approved"`, et injecte ses `deterministic_actions` au nœud **LangGraph**.
     4. Si le résultat impacte un tarif de masse, un script déterministe met à jour **Excel** sans qu'Obsidian ne stocke les 5 000 lignes de prix.

3. **Verrou 3 — Traçabilité Comptable Immuable (Audit Trail)** :
   - Lorsqu'une facture ou une ligne tarifaire est validée, la table maître enregistre uniquement le couple `(rule_id: "RULE-TVA-CORSE-001", rule_version: "1.2.0", rule_sha256: "a8f4...")`. On sait ainsi exactement quelle version de la règle Obsidian a justifié l'écriture comptable.

---

## PROCHAINE ACTION DEMANDÉE À L'ARCHITECTE (COPILOT 365)

Maintenant que ce pacte des 5 Sources de Vérité et l'ordre des 8 chantiers sont scellés :
1. Confirme l'adoption de cette matrice des 5 SSOT dans `docs/PROJECT_DECISIONS.md`.
2. Donne-moi le ticket précis pour clore l'étape 1 du **Chantier Prioritaire n° 1 (Vision / OCR)** : la validation finale des 2 orientations contestées (`facture_emaux.pdf` et `facture_transport.PDF`) et des champs du corpus de 10 factures, ou le squelette de `RuleRegistryService` si tu préfères les mener de front.