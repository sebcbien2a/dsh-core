# Audit de lot DSH-Core — 20261030-142245-f10de1e

- Généré le : 2026-10-30T14:32:03+01:00
- Corpus : `A:\dsh-core\workspace\invoices\input`
- Règles : `A:\dsh-core\knowledge\rules\accounting`
- Mode vision : `hybrid`
- Déterministe : non
- Run valide : oui
- Factures auditées : 6
- Escalades cloud : 1 (16.7 %) — quota 5 % DÉPASSÉ
- Budget escalade : 1/1 (épuisé: oui)

## Synthèse des décisions

| Décision | Nombre |
| --- | ---: |
| VALIDATED | 2 |
| HITL | 4 |
| REJECTED | 0 |

## Règles Obsidian

Règle cible : `RULE-TVA-ZERO-001`

| Résultat | Nombre |
| --- | ---: |
| NOT_TRIGGERED | 0 |
| NO_RULE_REACHED | 0 |
| RULE_EVALUATION_SKIPPED | 4 |
| RULE_MISSING | 0 |
| TRIGGERED_BLOCKED | 0 |
| TRIGGERED_RESOLVED | 2 |
| UNKNOWN | 0 |

## Points de blocage

| Code | Couche | Sévérité | Occurrences |
| --- | --- | --- | ---: |
| ESCALATION_BUDGET_EXCEEDED | vision | blocker | 4 |
| INVOICE_NOT_VALIDATED | rule | blocker | 4 |
| VISION_EXTRACTION_FAILED | production | blocker | 4 |
| MISSING_SIRET | extraction | warning | 2 |
| ESCALATED_TO_CLOUD | vision | info | 1 |
| IDENTITY_CONFLICT | supplier_memory | warning | 1 |

## Couverture d'extraction

| Champ | Présents | Total | Ratio |
| --- | ---: | ---: | ---: |
| supplier_name | 2 | 6 | 0.333 |
| siret | 0 | 6 | 0.0 |
| vat_number | 2 | 6 | 0.333 |
| country | 2 | 6 | 0.333 |
| invoice_number | 2 | 6 | 0.333 |
| issue_date | 2 | 6 | 0.333 |
| net_amount | 2 | 6 | 0.333 |
| tax_amount | 2 | 6 | 0.333 |
| gross_amount | 2 | 6 | 0.333 |

## Factures HITL

- `facture paintcar mai 2018.pdf` — aucun InvoiceSchema valide à évaluer
- `facture_paintcar (2).pdf` — aucun InvoiceSchema valide à évaluer
- `ILADBF12 paintcar janv pour fev.pdf` — aucun InvoiceSchema valide à évaluer
- `paintcar mars pour AVRIL 17 ILA23FB2.pdf` — aucun InvoiceSchema valide à évaluer

## Factures REJECTED

Aucune.

## Index des factures

| Fichier | Décision | Règle | Motif | Fournisseur |
| --- | --- | --- | --- | --- |
| facture ha paintcar avril pour juin 16  ILAA0F41.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| facture paintcar mai 2018.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture_paintcar (2).pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| ILADBF12 paintcar janv pour fev.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| paintcar mars pour AVRIL 17 ILA23FB2.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
