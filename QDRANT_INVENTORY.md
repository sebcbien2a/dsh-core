# Inventaire Qdrant — DSH-Core

## Méthode et limites

- Inventaire du serveur Qdrant local `http://localhost:6333`, relevé le
  **2026-10-19 vers 18:08 UTC** (20:08 heure locale).
- Version déclarée par le serveur : **Qdrant 1.19.1**.
- Le nombre de points a été vérifié avec l'API de comptage exacte
  (`exact=true`). Les points présents dans les collections non vides ont chacun
  un vecteur dense de **768 dimensions**; le nombre de vecteurs stockés indiqué
  ci-dessous est donc égal au nombre de points.
- « Dernière utilisation journalisée » désigne la dernière opération de données
  visible dans les journaux du conteneur Qdrant conservés sur les 30 derniers
  jours, avant le début des requêtes de cet audit (avant
  **2026-10-19 18:07 UTC**). Cela ne
  prouve pas le dernier accès applicatif : les journaux peuvent être incomplets
  ou expurgés. Les vérifications de présence de collection ne sont pas comptées
  comme opérations de données.
- Les dates `created_at` et `timestamp` mentionnées sont des valeurs de payload,
  pas un historique complet des accès. Une collection vide ou sans journal
  exploitable n'a pas de dernière utilisation vérifiable.
- La recherche du code couvre les sources Python du dépôt, y compris les scripts
  de test et de sauvegarde. Aucun contenu de point n'est reproduit dans ce
  rapport.

## Collections présentes sur le serveur

| Collection | Vecteurs / points | Dernière utilisation journalisée | Classification |
|---|---:|---|---|
| `agent_skills` | 4 | 2026-09-30 04:09:04 UTC — `POST /points/payload` | **TEST** |
| `document_bus` | 0 | Aucune opération de données trouvée dans les journaux conservés | **ACTIF** |
| `invoice_documents` | 3 | 2026-10-01 00:35:29 UTC — `PUT /points` | **INCONNU** |
| `mem0` | 0 | Aucune opération de données trouvée dans les journaux conservés | **ACTIF** |
| `mem0migrations` | 0 | Aucune opération de données trouvée dans les journaux conservés | **INCONNU** |
| `research_cache` | 0 | 2026-09-29 02:18:48 UTC — `POST /points` | **ACTIF** |
| `skills_catalog` | 0 | Aucune opération de données trouvée dans les journaux conservés | **ABANDONNÉ** |
| `teacher_student_metrics` | 16 | 2026-10-02 02:56:24 UTC — `POST /points/scroll` | **ACTIF** |

**Total présent : 23 vecteurs dans 8 collections.**

## Détail par collection

### `agent_skills` — TEST

- **État :** 4 vecteurs de 768 dimensions.
- **Écriture :** `repositories/skills_repository.py`, méthode
  `SkillsRepository.save_skill`; l'agent `agents/skill_creator_agent.py`
  l'utilise. `agents/teacher_student_agent.py` peut déclencher cet agent sous
  certaines conditions. Le script `test_learning_loop.py` crée également un
  skill.
- **Lecture :** `SkillsRepository.find_relevant_skills` et
  `SkillsRepository.increment_usage`, appelés par
  `services/skill_router.py::SkillRouter.retrieve_skill_for_task`.
  `test_learning_loop.py` exerce cette voie.
- **Raccordement applicatif :** `agents/research_agent.py` tente d'appeler
  `find_relevant_skill`, méthode absente de `SkillRouter`; son bloc d'exception
  absorbe l'échec. La lecture de skills par ce chemin de recherche n'aboutit
  donc pas comme écrit. Les points présents et l'activité journalisée
  correspondent au moins à une utilisation antérieure; les sources actuelles
  ne permettent pas d'attribuer cette activité à un appel de production précis.
- **Payload :** le dernier `created_at` observé est
  `2026-09-30T06:08:58.616308`; le payload contient `usage_count`, mais pas de
  date de dernière utilisation.
- **Classement :** collection de skills effectivement couverte par un test,
  avec un raccordement de recherche applicatif actuellement cassé.

### `document_bus` — ACTIF

- **État :** 0 vecteur.
- **Écriture prévue dans le code :** `memory_manager.py::indexer_document`
  appelle LlamaIndex `VectorStoreIndex.from_documents`. Le script
  `test_llamaindex_qdrant.py` construit aussi un index avec cette collection.
  Aucun appelant de `indexer_document` n'a été repéré dans le code applicatif.
- **Lecture :** `agents/research_agent.py` ouvre la collection comme
  `QdrantVectorStore` et son mode document interroge l'index via un retriever.
  Cette lecture est raccordée à la route de recherche de l'API; la collection
  est toutefois vide au relevé.
- **Activité :** les journaux conservés ne montrent pas d'opération de données
  sur cette collection; dernière utilisation non déterminable.
- **Classement :** cible d'une voie de lecture documentaire applicative, mais
  sans vecteur actuellement stocké.

### `invoice_documents` — INCONNU

- **État :** 3 vecteurs de 768 dimensions.
- **Écriture :** aucun writer vers cette collection n'a été trouvé dans les
  sources Python actuelles.
- **Lecture prévue dans le code :** `services/invoice_similarity_service.py`,
  méthodes `find_similar_invoices` et `find_invoices_by_supplier`. Aucune
  instanciation ou utilisation effective de ce service n'a été repérée.
- **Activité :** le dernier appel de données journalisé est une écriture
  `PUT /points` du 2026-10-01 00:35:29 UTC, mais son origine n'est pas attribuable
  au code actuel examiné. Le payload contient une date métier
  `date_facture=2024-01-15`, qui ne donne pas la date d'indexation.
- **Classement :** des données existent, mais leur writer, leur propriétaire et
  leur usage applicatif courant ne sont pas identifiés.

### `mem0` — ACTIF

- **État :** 0 vecteur.
- **Écriture :** le module racine `memory_manager.py` initialise Mem0 via
  `Memory.from_config`; `ajouter_souvenir` appelle `memoire_m0.add`. La
  fonction `register_skill` peut ajouter un souvenir à l'enregistrement d'un
  skill.
- **Lecture :** `recuperer_contexte` appelle `memoire_m0.search`; le routeur
  racine `router.py::route_prompt` l'utilise lorsqu'il récupère le contexte.
- **Collection effective :** la configuration de ce module ne précise pas de
  `collection_name`; `mem0` est la collection présente sur le serveur. La
  correspondance est donc déduite du client Mem0 et du nom observé.
- **Activité :** aucun appel de données sur `mem0` trouvé dans les journaux
  conservés; dernière utilisation non déterminable. L'import du module
  configure le client, mais cela ne démontre pas une écriture.
- **Classement :** voie mémoire présente et appelée par le code, sans données
  persistées au moment du relevé.

### `mem0migrations` — INCONNU

- **État :** 0 vecteur.
- **Écriture/lecture applicatives :** aucun accès direct trouvé dans les sources
  du projet.
- **Origine probable :** collection auxiliaire liée à l'initialisation ou aux
  migrations Mem0, puisque le projet initialise `Memory.from_config`; cette
  attribution est une déduction et n'est pas déclarée dans le code applicatif.
- **Activité :** aucune opération de données observée dans les journaux
  conservés.
- **Classement :** collection présente dont le rôle interne et le cycle de vie
  n'ont pas été confirmés.

### `research_cache` — ACTIF

- **État :** 0 vecteur.
- **Écriture :** `agents/research_agent.py::_save_cache` fait un `upsert` après
  une recherche approfondie réussie.
- **Lecture :** `ResearchAgent._check_cache` récupère le résultat avant
  l'exécution de la stratégie approfondie.
- **Création :** `ResearchAgent.__init__` crée la collection si elle est
  absente.
- **Activité :** dernier appel de données journalisé :
  `POST /points` le 2026-09-29 02:18:48 UTC. La collection est vide maintenant;
  l'écriture historique peut avoir été remplacée ou les points supprimés.
- **Classement :** cache raccordé au code de recherche, mais actuellement vide.

### `skills_catalog` — ABANDONNÉ

- **État :** 0 vecteur.
- **Écriture/lecture applicatives :** aucune référence opérationnelle trouvée
  dans le code Python courant. Le nom apparaît dans une ancienne documentation
  d'architecture comme collection prévue; le code actuel utilise plutôt
  `agent_skills`.
- **Activité :** aucun appel de données observé dans les journaux conservés.
- **Classement :** ancienne collection planifiée sans writer ni reader courant
  repéré.

### `teacher_student_metrics` — ACTIF

- **État :** 16 vecteurs de 768 dimensions.
- **Écriture :** `agents/teacher_student_agent.py::_record_metric` fait un
  `upsert`. `agents/research_agent.py` instancie cet agent quand Qdrant est
  disponible et appelle `handle_task_result` pour ses stratégies de recherche.
- **Lecture :** `TeacherStudentAgent._should_create_skill` compte des métriques;
  `repositories/teacher_student_repository.py::get_global_metrics` lit la
  collection avec `scroll`. Aucun appelant de ce repository de lecture n'a été
  repéré.
- **Activité :** dernière opération de données journalisée :
  `POST /points/scroll` le 2026-10-02 02:56:24 UTC. Le `timestamp` maximum
  observé dans les payloads est `2026-10-01T03:02:12.865580`.
- **Classement :** collection de métriques raccordée à l'agent de recherche
  pour l'écriture; le tableau de bord/repository de lecture n'est pas raccordé
  à un appelant repéré.

## Collections définies par le code mais absentes du serveur

| Collection | Vecteurs actuels | Code qui écrit | Code qui lit | Classification |
|---|---:|---|---|---|
| `invoices` | 0 — collection absente | `invoice_indexer.py::InvoiceIndexer.indexer_facture_json` (création et `upsert`) | `InvoiceIndexer.rechercher` | **TEST** |
| `episodic_memory` | 0 — collection absente | `repositories/memory_repository.py::MemoryRepository.save_memory` | `search_semantic_memory` et `get_session_history` | **ABANDONNÉ** |
| `mem0_memories` | 0 — collection absente | `test_mem0_qdrant.py` configure Mem0 et appelle `add` | `test_mem0_qdrant.py` appelle `search` | **TEST** |

### Précisions sur ces collections absentes

- `invoices` n'est créée qu'à l'instanciation de `InvoiceIndexer`, utilisée dans
  le bloc de lancement autonome du script. Aucun appelant applicatif n'a été
  repéré.
- `episodic_memory` est le nom par défaut du `MemoryRepository`, mais le
  `services.memory_manager.MemoryManager` qui l'utilise n'est instancié par
  aucun appelant repéré. La collection n'existe pas sur le serveur.
- `mem0_memories` est explicitement configurée par le script de test; elle ne
  figure pas dans l'inventaire runtime. Le `memory_manager.py` racine utilise une
  configuration Mem0 distincte sans nom de collection explicite.

## Synthèse des writers/readers Qdrant repérés

| Composant | Collection(s) | Écriture | Lecture |
|---|---|---|---|
| `repositories/skills_repository.py::SkillsRepository` | `agent_skills` par défaut | `save_skill` | `find_relevant_skills`, `increment_usage` |
| `agents/teacher_student_agent.py::TeacherStudentAgent` | `teacher_student_metrics` | `_record_metric` | `_should_create_skill` (`count`) |
| `repositories/teacher_student_repository.py::TeacherStudentRepository` | `teacher_student_metrics` | — | `get_global_metrics` (`scroll`) |
| `agents/research_agent.py::ResearchAgent` | `research_cache`, `document_bus` | `_save_cache` (`research_cache`) | `_check_cache`; retriever de `document_bus` |
| `memory_manager.py` (racine) | `document_bus`, Mem0 (`mem0` observé) | `indexer_document`; `ajouter_souvenir` | `interroger_documents`; `recuperer_contexte` |
| `services/invoice_similarity_service.py` | `invoice_documents` | Aucun writer trouvé | recherche vectorielle et par fournisseur |
| `invoice_indexer.py::InvoiceIndexer` | `invoices` | `indexer_facture_json` | `rechercher` |
| `repositories/memory_repository.py::MemoryRepository` | `episodic_memory` par défaut | `save_memory` | recherche sémantique et historique de session |
| `test_mem0_qdrant.py` | `mem0_memories` | `Memory.add` | `Memory.search` |

Les collections vides restent néanmoins susceptibles d'être créées ou utilisées
par ces chemins lorsqu'ils sont exécutés. Les classes de repository et services
non instanciés ne prouvent pas qu'une écriture ou une lecture a effectivement eu
lieu.

# AUDITS QDRANT P1-2 ET P1-3 — 15 OCTOBRE 2026

Cette section consigne les résultats communiqués pour les audits P1-2 et P1-3.
La date du 15 octobre 2026 est la date réelle d'intervention déclarée. Les
horodatages de fichiers ci-dessous sont des métadonnées observées pendant la
comparaison, et non des dates d'exécution des audits.

## P1-2 — Comparaison non destructive des stockages

- Stockage relié à l'instance Qdrant active observée :
  `A:\dsh-core\qdrant_storage`.
- Stockage secondaire inactif, sans seconde instance démarrée :
  `A:\dsh-core\docker\qdrant_storage`.
- La comparaison a été strictement non destructive; aucun stockage n'a été
  modifié, fusionné, renommé ou déplacé.
- Stockage actif : **4 366 859 237 octets**, **1 574 fichiers** et
  **738 répertoires**.
- Stockage secondaire : **3 553 976 763 octets**, **1 652 fichiers** et
  **775 répertoires**.
- Collections identifiables dans le stockage actif :
  `agent_skills`, `document_bus`, `invoice_documents`, `mem0`,
  `mem0migrations`, `research_cache`, `skills_catalog`,
  `teacher_student_metrics`.
- Collections identifiables dans le stockage secondaire :
  `document_bus`, `invoices`, `mem0`, `mem0_memories`, `mem0migrations`.
- Collections communes : `document_bus`, `mem0`, `mem0migrations`.
- Cinq collections exclusives au stockage actif :
  `agent_skills`, `invoice_documents`, `research_cache`, `skills_catalog`,
  `teacher_student_metrics`.
- Deux collections exclusives au stockage secondaire : `invoices` et
  `mem0_memories`.
- **1 544 fichiers** sont présents uniquement dans le stockage actif et
  **1 622 fichiers** uniquement dans le stockage secondaire. Six chemins
  communs ont des tailles différentes.
- Le répertoire `.deleted` est présent uniquement dans le stockage secondaire.
- Les dates extrêmes de dernière modification observées sur les fichiers sont
  des métadonnées : stockage actif, du 27 septembre 2026 au 19 octobre 2026;
  stockage secondaire, du 24 au 27 septembre 2026. Elles ne datent pas
  l'exécution de l'audit.
- **Statut P1-2 : `DIFFÉRENTS`.** Aucune équivalence, fusion ou autorité du
  stockage secondaire ne peut être déduite. Le stockage racine reste le seul
  stockage relié à l'instance active observée.

## P1-3 — Lecture des quatre points `agent_skills`

- La collection active `agent_skills` a été interrogée en lecture seule; quatre
  points ont été examinés.
- Les quatre points ont le même `task_name` et le même objectif général :
  extraire la TVA d'une facture présentant une inversion visuelle des montants
  HT et TTC. Leurs contenus textuels, identifiants, dates et compteurs d'usage
  diffèrent.
- Chaque vecteur visible compte **768 dimensions**. Les similarités cosinus
  calculées entre paires vont de **94,90 % à 99,04 %**; elles indiquent une
  proximité, pas une identité.
- Aucun doublon strict ni aucune contradiction de règle n'a été observé. La
  redondance fonctionnelle est probable et les formulations sont partiellement
  complémentaires.
- Les payloads ne fournissent aucun champ de version ou de cycle de vie
  exploitable. Un seul point a `usage_count=1`; les trois autres ont
  `usage_count=0`.
- Statut des quatre entrées : **`PROBABLE_DOUBLON`**.
- Réponse à « Peut-on désigner immédiatement un skill canonique sans perte
  d'information ? » : **NON**.
- Aucune suppression, fusion, réécriture ou promotion n'a été effectuée.
