# Audit Architecture V2 Argon

## Statut

- L'audit externe Argon est terminé.
- L'architecture trois machines est jugée viable sous conditions.
- Ce document est la synthèse consolidée ; le rapport complet est conservé
  hors dépôt ou dans l'historique utilisateur.
- Les conclusions et affirmations externes ne sont pas toutes prouvées.
- Ne pas relancer l'audit Argon.

## Éléments confirmés dans DSH-Core

- `LocalMasterWriter` atomique et récupérable : commit `716d23d`.
- 332 tests ont été validés lors de cette étape ; aucun test n'a été lancé pour
  la présente synthèse.
- Les contrats Pydantic comptables et bancaires sont présents.
- Des composants métier existent, mais leur raccordement reste incomplet.
- Hermes Desktop 0.22.4 est installé ; les approbations sont manuelles,
  cron/single-query/unattended sont refusés et aucun MCP/plugin n'a été ajouté.
- Qdrant, Neo4j, Mem0 et LangGraph restent partiellement raccordés.
- Hermes n'a pas encore accès à `A:\dsh-core`.

## Architecture retenue

- PC principal : autorité comptable, Git, Skills et orchestration métier.
- Oracle : services 24/7, veille et staging réversible.
- Mini PC salon : Hermes Desktop local, voix, médias et pilotage local ; aucune
  donnée comptable n'y est conservée.
- DeepSeek Harness est un outil externe, à intégrer sur mesure par un Skill
  Hermes ; ce n'est pas un plugin Hermes natif.
- `DSH_HOME` reste hors du dépôt ; workspaces isolés sous
  `A:\dsh-workspaces` et sorties Hermes sous `A:\dsh-hermes-output`.
- Les synchronisations utilisent des manifestes logiques versionnés. Aucune
  copie physique des stockages Qdrant ou Neo4j.
- Git est canonique pour les `SKILL.md` ; Qdrant est reconstructible ; Mem0
  n'est jamais la vérité comptable.
- Seul le PC principal peut publier les données comptables.

## Routage des modèles — hypothèse à tester

- Modèle local léger pour le fonctionnement hors ligne.
- Nemotron Ultra comme modèle de préparation, décomposition et RAG courant.
- DeepSeek V4.1 Flash comme expert, exécutant complexe et moteur de coding.
- Ne transmettre à DeepSeek que le contexte RAG utile.
- Mesurer les coûts et le taux d'escalade.
- Ne pas utiliser un modèle gratuit avec des données comptables confidentielles
  sans validation préalable de sa politique de données.

## Corrections proposées par Argon

- Vérifier XGrammar par rapport au mécanisme réellement utilisé par Ollama
  avant toute correction documentaire.
- Limiter la stack du mini PC 16 Go ; ne pas prendre un modèle 27B comme voie
  temps réel normale sur 8 Go de VRAM.
- Dix documents ne constituent pas un benchmark de production ; calibrer les
  seuils sur un corpus plus large.
- Séparer strictement le développement agentique de l'ingestion documentaire.

## Réserves — à vérifier, non établies comme faits

- Noms et disponibilité des modèles ; quotas OpenRouter ; tarifs.
- Compatibilité précise XGrammar, GBNF, Ollama et modèles.
- Consommation RAM/VRAM et délais de traitement.
- Taux de confiance, pourcentages d'escalade et estimations de tokens.

## État réel de P1-5 et P1-7

- **P1-5 — TERMINÉ ET PROUVÉ (artefacts présents ; tests non rejoués ici).**

  `schemas/skill_manifest_schema.py` définit notamment `skill_id`, `version`,
  `status` et `content_hash`; `tests/test_skill_manifest_schema.py` couvre le
  contrat. Le cycle de vie est documenté dans `SKILLS_REGISTRY_ARCHITECTURE.md`
  et implémenté avec des tests dans `services/skill_lifecycle_service.py` et
  `tests/test_skill_lifecycle_service.py`. Les commits ciblés incluent
  `e28d15a` et `af7cc04`. La roadmap marque encore P1-5 « À FAIRE » et la
  documentation d'architecture indique que l'implémentation n'a pas commencé :
  ces statuts sont obsolètes par rapport aux artefacts présents. Aucune
  exécution de tests n'a été faite pour cette vérification.
- **P1-7 — PARTIELLEMENT TERMINÉ.** `BANKING_RELATIONS_REVIEW.md` existe et
  les schémas/tests bancaires sont présents, notamment depuis `716d23d`.
  Toutefois, les décisions de propriété des comptes, cibles rapprochables,
  règles d'allocation, devises, états, parties, remises et références restent
  listées comme requises ; elles ne sont pas présentées comme signées. La
  revue ne donne pas pour chaque point un propriétaire et une condition de
  résolution explicites. Les critères de clôture ne sont donc pas prouvés.
- **P1-8 reste actif** ; le handoff cible d'abord le confinement Hermes en
  lecture seule.

## Prochaine étape

Terminer le confinement Hermes, enregistrer un contexte DSH-Core assaini en
lecture seule et vérifier qu'Hermes ne peut pas écrire dans `A:\dsh-core`.
Ensuite seulement, tester Mem0 et le prototype DeepSeek Harness dans une
sandbox.
