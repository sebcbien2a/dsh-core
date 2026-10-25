# Mapping Neo4j d'identité commerciale (clients et fournisseurs)

Document de conception. Aucune connexion Neo4j, aucune donnée réelle.

## Principes

- La règle d'identité est indépendante du rôle : CLIENT, SUPPLIER ou les deux.
- Aucun rôle n'est déduit des identifiants.
- N° TVA, SIREN et SIRET sont trois identifiants distincts, jamais fusionnés.
- Aucune fusion automatique d'entités (ni par nom, email, code, alias ou identifiant).
- Un fournisseur étranger sans SIRET reste acceptable.

## Règle TVA française

Module : `services/french_business_identity_rule.py`.

- Normalisation : retrait des espaces, points, tirets, slashs, underscores ; majuscules. La valeur brute est conservée.
- Format : `^FR[0-9]{11}$` (clé 2 chiffres + SIREN 9 chiffres).
- Clé attendue : `(12 + 3 * (SIREN mod 97)) mod 97`, sur 2 chiffres.
- Une clé correcte ne prouve ni l'attribution, ni l'activité, ni le nom de l'entreprise.
- Un numéro non français n'est jamais analysé avec cette règle (`NOT_FRENCH_VAT`).

Codes d'erreur : `VAT_VALUE_MISSING`, `NOT_FRENCH_VAT`, `INVALID_FRENCH_VAT_FORMAT`, `INVALID_FRENCH_VAT_KEY`.

## Contrôle de cohérence TVA / SIREN / SIRET

`check_french_identity_consistency(vat_number, explicit_siren, siret)`.

Sources comparables : SIREN de la TVA (format français et clé valides), SIREN explicite (9 chiffres), 9 premiers chiffres du SIRET (14 chiffres).

| Statut | Condition |
|---|---|
| CONSISTENT | au moins 2 sources valides, toutes concordantes |
| IDENTITY_CONFLICT | au moins 2 sources valides divergent |
| INSUFFICIENT_EVIDENCE | moins de 2 sources valides |
| INVALID_INPUT | un identifiant fourni a un format ou une clé invalide |

Codes : `INVALID_SIREN_FORMAT`, `INVALID_SIRET_FORMAT`, `INVALID_FRENCH_VAT_FORMAT`, `INVALID_FRENCH_VAT_KEY` (entrée invalide) ; `VAT_MISMATCH_EXPLICIT_SIREN`, `VAT_MISMATCH_SIRET`, `EXPLICIT_SIREN_MISMATCH_SIRET` (conflit) ; `*_MATCHES_*` (preuves).

## Mapping Neo4j proposé (non implémenté)

| Nœud / relation | Propriétés |
|---|---|
| `(:BusinessEntity {entity_id})` | noms, pays, champs personnalisés |
| `(:Identifier {type, country, normalized_value, human_confirmed})` | type : VAT, SIREN, SIRET, extensible |
| `(:BusinessEntity)-[:HAS_IDENTIFIER]->(:Identifier)` | un identifiant confirmé unique par pays + type + valeur |
| `(:BusinessEntity)-[:HAS_ROLE]->(:Role {name})` | CLIENT et/ou SUPPLIER, déclaré explicitement |
| `(:BusinessEntity)-[:HAS_ALIAS]->(:Alias)` | alias séparés par `;` à l'import client |

Le résultat de cohérence est une preuve de contrôle, jamais un déclencheur de fusion.
