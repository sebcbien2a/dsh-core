# HANDOFF DSH-CORE

## Référence du projet

- dépôt : `A:\dsh-core`
- roadmap : [docs/02_ROADMAP.md](./02_ROADMAP.md)
- journal : [docs/03_JOURNAL.md](./03_JOURNAL.md)
- dernier commit : `716d23d`
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

## Oracle

- Compatibilité contractuelle préparée.
- `ActorSystem.ORACLE` et le verrou ORACLE sont disponibles.
- Aucune table, connexion, transaction ou synchronisation Oracle réelle créée.

## Prochaine étape

P1-8 — Installer et connecter Hermes Agent Desktop avec une mémoire Mem0 locale,
initialement en lecture seule sur le dépôt.

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
