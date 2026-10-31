# DSH-CORE — PAQUET DE CONTEXTE GEMINI

## Objet et limites d’utilisation

Ce document rassemble le contexte utile à une nouvelle session Gemini. Il
résume les sources canoniques et un audit Neo4j actualisé. Il ne remplace pas
les preuves runtime plus récentes, les arbitrages de l’architecte ni les
documents complets lorsqu’une vérification détaillée est nécessaire.

Gemini intervient comme mémoire de sécurité, contre-auditeur et sous-traitant
ponctuel de tâches explicitement autorisées. Il n’est pas l’architecte
décisionnaire, n’a aucune autorité de publication et ne modifie aucun fichier,
code, configuration ou base sans mission explicite et périmètre autorisé.

Pour chaque affirmation, distinguer :

- **Fait observé** : vérifié directement dans le code ou un service, avec la
  portée et la date connues.
- **Cible validée** : destination décidée, qui n’est pas nécessairement
  implémentée.
- **Bloqué** : contrat ou décision nécessaire encore absent.
- **État inconnu** : preuve insuffisante ou relevés divergents.

Ne jamais présenter un test isolé comme un flux de bout en bout, ni déduire
l’état d’un service à partir de la seule documentation.

## Hiérarchie des sources

Pour toute nouvelle mission, lire les sources pertinentes et appliquer cet
ordre :

1. État réellement observé dans le code et les services, avec relevé
   reproductible et périmètre identifié.
2. `DSH_CORE_CURRENT_STATE.md` pour la photographie factuelle; ses relevés
   doivent être actualisés lorsque nécessaire.
3. `dsh core complet.md` pour la vision fonctionnelle canonique.
4. `dsh_core_master_specification_2026_2027.md` pour les contrats et décisions
   techniques validées.
5. `03_JOURNAL.md` pour la chronologie documentaire des audits et décisions.
6. La Roadmap V2 dans `docs/02_ROADMAP.md`, à partir de la ligne 9988 dans la
   version inventoriée. Ses phases remplacent les priorités historiques
   incompatibles.
7. Rapports spécialisés : `NEO4J_INVENTORY.md`, `DATABASE_STORAGE_MAP.md` et
   les autres audits du dépôt.
8. Les blocs historiques antérieurs de la roadmap, uniquement comme traces de
   l’évolution du raisonnement.

En cas de conflit, citer les sources et les deux affirmations. Retenir la
preuve observée pour décrire l’existant, et la spécification canonique pour
décrire la cible. Ne pas arbitrer silencieusement.

## Vision cible validée

- DSH-Core local reste l’unique source de vérité métier et le lieu de
  validation et publication officielles.
- Hermes Desktop fournit l’interface générale locale et encapsule DSH-Core;
  il ne remplace ni les services métier ni LangGraph.
- LangGraph est l’orchestrateur métier unique de la cible.
- Oracle est un satellite 24/7 qui prépare des analyses et propositions dans
  un staging réversible; il n’écrit jamais directement dans la vérité locale.
- Telegram est l’interface mobile cible avec cinq canaux : `PRESSE`,
  `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL` et `DOCUMENTS`.
- La publication des décisions et des skills demeure locale et contrôlée.
- Le système visé est multi-entreprises, documentaire et comptable, avec
  apprentissage supervisé à partir de corrections validées.
- Une similarité, un score, un souvenir ou une proposition de modèle ne vaut
  jamais validation métier.

Ces éléments décrivent la cible; Hermes, Oracle et Telegram ne sont pas
constatés comme installés ou raccordés dans le dépôt audité.

## État réel synthétique

### API et facture

- Le chemin facture le plus raccordé est
  `n8n_api.py — POST /api/v1/ingest` → `agents.invoice_agent.InvoiceAgent` →
  Ollama Vision → validation par l’`InvoiceData` locale de l’agent → réponse
  JSON à n8n. Il ne constitue pas un pipeline métier complet et n’établit pas
  de persistance facture dans Qdrant ou Neo4j.
- Plusieurs services de profilage, routage métier, rapprochement, fiscalité et
  skills existent mais ne sont pas raccordés au webhook actif; le graphe
  facture backend comporte des nœuds simulés ou placeholders.
- Trois corrections de code ont été testées de manière isolée avec des doubles
  sans base réelle ni service externe, puis commitées séparément :
  - `api.py` utilise `route_prompt` au lieu de `router_intelligent`;
  - `ResearchAgent` appelle `retrieve_skill_for_task` et gère `str | None`;
  - `IngestionManager` lit `message` plutôt que `last_error`.
- Le commit documentaire est `7d0f27d`; le commit des corrections est
  `658dc45`. Les validations rapportées comprennent `py_compile` et les tests
  isolés décrits ci-dessus. Elles ne prouvent pas le comportement complet en
  production.

### Mémoire, orchestration, interfaces et données de référence

- Qdrant, Neo4j, n8n, Langfuse et Open WebUI ont été observés actifs dans des
  relevés antérieurs. Un statut de service est ponctuel et doit être revérifié.
- Mem0 est présent dans une façade locale et peut fournir du contexte, mais
  cette voie est partielle; sa collection était vide dans le relevé indiqué
  par l’état courant.
- LangGraph est présent, mais l’orchestration métier complète n’est pas
  raccordée; certains nœuds sont élémentaires, simulés ou placeholders.
- Langfuse est actif au niveau infrastructure dans le relevé; son
  instrumentation applicative reste partielle.
- Aucun référentiel MasterData Excel complet ni flux Excel vers les
  référentiels n’est établi.
- Hermes Desktop, Hermes Oracle et l’intégration Telegram ne sont pas
  constatés dans le dépôt audité.

## Audit Neo4j actualisé — PARTIAL_READ_ONLY

Cet audit remplace, pour le périmètre observé, les anciens constats où la
lecture authentifiée était bloquée. La mention `PARTIAL_READ_ONLY` signifie
que l’authentification et plusieurs requêtes de lecture ont réussi, mais que
les privilèges précis et la provenance métier restent non établis.

- Instance : conteneur `dsh-neo4j`, Neo4j Community `2026.09.0`; bases `neo4j`
  et `system` observées `online`. La base interrogée était `neo4j`.
- L’authentification déjà configurée a réussi sans afficher ni reproduire de
  secret. La session `cypher-shell` utilisait `--access-mode read`.
- L’identité rapportée est `neo4j`. Les rôles sont retournés comme `NULL`.
- L’édition Community ne permet pas l’inspection des privilèges via les
  commandes `SHOW PRIVILEGE`; les privilèges effectifs ne sont donc pas
  complètement établis. Aucune écriture n’a été testée.
- Comptage observé : **3 nœuds et 2 relations**.
- Labels : `Facture`, `Fournisseur`, `MouvementBancaire`.
- Types de relations : `A_EMIS`, `REGLE`.
- Propriétés structurelles observées, sans leurs valeurs :
  - `Facture` : `date`, `montant`, `numero`;
  - `Fournisseur` : `nom`, `region`;
  - `MouvementBancaire` : `date`, `id`, `montant`.
- Aucune propriété de relation n’a été observée. Aucune contrainte n’a été
  retournée. Deux index techniques de lookup de tokens, nœuds et relations,
  sont `ONLINE`.
- Les repositories/services DSH-Core utilisent principalement un schéma en
  anglais (`Invoice`, `Supplier`, `Document`, `BELONGS_TO`, `CONTAINS`) qui
  diverge du graphe runtime observé.
- Aucun writer n’est attribué aux données actuelles. La provenance des données
  est inconnue.
- Aucun nœud, relation, propriété, index ou contrainte n’a été modifié; aucune
  migration ou écriture n’a été effectuée. Le conteneur n’a pas été redémarré.

Les comptes et métadonnées sont un relevé ponctuel, pas une garantie de l’état
futur. Toute action nécessitant l’origine ou l’exactitude des données reste
bloquée jusqu’à obtention de preuves.

## Qdrant

- L’instance active documentée utilise le stockage racine du dépôt; un
  répertoire Qdrant secondaire existe sous `docker` sans instance active
  associée au relevé.
- Les deux stockages diffèrent par taille et nombre de fichiers. Leur identité,
  parenté ou équivalence logique n’est pas prouvée. Aucun rapprochement
  destructif, fusion ou suppression n’est autorisé sur cette base.
- Huit collections ont été rapportées. Les relevés indiquent notamment :
  `agent_skills` (4 points), `skills_catalog` (0),
  `teacher_student_metrics` (16), `mem0` (0) et `mem0migrations` (0).
  D’autres comptes de points ne sont pas tous certifiés au même instant; ne
  pas les agréger en un instantané unique.
- Les quatre contenus `agent_skills` ont des empreintes distinctes; leur
  redondance fonctionnelle est probable mais aucun doublon strict n’est établi.
- `teacher_student_metrics` contient des métriques, pas des définitions de
  skills; la création ou réutilisation effective de skills n’est pas confirmée.
- Ne jamais copier directement `qdrant_storage` pour synchroniser Oracle et le
  local. La comparaison des deux stockages doit rester en lecture seule.

## InvoiceSchema gelé

- Aucune définition exécutable de `InvoiceSchema` n’a été retrouvée.
- `schemas/invoice_schema.py` et `agents/invoice_agent.py` définissent deux
  `InvoiceData` distincts; leur nom commun ne prouve aucune compatibilité.
- Les appelants dépendant de `InvoiceSchema` restent bloqués. Le JSON demandé
  par un prompt ou un document de reconstruction ne prouve pas les types,
  champs obligatoires, validateurs ou valeurs par défaut du schéma absent.
- Maintenir le gel. Ne pas substituer, renommer ou reconstruire le schéma sans
  arbitrage explicite et validation du contrat.

## Banque

L’état observé est incomplet : `IngestionManager` reconnaît
`BANK_STATEMENT`, mais sa branche retourne `pending_implementation`; le schéma
bancaire était vide au relevé. Aucun `BankingAgent`, parseur OFX/MT940,
ledger bancaire ou rapprochement N↔N opérationnel n’a été établi.

La cible validée prévoit `BankingRecord`, montants décimaux signés (débit
négatif, crédit positif), comptes et sociétés référencés, parseurs OFX via
`ofxparse` et MT940 via `mt940`, voies CSV et PDF distinctes, paiements
partiels/groupés et allocations N↔N. Elle couvre également chèques, remises,
virements, prélèvements, LCR, retours et contre-passations. Propriété des
comptes, identités, devises, frais, écarts, tolérances et règles d’allocation
restent à arbitrer; ne pas les inventer.

## Skills et apprentissage

- État observé : `skills/registry.json` comprend `ping_qdrant`, dont le script
  ne démontre pas un ping réseau; `workspace/skills/invoice_smtrt_sas.json`
  existe. Qdrant `skills_catalog` est vide et quatre contenus résident dans
  `agent_skills`.
- SkillOpt et le registre canonique local ne sont pas constatés comme
  implémentés de bout en bout.
- Cible : le registre des skills publiés est local. États :
  `CANDIDATE`, `TESTING`, `APPROVED`, `PUBLISHED`, `REJECTED`, `RETIRED`.
- SkillOpt teste et optimise avec validation de non-régression. Claude
  supervise le bootstrap de chaque nouveau skill; la supervision peut devenir
  partielle ou se déclencher sur exception selon la maturité démontrée.
- Oracle peut produire des candidats, mais ne publie pas. Oracle ne reçoit que
  les versions publiées après validation locale. Aucune fusion ou suppression
  de skill candidat n’est autorisée sans mission explicite.

## Blocages prioritaires

1. Provenance et autorité des données Neo4j; privilèges effectifs non
   inspectables dans l’édition actuelle et divergence entre schéma runtime et
   repositories.
2. Relation entre les deux stockages Qdrant et évaluation non destructive des
   quatre variantes `agent_skills`.
3. Contrat `InvoiceSchema` et compatibilité avec les modèles existants.
4. Décisions métier bancaires (comptes, allocations, devises, tolérances,
   frais, parties, annulations, remises).
5. Choix et implémentation du registre canonique des skills et du processus
   SkillOpt.
6. Contrats des trois tableaux de travail : pièces comptables, validation
   locale avant ventilation et rapprochement bancaire N↔N.
7. Audit des configurations et secrets, sans jamais lire ou reproduire leur
   valeur dans les rapports.
8. Pipeline complet, observabilité et intégration MasterData non démontrés.

## Oracle, manifeste et synchronisation

Les règles sont décidées comme cible; elles ne sont pas encore implémentées :

- Oracle écrit uniquement dans un staging réversible; la promotion officielle
  est locale.
- Synchronisation logique et versionnée, jamais copie physique des dossiers de
  base.
- Manifeste : `manifest_id`, `version`, `origine`, hashes, schéma, modèle
  d’embedding et dimension; une entrée par document avec chunks, texte extrait,
  métadonnées et artefacts.
- Statuts : `PENDING`, `ACCEPTED`, `ACCEPTED_REEMBED`,
  `DUPLICATE_SKIPPED`, `HUMAN_REVIEW_REQUIRED`, `REJECTED`, `PUBLISHED`.
- Conflits bloqués sous `SYNC_CONFLICT`, résolution locale; une version locale
  `PUBLISHED` est prioritaire sur une proposition Oracle `CANDIDATE`.
- Reprises idempotentes et traçables; aucun `DELETE` automatique.
- Code du manifeste, transport concret des lots, tests de reprise/idempotence
  et interface locale de résolution des conflits restent à réaliser.

## Interdictions pour Gemini

- Ne rien modifier sans mission explicite et périmètre autorisé.
- Ne modifier aucune base, configuration ou service sans autorisation
  spécifique; par défaut, rester en lecture seule.
- Ne jamais afficher, recopier, rechercher ou committer une valeur de secret,
  mot de passe, jeton ou clé API.
- Ne supprimer, fusionner, restaurer, migrer ou synchroniser aucun stockage,
  conteneur, collection, point ou fichier sans autorisation explicite.
- Ne pas remplacer `InvoiceSchema` par `InvoiceData`.
- Ne pas publier de skill candidat ni promouvoir une proposition Oracle.
- Ne pas présenter une cible comme implémentée sur la seule base de documents.
- Ne pas inventer de règle bancaire, origine des données, résultat de test ou
  privilège non vérifié.
- Ne pas nettoyer, réinitialiser ou écraser les changements préexistants Git.

## Prochain sprint

La Roadmap V2 fixe l’ordre suivant :

1. Tester les trois corrections de code.
2. Obtenir l’accès Neo4j en lecture authentifiée.
3. Comparer les deux stockages Qdrant sans modification.
4. Évaluer les quatre variantes `agent_skills`.
5. Décider du registre canonique des skills.
6. Résoudre les décisions bancaires.
7. Résoudre `InvoiceSchema`.
8. Figer les contrats des trois tableaux.
9. Implémenter seulement ensuite les premiers schémas et services.

**Avancement connu dans ce contexte :** les trois corrections ont passé les
tests ciblés isolés et sont commitées dans `658dc45`. L’accès Neo4j en lecture
authentifiée a réussi et un inventaire a été obtenu avec le statut
`PARTIAL_READ_ONLY`; privilèges et provenance demeurent incomplets. Les autres
étapes ne sont pas déclarées terminées par ce paquet. Vérifier les résultats
avant de poursuivre.

## Dates documentaires à vérifier

Les documents source portent plusieurs dates, notamment les 19 et 20 octobre
2026. Les conserver comme métadonnées documentaires; elles ne prouvent pas,
seules, la date réelle d’exécution ou d’observation. Vérifier horodatage,
contexte et reproductibilité avant toute affirmation chronologique. Signaler
les dates futures, incohérentes ou non vérifiées sans les corriger par
supposition.

## Première réponse attendue de Gemini

À la première prise de contexte, lire ce paquet puis consulter les sources
canoniques pertinentes. Répondre en dix points maximum en séparant faits
observés, cibles validées et éléments bloqués. Mentionner l’audit Neo4j
`PARTIAL_READ_ONLY`, les limites de provenance et de privilèges, ainsi que les
dates/relevés incertains. Confirmer qu’aucun fichier, code, configuration ou
base ne sera modifié sans mission explicite et périmètre autorisé. Ne proposer
aucun code, changement, migration ou nouvelle architecture; ne reproduire
aucun secret. Si une source n’est pas accessible, le signaler au lieu d’en
supposer le contenu.
