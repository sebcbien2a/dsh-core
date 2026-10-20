# 03 — JOURNAL CHRONOLOGIQUE DSH-CORE

Ce journal consigne les audits, corrections et décisions d'architecture. Une
architecture validée ou une cible décrite ne signifie pas que son code, ses
connecteurs ou ses stockages sont déjà implémentés.

## 15 octobre 2026 — état de référence

- Un état des lieux du dépôt, des points d'entrée, des composants et des
  stockages locaux est établi. Il sert de photographie de référence, et non de
  preuve que toutes les briques décrites sont raccordées.

## 19 octobre 2026 — audits et corrections

- Les audits techniques sont conduits et consolidés : imports et contrats,
  pipelines réellement raccordés, architecture, stockages et convergence
  locale/Oracle. Les constats séparent les composants actifs, partiels, non
  raccordés et les cibles.
- Trois corrections de code sont appliquées :
  1. `api.py` appelle `router.route_prompt` — défini dans le routeur — au lieu
     du symbole absent `router_intelligent`; l'indication d'image est transmise
     sous la forme attendue par cette fonction.
  2. `agents/research_agent.py` utilise
     `SkillRouter.retrieve_skill_for_task` et consomme son résultat comme une
     chaîne, conformément à la méthode et au type réellement définis.
  3. `services/ingestion_manager.py` renseigne `error_log` depuis le champ
     `message` renvoyé par `InvoiceAgent`, et non depuis `last_error`, absent
     dans cette réponse.
- Le contrat `InvoiceSchema` est gelé : aucune substitution par `InvoiceData`,
  aucun renommage et aucune modification de contrat ne sont autorisés avant
  qu'une compatibilité complète avec le JSON demandé par l'extraction soit
  établie. Le symbole reste non défini dans le schéma actuel.
- La convergence local/Oracle est retenue comme orientation : DSH-Core local
  reste la source de vérité; Hermes Desktop est l'interface centrale et
  Hermes Oracle un satellite disponible en continu. Cette décision
  d'architecture ne vaut pas validation d'une synchronisation technique
  déployée.

## 20 octobre 2026 — audits terminés et architectures consolidées

### Skills

- L'inventaire ne relève qu'un skill `ping_qdrant` dans le registre JSON et un
  skill de facture `invoice_smtrt_sas` dans `workspace/skills/`.
- Qdrant contient quatre variantes distinctes visant la même tâche
  d'extraction de TVA : doublons fonctionnels probables, sans égalité stricte
  des contenus démontrée. `skills_catalog` est vide; les métriques
  `teacher_student_metrics` ne sont pas des définitions de skills.
- Aucun `SKILL.md` métier DSH-Core, ni configuration Hermes ou SkillOpt, n'est
  trouvé dans le périmètre du dépôt. La gouvernance cible des skills est
  décidée et consignée plus bas; aucun skill n'est fusionné ni supprimé par
  l'audit.

### Qdrant et Neo4j

- Qdrant est joignable; huit collections totalisent 23 points dans le relevé
  d'audit, dont quatre dans `agent_skills` et seize dans
  `teacher_student_metrics`. Les collections et les données observées ne
  prouvent pas, à elles seules, que tous les chemins applicatifs sont
  raccordés.
- Deux répertoires de stockage Qdrant volumineux sont présents. Leur lien est
  jugé probable, mais ils ne sont pas démontrés comme copies identiques; le
  stockage secondaire n'est pas exposé par une instance active. Aucune
  synchronisation ni suppression de données n'est décidée.
- Neo4j est accessible comme service. Un relevé d'architecture rapporte trois
  nœuds (`Fournisseur`, `Facture`, `MouvementBancaire`) et deux relations
  (`A_EMIS`, `REGLE`), sans origine attribuée. La cartographie de stockage
  constate toutefois un refus `401` lors de sa tentative de lecture et ne
  certifie pas le contenu du graphe. Les repositories présents ne sont pas
  identifiés comme writers de ces données et leur schéma diffère du schéma
  observé.

### Architecture bancaire

- L'architecture bancaire est validée au niveau des responsabilités et des
  relations : ingestion distincte de la facture, parsing OFX par `ofxparse` et
  MT940 par `mt940`, mouvement bancaire rattaché à un compte, société résolue
  via ce compte, et allocations de rapprochement portées par des relations
  permettant le N↔N.
- Les identités stables des sociétés, comptes, clients et fournisseurs
  relèvent de leurs référentiels; les faits bancaires restent sur le mouvement
  et chaque allocation conserve son montant et son audit. Cette validation
  est conceptuelle : la branche bancaire reste `pending_implementation`, le
  schéma bancaire courant est vide et aucun BankingAgent ni workflow de
  rapprochement bancaire n'est raccordé.
- XGrammar est réservé à la contrainte de forme d'une éventuelle sortie
  générée; sa compatibilité avec le moteur d'inférence reste à valider. Les
  parseurs ne réalisent pas le rapprochement.

### Hermes local + Oracle, staging et synchronisation

- L'architecture validée conserve **DSH-Core local comme unique source de
  vérité métier**. **Hermes Desktop** est l'interface générale locale;
  **Telegram** est l'interface mobile avec cinq canaux : `PRESSE`,
  `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL` et `DOCUMENTS`.
- Hermes encapsule DSH-Core et fournit l'interface, sans remplacer LangGraph
  ni les services métier. Les traitements local et Oracle sont
  complémentaires. Oracle peut analyser les pièces et proposer des
  rapprochements pendant la nuit, mais écrit uniquement dans un staging
  réversible. Toute promotion vers les données officielles est effectuée
  localement.
- Trois espaces de travail sont validés :
  1. tableau général des pièces comptables;
  2. tableau de validation locale avant ventilation;
  3. tableau général de rapprochement bancaire avec allocations N↔N.
- Le retour Oracle → local suit une synchronisation **logique et versionnée**,
  décrite par un manifeste. Chaque manifeste porte `manifest_id`, `version`,
  `origine`, hashes, schéma, modèle d'embedding et dimension; il contient une
  entrée par document, avec ses chunks, texte extrait, métadonnées et artefacts.
  Il ne s'agit pas d'une réplication physique : les dossiers `qdrant_storage`
  et `neo4j_data` ne sont jamais copiés directement.
- La validation et la publication sont locales. Une entrée suit les statuts
  `PENDING`, `ACCEPTED`, `ACCEPTED_REEMBED`, `DUPLICATE_SKIPPED`,
  `HUMAN_REVIEW_REQUIRED`, `REJECTED` et `PUBLISHED`. Aucun `DELETE`
  automatique n'est permis. Un conflit bloque l'entrée sous `SYNC_CONFLICT`;
  une version locale `PUBLISHED` reste prioritaire sur une proposition Oracle
  `CANDIDATE`.
- Les décisions portent sur le format du manifeste et les règles de
  synchronisation. Restent à implémenter le code du manifeste, le transport
  concret des lots, les tests de reprise et d'idempotence, ainsi que
  l'interface locale de résolution des conflits.

### Gouvernance des skills

- Le local conserve le registre canonique des skills publiés. Oracle peut
  produire des skills candidats, mais ne publie pas directement dans ce
  registre.
- Le cycle de vie validé est : `CANDIDATE`, `TESTING`, `APPROVED`,
  `PUBLISHED`, `REJECTED`, `RETIRED`. SkillOpt teste et optimise les candidats
  avec une validation de non-régression avant approbation et publication.
- Oracle ne reçoit que les versions publiées après validation locale.

### Politique des modèles

- Le choix des modèles est défini par rôle (vision, base, expert, superviseur),
  et non par un unique `AI_MODE` global. Des modèles OpenRouter gratuits peuvent
  être utilisés pour les rôles vision, base et expert.
- Un fallback local Ollama est obligatoire. La disponibilité et la
  compatibilité des modèles retenus sont vérifiées au déploiement.
- Claude supervise systématiquement le bootstrap de tout nouveau skill.
  Lorsque le skill gagne en maturité, la supervision peut devenir partielle ou
  être déclenchée sur exception.
- La spécification maîtresse décrit, pour le rapprochement futur, une cible de
  score avec validation automatique à partir de 100, revue humaine de 70 à 99
  et mise en attente sous 70. La revue bancaire confirme que ces règles ne
  sont pas encore définies par les composants bancaires présents : ces seuils
  restent une cible documentaire et non un comportement applicatif vérifié.

## Décisions encore bloquées

- **Contrat facture :** résoudre la forme et la compatibilité de `InvoiceSchema`
  sans dégel prématuré; le contrat reconstruit depuis le prompt ne prouve pas
  les types ni les validateurs du modèle absent.
- **Skills :** choisir la voie de stockage de travail entre les formats
  existants (registre JSON, skills `workspace/skills/` et collection Qdrant)
  et traiter les quatre variantes probablement redondantes. Le registre
  canonique local des versions publiées, le cycle de vie et la validation
  SkillOpt sont décidés; leur implémentation et leur convergence restent à
  réaliser.
- **Stockages :** établir si les deux répertoires Qdrant sont des générations
  distinctes avant toute opération; obtenir un accès Neo4j en lecture et
  attribuer l'origine des données avant toute modification.
- **Banque :** arrêter la propriété simple ou partagée d'un compte, les cibles
  rapprochables, les devises/tolérances/écarts et états d'allocation, les rôles
  et identités des contreparties, ainsi que le périmètre des remises de chèques
  et leur composition.
- **Oracle et synchronisation — implémentation :** coder le manifeste décidé,
  le transport concret des lots, les tests de reprise et d'idempotence, et
  l'interface de résolution des conflits `SYNC_CONFLICT`. La synchronisation
  logique versionnée, les statuts, l'absence de copie directe des dossiers de
  bases, l'interdiction du `DELETE` automatique et la séparation staging
  Oracle / publication locale ne sont pas des décisions en attente.

## Sources consolidées

- [dsh core complet.md](./dsh%20core%20complet.md)
- [dsh_core_master_specification_2026_2027.md](./dsh_core_master_specification_2026_2027.md)
- [02_ROADMAP.md](./docs/02_ROADMAP.md)
- [DSH_CORE_CURRENT_STATE.md](./DSH_CORE_CURRENT_STATE.md)
- [PIPELINE_OFFICIEL.md](./PIPELINE_OFFICIEL.md)
- [04_AUDIT_ARCHITECTURE.md](./04_AUDIT_ARCHITECTURE.md)
- [05_DECISIONS_ARCHITECTURE.md](./05_DECISIONS_ARCHITECTURE.md)
- [SKILLS_DUPLICATES_INVENTORY.md](./SKILLS_DUPLICATES_INVENTORY.md)
- [DATABASE_STORAGE_MAP.md](./DATABASE_STORAGE_MAP.md)
- [BANKING_ARCHITECTURE.md](./BANKING_ARCHITECTURE.md)
- [BANKING_RECORD_SPEC.md](./BANKING_RECORD_SPEC.md)
- [BANKING_SCHEMA_REVIEW.md](./BANKING_SCHEMA_REVIEW.md)
- [BANKING_RELATIONS_REVIEW.md](./BANKING_RELATIONS_REVIEW.md)

## 15 octobre 2026 — audit Neo4j authentifié en lecture

- L'authentification existante a réussi sans afficher le secret; l'inventaire
  a été exécuté avec `cypher-shell --access-mode read`.
- L'audit de la base `neo4j` a relevé 3 nœuds (`Facture`,
  `Fournisseur`, `MouvementBancaire`) et 2 relations (`A_EMIS`, `REGLE`).
  Aucune valeur de propriété métier n'a été reproduite.
- Le schéma runtime diverge des repositories DSH-Core, qui utilisent
  principalement des labels anglais. La provenance des nœuds reste inconnue
  et aucun writer applicatif n'est attribué.
- Aucune écriture ni migration n'a été effectuée. Le ticket P1-1 est
  techniquement exécuté pour l'accès et l'inventaire read-only; l'édition
  Community n'a pas permis de certifier un rôle exclusivement read-only.
- La définition d'un mécanisme permanent de lecture seule reste nécessaire;
  aucune écriture Neo4j n'est autorisée avant cette définition.
