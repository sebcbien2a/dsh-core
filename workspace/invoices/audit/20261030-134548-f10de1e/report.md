# Audit de lot DSH-Core — 20261030-134548-f10de1e

- Généré le : 2026-10-30T13:54:49+01:00
- Corpus : `A:\dsh-core\workspace\invoices\input`
- Règles : `A:\dsh-core\knowledge\rules\accounting`
- Mode vision : `hybrid`
- Déterministe : non
- Run valide : oui
- Factures auditées : 6
- Escalades cloud : 0 (0.0 %) — quota 5 % respecté

## Synthèse des décisions

| Décision | Nombre |
| --- | ---: |
| VALIDATED | 0 |
| HITL | 6 |
| REJECTED | 0 |

## Règles Obsidian

Règle cible : `RULE-TVA-ZERO-001`

| Résultat | Nombre |
| --- | ---: |
| NOT_TRIGGERED | 0 |
| NO_RULE_REACHED | 0 |
| RULE_EVALUATION_SKIPPED | 5 |
| RULE_MISSING | 0 |
| TRIGGERED_BLOCKED | 1 |
| TRIGGERED_RESOLVED | 0 |
| UNKNOWN | 0 |

## Points de blocage

| Code | Couche | Sévérité | Occurrences |
| --- | --- | --- | ---: |
| INVOICE_NOT_VALIDATED | rule | blocker | 5 |
| VISION_EXTRACTION_FAILED | production | blocker | 5 |
| AMBIGUOUS_ZERO_VAT | rule | blocker | 1 |
| MISSING_SIRET | extraction | warning | 1 |
| MISSING_VAT_NUMBER | extraction | warning | 1 |

## Couverture d'extraction

| Champ | Présents | Total | Ratio |
| --- | ---: | ---: | ---: |
| supplier_name | 1 | 6 | 0.167 |
| siret | 0 | 6 | 0.0 |
| vat_number | 0 | 6 | 0.0 |
| country | 1 | 6 | 0.167 |
| invoice_number | 1 | 6 | 0.167 |
| issue_date | 1 | 6 | 0.167 |
| net_amount | 1 | 6 | 0.167 |
| tax_amount | 1 | 6 | 0.167 |
| gross_amount | 1 | 6 | 0.167 |

## Factures HITL

- `facture ha paintcar avril pour juin 16  ILAA0F41.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf` — aucun InvoiceSchema valide à évaluer
- `facture paintcar mai 2018.pdf` — aucun InvoiceSchema valide à évaluer
- `facture_paintcar (2).pdf` — aucun InvoiceSchema valide à évaluer
- `ILADBF12 paintcar janv pour fev.pdf` — aucun InvoiceSchema valide à évaluer
- `paintcar mars pour AVRIL 17 ILA23FB2.pdf` — aucun InvoiceSchema valide à évaluer

## Factures REJECTED

Aucune.

## Index des factures

| Fichier | Décision | Règle | Motif | Fournisseur |
| --- | --- | --- | --- | --- |
| facture ha paintcar avril pour juin 16  ILAA0F41.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | CRISOL COLOR, S.L. |
| FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture paintcar mai 2018.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture_paintcar (2).pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| ILADBF12 paintcar janv pour fev.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| paintcar mars pour AVRIL 17 ILA23FB2.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
