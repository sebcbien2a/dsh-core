# Audit de lot DSH-Core — 20261030-155907-f10de1e

- Généré le : 2026-10-30T16:34:35+01:00
- Corpus : `A:\dsh-core\workspace\invoices\input`
- Règles : `A:\dsh-core\knowledge\rules\accounting`
- Mode vision : `hybrid`
- Déterministe : non
- Run valide : oui
- Factures auditées : 69
- Escalades cloud : 3 (4.3 %) — quota 5 % respecté
- Budget escalade : 4/4 (épuisé: oui)

## Synthèse des décisions

| Décision | Nombre |
| --- | ---: |
| VALIDATED | 40 |
| HITL | 29 |
| REJECTED | 0 |

## Règles Obsidian

Règle cible : `RULE-TVA-ZERO-001`

| Résultat | Nombre |
| --- | ---: |
| NOT_TRIGGERED | 34 |
| NO_RULE_REACHED | 0 |
| RULE_EVALUATION_SKIPPED | 14 |
| RULE_MISSING | 0 |
| TRIGGERED_BLOCKED | 15 |
| TRIGGERED_RESOLVED | 6 |
| UNKNOWN | 0 |

## Points de blocage

| Code | Couche | Sévérité | Occurrences |
| --- | --- | --- | ---: |
| MISSING_SIRET | extraction | warning | 49 |
| MISSING_INVOICE_NUMBER | extraction | warning | 31 |
| MISSING_VAT_NUMBER | extraction | warning | 29 |
| MISSING_ISSUE_DATE | extraction | warning | 21 |
| AMBIGUOUS_ZERO_VAT | rule | blocker | 15 |
| INVOICE_NOT_VALIDATED | rule | blocker | 14 |
| VISION_EXTRACTION_FAILED | production | blocker | 13 |
| ESCALATION_BUDGET_EXCEEDED | vision | blocker | 12 |
| MISSING_COUNTRY | extraction | warning | 7 |
| ESCALATED_TO_CLOUD | vision | info | 3 |
| SUPPLIER_UNKNOWN | extraction | warning | 3 |
| IDENTITY_CONFLICT | supplier_memory | warning | 1 |
| OCR_TEXT_UNAVAILABLE | production | blocker | 1 |

## Couverture d'extraction

| Champ | Présents | Total | Ratio |
| --- | ---: | ---: | ---: |
| supplier_name | 52 | 69 | 0.754 |
| siret | 6 | 69 | 0.087 |
| vat_number | 26 | 69 | 0.377 |
| country | 48 | 69 | 0.696 |
| invoice_number | 24 | 69 | 0.348 |
| issue_date | 34 | 69 | 0.493 |
| net_amount | 55 | 69 | 0.797 |
| tax_amount | 55 | 69 | 0.797 |
| gross_amount | 55 | 69 | 0.797 |

## Factures HITL

- `05 SEPT 45.81.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `17.01.25.W250117W1957907O267468286.pdf` — supplier.vat_number=FR67921781480; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `18 MAI 2025.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `25 NOV 74.33.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `28.02.25.W250228W1957907O270618214.pdf` — supplier.vat_number=FR67921781480; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `4 FEV 25 4398283-order-invoice-0abc8d4db78d3665d0c3e9e43d97135c.pdf` — supplier.vat_number=FR11810908392; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `4 FEV 25 4398283-order-invoice-6e6c0ed50000e4b80f686b6f541accf6.pdf` — supplier.vat_number=FR11810908392; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `4 FEV 25 OVO1698014_20250204.pdf` — supplier.vat_number=<absent>; supplier.country=<inconnu>; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `59.89 16 MAY 2025.pdf` — supplier.vat_number=<absent>; supplier.country=<inconnu>; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `68,22 DU 17 MARS 25.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `96,56 11 FEV 25 67ac6ba8a6de6_invoice_70100_20250212_103915.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `ABONNEMENT.pdf` — aucun InvoiceSchema valide à évaluer
- `AMENDE 144 EUR 30.06.26.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `facture annonce legale.pdf` — supplier.vat_number=<absent>; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `facture électricité.pdf` — aucun InvoiceSchema valide à évaluer
- `facture-1.pdf` — supplier.vat_number=FR254992471381; supplier.country=FR; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `facture_creation_holding.pdf` — aucun InvoiceSchema valide à évaluer
- `facture_free.pdf` — aucun InvoiceSchema valide à évaluer
- `facture_location5.pdf` — aucun InvoiceSchema valide à évaluer
- `facture_paintcar (2).pdf` — aucun InvoiceSchema valide à évaluer
- `ILADBF12 paintcar janv pour fev.pdf` — aucun InvoiceSchema valide à évaluer
- `OVO1735289_20250211.pdf` — supplier.vat_number=<absent>; supplier.country=<inconnu>; routage: ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME; persister le régime TVA fournisseur après validation humaine
- `paintcar mars pour AVRIL 17 ILA23FB2.pdf` — aucun InvoiceSchema valide à évaluer
- `test_image.jpg` — aucun InvoiceSchema valide à évaluer
- `ticket patapain 180.pdf` — aucun InvoiceSchema valide à évaluer
- `Votre document edf.pdf` — aucun InvoiceSchema valide à évaluer
- `W250228W1957907O270618214.pdf` — aucun InvoiceSchema valide à évaluer
- `W250505W1957907O275367852.pdf` — aucun InvoiceSchema valide à évaluer
- `W250609W1957907O277794510.pdf` — aucun InvoiceSchema valide à évaluer

## Factures REJECTED

Aucune.

## Index des factures

| Fichier | Décision | Règle | Motif | Fournisseur |
| --- | --- | --- | --- | --- |
| 05 SEPT 27.78.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURE DIFFUSION |
| 05 SEPT 45.81.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | ASSISTANCE PEINTURE DIFFUSION |
| 06 OCTOBRE 14,99.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 10 NOVEMBRE 25 19,90.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 12 DECE 199.99.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURE DIFFUSION |
| 14octobre18tick1362280.38142200.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | RESERVATION INTERNET |
| 15.04.25.W250415W1957907O274065381.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Chronopost SAS est membre du Groupe TVA La Poste, |
| 17.01.25.W250117W1957907O267468286.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | CHRONOPOST SAS |
| 18 MAI 2025.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | ASSISTANCE PEINTURES DIFFUSION |
| 19 DEC 185.91.pdf | VALIDATED | TRIGGERED_RESOLVED | ZERO_VAT_DISAMBIGUATED | ASSISTANCE PEINTURE DIFFUSION |
| 19.08.25 52,22.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 21 SEPT 25 16,19 EUR.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 23.01.25;W250123W1957907O267912252.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Chronopost SAS |
| 23dec17 tick9263237.30015922.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | CORSICA FERRIES |
| 25 NOV 74.33.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | ASSISTANCE PEINTURE DIFFUSION |
| 28 janv 25 Factura.pdf | VALIDATED | TRIGGERED_RESOLVED | ZERO_VAT_DISAMBIGUATED | TRADEINN RETAIL SERVICES SL |
| 28 novembre 25 195,95 eur.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 28.02.25.W250228W1957907O270618214.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | CHRONOPOST SAS |
| 2aout18tick5227857.45809776.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Corsica Ferries |
| 30 JUILLET 25 128;19.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 4 FEV 25 4398283-order-invoice-0abc8d4db78d3665d0c3e9e43d97135c.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | assistance peintures diffusion |
| 4 FEV 25 4398283-order-invoice-6e6c0ed50000e4b80f686b6f541accf6.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | assistance peintures diffusion |
| 4 FEV 25 96511490_130684960.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | sebastien paul |
| 4 FEV 25 OVO1698014_20250204.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | assistance peintures diffusion |
| 59.89 16 MAY 2025.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | PAUL sebastien assistance peintures diffusion |
| 68,22 DU 17 MARS 25.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | ASSISTANCE PEINTURES DIFFUSION |
| 689 EUR DU 25 FEVRIER 25.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | ASSISTANCE PEINTURES DIFFUSION |
| 7juillet18tick2789169.38666940.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | CORSICA FERRIES |
| 96,56 11 FEV 25 67ac6ba8a6de6_invoice_70100_20250212_103915.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | SAS ASSISTANCE PEINTURES DIFFUSION |
| ABONNEMENT.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| AMENDE 144 EUR 30.06.26.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | FOURNISSEUR_INCONNU |
| Factura.pdf traddin 21 janv 25.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Datos cliente: SAS ASSISTANCE PEINTURES DIFFUSION |
| FACTURE 16014.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Autre S.A.S. Assistance Peintures Diffusion |
| facture accompagnement.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | CC I LO IR - ET |
| facture annonce legale.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | Constitu tion SA S |
| Facture APD 210744[28] general paint.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Messieurs Jean et Sébastien PAUL |
| FACTURE BILAN 2024 - 2511-F-2025-007-EI-ATIA-SONIA-TALENT-CO.pdf | VALIDATED | TRIGGERED_RESOLVED | ZERO_VAT_DISAMBIGUATED | EI - ATIA SONIA / TALENT&CO |
| FACTURE DANIEL OCTOBRE MISE EN AVRIL 25.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Autre S.A.S. Assistance Peintures Diffusion |
| facture ha paintcar avril pour juin 16  ILAA0F41.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| Facture n° 2609649432.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Monsieur PAUL SEBASTIEN |
| FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| facture paintcar mai 2018.pdf | VALIDATED | TRIGGERED_RESOLVED | SUPPLIER_REGIME_APPLIED | CRISOL COLOR, S.L. |
| Facture Sia 251430 2OAUT16 POUR 2octobre16.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | sia Abrasives France |
| facture électricité.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture-1.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | Identiﬁant : 51981572 |
| Facture_000003 ASSISTANCE_PEINTURES_DIFFUSION.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Entreprise individuelle HOUALET OLIVIER |
| facture_creation_holding.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture_emaux.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Les Emaux de l'Atlantique |
| facture_free.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| Facture_lidle.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Sebastien Paul |
| facture_location5.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture_paintcar (2).pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| facture_transport.PDF | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | FOURNISSEUR_INCONNU |
| facture_transport2.PDF | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | CATROUX TRANSPORTS |
| Factures FA2123 DIP¨AOUT POUR OCT 16.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Assistance Peintures Diffusion |
| Factures FA2126 DIP AOPUT POUR OCT 16.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Assistance Peintures Diffusion |
| Factures FA2192 DIP SEPT POUR NOV 16.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Assistance Peintures Diffusion |
| FACTURES JANVIER ET FEVRIER.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Autre S.A.S. Assistance Peintures Diffusion |
| ILADBF12 paintcar janv pour fev.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| OVO1735289_20250211.pdf | HITL | TRIGGERED_BLOCKED | AMBIGUOUS_ZERO_VAT | assistance peintures diffusion |
| paintcar mars pour AVRIL 17 ILA23FB2.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| recap frais transport livraison chrono boxtal.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | FOURNISSEUR_INCONNU |
| test_image.jpg | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| ticket patapain 180.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| Votre document edf.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| W250228W1957907O270618214.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| W250505W1957907O275367852.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
| W250604W1957907O277534980.pdf | VALIDATED | NOT_TRIGGERED | RULE_NOT_TRIGGERED | Chronopost SAS est membre du Groupe TVA La Poste, |
| W250609W1957907O277794510.pdf | HITL | RULE_EVALUATION_SKIPPED | INVOICE_NOT_VALIDATED | <absent> |
