---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 3 — Distillation, Template Skills & Mémoire Fournisseur
status: ACTIVE
upstream_dependencies: [schemas/supplier_identity_schema.py, services/supplier_repository.py, services/template_skill_store.py]
downstream_consumers: [services/supplier_memory_service.py, services/template_skill_store.py]
verification_tests: [tests/test_supplier_enrichment.py, tests/test_supplier_identity_repository.py, tests/test_skill_distillation.py]
contract_version: 1.0.1
---

# Spécification de migration fournisseur → skill

Statut : PROPOSITION. Q1 et Q3 sont décidées ; Q4 reste ouverte. L'implémentation reste conditionnée aux décisions humaines des questions ouvertes (Q2, Q4 à Q10).
Aucun code, aucune donnée et aucun test ne sont modifiés par ce document.

## Sources utilisées

- `docs/PROJECT_HANDOVER.md` (contrat fournisseur international, lignes 114 à 162).
- `schemas/supplier_identity_schema.py` : `SupplierIdentity`, `SupplierIdentifier`, `SupplierLayoutRule`.
- `schemas/skill_manifest_schema.py` : `SkillManifest`, `SkillStatus`.
- `repositories/supplier_identity_repository.py`, `repositories/supplier_registration_repository.py`, `repositories/canonical_skill_repository.py`.
- `services/supplier_registration_service.py`, `services/supplier_registration_coordinator.py`.
- Ancien lot : `services/supplier_repository.py`, `services/invoice_skill_applier.py`, `services/invoice_skill_creator.py`.
- Tests : `tests/test_supplier_identity_repository.py` (l.264), `tests/test_supplier_registration_repository.py` (l.344).

Les contenus de `workspace/suppliers/suppliers.json` n'ont pas été lus pour cette spécification.

## 1. Périmètre

1. Migrer les responsabilités utiles de l'ancien `SupplierRepository` (indexé par SIRET, identifiants `SUP_xxxx`) vers l'architecture actuelle :
   - l'identité reste dans `SupplierIdentityRepository` ;
   - les enregistrements passent par `SupplierRegistrationRepository` et le coordinateur ;
   - la relation fournisseur → skill est créée dans un nouveau composant, `SupplierSkillBinding`.
2. Conservation temporaire des composants anciens (section 10). Ils ne sont ni modifiés ni supprimés par cette étape.
3. Aucune intégration au pipeline métier dans cette étape. Conformément au handover (l.118, 131, 150, 158), le résultat de la migration reste isolé et sans appel depuis le pipeline.
4. L'ancien `SupplierRepository` ne doit être importé par aucun composant actuel. Les tests existants l'imposent et restent inchangés.
5. Hors périmètre : modification de la sémantique de `SupplierIdentity`, calcul TVA/HT/TTC, accès réseau, OCR, exécution de modèle.

## 2. Modèle SupplierSkillBinding (définition, sans implémentation)

| Champ | Type | Règle |
|---|---|---|
| `binding_id` | UUID | Généré à la création. Clé primaire. Immuable. |
| `supplier_id` | UUID | Référence un `SupplierIdentity` existant. Immuable. |
| `skill_id` | UUID | Référence un `SkillManifest.skill_id` existant dans `CanonicalSkillRepository`. Immuable. |
| `skill_version` | chaîne SemVer stricte | Version SemVer stricte de `SkillManifest`, identifiant avec `skill_id` la révision canonique exacte, figée dans le binding pour garantir la reproductibilité. Aucune sélection dynamique par `get_latest`. |
| `enabled` | booléen | Mutable. Défaut `false` à la migration (voir section 6). |
| `provenance_initiale` | objet | Immuable après création. Les confirmations humaines et événements ultérieurs sont conservés uniquement dans `workspace/supplier_skill_bindings/events.jsonl`. |
| `created_at` | datetime UTC | Immuable. |
| `updated_at` | datetime UTC | Mis à jour à chaque mutation autorisée. |
| `disabled_at` | datetime UTC ou nul | Renseigné si et seulement si `enabled` vaut `false` après une désactivation. Effacé à la réactivation. |
| `usage_count` | entier ≥ 0 | Mutable par incrément unique. Jamais décrémenté. |
| `last_used_at` | datetime UTC ou nul | Mutable, jamais antérieur à la valeur précédente. |

Provenance minimale :
- `origin` : `NATIVE` (créé par l'architecture actuelle) ou `LEGACY_MIGRATION`.
- `legacy_supplier_id` : ancien `SUP_xxxx`, si applicable.
- `legacy_skill_name` : ancien `skill_name`, si applicable.
- `legacy_source` : chemin logique de la source (sans contenu) et empreinte SHA-256 du fichier source au moment de la migration.
- `migration_id` : identifiant du journal de migration (section 7).

`provenance_initiale` est immuable après création. Les confirmations humaines et événements ultérieurs relatifs aux bindings sont consignés uniquement dans `workspace/supplier_skill_bindings/events.jsonl`, journal append-only ; ils ne modifient jamais la provenance initiale.

### Propriétaire et persistance (Q1 — DÉCIDÉE)

- Le repository propriétaire est `SupplierSkillBindingRepository`.
- Son fichier d'état est `workspace/supplier_skill_bindings/bindings.json`.
- Son journal append-only est `workspace/supplier_skill_bindings/events.jsonl`. Les confirmations humaines et événements ultérieurs relatifs aux bindings sont consignés uniquement dans ce journal ; `provenance_initiale` n'est jamais modifiée rétrospectivement.
- Il n'existe aucune atomicité globale entre le fichier d'état et le journal. Le protocole de reprise doit détecter et traiter explicitement toute divergence entre eux.

### Contraintes d'unicité

1. `binding_id` unique.
2. Le triplet `(supplier_id, skill_id, skill_version)` est unique. Une même paire fournisseur-skill peut avoir des bindings vers des versions différentes.
3. `supplier_id` et `skill_id` doivent exister et être cohérents au moment de la création ; `skill_version` doit respecter le SemVer strict de `SkillManifest` et désigner une version existante de ce `skill_id`.
4. Seule une version canonique au statut `PUBLISHED` peut être appliquée automatiquement en production.
5. `APPROVED` signifie validée mais non publiée et n'est donc pas applicable en production. `CANDIDATE` et `TESTING` sont réservés aux essais isolés. `REJECTED` et `RETIRED` interdisent toute application.
6. Si la version figée par un binding actif devient `REJECTED` ou `RETIRED`, le binding est conservé pour l'historique mais devient non applicable.
7. Q3 — OPTION B : le binding référence la révision exacte par `(skill_id, skill_version)`. Aucune sélection dynamique par `get_latest` ni sélection silencieuse de la dernière version n'est autorisée. La publication d'une nouvelle version ne modifie jamais automatiquement un binding existant.
8. Pour un même couple `(supplier_id, skill_id)`, au maximum un binding peut avoir `enabled = true`. Plusieurs bindings désactivés peuvent conserver les anciennes versions.
9. Le passage à une autre version exige toujours une proposition explicite, une validation humaine et un événement inscrit dans `events.jsonl` ; l'ancien binding est désactivé et conservé avant la création et l'activation d'un nouveau binding portant la nouvelle `skill_version`. Un fournisseur désactivé (`active = false`) ne peut recevoir aucun nouveau binding actif.

### Immutabilité et mutations

- Champs immuables : `binding_id`, `supplier_id`, `skill_id`, `skill_version`, `created_at`, `provenance_initiale`.
- Mutations autorisées uniquement :
  - activer ou désactiver (`enabled`, `disabled_at`, `updated_at`) ; l'activation est atomiquement refusée si un autre binding de même `(supplier_id, skill_id)` est encore actif ;
  - demander un passage à une nouvelle `skill_version` par proposition explicite, validation humaine et événement append-only dans `events.jsonl` ; la version figée du binding existant n'est pas modifiée et le changement s'effectue par désactivation de l'ancien puis création d'un nouveau binding ;
  - enregistrer un usage (incrément de `usage_count`, mise à jour de `last_used_at`) ;
  - enregistrer une confirmation humaine ou un événement ultérieur uniquement dans `events.jsonl`, sans modifier `provenance_initiale`.
- Aucune suppression physique : un binding est désactivé, jamais supprimé.
- L'écriture de `bindings.json` est atomique par fichier selon la convention JSON actuelle (fichier temporaire, sauvegarde `.bak`, remplacement). `events.jsonl` est append-only ; aucune atomicité globale n'existe entre les deux fichiers.
- Il n'existe aucune atomicité globale entre `bindings.json` et `events.jsonl` ; le protocole de reprise doit détecter et traiter explicitement leur divergence.
- Un enregistrement invalide est refusé sans écrasement du fichier existant.

## 3. Séparation des responsabilités

| Composant | Responsabilité unique | Interdit |
|---|---|---|
| `SupplierIdentity` / `SupplierIdentityRepository` | Identité canonique (UUID, noms, alias, identifiants, adresses). | Porter un skill, un compteur d'usage ou une trace de migration de skill. Les `custom_fields` ne servent pas à porter ces informations. |
| `CanonicalSkillRepository` / `SkillManifest` | Révisions immuables du skill. | Être modifié par un usage ou un rattachement fournisseur. |
| `SupplierSkillBindingRepository` / `SupplierSkillBinding` | `SupplierSkillBindingRepository` possède la relation mutable fournisseur-skill, son état et son usage ; persistance dans `workspace/supplier_skill_bindings/bindings.json`, événements append-only dans `workspace/supplier_skill_bindings/events.jsonl`. | Contenir l'identité fournisseur ou le contenu du skill. |

Règle explicite : les statistiques d'usage ne modifient jamais le skill canonique.
`SkillManifest.usage_count` existe dans le schéma actuel (`skill_manifest_schema.py` l.161), mais une révision est immuable. Le compteur par fournisseur vit uniquement dans `SupplierSkillBinding.usage_count`. La valeur du manifeste reste inchangée par cette étape.

## 4. Rapprochement document → fournisseur

Entrées : identifiants lus dans le document (SIRET, SIREN, TVA, autres), noms imprimés.

Règles :
1. **Identifiant confirmé** : un identifiant dont `human_confirmed` vaut `true`, non vide, retrouvé via `find_by_confirmed_identifier` (clé `pays + type + valeur normalisée`) donne un rapprochement déterministe, si un seul fournisseur actif correspond.
2. **SIREN** : le rapprochement par SIREN n'est déterministe que si un identifiant `SIREN` confirmé existe dans le fournisseur. Aucun SIREN n'est déduit d'un SIRET (handover l.129). Voir question ouverte Q2.
3. **Nom imprimé ou alias** : suggestion uniquement (`suggest_by_name`). Jamais de liaison, de création de binding ni de fusion sur le nom ou l'alias seul (handover l.124).
4. **Identifiant non confirmé** : traité comme une suggestion, jamais comme un rapprochement déterministe.
5. **Aucun fournisseur trouvé** : résultat `NOT_FOUND`. Aucun fournisseur, aucun binding ni aucun skill n'est créé ou appliqué automatiquement. Le document est signalé pour une éventuelle proposition d'enregistrement explicite (`CREATE`) via le service d'enregistrement, avec validation humaine.
6. **Plusieurs fournisseurs correspondent** : résultat `AMBIGUOUS`. Aucun skill n'est appliqué. Une décision humaine désigne le fournisseur ou rejette. Une collision d'identifiants confirmés entre fournisseurs actifs est déjà bloquée par le repository ; un cas multiple signale donc des suggestions par nom ou une incohérence de données.
7. **Fournisseur trouvé, binding absent ou désactivé** : aucun skill appliqué. En production, seule une version au statut `PUBLISHED` est applicable ; `APPROVED` n'est pas encore publiée, `CANDIDATE` et `TESTING` sont réservés aux essais isolés, et `REJECTED` ou `RETIRED` interdisent toute application.
8. **Validation humaine obligatoire** pour toute ambiguïté, toute création d'un binding issu de la migration, et toute réactivation.

## 5. Migration des identifiants

1. Une **table de correspondance explicite** `ancien SUP_xxxx → supplier_id UUID` est le seul moyen de convertir un ancien identifiant. Elle est écrite et validée par un humain. Elle est versionnée dans le journal de migration, avec la provenance de chaque ligne.
2. **Aucune génération implicite** d'UUID à partir de `SUP_xxxx` (pas de hachage, pas de conversion déterministe).
3. **Skills sans `supplier_id`** (anciens skills avec `match_signature`) : ils ne reçoivent aucun binding automatique. Ils sont listés dans un rapport « à traiter », avec leur nom de fichier et leur ancienne signature. Un humain décide du fournisseur cible.
4. **Fournisseur ancien sans identité actuelle** : aucune identité n'est créée silencieusement. La migration produit une proposition `CREATE` explicite, via le service d'enregistrement, avec confirmation humaine et justification. Tant qu'elle n'est pas confirmée, les skills de ce fournisseur restent sans binding.
5. **Collisions d'identifiants** :
   - SIRET ou TVA de l'ancien dépôt déjà porté par un fournisseur actif confirmé : la ligne est classée `CONFLICT`, sans écriture. Un humain décide.
   - Plusieurs anciens `SUP_xxxx` pointent vers le même UUID : refusé, sauf décision humaine tracée.
   - Un même `SUP_xxxx` pointe vers deux UUID : refusé.
   - Les identifiants issus de l'ancien dépôt entrent dans l'identité actuelle comme **non confirmés** (`human_confirmed = false`). Le repository les exclut alors de l'index de collision.

## 6. Données historiques

1. `usage_count` et `last_used` historiques sont considérés **non fiables** : l'ancien `attach_or_update_skill` ne persiste rien (fichier de 166 lignes tronqué avant toute écriture, identique à `backup/v8_6_5_stable`), et le skill de référence montre une régression 3 → 2. À la migration, `usage_count` du binding vaut 0 et `last_used_at` est nul. Les anciennes valeurs sont conservées uniquement dans la provenance, étiquetées « non fiables ».
2. `human_validations` historique n'est **jamais converti** automatiquement en `human_confirmed`. Aucun identifiant ni fournisseur n'est certifié automatiquement par la migration.
3. `verification.status` (`PENDING` / `VERIFIED`) ancien n'a aucune équivalence automatique. Il est conservé tel quel dans la provenance.
4. Chaque donnée migrée porte sa provenance : source, empreinte SHA-256 du fichier, date de migration, identifiant de migration.
5. Les binding issus de la migration sont créés avec `enabled = false` jusqu'à validation humaine explicite.

## 7. Transactions et reprise

Contraintes de base : `SupplierIdentityRepository`, `SupplierRegistrationRepository` et `SupplierSkillBindingRepository` utilisent des fichiers JSON distincts. Pour les bindings, les fichiers sont `workspace/supplier_skill_bindings/bindings.json` et `workspace/supplier_skill_bindings/events.jsonl` ; il n'existe aucune atomicité globale entre ces deux fichiers. Le protocole de reprise doit détecter et traiter leur divergence. Il n'existe pas non plus d'atomicité globale entre les repositories (handover l.151). La migration reprend donc le modèle du coordinateur de type saga : journal écrit avant toute modification.

### Ordre des opérations

1. Figer les entrées : calculer et enregistrer l'empreinte SHA-256 de chaque fichier source ; écrire le journal `PREPARED` avec le plan complet.
2. Valider la table de correspondance et détecter les collisions (aucune écriture).
3. Obtenir les confirmations humaines nécessaires.
4. Créer ou mettre à jour les identités via le service d'enregistrement et le coordinateur (non modifiés).
5. Créer les `SupplierSkillBinding` correspondants.
6. Vérifier la cohérence de bout en bout, puis écrire `COMPLETED`.
7. Les fichiers anciens ne sont jamais modifiés ni supprimés par la migration.

### Journal de migration

- Journal séparé des données fournisseur et des bindings, par `migration_id`.
- États : `PREPARED`, `IDENTITIES_APPLIED`, `BINDINGS_APPLIED`, `COMPLETED`, `RECOVERY_REQUIRED`.
- Chaque ligne du plan porte : source, cible, empreinte avant, empreinte après, résultat.
- Le journal `PREPARED` est écrit avant toute modification.
- Une divergence entre le fichier d'état des bindings et `events.jsonl` est détectée et traitée explicitement à la reprise ; elle ne peut être ignorée ni considérée comme une réussite.

### Idempotence et reprise

- Une relance avec les mêmes entrées ne crée ni doublon ni modification : chaque ligne est vérifiée par son empreinte et sa clé `(supplier_id, skill_id, skill_version)`.
- Après interruption, la reprise lit le journal, détermine l'état de chaque ligne par empreintes, et reprend sans double écriture.
- Une divergence inattendue (fichier source modifié depuis `PREPARED`, état cible inattendu) classe la migration `RECOVERY_REQUIRED`.

### Rollback

- Pas de rollback automatique, conformément au coordinateur existant.
- Le retour arrière se fait par désactivation du binding ; une identité ne peut être désactivée dans ce cadre que si elle a été créée par la migration, jamais si elle préexistait. Aucun élément n'est supprimé automatiquement ; une restauration depuis les fichiers `.bak` ou la sauvegarde externe exige une décision humaine.
- Aucune modification partielle silencieuse : toute ligne non appliquée est listée avec son motif dans le journal, et le résultat global n'est jamais `COMPLETED` s'il subsiste une ligne en échec.

## 8. Critères d'acceptation (vérifiables, sans écrire les tests)

1. Aucun module actuel n'importe `services.supplier_repository` (les deux tests existants restent verts).
2. Aucun `SUP_xxxx` n'est présent dans les données actuelles hors table de correspondance et provenance.
3. Chaque `supplier_id` d'un binding existe dans `SupplierIdentityRepository`, et le couple `skill_id` / `skill_version` désigne une version présente dans `CanonicalSkillRepository`.
4. Le triplet `(supplier_id, skill_id, skill_version)` est unique ; un doublon est refusé sans écriture.
5. Un binding migré est créé désactivé, avec `usage_count = 0`, `last_used_at` nul, et la provenance complète.
6. Aucun identifiant migré n'est `human_confirmed` sans action humaine tracée.
7. Un rapprochement par nom ou alias seul ne crée jamais de binding, de lien ni de fusion.
8. Un rapprochement par identifiant non confirmé reste une suggestion.
9. `NOT_FOUND` et `AMBIGUOUS` n'appliquent aucun skill.
10. Seule une version au statut `PUBLISHED` peut être appliquée automatiquement en production ; `APPROVED` n'est pas applicable avant publication, `CANDIDATE` et `TESTING` sont réservés aux essais isolés, et `REJECTED` ou `RETIRED` interdisent toute application.
11. Si la version d'un binding actif devient `REJECTED` ou `RETIRED`, ce binding est conservé pour l'historique mais n'est pas applicable.
12. Le binding identifie une révision exacte par `(skill_id, skill_version)` avec SemVer strict ; l'usage ne sélectionne pas dynamiquement une version via `get_latest`. La publication d'une nouvelle version ne change pas la `skill_version` figée dans un binding existant ; un changement exige une proposition explicite, une validation humaine et un événement dans `events.jsonl`, puis la désactivation avec conservation de l'ancien binding avant création du nouveau.
13. Un usage incrémente uniquement `usage_count` et `last_used_at` du binding. Les fichiers de `CanonicalSkillRepository` restent identiques (empreintes).
14. Pour un même `(supplier_id, skill_id)`, au maximum un binding a `enabled = true`; activer une nouvelle version est atomiquement refusé si une autre version du même couple est encore active.
15. Une mutation invalide laisse le fichier existant inchangé.
16. Idempotence : deux exécutions successives donnent des fichiers identiques.
17. Interruption simulée après chaque état du journal : reprise sans doublon et sans perte ; toute divergence entre `bindings.json` et `events.jsonl` est détectée et traitée.
18. Aucune modification des fichiers sources anciens (empreintes identiques avant et après).
19. Aucun accès réseau, aucun calcul de montant.
20. `SupplierSkillBindingRepository` utilise `workspace/supplier_skill_bindings/bindings.json` comme fichier d'état et `workspace/supplier_skill_bindings/events.jsonl` comme journal append-only ; la reprise détecte et traite explicitement toute divergence, sans supposer d'atomicité globale entre les deux fichiers.
Cas limites à couvrir :
- SIRET identique avec raison sociale différente ;
- TVA déjà attribuée à un autre fournisseur ;
- SIREN seul, sans SIRET ;
- fournisseur étranger sans SIRET (TVA étrangère uniquement) ;
- deux fournisseurs de même nom légal, sans identifiant ;
- fournisseur désactivé portant un identifiant confirmé ;
- `PUBLISHED` applicable automatiquement en production ; `APPROVED` non applicable avant publication ; `CANDIDATE` et `TESTING` limités aux essais isolés ; `REJECTED` et `RETIRED` non applicables ;
- version d'un binding actif passant à `REJECTED` ou `RETIRED` : binding conservé pour l'historique mais non applicable ;
- publication d'une nouvelle version sans mise à jour automatique d'un binding existant ni sélection silencieuse de la dernière version ;
- proposition de changement de `skill_version` sans validation humaine ou sans événement journalisé ;
- création d'un nouveau binding après désactivation de l'ancien pour la nouvelle `skill_version` ;
- tentative d'activer une nouvelle version alors qu'un autre binding du même `(supplier_id, skill_id)` est encore actif : refus atomique sans changement d'état ;
- binding désactivé puis réactivé ;
- horodatage de dernier usage antérieur à la valeur précédente ;
- interruption entre deux fichiers JSON ;
- divergence entre `workspace/supplier_skill_bindings/bindings.json` et `workspace/supplier_skill_bindings/events.jsonl` ;
- skill historique avec `supplier_id` pointant vers un fournisseur absent ;

## 9. Questions ouvertes

Décisions impossibles à déduire des sources :

- **Q1 — DÉCIDÉE** : `SupplierSkillBindingRepository` est propriétaire du dépôt ; le fichier d'état est `workspace/supplier_skill_bindings/bindings.json` et le journal append-only des confirmations et événements ultérieurs est `workspace/supplier_skill_bindings/events.jsonl`. Aucune atomicité globale n'existe entre les deux fichiers ; le protocole de reprise doit détecter et traiter leur divergence.
- **Q2** : le SIREN doit-il pouvoir être un identifiant confirmé distinct, ou peut-il être dérivé d'un SIRET confirmé ? Le handover interdit actuellement toute déduction automatique.
- **Q3 — DÉCIDÉE, OPTION B** : le binding fige la révision exacte par `skill_id` + `skill_version` conforme au SemVer strict de `SkillManifest`. Aucune sélection dynamique par `get_latest` n'est autorisée. Seule une révision `PUBLISHED` est applicable en production ; `CANDIDATE` et `TESTING` restent limités aux essais isolés, `APPROVED` n'est pas encore applicable en production, et `REJECTED`/`RETIRED` sont interdits. Une nouvelle révision ne modifie aucun binding existant ; son adoption exige une validation humaine explicite, la désactivation et la conservation de l'ancien binding, puis la création d'un nouveau binding pour la nouvelle version.
- **Q4 — OUVERTE** : Aucun convertisseur validé n’existe actuellement pour transformer les anciens fichiers workspace/skills/*.json en SkillManifest canoniques. Les correspondances directes sont insuffisantes et plusieurs champs canoniques obligatoires n’ont pas d’équivalent legacy. La stratégie de conversion exige une décision humaine séparée avant toute implémentation.
- **Q5** : le contenu et le volume de l'ancien `workspace/suppliers/suppliers.json`, non lu, sont-ils compatibles avec la table de correspondance ?
- **Q6** : qui valide la table de correspondance et les confirmations (rôle, procédure) ?
- **Q7** : les alias dérivés par l'ancien creator (`_build_aliases`) sont-ils repris comme `aliases` d'identité, ou abandonnés ?
- **Q8** : la saga de migration dispose-t-elle de son propre journal, ou étend-elle le coordinateur existant (statuts actuels : `PREPARED`, `SUPPLIER_APPLIED`, `COMPLETED`, `RECOVERY_REQUIRED`) ?
- **Q9** : la donnée de test `SMTRT SAS` (SIRET `67162081300103`, `SUP_0001`) est-elle une donnée réelle ou un jeu d'essai ? Sert-elle de cas de référence pour la migration ?
- **Q10** : comment et selon quelles conditions une révision `CANDIDATE` devient-elle `PUBLISHED` (tests, approbateur, validation humaine, nouvelle révision ou cycle de vie existant) ?

## 10. Plan de retrait

Conditions cumulatives avant suppression de chaque élément ancien. Aucune suppression n'est autorisée tant qu'une condition manque.

Conditions communes :
- sauvegarde externe vérifiée et conservée ;
- migration `COMPLETED` et critères d'acceptation 1 à 20 vérifiés ;
- décision humaine écrite et tracée.

| Élément | Conditions spécifiques |
|---|---|
| `services/supplier_repository.py` | Plus aucune référence active à l'import ; chaque fournisseur ancien est soit migré, soit explicitement rejeté ; aucune fonction listée en section 3 de l'audit n'est restée sans équivalent actuel. |
| Références `SUP_xxxx` | Table de correspondance figée et archivée ; aucun skill ni donnée active ne dépend de `SUP_xxxx`. |
| Ancien `suppliers.json` | Archivé hors dépôt actif avec son empreinte ; aucune lecture résiduelle par le code ; ni supprimé ni ignoré avant décision humaine. |
| Logique legacy de certification (`human_validations`, `VERIFIED`) | Remplacée par `human_confirmed` + justification ; historique conservé en provenance ; aucune certification automatique n'est perdue sans trace. |
| Logique legacy d'usage (`usage_count`, `last_used`, `attach_or_update_skill`) | Remplacée par `SupplierSkillBinding` ; le rattachement et l'usage sont couverts par des tests ; les anciennes valeurs sont archivées comme non fiables. |
| Anciens tests et scripts (`test_skill_creator.py`, scripts d'apprentissage) | Remplacés par des tests couvrant le creator, l'applier et le binding avec dossiers temporaires, sans écriture dans les données réelles. |
| `services/invoice_skill_applier.py`, `services/invoice_skill_creator.py` (ancien format) | Matching par identifiant confirmé migré ; dérivation d'alias, extraction SIRET/TVA et ancre de numéro de facture réaffectées ou abandonnées par décision explicite. |

Les tests existants interdisant l'import de l'ancien dépôt restent en place après le retrait.
