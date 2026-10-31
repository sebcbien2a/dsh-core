---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 3 — Distillation, Template Skills & Mémoire Fournisseur
contract_version: 1.0.1
status: ACTIVE
upstream_dependencies: [schemas/document_perception.py, schemas/supplier_identity_schema.py, services/document_engine.py, services/cloud_vision_extractor.py, workspace/suppliers/suppliers.json]
downstream_consumers: [services/document_engine.py (préfiltre LLM), backend/app/graphs/invoice_graph.py (supplier_enrichment), services/invoice_skill_creator.py]
verification_tests: [tests/test_skill_distillation.py, tests/test_supplier_enrichment.py, tests/test_supplier_identity_repository.py, tests/test_supplier_registration_repository.py]
---

# Template Skills & Mémoire Fournisseur

## 1. Template Skills (distillation teacher → extraction déterministe)

- **Store** : `services/template_skill_store.py` — `TemplateSkill` / `TemplateAnchor`, écriture atomique dans `workspace/skills/`.
- **Distillation** : `CloudDocumentEngine.teacher_distill()` (Gemini 3.8 Flash) produit des skills **filtrés** :
  `filter_teacher_anchors()` garde uniquement les formes avec `raw_text_target`, kind ∈
  {circle, ellipse, square, highlight, strike, arrow}, confidence ≥ 0.5, dédup IoU > 0.7, cap 12 —
  le bruit vectoriel (11 385 tracés) n'atteint jamais le disque.
- **Consommation** : `TemplateSkillApplier` extrait les champs par **ancrage géométrique**
  (label → valeur selon `relative_position`) AVANT tout appel NuExtract/VLM ; si `coverage ≥ 0.6`,
  aucun modèle n'est appelé (extraction déterministe).
- **Matching** : `find_by_supplier` (supplier_id / SIREN / TVA / alias) puis `find_by_signature`
  (fingerprint de labels page 1, seuil 0.6).

## 2. Skills actifs (`workspace/skills/`)

| Skill | Fournisseur | Apport |
|---|---|---|
| `invoice_smtrt_sas.json` v2 | SMTRT (SUP_0001) | `trusted_labels` TOTAL H.T./MT TVA/NET A PAYER, regex n° 8 chiffres |
| `invoice_edf.json` | EDF | ancre TTC (corrige « montant TTC absent »), regex n° facture long |
| `invoice_crisol_color.json` | CRISOL COLOR (SUP_0002 / ESB17443482) | regex `^\d{4}/PI/\d+$`, labels Base/Total |

## 3. Mémoire fournisseur certifiée

- **Référentiel** : `workspace/suppliers/suppliers.json` (SMTRT SUP_0001, CRISOL COLOR SUP_0002 —
  clé TVA `ESB17443482`, `siret: null` pour l'étranger, `vat_regime: INTRACOM_AUTOLIQUIDATION`).
- **Résolution** : `services/supplier_memory_service.py` — cascade SIREN → TVA intra → nom flou
  (`SupplierRepository.find_by_siren/tva/name`), backfill `siret`/`vat_number`/`country`/`vat_regime`
  certifiés avant évaluation des règles.
- **Anti-corruption** : un identifiant extrait qui contredit la valeur certifiée ne remplace jamais
  cette dernière ; warning `IDENTITY_CONFLICT` consigné (ex. TVA client lue sur facture Crisol).
- **Gouvernance** : `SupplierLayoutRule` distillé = **candidate** (`active=False`,
  `human_confirmed=False`) ; activation = confirmation humaine + incrémentation de version.
  Voir annexe `docs/SUPPLIER_SKILL_MIGRATION_SPEC.md`.
