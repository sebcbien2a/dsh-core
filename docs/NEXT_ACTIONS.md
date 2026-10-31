---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 5 — Feuille de route post-P8
contract_version: 1.0.1
status: ACTIVE
upstream_dependencies: [docs/PROJECT_HANDOVER.md, docs/DOCUMENT_PERCEPTION_CONTRACT.md]
downstream_consumers: [services/spreadsheet_invoice_producer.py, services/banking_invoice_producer.py, services/persistence_manager.py, schemas/master_tables_schema.py]
verification_tests: [tests/test_spreadsheet_producer.py, tests/test_banking_producer.py, tests/test_persistence_manager.py]
---

# Prochaines actions

## Priorité 1 — Validation humaine des orientations
- Confirmer la vérité terrain des deux orientations contestées avant de modifier les annotations ou les résultats de référence.
- `facture_emaux.pdf`
- `facture_transport.PDF`

## Priorité 2 — Validation Oracle Linux de PP-LCNet
- Valider PP-LCNet, le pilote CUDA et la mesure mémoire sur la version cible d’Oracle Linux avant toute mise en production.

## Priorité 3 — Benchmark ExtractionManager vs NuExtract3
- Comparer les deux extracteurs sur les mêmes textes OCR et la vérité terrain des six champs : fournisseur, date, HT, TVA, TTC et SIRET.
- Exclure les règles de réconciliation de la comparaison brute.

## Abandonné actuellement
- LightOnOCR pour l’orientation : résultat de 20 %, insuffisant pour une intégration.

## Reprise en nouvelle conversation
Lire uniquement PROJECT_STATE.md, PROJECT_DECISIONS.md et NEXT_ACTIONS.md.
Ne pas relire le dépôt tant qu'une information précise n'est pas manquante.
