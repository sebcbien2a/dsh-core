# Architecture du registre canonique des skills

**Décision : 15 octobre 2026**
**Statut : architecture décidée; implémentation non commencée**

## 1. Autorité et source de vérité

DSH-Core local est l'unique autorité de publication des skills. Le registre
canonique est local, explicite, versionné et sauvegardable par Git. Qdrant,
Mem0 et Oracle ne sont pas des sources de vérité pour les définitions, les
versions officielles ou les statuts de publication.

## 2. Structure cible

La structure documentaire cible est :

```text
skills/catalog/<skill_id>/
  manifest.json
  versions/
    <version>/
      skill.json
      implementation.py
      tests/
      evidence.json
```

Cette arborescence est une cible d'architecture uniquement. Aucun de ces
dossiers n'est créé par cette décision. Le schéma JSON détaillé et ses tests
seront définis dans P1-5.

## 3. Identité, versions et preuves

Chaque skill doit porter les champs suivants :

- `skill_id` : identifiant stable et indépendant de son nom ;
- `name`, `domain`, `version` (version sémantique) et `content_hash` ;
- `status`, `created_at`, `updated_at`, `author`, `approver` et `source` ;
- `supersedes` et `dependencies` ;
- `implementation_ref` et `test_evidence_ref` ;
- `embedding_model` et `embedding_dimension` ;
- `usage_count` et `last_used_at`.

Une version publiée est immuable. Toute correction produit une nouvelle
version reliée à la précédente par `supersedes`; elle n'écrase jamais un
artefact publié. Le schéma exact, les formats des références et les règles de
validation relèvent du ticket P1-5.

## 4. Cycle de vie et utilisation

Le cycle obligatoire est :

```text
CANDIDATE → TESTING → APPROVED → PUBLISHED
                    ↘ REJECTED
PUBLISHED → RETIRED
```

`REJECTED` et `RETIRED` sont des sorties possibles du cycle. Seuls les skills
`PUBLISHED` sont utilisables automatiquement en production. Les skills
`TESTING` sont utilisables uniquement dans des tests isolés. `CANDIDATE`,
`APPROVED`, `REJECTED` et `RETIRED` ne sont pas sélectionnables
automatiquement en production.

## 5. Rôle des systèmes

### DSH-Core local

Le registre local porte l'autorité, l'historique explicite des versions et les
décisions d'approbation et de publication. Lui seul approuve et publie.

### Qdrant

Qdrant est uniquement une projection de recherche sémantique. Une projection
contient :

- `skill_id`, `version`, `status` et `content_hash` ;
- le résumé ou le contenu nécessaire à la recherche ;
- le vecteur, le modèle d'embedding et sa dimension ;
- la référence vers l'artefact canonique local.

Qdrant ne décide jamais qu'un skill est publié. Toute projection doit pouvoir
être reconstruite intégralement depuis le registre canonique local. Aucune
collection existante n'est supprimée ou migrée par cette décision.

### Mem0

Mem0 conserve uniquement des souvenirs contextuels et des événements d'usage.
Il ne porte ni l'autorité, ni la version officielle, ni le statut de
publication d'un skill.

### Oracle

Oracle peut produire des skills `CANDIDATE` et des preuves de tests, et écrit
uniquement en staging. Il ne peut ni approuver ni publier. Seul DSH-Core local
peut faire ces opérations.

## 6. Voies existantes conservées

Les voies actuelles restent en place, sans suppression ni migration :

| Voie | Classification documentaire |
|---|---|
| `skills/registry.json` | `LEGACY_ACTIVE` |
| `skills/*.py` | `LEGACY_ARTIFACT` |
| `workspace/skills/*.json` | `LEGACY_DOMAIN_SKILL` |
| Qdrant `agent_skills` | `LEGACY_VECTOR_PROJECTION` |
| Qdrant `skills_catalog` | `UNUSED_EMPTY` |
| Mem0 | `CONTEXT_ONLY` |

## 7. Quatre points `agent_skills`

Les quatre points actuels demeurent inchangés et sont classés
`LEGACY_CANDIDATE_UNREVIEWED`. Aucun n'est désigné comme skill canonique.
Leur convergence ultérieure exigera l'extraction de leurs différences, la
construction d'une version candidate consolidée, des tests de
non-régression, une validation humaine et la publication d'une nouvelle
version canonique.

## 8. Skills facture

Les skills facture suivent le même contrat canonique général et peuvent y
ajouter des champs propres à leur domaine. Ils ne forment pas un second
registre canonique.

## 9. Interdictions et garde-fous

- Aucun `DELETE` automatique.
- Aucune publication directe par Oracle.
- Aucune décision de publication fondée sur un score vectoriel.
- Aucun écrasement silencieux d'une version.
- Aucune utilisation automatique d'un `CANDIDATE`.
- Aucune migration avant sauvegarde, tests et plan de retour arrière.

Cette décision n'autorise aucune fusion, suppression, migration ou publication
des voies existantes.
