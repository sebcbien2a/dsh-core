# HANDOFF DSH-CORE

## Référence du projet

- dépôt : `A:\dsh-core`
- roadmap : [docs/02_ROADMAP.md](./02_ROADMAP.md)
- journal : [docs/03_JOURNAL.md](./03_JOURNAL.md)
- architecture cible : [docs/DSH_CORE_AI_ARCHITECTURE_V2.md](./DSH_CORE_AI_ARCHITECTURE_V2.md)
- dernier commit : `81a3a89`
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
  roadmap n’en conserve qu’une synthèse candidate, à auditer avant développement.

## Oracle

- Compatibilité contractuelle préparée.
- `ActorSystem.ORACLE` et le verrou ORACLE sont disponibles.
- Aucune table, connexion, transaction ou synchronisation Oracle réelle créée.

## Prochaine étape

P1-8E : liste blanche Docker de Hermes pour DSH-Core (montages `:ro`
individuels, sorties dans un volume séparé), après validation humaine.

Architecture cible (validée, non installée) : trois machines — PC principal
(vérité comptable, Vision local, LangGraph, Neo4j, Qdrant, Mem0, Hermes),
Oracle (24/7, staging), mini PC salon (vocal, médias). DeepSeek Harness sera
une Skill Hermes de développement isolée (coding, expertise, escalade).

État réel de Hermes : 0.22.4 installé, approbations `manual`, cron /
single-query / unattended en `deny`, aucun MCP ni plugin, `terminal.backend`
`local`, aucun accès DSH-Core accordé.

Ne pas relancer les audits déjà terminés (P1-8A à P1-8D, writer). Ne pas
confondre architecture validée et composants installés.

Contraintes :

- Git et les Markdown restent la vérité architecturale.
- Les tableaux maîtres restent la vérité métier.
- Mem0 stocke le contexte et les décisions.
- Hermes propose mais ne committe pas.
- Aucun accès d’écriture aux tableaux maîtres dans la première version.

Niveau d’effort recommandé : Équilibré.

## Règle documentaire permanente

Pour chaque futur ticket modifiant ou créant du code :

- mettre à jour les Markdown concernés ;
- ajouter une entrée courte et datée dans le journal ;
- mettre à jour le statut de la roadmap ;
- actualiser ce handoff si l’étape change l’architecture ou la prochaine tâche.
