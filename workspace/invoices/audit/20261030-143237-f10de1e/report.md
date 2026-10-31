# Audit de lot DSH-Core — 20261030-143237-f10de1e

- Généré le : 2026-10-30T14:42:33+01:00
- Corpus : `A:\dsh-core\workspace\invoices\input`
- Règles : `A:\dsh-core\knowledge\rules\accounting`
- Mode vision : `hybrid`
- Déterministe : non
- Run valide : oui
- Factures auditées : 6
- Escalades cloud : 5 (83.3 %) — quota 5 % DÉPASSÉ
- Budget escalade : 5/5 (épuisé: oui)

## Synthèse des décisions

| Décision | Nombre |
| --- | ---: |
| VALIDATED | 6 |
| HITL | 0 |
| REJECTED | 0 |

## Règles Obsidian

Règle cible : `RULE-TVA-ZERO-001`

| Résultat | Nombre |
| --- | ---: |
| NOT_TRIGGERED | 0 |
| NO_RULE_REACHED | 0 |
| RULE_EVALUATION_SKIPPED | 0 |
| RULE_MISSING | 0 |
| TRIGGERED_BLOCKED | 0 |
| TRIGGERED_RESOLVED | 6 |
| UNKNOWN | 0 |

## Points de blocage

| Code | Couche | Sévérité | Occurrences |
| --- | --- | --- | ---: |
| MISSING_SIRET | extraction | warning | 6 |
| ESCALATED_TO_CLOUD | vision | info | 5 |
| IDENTITY_CONFLICT | supplier_memory | warning | 1 |

## Couverture d'extraction

| Champ | Présents | Total | Ratio |
| --- | ---: | ---: | ---: |
| supplier_name | 6 | 6 | 1.0 |
| siret | 0 | 6 | 0.0 |
| vat_number | 6 | 6 | 1.0 |
| country | 6 | 6 | 1.0 |
| invoice_number | 6 | 6 | 1.0 |
| issue_date | 6 | 6 | 1.0 |
| net_amount | 6 | 6 | 1.0 |
| tax_amount | 6 | 6 | 1.0 |
| gross_amount | 6 | 6 | 1.0 |

## Factures HITL

Aucune.

## Factures REJECTED

Aucune.

## Index des factures

| Fichier | Décision | Règle | Motif | Fournisseur |
| --- | --- | --- | --- | --- |
| facture ha paintcar avril pour juin 16  ILAA0F41.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| facture paintcar mai 2018.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| facture_paintcar (2).pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| ILADBF12 paintcar janv pour fev.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| paintcar mars pour AVRIL 17 ILA23FB2.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
