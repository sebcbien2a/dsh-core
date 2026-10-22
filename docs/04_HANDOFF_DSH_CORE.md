# HANDOFF DSH-CORE

## Référence du projet

- dépôt : `A:\dsh-core`
- roadmap : [docs/02_ROADMAP.md](./02_ROADMAP.md)
- journal : [docs/03_JOURNAL.md](./03_JOURNAL.md)
- architecture cible : [docs/DSH_CORE_AI_ARCHITECTURE_V2.md](./DSH_CORE_AI_ARCHITECTURE_V2.md)
- synthèse audit Argon : [docs/05_AUDIT_ARCHITECTURE_V2_ARGON.md](./05_AUDIT_ARCHITECTURE_V2_ARGON.md)
- commit de base observé avant consolidation Argon :
  `3f979a05c149b0497e2ac4d85dae5d715a4fe962`
  (`3f979a0`, `docs: corriger le handoff et les dates d architecture`)
- branche courante : `main`

## État du socle

- Contrats comptables et bancaires définis pour le périmètre actuellement
  implémenté.
- Tableaux maîtres avec paiements personnels, mixtes et partiels.
- Lots LCR et remises de chèques, avec allocations.
- Writer local atomique et récupérable.
- Verrouillage à bail LOCAL et ORACLE.
- Contrôle des versions, idempotence, audit et `StatusHistory`.
- Rollback, recovery et contrôle de l’intégrité des hashes.
- Écritures confinées sous `root_path`.

## Validation

- 332 tests réussis.
- 0 xfail.
- 0 échec.
- Compilation réussie.
- Commit `716d23d`.

## Limites actuelles

- Aucun writer Excel.
- Aucune connexion OneDrive.
- Aucun repository Oracle réel.
- Aucun import multi-format réel.
- Aucune interface humaine.
- Aucune garantie ACID multi-fichiers.
- Hermes et Mem0 non encore connectés.
- `architecture-ia-v2.md` est une source externe reçue, absente du dépôt ; la
  roadmap n’en conserve qu’une synthèse candidate. L'audit Argon est terminé.

## Oracle

- Compatibilité contractuelle préparée.
- `ActorSystem.ORACLE` et le verrou ORACLE sont disponibles.
- Aucune table, connexion, transaction ou synchronisation Oracle réelle créée.

## Prochaine étape

Après validation documentaire, poursuivre le confinement Hermes en lecture
seule : liste blanche Docker de DSH-Core (montages `:ro` individuels, sorties
dans un volume séparé), puis vérifier l'impossibilité d'écrire dans
`A:\dsh-core`.

Architecture cible (validée, non installée) : trois machines — PC principal
(vérité comptable, Vision local, LangGraph, Neo4j, Qdrant, Mem0, Hermes),
Oracle (24/7, staging), mini PC salon (vocal, médias, pilotage local). Seul le
PC principal publie les données comptables. DeepSeek Harness est un outil
externe à intégrer sur mesure par un Skill Hermes, pas un plugin natif.

État réel de Hermes : 0.22.4 installé, approbations `manual`, cron /
single-query / unattended en `deny`, aucun MCP ni plugin, `terminal.backend`
`local`, aucun accès DSH-Core accordé.

Audit Argon terminé ; synthèse dans [docs/05_AUDIT_ARCHITECTURE_V2_ARGON.md](./05_AUDIT_ARCHITECTURE_V2_ARGON.md).
Ne pas relancer l'audit Argon ni les audits déjà terminés (P1-8A à P1-8D,
writer). Ne pas confondre architecture validée et composants installés.

État des tickets vérifié sur les artefacts ciblés :

- **P1-5 — TERMINÉ ET PROUVÉ** : schéma, tests et cycle de vie documenté et
  implémenté présents. Les mentions « À FAIRE » et « implémentation non
  commencée » dans les documents ciblés sont obsolètes ; tests non rejoués
  pendant cette vérification.
- **P1-7 bancaire — PARTIELLEMENT TERMINÉ** : contrat et tests existent, mais
  des décisions métier bloquantes restent ouvertes et non signées. Voir la
  synthèse Argon pour les preuves et réserves.
- **P1-8 — EN COURS** ; le confinement Hermes en lecture seule est la prochaine
  action.

Contraintes :

- Git et les Markdown restent la vérité architecturale.
- Les tableaux maîtres restent la vérité métier.
- Mem0 stocke le contexte et les décisions.
- Hermes propose mais ne committe pas.
- Aucun accès d’écriture aux tableaux maîtres dans la première version.

Niveau d’effort recommandé : Équilibré.

Avant le prochain changement de chat, mettre à jour ce handoff avec le
dernier commit réel, l’état Git et la prochaine action unique.

## Règle documentaire permanente

Pour chaque futur ticket modifiant ou créant du code :

- mettre à jour les Markdown concernés ;
- ajouter une entrée courte et datée dans le journal ;
- mettre à jour le statut de la roadmap ;
- actualiser ce handoff si l’étape change l’architecture ou la prochaine tâche.
