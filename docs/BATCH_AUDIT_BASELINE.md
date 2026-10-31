---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 4 — Audit de Lot, Baselines & Gouvernance Cloud/Local
contract_version: 1.0.1
status: ACTIVE
upstream_dependencies: [backend/app/graphs/invoice_graph.py, services/rule_registry_service.py, services/document_pipeline.py, services/cloud_vision_extractor.py, model_router.py]
downstream_consumers: [workspace/invoices/audit/ (rapports + baseline.json), docs/AI_MODEL_ROUTING_AND_HARNESSES.md]
verification_tests: [tests/test_run_batch_audit.py, tests/test_vision_supervisor.py, tests/test_model_router.py]
---

# Audit de Lot, Baselines & Gouvernance Cloud/Local

## 1. Batch audit (`scripts/run_batch_audit.py`)

- Parcourt le corpus (`--input`, `--pattern`, `--limit`), exécute le graphe complet **sans persistance**
  (`persistence_manager=None`), et consigne par facture : décision (VALIDATED/HITL/REJECTED), règle
  Obsidian (outcome, conditions), métadonnées d'extraction, blocages sémantiques, perception.
- **Rapports** : `run_manifest.json`, `records.jsonl` (append, `--resume`), `summary.json`,
  `report.md` (dont section `## Perception documentaire` et `## Baseline`).
- **Codes retour** : 0 = tout VALIDATED, 2 = run complet avec HITL/REJECTED, 1 = fatale.
- **Options clés** : `--vision none|hybrid|cloud|ollama`, `--document-pipeline on|off`,
  `--max-cloud-escalations N`, `--baseline-out PATH`.

## 2. Baseline signée (`workspace/invoices/audit/baseline.json`)

| Champ | Valeur |
|---|---|
| run_id | `p8-1-final2` |
| contract_version | 1.0.1 |
| décisions | 29 VALIDATED / 40 HITL / 0 REJECTED (parité off/on 100 %) |
| perception | 41 complex_pdf, 22 native_pdf, 5 scanned_pdf, 1 image |
| tests_passed | 1 321 |
| perf | on = 12,4 s / 69 docs (**180 ms/doc**) vs off = 1,5 s — **dérogation validée** : off n'exécute pas l'arbre L1-L5 ; la cible est le temps absolu ≤ 200 ms/doc, plus le ratio |
| signature | sha256 du bloc baseline + sha256 du contrat + sha256 des skills |

## 3. Gouvernance Cloud / Local

- **Moteur par défaut** : `LocalDocumentEngine` (offline souverain) ; `DOCUMENT_ENGINE=hybrid|cloud`
  opt-in. `perceive_document()` **ne lève jamais** (fallback `PERCEPTION_FAILED:<code>`).
- **Escalade cloud stricte** : `SUPERVISOR_ESCALATION_MARKERS` = {INVALID_VISION_PAYLOAD,
  INVALID_PRODUCER_PAYLOAD, montant TTC absent, RÉPONSE_OLLAMA_VIDE, LOCAL_INFERENCE_TIMEOUT} ;
  superviseur = **Gemini 3.8 Flash** (`resolve_multimodal_fallback_model`).
- **Budget dur** : `CloudEscalationBudget` (**entier strict**, `BUDGET_MAX_MUST_BE_INT`), plafond
  `budget_max(ratio, total)` avec ratio `CLOUD_ESCALATION_BUDGET_RATIO` (défaut 5 %) ; épuisé →
  `ESCALATION_BUDGET_EXCEEDED` (HITL, jamais de crash) ; traçabilité `escalation_cause`
  (`local_inference_timeout` | `schema_anchoring_failure`).
- **Timeout local** : 90 s (`LOCAL_INFERENCE_TIMEOUT_SECONDS`), non retenté.

## 4. Historique des runs de référence

`p8-off` / `p8-on` (P8) → `p8-1-final`, `p8-1-final2` (P8.1, baseline courante) —
`workspace/invoices/audit/<run_id>/report.md`.
