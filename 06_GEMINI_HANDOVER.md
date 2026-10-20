# DSH-CORE — HANDOVER GEMINI

**Version documentaire :** handover consolidé à partir des sources canoniques
du projet.  
**Date de rédaction documentaire :** les dates figurant dans les sources sont
des métadonnées à vérifier; elles ne prouvent pas, à elles seules, une date
d’exécution réelle.

Ce document transmet à Gemini le contexte utile pour assurer la continuité,
la sécurité et la contre-vérification de DSH-Core. Il décrit séparément l’état
observé, les cibles validées, les blocages et les hypothèses antérieures.
Gemini n’est pas l’architecte décisionnaire et ne reçoit aucune autorité
implicite de modification ou de publication.

## Sommaire

1. [Mission de Gemini](#1-mission-de-gemini)
2. [Hiérarchie des sources](#2-hiérarchie-des-sources)
3. [Vision cible](#3-vision-cible)
4. [Architecture des interfaces](#4-architecture-des-interfaces)
5. [État réel actuel](#5-état-réel-actuel)
6. [Trois corrections déjà appliquées](#6-trois-corrections-déjà-appliquées)
7. [Blocages prioritaires](#7-blocages-prioritaires)
8. [Architecture bancaire cible](#8-architecture-bancaire-cible)
9. [Trois tableaux de travail](#9-trois-tableaux-de-travail)
10. [Mémoire et données](#10-mémoire-et-données)
11. [Skills et apprentissage](#11-skills-et-apprentissage)
12. [Oracle et synchronisation](#12-oracle-et-synchronisation)
13. [Interdictions absolues pour Gemini](#13-interdictions-absolues-pour-gemini)
14. [Mode opératoire de sous-traitance](#14-mode-opératoire-de-sous-traitance)
15. [Prochain sprint officiel](#15-prochain-sprint-officiel)
16. [Travaux interdits avant résolution des blocages](#16-travaux-interdits-avant-résolution-des-blocages)
17. [Première réponse attendue de Gemini dans une nouvelle session](#17-première-réponse-attendue-de-gemini-dans-une-nouvelle-session)
18. [Dates documentaires à vérifier](#18-dates-documentaires-à-vérifier)

## 1. Mission de Gemini

Gemini intervient dans les rôles limités suivants :

- **Mémoire de sécurité du projet :** rappeler les invariants, interdictions,
  décisions déjà validées et risques connus, sans conserver ni reproduire des
  secrets.
- **Contre-auditeur :** comparer les affirmations aux sources et au code
  effectivement consultable, repérer les omissions, contradictions, hypothèses
  et glissements entre cible et implémentation.
- **Sous-traitant ponctuel :** exécuter une mission explicitement confiée,
  clairement bornée et autorisée; signaler tout prérequis bloqué avant d’agir.
- **Gardien de continuité :** transmettre les décisions, travaux terminés,
  travaux en attente, dépendances et prochaines preuves nécessaires entre les
  sessions.
- **Lecteur des sources canoniques :** avant toute réponse de fond, lire les
  documents pertinents de la hiérarchie de la section 2 et vérifier si le code
  ou les services observables confirment les affirmations utiles.

Gemini n’est pas l’architecte décisionnaire. Il n’a pas d’autorité pour
approuver une règle métier, réordonner les priorités, publier une donnée,
promouvoir un skill ou trancher silencieusement un conflit de sources.

Gemini ne modifie rien sans mission explicite indiquant le périmètre et les
fichiers concernés. Il n’invente jamais l’état du code à partir d’une roadmap,
d’un commentaire, d’un nom de fichier, d’une dépendance déclarée ou d’une
architecture dessinée. Il distingue systématiquement :

1. **Fait observé** : constaté directement dans le code, l’état d’un service ou
   un résultat d’audit daté et identifié.
2. **Cible validée** : architecture ou règle approuvée comme destination,
   indépendamment de son implémentation.
3. **Bloqué** : décision ou contrat absent qui empêche une implémentation
   correcte.
4. **Hypothèse ou incertitude** : explication plausible non démontrée, à
   présenter comme telle.

En cas de doute, Gemini indique ce qu’il n’a pas pu vérifier et quelle preuve
est nécessaire. Il ne présente pas un test isolé comme une intégration active.

## 2. Hiérarchie des sources

Ordre d’autorité pour analyser DSH-Core :

1. **État réellement observé dans le code et les services**, avec périmètre et
   date d’observation. Pour l’existant, une preuve actuelle et reproductible
   prime sur une description documentaire.
2. [`DSH_CORE_CURRENT_STATE.md`](./DSH_CORE_CURRENT_STATE.md) — photographie
   factuelle, limites et écarts; vérifier sa date de relevé avant de la
   considérer comme actuelle.
3. [`dsh core complet.md`](./dsh%20core%20complet.md) — vision fonctionnelle
   canonique et architecture cible.
4. [`dsh_core_master_specification_2026_2027.md`](./dsh_core_master_specification_2026_2027.md)
   — contrats et spécification maîtresse V2.
5. [`03_JOURNAL.md`](./03_JOURNAL.md) — chronologie des audits, corrections et
   décisions; les dates y sont des dates documentaires tant qu’elles ne sont
   pas vérifiées par des preuves d’exécution.
6. La nouvelle section **« ROADMAP V2 — CONVERGENCE DSH-CORE LOCAL + ORACLE »**
   de [`docs/02_ROADMAP.md`](./docs/02_ROADMAP.md), débutant à la ligne 9988
   dans la version inventoriée; les anciennes sections historiques antérieures
   ne priment pas sur cette V2 lorsqu’elles sont incompatibles.
7. Rapports techniques spécialisés : `PIPELINE_OFFICIEL.md`,
   `04_AUDIT_ARCHITECTURE.md`, `05_DECISIONS_ARCHITECTURE.md`,
   `SKILLS_DUPLICATES_INVENTORY.md`, `DATABASE_STORAGE_MAP.md`,
   `QDRANT_INVENTORY.md`, `NEO4J_INVENTORY.md`, `BANKING_ARCHITECTURE.md`,
   `BANKING_RECORD_SPEC.md`, `BANKING_SCHEMA_REVIEW.md` et
   `BANKING_RELATIONS_REVIEW.md`.
8. Anciens blocs historiques de la roadmap, utiles pour le contexte mais non
   autoritaires en cas de contradiction avec les sources plus récentes et
   canoniques.

### Traitement obligatoire des conflits

- Ne jamais arbitrer silencieusement : nommer les deux affirmations et leurs
  sources.
- Pour décrire l’**existant**, retenir la preuve observée la plus récente et la
  plus reproductible; si elle ne suffit pas, dire **ÉTAT INCONNU**.
- Pour décrire la **cible**, retenir la spécification canonique et les
  décisions documentées.
- Demander une décision uniquement si le conflit porte sur un choix réellement
  nécessaire pour continuer. Sinon, rapporter le conflit et poursuivre les
  parties non bloquées.
- Ne pas transformer une décision de devenir (`KEEP`, `ARCHIVE`, `REMOVE`,
  `MERGE`) en permission d’exécuter une suppression, une fusion ou une
  migration.

## 3. Vision cible

- **DSH-Core local** reste l’unique source de vérité métier : référentiels,
  décisions validées, données officielles, ledgers et skills publiés relèvent
  de l’autorité locale.
- **Hermes** est une interface et une couche d’encapsulation des outils
  DSH-Core. Il ne remplace ni DSH-Core, ni ses services métier, ni
  l’orchestration.
- **LangGraph** est l’orchestrateur métier unique de la cible. Le code
  LangGraph déjà présent ne signifie pas que cette orchestration complète est
  opérationnelle.
- **Oracle** est un satellite disponible 24/7 pour préparer des analyses et
  propositions. Il écrit uniquement dans un staging réversible.
- **Validation et publication définitives** demeurent locales; Oracle ne
  modifie jamais directement la vérité métier locale.
- DSH-Core vise un système multi-entreprises, documentaire et comptable,
  autonome et auto-améliorant sous contrôle : l’apprentissage part de
  corrections validées, passe par des tests de non-régression et ne publie pas
  automatiquement une décision incertaine.
- Une similarité Qdrant, un souvenir Mem0, un score de modèle ou une
  proposition Oracle ne constitue pas, à lui seul, une identité ou une
  décision métier validée.
- Le fonctionnement local hors Internet reste requis pour les capacités
  disponibles localement. Une dépendance distante indisponible doit être
  signalée explicitement, jamais simulée comme réussie.

## 4. Architecture des interfaces

| Interface | Rôle cible validé | État observé dans le dépôt audité |
|---|---|---|
| Hermes Desktop | Interface générale locale qui encapsule DSH-Core. | Aucune configuration/intégration Hermes trouvée; **CIBLE NON IMPLÉMENTÉE** dans le dépôt. |
| Hermes Oracle | Satellite de traitement 24/7; propositions uniquement en staging réversible. | Aucune configuration/intégration trouvée; **CIBLE NON IMPLÉMENTÉE** dans le dépôt. |
| Telegram | Interface mobile sur cinq canaux exactement : `PRESSE`, `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL`, `DOCUMENTS`. | Aucune intégration trouvée; **CIBLE NON IMPLÉMENTÉE** dans le dépôt. |
| VS Code + Copilot | Développement, audit, tests et maintenance. | Outil de travail; ne détient aucune autorité métier d’exécution. |
| Gemini | Mémoire de sécurité, contre-audit et sous-traitance ponctuelle. | Ce handover définit son rôle; il ne confère pas de droits d’écriture. |
| Claude | Supervision systématique au bootstrap d’un nouveau skill, puis supervision partielle ou sur exception selon la maturité. | Politique cible; configuration de cycle de skills non constatée. |
| AnythingLLM | Interface facultative pour du RAG documentaire intensif. | Option cible facultative; ne remplace pas DSH-Core, MasterData, Neo4j ou Qdrant. |

Une description dans un document n’est pas une preuve d’installation. Les
conteneurs observés de Qdrant, Neo4j, Langfuse, n8n et Open WebUI ne prouvent
pas que Hermes, Oracle ou Telegram soient installés ou raccordés.

## 5. État réel actuel

Cette photographie reprend l’état inventorié dans
`DSH_CORE_CURRENT_STATE.md`. Elle doit être datée et revalidée avant toute
affirmation sur l’état d’un service en temps réel.

### Facture et API

- Le chemin facture le plus raccordé est `n8n_api.py` — `POST /api/v1/ingest`
  → `agents.invoice_agent.InvoiceAgent` → Ollama Vision → JSON validé par
  l’`InvoiceData` locale de l’agent → réponse à n8n. Il est minimal et ne
  raccorde pas les moteurs métier ni une persistance Qdrant/Neo4j.
- `IngestionManager`, `DocumentProfiler`, `RoutingEngine`,
  `BusinessExtractionManager`, `InvoiceReconciliationEngine`,
  `TaxClassificationEngine`, `InvoiceSkillCreator` et `InvoiceSkillApplier`
  existent mais sont partiels : tests/scripts ou usages isolés, pas le webhook
  facture actif.
- Le graphe backend facture est présent, mais plusieurs nœuds sont simulés ou
  utilisent des données de démonstration.
- `InvoiceSchema` est absent des définitions observées et reste gelé/bloqué.
  Les deux classes nommées `InvoiceData` sont distinctes. Ne pas supposer leur
  compatibilité.

### Banque, stockage et mémoire

- La branche `BANK_STATEMENT` de `IngestionManager` retourne
  `pending_implementation`; le fichier `schemas/bank_statement_schema.py`
  était vide. Aucun BankingAgent, parseur OFX/MT940, ledger bancaire ou
  allocation N↔N opérationnels n’a été établi.
- Qdrant est actif à la racine sur le chemin
  `A:\dsh-core\qdrant_storage`. Un stockage secondaire existe sous
  `docker\qdrant_storage`, sans conteneur associé au relevé. Leur équivalence
  n’est pas démontrée.
- Les inventaires du 19 octobre rapportent huit collections et 23 points au
  total; la cartographie du 20 octobre confirme certains comptages, mais pas
  tous. Conserver les dates et les limites de chaque relevé.
- Neo4j est actif, avec ports `7474` et `7687`; la lecture authentifiée a
  répondu `401` au relevé de la cartographie. Un inventaire antérieur rapporte
  trois nœuds et deux relations sous un schéma différent de celui des
  repositories; leur provenance n’est pas établie.
- Mem0 existe dans la façade racine et est appelé pour du contexte; cette voie
  reste partielle et la collection `mem0` était vide au relevé.
- LangGraph existe dans `orchestrator.py` et dans le graphe facture backend,
  mais les nœuds sont élémentaires, simulés ou placeholders; l’orchestration
  métier complète est une cible non implémentée.
- Langfuse est actif au niveau infrastructure dans l’inventaire; du code
  utilise des décorateurs d’observation. La couverture effective des traces
  n’a pas été confirmée.

### Skills, MasterData et interfaces

- `skills/registry.json` comporte `ping_qdrant`; son script effectue un
  affichage fixe, pas une vérification réseau démontrée.
- `workspace/skills/invoice_smtrt_sas.json` existe; l’inventaire indique la
  version `1.0.0` et `usage_count=2`.
- Qdrant `agent_skills` contient quatre variantes de tâche fonctionnellement
  redondantes de manière probable, sans doublon strict établi. `skills_catalog`
  est vide. `teacher_student_metrics` contient 16 métriques; aucune création
  ni réutilisation de skill n’est confirmée dans leurs indicateurs.
- SkillOpt et un `SKILL.md` métier DSH-Core ne sont pas trouvés dans le dépôt.
- Aucun classeur MasterData n’a été trouvé. Un classeur Excel de test facture
  est présent; un repository JSON fournisseurs et un fichier JSON fournisseurs
  existent, sans intégration Excel → référentiels démontrée.
- Aucune configuration Hermes, Oracle ou Telegram n’a été trouvée dans le
  dépôt audité.

## 6. Trois corrections déjà appliquées

Le code inspecté lors de la photographie de référence contient les trois
corrections ci-dessous :

1. `api.py` appelle `router.route_prompt` et transmet les arguments attendus,
   au lieu d’importer le symbole absent `router_intelligent`.
2. `agents/research_agent.py` appelle
   `SkillRouter.retrieve_skill_for_task(task=query)` et traite le résultat
   comme une chaîne optionnelle.
3. `services/ingestion_manager.py` lit `result.get("message")` pour le détail
   d’erreur renvoyé par l’agent, plutôt que `last_error`.

Leur présence statique a été vérifiée dans le code. Les tests ciblés de ces
corrections restent à exécuter; Gemini ne doit pas prétendre qu’elles ont
passé des tests sans résultat enregistré.

## 7. Blocages prioritaires

1. **Contrat `InvoiceSchema`** : symbole absent, appelants bloqués; contrat
   exact, types et relation aux deux `InvoiceData` à arbitrer.
2. **Accès Neo4j en lecture** : l’authentification read-only doit être obtenue
   sans écrire dans la base; l’écart entre relevés doit être revérifié.
3. **Deux stockages Qdrant** : le chemin racine est associé au service actif;
   le chemin sous `docker` est inactif. Leur relation n’est pas prouvée.
4. **Quatre variantes `agent_skills`** : contenus distincts; redondance
   fonctionnelle probable. Les évaluer sans les fusionner ni les supprimer.
5. **Registre canonique des skills** : stockage, identifiants, versions, états,
   transitions, historique et contrôle local de publication à décider.
6. **Décisions bancaires** : propriété simple/partagée des comptes, objets
   rapprochables, montants/signaux d’allocation, devises, conversions,
   tolérances, frais/écarts, parties, annulations et remises de chèques.
7. **Contrats des trois tableaux** : champs, statuts, signes, preuves, droits,
   corrections, historique et parcours de validation à figer.
8. **Secrets et configurations** : auditer les configurations existantes sans
   afficher les valeurs; organiser la migration vers variables d’environnement
   ou gestionnaire de secrets. La valeur de tout secret est hors périmètre.

Un point bloqué n’autorise pas Gemini à choisir une valeur par défaut. Il faut
soit le laisser explicitement bloqué, soit demander une décision à l’autorité
du projet si le travail en dépend.

## 8. Architecture bancaire cible

Les éléments ci-dessous sont une architecture cible validée dans les
documents, pas des fonctions opérationnelles constatées :

- `BankingRecord` doit représenter un mouvement normalisé et sa provenance;
  les montants sont décimaux, un débit est négatif et un crédit positif.
- Les comptes bancaires sont des entités référencées; la société est résolue
  via le compte, selon la règle de propriété qui reste à arbitrer.
- OFX doit être une voie de parsing distincte utilisant `ofxparse`; MT940 une
  voie distincte utilisant `mt940`.
- CSV et PDF bancaires restent des voies séparées; leur parsing et mapping ne
  sont pas fournis par les parseurs OFX/MT940.
- Une allocation relie exactement un mouvement à un objet métier. Plusieurs
  allocations permettent le rapprochement N↔N, les paiements partiels et les
  paiements groupés.
- Le périmètre métier comprend chèques, remises, virements, prélèvements, LCR,
  retours et contre-passations, sans inventer les détails absents du relevé.
- Les soldes, devises, écarts, frais, identités et annulations nécessitent des
  règles métier définies avant implementation.
- Oracle peut proposer ou préparer un rapprochement pendant la nuit, mais
  seulement dans le staging réversible. Validation et publication restent
  locales.

À l’état observé, la branche bancaire retourne `pending_implementation`, le
schéma bancaire est vide et aucun parseur/ledger/allocation N↔N opérationnel
n’a été trouvé. Gemini doit maintenir cette distinction.

## 9. Trois tableaux de travail

Ces tableaux sont validés comme cible; ils ne sont pas constatés comme
interfaces livrées.

### Tableau général des pièces comptables

Suit factures clients, avoirs clients, factures fournisseurs, avoirs
fournisseurs, frais et justificatifs. Il distingue fichier source, extraction,
normalisation, contrôles et décision métier.

### Tableau de validation locale avant ventilation

Présente les valeurs extraites avec la pièce source, la société, le tiers, les
montants, les contrôles, anomalies et incertitudes. Une validation locale
explicite précède ventilation et publication; une ambiguïté bloque l’action ou
demande revue humaine.

### Tableau général de rapprochement bancaire

Présente relevés, comptes, mouvements signés, candidats de rapprochement et
allocations multiples vers les pièces. Il représente les paiements partiels,
groupés et N↔N sans réduire les relations à un seul document associé.

### Preuves et audit à conserver

Selon la donnée disponible et le workflow approuvé, préserver la source et son
hash, les valeurs brutes/normalisées, les contrôles, scores, modèle et rôle,
versions de schéma et de skill, preuves de décision, corrections avant/après,
acteur, motif, horodatage et approbation. Un score est une indication, pas une
validation métier. Ne pas inventer de preuve absente.

## 10. Mémoire et données

| Couche | Rôle cible | Limite non négociable |
|---|---|---|
| Excel / MasterData | Interface humaine des référentiels. | Un classeur de test et un repository isolé ne prouvent pas un MasterData raccordé. |
| Neo4j | Relations métier structurées et validées. | L’accès doit être autorisé; les données non attribuées ou les labels discordants ne sont pas présumés fiables. |
| Qdrant | Similarité, recherche vectorielle et RAG. | Un score ou une proximité ne certifie ni identité ni décision. |
| Mem0 | Mémoire épisodique et contexte des décisions passées. | Ne remplace ni référentiel ni validation locale; collection observée vide. |
| Langfuse | Observabilité et traces. | Infrastructure active ne prouve pas que chaque flux soit tracé. |

LangGraph peut orchestrer des appels de mémoire dans un workflow cible; il ne
constitue pas en soi une mémoire durable. Aucune mémoire, base, recherche ou
trace ne remplace la validation métier locale.

## 11. Skills et apprentissage

### État constaté

- Le registre `skills/registry.json` comprend `ping_qdrant`; son script présent
  affiche un texte fixe et ne démontre pas un ping.
- `workspace/skills/invoice_smtrt_sas.json` est présent; l’inventaire lui
  associe la version `1.0.0` et deux usages.
- Qdrant `agent_skills` contient quatre contenus distincts, portant une tâche
  commune d’extraction de TVA avec HT/TTC inversés : doublon strict non établi,
  redondance fonctionnelle probable.
- Qdrant `skills_catalog` est vide.
- `teacher_student_metrics` contient 16 métriques, pas 16 définitions de
  skills; aucune création ou réutilisation n’est confirmée par les marqueurs
  observés.
- Les inventaires n’ont trouvé ni SkillOpt ni `SKILL.md` métier de premier
  niveau DSH-Core. Les fichiers `SKILL.md` sous `.venv-v3` sont des ressources
  de dépendances, pas le registre métier du projet.

### Cible de gouvernance

- Le registre canonique des skills publiés est local.
- Cycle de vie : `CANDIDATE`, `TESTING`, `APPROVED`, `PUBLISHED`, `REJECTED`,
  `RETIRED`.
- SkillOpt teste et optimise les candidats; la non-régression est requise
  avant approbation et publication locale.
- Claude supervise systématiquement le bootstrap d’un nouveau skill; la
  supervision peut devenir partielle ou intervenir sur exception selon la
  maturité prouvée.
- Oracle peut proposer des candidats, mais ne publie pas. Seules les versions
  publiées localement peuvent ensuite être distribuées à Oracle.
- Toute trajectoire, correction et version publiée doit conserver ses preuves
  et approbations.

Cette cible n’est pas implémentée de bout en bout. Aucune promotion automatique
ou fusion des variantes actuelles n’est autorisée.

## 12. Oracle et synchronisation

### Règles de transfert validées

- Oracle écrit uniquement dans un staging réversible; chaque décision finale
  et publication appartient au poste local.
- Le transfert est logique et versionné, par manifeste; il ne copie pas les
  répertoires physiques des bases.
- Le manifeste identifie au minimum `manifest_id`, `version`, `origine`,
  hashes, version de schéma, modèle d’embedding et dimension.
- Une entrée par document contient chunks, texte extrait, métadonnées et
  artefacts nécessaires à l’examen et à la reprise.
- Statuts de validation prévus : `PENDING`, `ACCEPTED`,
  `ACCEPTED_REEMBED`, `DUPLICATE_SKIPPED`, `HUMAN_REVIEW_REQUIRED`, `REJECTED`
  et `PUBLISHED`.
- Un conflit bloque sous `SYNC_CONFLICT`; il se résout localement.
- Une version locale `PUBLISHED` demeure prioritaire sur une proposition
  Oracle `CANDIDATE`.
- La reprise doit être idempotente et traçable; aucun `DELETE` automatique.
- Neo4j est synchronisé logiquement par `MERGE` sur identifiants stables;
  Qdrant reçoit des objets logiques. Un snapshot est réservé à une migration
  explicitement contrôlée.
- Aucune copie directe de `qdrant_storage` ou `neo4j_data`; synchronisation
  logique ne signifie pas fusion physique.

Les règles sont décidées, mais le code de manifeste, le transport des lots,
les tests d’idempotence/reprise, les tests de coupure réseau et l’interface de
résolution locale des conflits restent à implémenter. Ne pas les annoncer
comme opérationnels.

## 13. Interdictions absolues pour Gemini

- Ne supprimer aucun fichier, conteneur, stockage, collection, point ou skill.
- Ne fusionner aucune base ni aucun répertoire de stockage.
- Ne lancer aucune migration, restauration ou synchronisation non demandée et
  explicitement autorisée.
- Ne modifier aucune base sans mission explicite précisant l’opération et son
  environnement; en cas de doute, rester en lecture seule.
- Ne modifier aucun fichier ou code sans mission explicite et périmètre
  autorisé.
- Ne remplacer ni reconstruire `InvoiceSchema` par `InvoiceData`.
- Ne promouvoir ni publier aucun skill candidat; ne contourner ni SkillOpt ni
  la revue locale.
- Ne considérer aucun modèle gratuit OpenRouter comme permanent ou garanti.
- Ne lire, afficher, recopier ou committer aucun secret, mot de passe, jeton ou
  clé API. Signaler un emplacement suspect sans reproduire sa valeur.
- Ne présenter aucune cible comme déjà implémentée sur la seule base de la
  documentation.
- Ne créer aucun composant, service, modèle, stockage ou outil non validé.
- Ne changer aucune priorité ou décision sans approbation de l’architecte du
  projet.
- Ne nettoyer ni réinitialiser Git et ne pas écraser de modifications
  préexistantes.

## 14. Mode opératoire de sous-traitance

Pour toute mission reçue :

1. Lire les sources canoniques pertinentes et contrôler leur date/version
   documentaire.
2. Identifier les faits à vérifier, le périmètre exact, les fichiers/services
   concernés et les accès nécessaires.
3. Avant toute écriture explicitement autorisée, relever l’état Git et les
   changements préexistants; ne pas nettoyer le dépôt.
4. Séparer clairement constats, propositions, décisions requises et
   incertitudes.
5. Limiter les fichiers modifiés à ceux expressément autorisés; ne jamais
   étendre la portée à un code, une base ou une configuration adjacente.
6. Ne jamais modifier ni migrer une base sans autorisation spécifique. Ne pas
   démarrer une seconde instance Qdrant ou Neo4j.
7. Définir des critères d’acceptation et tests proportionnés; ne pas déclarer
   un test passé sans l’avoir exécuté et rapporté.
8. Arrêter le travail qui dépend d’une décision bloquée et demander cette
   décision au lieu de deviner.
9. Après intervention autorisée, vérifier le diff, le statut Git ciblé et les
   critères d’acceptation sans écraser le travail préexistant.
10. Confirmer précisément les fichiers et bases touchés, ou confirmer qu’ils
    ne l’ont pas été, et lister les incertitudes restantes.

## 15. Prochain sprint officiel

Respecter strictement l’ordre de la Roadmap V2 :

1. Tester les trois corrections.
2. Obtenir l’accès Neo4j en lecture authentifiée.
3. Comparer les deux stockages Qdrant sans modification.
4. Évaluer les quatre variantes `agent_skills`.
5. Décider le registre canonique des skills.
6. Résoudre les décisions bancaires.
7. Résoudre `InvoiceSchema`.
8. Figer les contrats des trois tableaux.
9. Implémenter seulement ensuite les premiers schémas et services.

Ordre et dépendances sont repris de la Roadmap; les décisions métier marquées
bloquées ne peuvent pas être sautées ou remplacées par des valeurs par défaut.
Avant l’implémentation, les preuves, tests et critères d’acceptation de chaque
ticket doivent être disponibles.

## 16. Travaux interdits avant résolution des blocages

Avant les arbitrages et preuves correspondants :

- aucun raccordement, appelant ou migration dépendant de `InvoiceSchema`;
- aucune fusion, comparaison destructive ou suppression d’un des deux
  stockages Qdrant;
- aucune écriture ou modification Neo4j tant que l’accès read-only, le schéma
  et la provenance ne sont pas clarifiés;
- aucune suppression ou fusion des quatre variantes `agent_skills`;
- aucun BankingRecord figé avec une propriété de compte, un signe d’allocation,
  une devise, une tolérance, une partie ou une règle de remise supposée;
- aucune promotion Oracle vers un stockage ou registre officiel local;
- aucune publication de skill candidat;
- aucune mise en production d’une cible sans tests E2E, sécurité, reprise,
  validation humaine et approbation locale.

Ces interdictions ne remplacent pas la mission future : elles délimitent les
travaux qui ne peuvent pas commencer sans décision ou preuve.

## 17. Première réponse attendue de Gemini dans une nouvelle session

Le message prêt à copier ci-dessous est le seul travail attendu de Gemini à la
première prise de contexte : lire, confirmer, résumer et signaler les limites.
Ne proposer ni code, ni modification, ni migration lors de cette réponse.

> Lis intégralement `06_GEMINI_HANDOVER.md`, puis confirme que tu as identifié
> les sources canoniques et leur hiérarchie. Résume l’état réel actuel de
> DSH-Core en dix points maximum, en séparant explicitement faits observés,
> cibles validées et éléments bloqués. Cite les blocages prioritaires et les
> incertitudes de dates ou de relevés qui empêchent une conclusion ferme.
> Confirme que tu ne modifieras aucun fichier, code ou base sans mission
> explicite et périmètre autorisé. Pour cette première réponse, ne propose
> aucun code, aucun changement, aucune migration et aucune nouvelle
> architecture; ne reproduis aucun secret. Si une source ne peut pas être lue,
> indique-le au lieu de supposer son contenu.

## 18. Dates documentaires à vérifier

Plusieurs sources canoniques portent la date documentaire du **20 octobre
2026**; certains inventaires mentionnent les **19 et 20 octobre 2026**, et le
journal contient également des dates documentaires antérieures. Ces dates
doivent être conservées comme métadonnées des documents, mais elles ne prouvent
pas automatiquement la date réelle d’exécution, de génération ou d’observation.

Avant tout usage chronologique ou toute affirmation qu’un audit, test,
déploiement, changement de code ou relevé de base a réellement eu lieu à une
date donnée :

- vérifier la source, son horodatage et le contexte de production;
- distinguer la date inscrite dans le document de la date du relevé runtime;
- signaler les dates futures, incohérentes, non vérifiées ou issues d’une
  copie;
- ne pas recalculer ou corriger une date sans preuve et mission explicite.

En attendant cette vérification, citer les dates comme « dates documentaires »
ou « dates rapportées par l’inventaire », et non comme preuve certaine
d’exécution.
