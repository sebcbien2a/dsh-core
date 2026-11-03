---
master_doc: PROJECT_HANDOVER.md
branch_domain: P14/P15/P16 — Machine à états OneDrive / Gmail, Couche Obsidian Vault & production
contract_version: 1.3.0
status: FROZEN
upstream_dependencies: [docs/PROJECT_HANDOVER.md, docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md, services/document_pipeline.py, services/sqlite_accounting_store.py]
downstream_consumers: [schemas/folder_state_schema.py (P14.1), services/folder_tree_service.py (P14.1), services/acompleter_workbook_service.py (P14.2), services/folder_sync_service.py (P14.3), services/inbox_ingestion_service.py (P14.4), services/document_flow_watcher.py (P14.5 / P15.3), schemas/obsidian_vault_schema.py (P15.1), services/obsidian_vault_service.py (P15.1), services/vault_accounting_bridge.py (P15.1), schemas/declarative_task_schema.py (P15.2), services/declarative_tarif_service.py (P15.2), services/declarative_pdf_service.py (P15.2), services/instructions_task_engine.py (P15.2), services/accounting_spreadsheet_export_service.py (P16.1/P16.2)]
verification_tests: [tests/test_folder_state_model_exhaustive.py (P14.1), tests/test_acompleter_workbook_exhaustive.py (P14.2), tests/test_folder_sync_service_exhaustive.py (P14.3), tests/test_inbox_ingestion_exhaustive.py (P14.4), tests/test_obsidian_vault_service_exhaustive.py (P15.1), tests/test_vault_accounting_bridge_exhaustive.py (P15.1), tests/test_declarative_workflows_exhaustive.py (P15.2), tests/test_document_flow_watcher_exhaustive.py (P14.5/P15.3), tests/test_category_summary_export_exhaustive.py (P16.1), tests/test_supplier_ledger_export_exhaustive.py (P16.2)]
---

# P14 / P15 / P16 — Spécification formelle : Machine à états OneDrive / Gmail, Couche Obsidian Vault & chaîne de production

> Document de cadrage **clos** : il fige l'architecture finale **P14.1→P14.5** (dossiers `00_` à `05_`, 8 catégories, `_A_COMPLETER.xlsx`, synchronisation bidirectionnelle, ingestion `00_INBOX`, rafraîchissement automatique des exports), **P15.1→P15.3** (Vault Obsidian, workflows `instructions.md`, watcher d'arrière-plan) et **P16.1→P16.2** (synthèses par catégorie, grand livre fournisseur).

## 0. Principes non négociables

1. **Non-régression** : base `pytest tests -q` = 3 207 tests passés hors-ligne, 2 conditionnés, 0 échec. Chaque sous-phase P14.x / P15.x / P16.x ajoute ses tests sans modifier les tests existants.
2. **Hors-ligne** : P14 (et P15.2) ne passent aucun appel réseau ; toute dépendance externe est importée en lazy (openpyxl / pandas déjà en usage dans le dépôt).
3. **Non-destructif** : jamais de suppression ni d'écrasement d'une donnée humaine (ligne `_A_COMPLETER.xlsx` complétée, fichier déplacé manuellement).
4. **Déterministe & idempotent** : mêmes entrées → mêmes sorties ; clé d'unicité = **hash SHA-256** du document (aligné sur la déduplication P13.2).
5. **Contrats gelés** : perception v1.0.1 et persistance v1.0.2 inchangés ; toute évolution = phase + migration + incrément de version.
6. **Python** : `A:\dsh-core\.venv-v3\Scripts\python.exe`.

## 1. P14 — Machine à états documentaire

### 1.1 P14.1 — Architecture des dossiers (états `00_` à `05_`)

| Dossier | Rôle |
|---|---|
| `00_INBOX` | Réception brute (pièces jointes Gmail entrantes ou dépôt manuel OneDrive) |
| `01_EN_COURS` | Document pris en charge par le pipeline d'extraction / OCR (`services/document_pipeline.py`) |
| `02_A_COMPLETER` | Confiance insuffisante, donnée manquante ou arbitrage requis → génère/met à jour une ligne dans `_A_COMPLETER.xlsx` |
| `03_VALIDE_PRET_FEC` | Document complet, réconcilié et validé (humainement ou auto), prêt pour l'injection dans `services/sqlite_accounting_store.py` |
| `04_EXPORT_COMPTA` | Écritures générées et figées dans le FEC / CSV |
| `05_ARCHIVE_ERREUR` | Doublons SHA-256, documents hors-périmètre ou fichiers corrompus ; rejets manuels (`REJET_MANUEL/`) |

#### Machine à états — transitions normatives

| De | Vers | Déclencheur |
|---|---|---|
| `00_INBOX` | `01_EN_COURS` | Prise en charge par le pipeline |
| `00_INBOX` | `05_ARCHIVE_ERREUR` | Doublon SHA-256 déjà connu |
| `01_EN_COURS` | `03_VALIDE_PRET_FEC` | Extraction complète, confiance suffisante, réconciliation OK |
| `01_EN_COURS` | `02_A_COMPLETER` | Confiance insuffisante, donnée manquante ou arbitrage requis |
| `01_EN_COURS` | `05_ARCHIVE_ERREUR` | Fichier corrompu ou hors-périmètre |
| `02_A_COMPLETER` | `03_VALIDE_PRET_FEC` | Statut `VALIDE` dans `_A_COMPLETER.xlsx` **ou** déplacement manuel du fichier vers `03_` |
| `02_A_COMPLETER` | `05_ARCHIVE_ERREUR` | Statut `REJETE` dans `_A_COMPLETER.xlsx` → réacheminement vers `05_ARCHIVE_ERREUR/REJET_MANUEL/` |
| `03_VALIDE_PRET_FEC` | `04_EXPORT_COMPTA` | Écritures FEC / CSV générées et figées |
| `03_VALIDE_PRET_FEC` | `05_ARCHIVE_ERREUR` | Statut `REJETE` (annule et remplace : facture déjà validée puis annulée / remplacée) |

Toute transition est journalisée avec `(sha256, état_source, état_cible, horodatage, origine)`.

### 1.2 Les 8 catégories métiers

Sous-dossiers ou préfixes normalisés **dans `03_` et `04_`** uniquement (chemin canonique : `03_VALIDE_PRET_FEC/<CATEGORIE>/` et `04_EXPORT_COMPTA/<CATEGORIE>/`) :

| # | Catégorie | Périmètre |
|---|---|---|
| 1 | `ACHATS_MARCHANDISES` | Matières premières, peintures, vernis, fournitures |
| 2 | `FRAIS_GENERAUX` | Loyers, énergie, télécom, honoraires, assurances |
| 3 | `TRANSPORTS_LOGISTIQUE` | Factures transporteurs, douanes, fret |
| 4 | `IMMOBILISATIONS` | Matériel industriel, informatique, véhicules |
| 5 | `BANQUE_FINANCE` | Relevés bancaires, agios, frais financiers |
| 6 | `FISCAL_SOCIAL` | TVA, taxes, charges sociales, salaires |
| 7 | `VENTES_CLIENTS` | Factures clients émises, avoirs clients |
| 8 | `DIVERS_A_CLASSER` | Catégorie tampon avant arbitrage |

`DIVERS_A_CLASSER` est la valeur par défaut de la **catégorie suggérée** quand le pipeline ne peut pas trancher ; l'humain tranche dans `_A_COMPLETER.xlsx`.

### 1.3 Modèle de données (livrable P14.1 — `schemas/folder_state_schema.py`)

- `FolderState` (enum) : les six noms de dossiers comme valeurs stables — `00_INBOX`, `01_EN_COURS`, `02_A_COMPLETER`, `03_VALIDE_PRET_FEC`, `04_EXPORT_COMPTA`, `05_ARCHIVE_ERREUR`.
- `DocumentCategory` (enum) : les 8 valeurs de la section 1.2.
- `DocumentStateRecord` : `sha256` (clé d'unicité), `nom_fichier`, `state`, `category`, `champs_manquants` (`MissingFieldEnum`, liste fermée : `date_piece`, `numero_piece`, `nom_fournisseur`, `montant_ht`, `montant_tva`, `montant_ttc`, `categorie`, `taux_tva`), `categorie_suggeree`, `statut` (`EN_ATTENTE`, `VALIDE` ou `REJETE`), `source` (`GMAIL_ATTACHMENT`, `ONEDRIVE_DROP` ou `MANUAL`), `horodatage`.
- `services/folder_tree_service.py` : `ensure_tree(tenant_id, root=None)` crée / valide l'arborescence `<root>/<tenant_id>/00_INBOX` à `05_ARCHIVE_ERREUR` (avec `<CATEGORIE>/` sous `03_` et `04_`, plus `05_ARCHIVE_ERREUR/REJET_MANUEL/`) ; `<root>` configurable via la variable d'environnement `DSH_DOCUMENT_FLOW_ROOT` (défaut `workspace/document_flow`) ; refuse les états ou catégories inconnus et toute traversée de répertoire (`..`, chemins absolus) — même protection que `RuleRegistryService` (P10.3).

### 1.4 P14.2 — Gestionnaire `_A_COMPLETER.xlsx` anti-conflit (`services/acompleter_workbook_service.py`)

**Colonnes normatives** (ordre figé) :

| Colonne 1 | Colonne 2 | Colonne 3 | Colonne 4 | Colonne 5 | Colonne 6 |
|---|---|---|---|---|---|
| `ID document` | `Hash SHA-256` | `Nom fichier` | `Champs manquants` | `Catégorie suggérée` | `Statut` |

- `ID document` : identifiant déterministe dérivé du `sha256` (aligné sur la dérivation d'identifiants par hash du contrat de persistance v1.0.2).
- `Statut` : `EN_ATTENTE` (défaut à l'écriture pipeline), `VALIDE` (déclencheur Humain → Pipeline) ou `REJETE` (routage automatique vers `05_ARCHIVE_ERREUR/REJET_MANUEL/` — décision 3).

**Règles d'écriture (sens Pipeline → Humain)** :

1. Ajout **non-destructif** d'une ligne à chaque document tombant en `02_A_COMPLETER` : ID document, hash SHA-256, nom fichier, champs manquants, catégorie suggérée, statut `EN_ATTENTE`.
2. **Idempotence par hash** : si une ligne existe déjà pour ce `Hash SHA-256`, la ligne n'est modifiée que si `Statut = EN_ATTENTE` (mise à jour des champs manquants / catégorie suggérée) ; si `Statut = VALIDE` (ou toute autre valeur posée par l'humain), **aucune modification**.
3. Jamais de suppression de ligne, jamais de réécriture globale du classeur.

**Sécurité OneDrive / Excel** :

1. Avant toute écriture ou lecture, vérifier l'absence de fichier de verrouillage Excel (`~$*.xlsx`) dans le dossier du classeur ; si présent → aucune écriture, **exception contrôlée** `WorkbookLockedError` (code `WORKBOOK_LOCKED`) ; report et nouvelle tentative à la passe suivante (gérés par P14.3).
2. Écriture **atomique** : sauvegarde dans un fichier temporaire puis remplacement (`os.replace`) — un crash ou une synchronisation OneDrive concurrente ne laisse qu'une version intacte.
3. Le classeur `_A_COMPLETER.xlsx` est le **seul** canal de saisie humaine structurée ; pas d'API d'écriture concurrente.
4. Issues de `upsert` (clé primaire stricte = `Hash SHA-256`) : `created` (ligne ajoutée, `Statut = EN_ATTENTE`), `updated` (ligne `EN_ATTENTE` rafraîchie — `Champs manquants` / `Catégorie suggérée` uniquement), `unchanged` (aucun changement nécessaire), `preserved` (statut humain, ligne strictement intacte, fichier non réécrit).

### 1.5 P14.3 — Service de synchronisation bidirectionnelle (`services/folder_sync_service.py`)

**Sens Pipeline → Humain** : document en `02_A_COMPLETER` → ajout / mise à jour d'une ligne `_A_COMPLETER.xlsx` (règles section 1.4).

**Sens Humain → Pipeline** :

- Détection du changement (décision 4) : `mtime` + SHA-256 du classeur dès disparition du verrou `~$*.xlsx` ; déclencheurs : `Statut = VALIDE` dans `_A_COMPLETER.xlsx`, `Statut = REJETE` (→ `05_ARCHIVE_ERREUR/REJET_MANUEL/`) ou fichier déplacé manuellement vers `03_VALIDE_PRET_FEC/`.
- Actions atomiques : (1) mise à jour du store SQLite via `SqliteAccountingStore` (contrats `FecEntryStore` / `ArbitrationStore`, `services/sqlite_accounting_store.py`) ; (2) déplacement automatique du PDF de `02_A_COMPLETER/` vers `03_VALIDE_PRET_FEC/<CATEGORIE>/` (catégorie complétée par l'humain, sinon catégorie suggérée).
- Un déplacement manuel vers `03_` est prioritaire et ne doit jamais être annulé par le service : l'état et la catégorie (du dossier de destination) sont synchronisés vers `SqliteAccountingStore` et le `Statut` de la ligne est aligné sur `VALIDE` (`align_statut`, seule écriture pipeline de cette colonne).
- Traitement `VALIDE` : la catégorie doit être valide et `categorie` ne doit plus figurer dans `Champs manquants` ; alors la ligne est persistée (`SqliteAccountingStore.upsert_document_line`) puis le fichier est déplacé atomiquement vers `03_VALIDE_PRET_FEC/<CATEGORIE>/` (sinon : `skipped_invalid`, fichier laissé en `02_`).
- Traitement `REJETE` : déplacement atomique vers `05_ARCHIVE_ERREUR/REJET_MANUEL/` et traçage de l'état dans `SqliteAccountingStore` (`status = REJETE` via `deactivate_rejected_document`).
- **Annule et remplace** (`03_VALIDE_PRET_FEC` → `05_ARCHIVE_ERREUR`) : si une ligne déjà validée passe à `REJETE` dans `_A_COMPLETER.xlsx`, le PDF est déplacé atomiquement de `03_VALIDE_PRET_FEC/<CATEGORIE>/` vers `05_ARCHIVE_ERREUR/REJET_MANUEL/`, la ligne store est forcée à `REJETE` (`SqliteAccountingStore.deactivate_rejected_document` — exception explicite à la règle `preserved`) et la pièce sort du pool de rapprochement (`list_lines("ALL")` exclut `REJETE`) et des exports CSV. Une pièce `REJETE` ne crée jamais d'écriture FEC.
- L'humain peut modifier `Statut` d'une ligne `VALIDE` vers `REJETE` dans Excel ; `AcompleterWorkbook.align_statut` accepte toutes les transitions de statut.

**Rejets / erreurs** : doublons SHA-256 (dans `02_` ou déjà présents sous `03_`), documents hors-périmètre, fichiers illisibles → `05_ARCHIVE_ERREUR/` (jamais supprimés, noms unifiés en cas de collision).

**Points d'entrée du service** (figés en P14.3) :

- `FolderSyncService(tenant_id, store, root=None)` — bind à `<root>/<tenant_id>/`, store = `SqliteAccountingStore`.
- `sync_pipeline_to_human(records=()) -> SyncReport` — toute ligne `02_` sans ligne Excel est créée (`rows_written`) ; les records explicites suivent les règles non destructives P14.2 (`rows_updated`).
- `sync_human_to_pipeline() -> SyncReport` — détection `mtime` + SHA-256 du classeur (skip si inchangé), priorité au déplacement manuel, `VALIDE` / `REJETE`, doublons et erreurs.
- `SyncReport` : `{skipped, deferred_lock, rows_written, rows_updated, moved_to_03, archived, manual_moves, skipped_invalid, traces}` — état observable pour les tests et l'UI future.

**Tests hors-ligne** : `tests/test_folder_sync_service_exhaustive.py` — transitions complètes, idempotence, verrou `~$`, déplacement manuel prioritaire, intégration SQLite sur base temporaire, annule et remplace (`03_`→`05_` + désactivation store + exclusion du pool / exports CSV).

### 1.6 P14.4 — Ingestion automatique `00_INBOX` (`services/inbox_ingestion_service.py`) — **IMPLÉMENTÉ**

- **Objectif** : automatiser la prise en charge des fichiers déposés en `00_INBOX` sans intervention humaine.
- **Déroulé** : parcours du tenant, calcul du SHA-256, rejet des doublons déjà connus de `SqliteAccountingStore` ou présents sous `05_ARCHIVE_ERREUR` (déplacement tracé, jamais de suppression), déplacement atomique vers `01_EN_COURS`, extraction réelle via `PdfInvoiceProducer` / `services/document_pipeline.py` (texte natif déterministe, repli vision locale lazy pour les scans).
- **Routage conditionnel** : champs complets + confiance ≥ 0,75 → `03_VALIDE_PRET_FEC/<CATEGORIE>/` + ligne `VALIDE` dans le store ; champs manquants, catégorie `DIVERS_A_CLASSER` ou confiance faible → `02_A_COMPLETER/` + ligne `EN_ATTENTE` et upsert non destructif `_A_COMPLETER.xlsx` ; fichier illisible / corrompu / extraction en échec → `05_ARCHIVE_ERREUR/`.
- **API** : `process_inbox(paths=None)` (scan complet ou sous-ensemble stabilisé fourni par le watcher) ; rapport `InboxIngestionReport` (`scanned`, `duplicates`, `moved_to_03`, `moved_to_02`, `archived`, `rows_written`, `rows_updated`, `deferred_lock`, `vault_events`, `documents`, `traces`).
- **Tests** : `tests/test_inbox_ingestion_exhaustive.py` (31 tests).

### 1.7 P14.5 — Rafraîchissement automatique de production (`services/document_flow_watcher.py`) — **IMPLÉMENTÉ**

- **Option** : `DocumentFlowWatcher(..., auto_export=True)` (défaut) et flag CLI `--no-auto-export` dans `scripts/run_document_watcher.py`.
- **Déclencheur** : uniquement lorsqu'un cycle `run_once()` a effectivement validé, déplacé ou rejeté au moins une pièce (`moved_to_03`, `moved_to_02`, `archived`, `manual_moves`).
- **Effets** : régénération des classeurs `CategorySummaryExporter` pour les catégories impactées et du `SupplierLedgerExporter` sous `workspace/exports/accounting/<tenant>/`, puis écriture/mise à jour de `01-contexte/cockpit_compta.md` (compteur `02_A_COMPLETER`, dernières pièces validées par catégorie, alertes `residual_delta != 0` via `collect_supplier_diagnostics`).
- **Robustesse** : toute erreur d'export est capturée dans `WatcherCycleReport.export_errors` et tracée (`AUTO_EXPORT_ERROR:*`) sans interrompre le flux documentaire ; les exports partiels restent disponibles.
- **Tests** : `tests/test_document_flow_watcher_exhaustive.py` (15 tests, dont 5 dédiés à l'auto-export et au cockpit).

## 2. P15 — Couche Obsidian Vault & workflows déclaratifs — **IMPLÉMENTÉE**

### 2.1 P15.1 — Bridge Obsidian Vault (`schemas/obsidian_vault_schema.py` / `services/obsidian_vault_service.py`) — **IMPLÉMENTÉ**

- **Objectif** : synchronisation en **Markdown lisible par l'humain** des faits (Mem0 — `memory_manager.py`, collection `mem0_memories`), entités (Neo4j **via le contrat de persistance v1.0.2** ; le legacy `services/neo4j_service.py` reste non branché) et index (Qdrant — payload `InvoiceVectorPayloadV1`) vers le dossier Vault **synchronisé OneDrive**.
- **Racine & arborescence** : racine configurable par la variable d'environnement `OBSIDIAN_VAULT_PATH` (défaut `workspace/vault`) ; `ensure_vault_tree()` crée `_system/`, `01-contexte/`, `02-projets/`, `03-rag/`, `04-graph/entities/`, `05-memoire/facts/`, `05-memoire/events/`, `06-journal/` et écrit `_system/schema.yaml` **uniquement s'il est absent** (jamais d'écrasement).
- **Contrat `_system/schema.yaml`** (modélisé par `VaultSchemaModel`) : types de notes (`fact`, `decision`, `entity`, `rag-chunk`), règles de routage (`fact -> 05-memoire/facts/{entity}/{predicate}.md`, `decision -> 02-projets/{project}/decisions.md`, `entity -> 04-graph/entities/{entity}.md`, `rag-chunk -> 03-rag/`, `context -> 01-contexte/active.md`) et contraintes : `max_hot_context: 2000`, `max_warm_context: 4000`, `one_fact_one_file: true`, `wikilinks_required: true`.
- **`vault_writer` (`VaultWriter`)** :
  - **Faits atomiques** : écriture dans `05-memoire/facts/{entity}/{predicate}.md` (règle `one_fact_one_file` : une note = un fait = un couple `(entity, predicate)`), frontmatter YAML complet (`id`, `type`, `entity`, `predicate`, `value`, `source`, `status`, `locked`, `wikilinks`, `created_at`, `updated_at`, `content_hash`) et corps portant les `[[wikilinks]]`.
  - **Entités** : mise à jour de `04-graph/entities/{entity}.md` avec fusion des relations **sans duplication** (clé `(predicate, target)`, ordre de première apparition conservé).
  - **Décisions & contexte actif** : ajout dans `02-projets/{project}/decisions.md` (identifiant `D-XXXXXXXX` dérivé par hash de `(project, decision_text)` → idempotence) et rafraîchissement de `01-contexte/active.md` (projet en cours, 5 dernières décisions — les plus récentes d'abord — et prochaine action).
  - **Couche de revue humaine** : toute note dont le frontmatter porte `source: human`, `locked: true` ou `status: preserved`, **ou** dont le contenu a divergé de la version générée (`content_hash` mémisé ≠ hash recalculé, y compris note sans frontmatter ou sans `content_hash`), n'est **jamais écrasée** : le résultat est `status: preserved` (motifs stables `human_source`, `locked`, `preserved_status`, `content_diverged`, `missing_content_hash`, `unmanaged_note`) et le fichier reste **octet à octet identique**. Issues d'écriture alignées sur P14.2 : `created` / `updated` / `unchanged` / `preserved`. Écritures atomiques (`tmp` puis `os.replace`, compatible OneDrive).
- **`vault_reader` (`VaultReader`)** : assemblage du contexte par priorité `hot -> warm -> rag` (`ContextBundle`, méthode `render()`) :
  - `hot` : `01-contexte/active.md`, plafonné à `max_hot_context` (2 000 tokens, estimation déterministe `ceil(len/4)`, troncature en frontière de ligne) ;
  - `warm` : `02-projets/{project}/*.md`, plafonné à `max_warm_context` (4 000 tokens) ;
  - `rag` : recherche vectorielle via **interface injectable** (`VectorSearchClient.search(query, limit)`), vide hors-ligne.
  - Lecture seule stricte ; les budgets sont lus dans `_system/schema.yaml`.
- **`rag_sync` & `chunk_by_heading`** : découpage sémantique des fichiers Markdown de `03-rag/` **par titres H2/H3** (préambule H1 conservé comme chunk `heading_level=0`), sous-découpage des sections longues en fenêtres `max_chars=2000` avec recouvrement `overlap=0.15` ; **idempotence par hash SHA-256** du fichier (skip intégral si non modifié, index `_system/rag_sync_index.yaml`, écriture atomique) ; upsert des chunks dans un **client Qdrant injectable/mockable** (`VectorStoreClient.upsert/delete`, payload `{source, heading, heading_level, index, text, sha256}`), ids de chunks dérivés par hash (`uuid5(NAMESPACE_URL, "rag:<source>:<index>:<sha256>"`) et purge des ids périmés à chaque re-synchronisation.
- **Tests** : `tests/test_obsidian_vault_service_exhaustive.py` (162 tests hors-ligne) — arborescence et `schema.yaml`, écritures `vault_writer`, non-régression sur notes humaines (source / lock / divergence, octets stables), budgets de tokens `vault_reader`, chunking H2/H3 et `rag_sync` avec Qdrant mocké.
- **Câblage comptable P14/P16 (`services/vault_accounting_bridge.py`)** : `VaultAccountingBridge` / `VaultValidationRecord` / `VaultBridgeReport` branchés sur `InboxIngestionService` (validation directe `03_`) et `FolderSyncService` (`VALIDE` Excel + déplacement manuel) ; chaque pièce validée enrichit `04-graph/entities/{fournisseur}.md` (relations `achat` → `[[piece-...]]`, résumé dernière date/montant/catégorie, slugs déterministes) et écrit `05-memoire/events/validation-{document_id}.md` via `VaultWriter.write_event` ; échec Vault non bloquant (`written` / `partial` / `failed`, trace `VAULT_ERROR`), la ligne SQLite et le déplacement vers `03_VALIDE_PRET_FEC` restent intacts. Couverture : `tests/test_vault_accounting_bridge_exhaustive.py` (22 tests).

### 2.2 P15.2 — Moteur de tâches déclaratives par dossier (`instructions.md`) — **IMPLÉMENTÉ**

- **Entrée** : un dossier projet contenant `instructions.md`, les entrées (`original.pdf`, `original-annote.pdf`, `template-reference.pdf`, `tarifs-existants.xlsx`, `fournisseurs/*.pdf`, `assets/`) et les sorties (`output/`, `log/`). Toute résolution de chemin passe par `ensure_contained` (refus de `..`, des chemins absolus hors dossier et des liens sortants, code stable `PATH_TRAVERSAL`).
- **Schéma & parseur (`schemas/declarative_task_schema.py`)** : `TaskFolderLayout`, `ParsedInstructions`, `ContentModification`, `MergeRules`, `PriceRules`, `TaskConstraints`, `WorkflowType` (`PDF_PRESENTATION` / `TARIFS_EXCEL`) et `InstructionSection`. Parsing tolérant du frontmatter YAML (`version`, `cible`, `options`) et des sections `Source / Fichiers`, `Modifications de contenu`, `Règles de fusion`, `Règles de prix`, `Contraintes` (accents, alias FR/EN, titres numérotés, listes à puces ou numérotées). Détection déterministe du workflow : frontmatter explicite prioritaire, puis fichiers présents et marqueurs de contenu ; sections inconnues conservées dans `extra_sections`, étapes inconnues converties en avertissements (`modification_ignoree:*`) sauf mode `strict`.
- **Workflow 1 — fusion non destructive de tarifs (`services/declarative_tarif_service.py`)** :
  - garantie absolue de non-suppression : chaque ligne existante est réécrite dans `output/tarifs-mis-a-jour.xlsx` ; une ligne absente des tarifs fournisseurs conserve ses valeurs avec `statut = "inchangé"` ;
  - clé de rapprochement = `code` article (insensible à la casse et aux espaces) : code absent → ajout `statut = "nouveau"` + `date_ajout` ; prix différent → `statut = "modifié"` + `ancien_prix` conservé ; prix identique → `statut = "inchangé"` ;
  - déduction bidirectionnelle `prix_kg` ↔ `prix_conditionnement` via `poids` (arrondi financier half-up à 2 décimales) et `alerte = "prix manquant"` si aucun prix ne peut être établi ;
  - extraction déterministe des PDF fournisseurs (lignes `|`, tabulations, `;`, colonnes espacées et paires `clé=valeur`), dédoublonnage par code avec fusion des champs complémentaires ;
  - réparation Excel : défusion de toutes les cellules de données (un merge strictement contenu dans la ligne d'en-tête peut rester), bordures fines sur les seules cellules non vides, bordure double sous l'en-tête, codes article forcés en texte, onglet `Résumé` inséré en première position ;
  - écriture atomique (`tmp` puis `os.replace`) et journal d'audit Markdown `log/{YYYY-MM-DD}.md` (totaux, avertissements, détail des lignes, rappel explicite de la garantie de non-suppression).
- **Workflow 2 — refonte PDF / PLV (`services/declarative_pdf_service.py`)** : extraction texte + blocs via PyMuPDF, application séquentielle et déterministe des suppressions / remplacements / ajouts (`avant`, `après`, `début`, `fin`), génération d'un gabarit HTML/CSS autonome (palette, colonnes 1→4, logo `assets/`, titre, format papier) écrit dans `output/resultat.html`, puis rendu `output/resultat.pdf`. Le renderer est injectable (objet `.render(presentation, path)` ou callable `(html, path)`) ; la chaîne par défaut WeasyPrint → ReportLab → PyMuPDF absorbe proprement l'absence ou la panne de WeasyPrint/GTK sous Windows. Le workflow PDF journalise aussi `log/{YYYY-MM-DD}.md`.
- **Orchestrateur (`services/instructions_task_engine.py`)** : `InstructionsTaskEngine` / `run_instructions_task(root)` chargent le dossier, parsent `instructions.md` une fois et dispatchent vers le service de tarifs ou de PDF.
- **Tests** : `tests/test_declarative_workflows_exhaustive.py` (58 tests hors-ligne) — anti-traversée et layout, parseur/détection, garantie absolue de non-suppression, `ancien_prix`, déductions `prix_kg` ↔ conditionnement, défusion des données, formatage et log d'audit, workflow PDF de bout en bout (extraction, opérations, template, ReportLab natif, fallback WeasyPrint → ReportLab, injection de renderer, PyMuPDF).

### 2.3 P15.3 — Watcher d'arrière-plan du flux documentaire (`services/document_flow_watcher.py` / `scripts/run_document_watcher.py`) — **IMPLÉMENTÉ**

- **Surveillance** : `DocumentFlowWatcher` scrute en continu `00_INBOX` et le classeur humain ; ingestion uniquement des fichiers dont taille + `mtime` sont stables pendant `settle_delay` (évite les lectures partielles OneDrive / scans en cours) via `InboxIngestionService.process_inbox(paths)`.
- **Cycle** : chaque `run_once()` enchaîne ingestion stabilisée puis `FolderSyncService.sync_human_to_pipeline()` après contrôle du verrou `~$*.xlsx` (sync différée et cycle non bloquant si Excel est ouvert).
- **Boucle & verrou** : `run_forever()` avec `poll_interval`, arrêt propre `KeyboardInterrupt` / `SIGINT` / `SIGTERM`, verrou mono-instance `.watcher.lock` par tenant (`WatcherAlreadyRunningError`).
- **CLI** : `scripts/run_document_watcher.py --tenant default --interval 5 [--settle-delay 2] [--once] [--no-auto-export]`, logs concis (détection, routage `02_`/`03_`/`05_`, synchronisation Excel, exports).
- **Tests** : `tests/test_document_flow_watcher_exhaustive.py` (15 tests, dont stabilisation, verrou, boucle et auto-export / cockpit).

## 3. P16 — Exports analytiques comptables — **IMPLÉMENTÉE**

### 3.1 P16.1 — Synthèse par catégorie (`CategorySummaryExporter.export_category_summary`)

- **Source** : `SqliteAccountingStore.list_category_lines` (catégorie explicite ou inférée du segment `<ETAT>/<CATEGORIE>/` de `invoice_url`, isolation tenant) ; exclusion stricte des pièces `REJETE`.
- **Sortie** : classeur `openpyxl` normé (Date pièce / Tiers / N° Pièce / Réf externe / HT / TVA / TTC / Statut lettrage-rapprochement), filtres, volets figés, bordures, largeurs auto-ajustées, ligne `TOTAL` en formules `=SUM`, aucune cellule de données fusionnée, neutralisation des formules injectées, écriture atomique sous `workspace/exports/accounting/<tenant>/`.
- **Tests** : `tests/test_category_summary_export_exhaustive.py` (48 tests).

### 3.2 P16.2 — Grand livre auxiliaire par fournisseur (`SupplierLedgerExporter.export_supplier_ledger`)

- **Source** : `SqliteAccountingStore.list_supplier_ledger(tenant_id, supplier=None)` ; exclusion stricte des pièces `REJETE`, des documents non validés et des dépenses personnelles.
- **Sortie** : un onglet par tiers (ou un tiers filtré), colonnes Date / Type (FACTURE, AVOIR, PAIEMENT) / N° Pièce-Réf / Libellé / Débit / Crédit / Solde Cumulé / Code Lettrage-Statut ; numéro de pièce fournisseur extrait du libellé ou des métadonnées (repli UUID tronqué uniquement en l'absence de numéro), formules de solde progressif, totaux Débit/Crédit/Solde final, points d'équilibre, diagnostic combinatoire `Factures - Avoirs = Paiement` (jusqu'à 30 pièces) et `residual_delta` ; écriture atomique sous `workspace/exports/accounting/<tenant>/`.
- **Diagnostics partagés** : `collect_supplier_diagnostics(store, tenant_id)` expose les mêmes calculs au cockpit Obsidian (`residual_delta != 0`).
- **Tests** : `tests/test_supplier_ledger_export_exhaustive.py` (25 tests).

## 4. Découpage atomique P14 / P15 / P16 (ordre strict d'implémentation)

| Sous-phase | Livrables | Tests |
|---|---|---|
| **P14.1 — Modèle de données + arborescence** | `schemas/folder_state_schema.py` (enums, `DocumentStateRecord`, liste fermée des champs manquants) ; `services/folder_tree_service.py` (`ensure_tree`, validation états / catégories, anti-traversée) | `tests/test_folder_state_model_exhaustive.py` |
| **P14.2 — Gestionnaire `_A_COMPLETER.xlsx` anti-conflit** | `services/acompleter_workbook_service.py` (ajout non-destructif, idempotence par hash, détection `~$*.xlsx`, écriture atomique `os.replace`) | `tests/test_acompleter_workbook_exhaustive.py` |
| **P14.3 — Synchronisation bidirectionnelle** | `services/folder_sync_service.py` (Pipeline → Humain, Humain → Pipeline avec mise à jour `SqliteAccountingStore`, déplacement `02_` → `03_VALIDE_PRET_FEC/<CATEGORIE>/`, annule et remplace `03_` → `05_/REJET_MANUEL/` + désactivation store, archivage `05_`) | `tests/test_folder_sync_service_exhaustive.py` |
| **P14.4 — Ingestion automatique `00_INBOX`** | `services/inbox_ingestion_service.py` (SHA-256, doublons, `00_`→`01_` atomique, extraction réelle, routage `03_`/`02_`+Excel/`05_`, `process_inbox(paths)`) ; `scripts/run_e2e_p14_p16.py` | `tests/test_inbox_ingestion_exhaustive.py` |
| **P14.5 — Rafraîchissement automatique** | `services/document_flow_watcher.py` (`auto_export`, `CategorySummaryExporter`, `SupplierLedgerExporter`, cockpit `01-contexte/cockpit_compta.md`) ; `scripts/run_document_watcher.py` | `tests/test_document_flow_watcher_exhaustive.py` |
| **P15.1 — Bridge Obsidian Vault** | `schemas/obsidian_vault_schema.py` (types, routage, contraintes, arborescence `OBSIDIAN_VAULT_PATH`) ; `services/obsidian_vault_service.py` (`ensure_vault_tree`, `VaultWriter`, `VaultReader`, `chunk_by_heading`, `rag_sync`) ; `services/vault_accounting_bridge.py` (validation comptable non bloquante) | `tests/test_obsidian_vault_service_exhaustive.py`, `tests/test_vault_accounting_bridge_exhaustive.py` |
| **P15.2 — Moteur `instructions.md`** | `schemas/declarative_task_schema.py` (dossier projet, parseur tolérant, sections, détection de workflow, `ensure_contained`) ; `services/declarative_tarif_service.py` (fusion non destructive, déductions de prix, réparation openpyxl, sortie atomique + audit) ; `services/declarative_pdf_service.py` (extraction PyMuPDF, opérations déclaratives, gabarit HTML/CSS, fallback WeasyPrint → ReportLab → PyMuPDF) ; `services/instructions_task_engine.py` (dispatch) | `tests/test_declarative_workflows_exhaustive.py` |
| **P15.3 — Watcher d'arrière-plan** | `services/document_flow_watcher.py` (`run_once`, `run_forever`, stabilisation taille/mtime, verrou `.watcher.lock`, auto-export non bloquant) ; `scripts/run_document_watcher.py` | `tests/test_document_flow_watcher_exhaustive.py` |
| **P16.1 — Synthèse par catégorie** | `services/accounting_spreadsheet_export_service.py` (`CategorySummaryExporter`) ; migration `category` / `amount_*` dans `services/sqlite_accounting_store.py` | `tests/test_category_summary_export_exhaustive.py` |
| **P16.2 — Grand livre fournisseur** | `services/accounting_spreadsheet_export_service.py` (`SupplierLedgerExporter`, `collect_supplier_diagnostics`) ; migration `document_type` + index fournisseur dans `services/sqlite_accounting_store.py` | `tests/test_supplier_ledger_export_exhaustive.py` |

Chaque sous-phase se clôture par `.\.venv-v3\Scripts\python.exe -m pytest tests -q` = suite existante + nouveaux tests de la sous-phase, 0 échec (baseline courante : **3 207 tests passés**, 2 conditionnés).

## 5. Décisions arrêtées (arbitrages officiels — §5 clos)

1. **Racine & tenant** : racine configurable par la variable d'environnement `DSH_DOCUMENT_FLOW_ROOT` (défaut `workspace/document_flow`, local ou dossier synchronisé OneDrive) ; sous-arborescence isolée par `tenant_id` : `<root>/<tenant_id>/00_INBOX`, etc. (isolation P10.1 respectée).
2. **Champs manquants** : enum fermé `MissingFieldEnum` = `["date_piece", "numero_piece", "nom_fournisseur", "montant_ht", "montant_tva", "montant_ttc", "categorie", "taux_tva"]`.
3. **Statut `REJETE`** : retenu. Si `Statut == REJETE` dans `_A_COMPLETER.xlsx`, routage automatique vers `05_ARCHIVE_ERREUR/REJET_MANUEL/` (transition `02_A_COMPLETER` → `05_ARCHIVE_ERREUR`).
4. **Détection de synchronisation** : déclenchement sur `mtime` + SHA-256 du classeur dès disparition du verrou `~$*.xlsx`.

---

*Source de vérité pour P14 / P15 / P16 ; le handover (`docs/PROJECT_HANDOVER.md`) reste le point d'entrée maître.*
