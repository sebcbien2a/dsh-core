# Audit de séparation `dsh-core` vs `apd-compta`

> Généré automatiquement par `scripts/audit_dsh_vs_compta_map.py` le 2026-11-07 04:56 UTC.
> **Aucun fichier n'a été déplacé, renommé ou supprimé.**

## 1. Synthèse

| Indicateur | Valeur |
|---|---:|
| Fichiers analysés | 1230 |
| Taille totale | 287225881 octets |
| Socle `dsh-core` | 341 |
| Application `apd-compta` | 696 |
| Tests / artefacts à purger | 193 |

## 2. Socle tri-système `dsh-core`

Harnais, Hermes, Codex, mémoire 4D (Qdrant / Neo4j / Mem0 / Obsidian),
Vision, OptSkill et Audit.

| Fichier | Taille (o) | Justification |
|---|---:|---|
| `## finetuning et choix  d'héberg.txt` | 11246 | racine / non classé |
| `.env` | 2483 | racine / non classé |
| `.env.example` | 1752 | racine / non classé |
| `.gitignore` | 1302 | racine / non classé |
| `03_JOURNAL.md` | 15476 | racine / non classé |
| `04_AUDIT_ARCHITECTURE.md` | 14318 | socle tri-système |
| `05_DECISIONS_ARCHITECTURE.md` | 13169 | racine / non classé |
| `06_GEMINI_HANDOVER.md` | 30180 | racine / non classé |
| `AGENTS.md` | 975 | socle tri-système |
| `agents/coder_agent.py` | 3829 | socle tri-système |
| `agents/invoice_agent.py` | 6401 | socle tri-système |
| `agents/invoice_parser_agent.py` | 1060 | socle tri-système |
| `agents/memory_agent.py` | 0 | socle tri-système |
| `agents/pdf_extractor_agent.py` | 1966 | socle tri-système |
| `agents/research_agent.py` | 13576 | socle tri-système |
| `agents/skill_creator_agent.py` | 4029 | socle tri-système |
| `agents/summary_agent.py` | 1189 | socle tri-système |
| `agents/teacher_student_agent.py` | 6870 | socle tri-système |
| `anythingllm_tool.py` | 2373 | socle tri-système |
| `api.py` | 7351 | racine / non classé |
| `api_routes/canonical_skills.py` | 8621 | socle tri-système |
| `backend/app/graphs/invoice_graph.py` | 20803 | socle tri-système |
| `backend/app/graphs/n8n_api.py` | 3084 | socle tri-système |
| `backend/app/main.py` | 476 | socle tri-système |
| `backend/app/middleware/tenant_middleware.py` | 4622 | socle tri-système |
| `backend/app/n8n_api.py` | 3084 | socle tri-système |
| `backend/app/routers/accounting_export.py` | 5502 | socle tri-système |
| `backend/app/routers/reconciliation_arbitration.py` | 16840 | socle tri-système |
| `benchmarks/data/ocr_baseline_texts.example.json` | 1515 | socle tri-système |
| `benchmarks/data/ocr_baseline_texts.json` | 31832 | socle tri-système |
| `benchmarks/extractor_benchmark_runner.py` | 18228 | socle tri-système |
| `benchmarks/extractor_comparison_plan.md` | 8422 | socle tri-système |
| `benchmarks/orientation_benchmark.py` | 8200 | socle tri-système |
| `benchmarks/README.md` | 3334 | socle tri-système |
| `benchmarks/results/.gitkeep` | 0 | socle tri-système |
| `benchmarks/results/orientation_metadata_baseline.json` | 2909 | socle tri-système |
| `benchmarks/validate_ocr_ground_truth.py` | 21427 | socle tri-système |
| `config/learned_accounting_rules.json` | 22276 | socle tri-système |
| `COPILOT_SESSION_DSH_CORE_2026-10-15.json` | 72710 | racine / non classé |
| `DATABASE_STORAGE_MAP.md` | 8716 | socle tri-système |
| `docker-compose.yml` | 2148 | socle tri-système |
| `docker/docker-compose.yml` | 374 | socle tri-système |
| `docs/00_SYSTEM_ARCHITECTURE_V5_DRAFT.md` | 7461 | documentation du socle |
| `docs/01_ARCHITECTURE_ACTUELLE.md` | 1610 | documentation du socle |
| `docs/01_ARCHITECTURE_CIBLE_V2.md` | 620 | documentation du socle |
| `docs/02_ROADMAP.md` | 12610 | documentation du socle |
| `docs/03_JOURNAL.md` | 3955 | documentation du socle |
| `docs/04_HANDOFF_DSH_CORE.md` | 5858 | documentation du socle |
| `docs/04_PROJECT_INDEX.md` | 1586 | documentation du socle |
| `docs/04_PROJECT_INDEX_DRAFT.md` | 7004 | documentation du socle |
| `docs/05_AUDIT_ARCHITECTURE_V2_ARGON.md` | 5150 | socle tri-système |
| `docs/05_MASTER_BLUEPRINT_V5.md` | 1245 | documentation du socle |
| `docs/06_ARCHITECTURE_V6.md` | 10142 | documentation du socle |
| `docs/06_DOSSIER_TERRAIN_MULTIMODAL.md` | 10079 | documentation du socle |
| `docs/06_MODELS_AND_KEYS_REGISTRY.md` | 10924 | documentation du socle |
| `docs/agents.md` | 432 | socle tri-système |
| `docs/AGENTS_REGISTRY_TARGET.md` | 2003 | socle tri-système |
| `docs/AI_MODEL_ROUTING_AND_HARNESSES.md` | 20166 | socle tri-système |
| `docs/BATCH_AUDIT_BASELINE.md` | 3064 | socle tri-système |
| `docs/collections_qdrant.md` | 7288 | socle tri-système |
| `docs/DOCUMENT_PERCEPTION_CONTRACT.md` | 11860 | socle tri-système |
| `docs/DSH_CORE_AI_ARCHITECTURE_V2.md` | 7208 | documentation du socle |
| `docs/fichier dans dsh core.png` | 538456 | documentation du socle |
| `docs/NEXT_ACTIONS.md` | 1563 | documentation du socle |
| `docs/P14_P15_SPECIFICATION.md` | 38426 | documentation du socle |
| `docs/PERSISTENCE_QDRANT_NEO4J_CONTRACT.md` | 13762 | socle tri-système |
| `docs/PROJECT_DECISIONS.md` | 5693 | documentation du socle |
| `docs/PROJECT_HANDOVER.md` | 82277 | documentation du socle |
| `docs/PROJECT_STATE.md` | 4169 | documentation du socle |
| `docs/roadmap.md` | 499 | documentation du socle |
| `docs/TEMPLATE_SKILLS_MEMORY.md` | 3072 | socle tri-système |
| `docs/workflows.md` | 503 | documentation du socle |
| `dsh core complet.md` | 38475 | racine / non classé |
| `DSH_CORE_CURRENT_STATE.md` | 44708 | racine / non classé |
| `dsh_core_master_specification_2026_2027.md` | 51490 | racine / non classé |
| `dsh_harness.py` | 4583 | socle tri-système |
| `fichiersmodifies.txt` | 268900 | racine / non classé |
| `frontend/components/reconciliation/ArbitrationModal.tsx` | 8002 | socle tri-système |
| `frontend/components/reconciliation/ReconciliationTable.tsx` | 16612 | socle tri-système |
| `frontend/types/reconciliation.ts` | 1412 | socle tri-système |
| `GEMINI_CONTEXT_PACK.md` | 16560 | racine / non classé |
| `generate_current_state_audit.ps1` | 13217 | socle tri-système |
| `generate_journal.py` | 3348 | racine / non classé |
| `image.png` | 553786 | racine / non classé |
| `IMPORTS_BROKEN_REPORT.md` | 6819 | racine / non classé |
| `INGESTION_MANAGER_PATCH.md` | 1710 | racine / non classé |
| `invoice_agent.py` | 6583 | socle tri-système |
| `invoice_indexer.py` | 4307 | socle tri-système |
| `knowledge/03_systeme_cor/01_ARCHITECTURE_CIBLE.md` | 10816 | socle tri-système |
| `knowledge/03_systeme_cor/02_DEPLOIEMENT_ET_PHASES.md` | 3779 | socle tri-système |
| `knowledge/03_systeme_cor/03_JOURNAL_SUIVI.md` | 4173 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs.htm` | 605429 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/2_AvE0.htm` | 369356 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/2_data/api_AvE0.js` | 14576 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/2_data/cb=gapi_AvE0.loaded_0` | 106527 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/2_data/css2_AvE0.css` | 30496 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/2_data/m=_b,_tp_AvE0` | 184357 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/697727167-KixCss_ltr_AvE0.css` | 4099633 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/a_002_AvE0.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/a_AvE0.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_AvE0.htm` | 187770 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/logo_googleg_gradient_color_1x_web_24dp_AvE0.png` | 924 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/logo_googleg_gradient_color_2x_web_24dp_AvE0.png` | 2075 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/m=_b,_tp_AvE0` | 224903 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/photo_002_AvE0.png` | 612 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/photo_003_AvE0.png` | 733 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/photo_004_AvE0.png` | 695 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/photo_AvE0.png` | 633 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_24px_1x_asknjuyerc_AvE0.png` | 700 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_24px_2x_asknjuyerc_AvE0.png` | 1671 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_24px_3x_asknjuyerc_AvE0.png` | 3021 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_56px_1x_asknjuyerc_AvE0.png` | 1529 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_56px_2x_asknjuyerc_AvE0.png` | 4109 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/pr_56px_3x_asknjuyerc_AvE0.png` | 7259 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/tier_wave_4d15c88d26ad039061707f8af71fc76d_AvE0.svg` | 1220 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/unnamed_002_AvE0.png` | 1180 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/account_data/unnamed_AvE0.png` | 2735 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/bscframe_AvE0.htm` | 125 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/building-blocks-sidebar-promo_AvE0.svg` | 8262 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/cb=gapi_AvE0.loaded_0` | 326542 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/cb=gapi_AvE0.loaded_1` | 55020 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/cb=gapi_AvE0.loaded_2` | 62 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/client_AvE0.js` | 14582 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/edit_002_AvE0.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/edit_AvE0.htm` | 651 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/lazy.min_AvE0.js` | 129011 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/m=kix_core_AvE0` | 3571823 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/pr_32px_asknjuyerc_AvE0.png` | 4764 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/proxy_AvE0.htm` | 506 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/proxy_data/cb=gapi_AvE0.loaded_0` | 80116 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/proxy_data/googleapis.proxy_AvE0.js` | 14598 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/RotateCookiesPage_AvE0.htm` | 276 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/RotateCookiesPage_data/m=hfcr_AvE0` | 10627 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/rs=AA2YrTt998CdWnSLmb9zS1V16Pmj71mO1w_AvE0.css` | 7335 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/rs=AA2YrTuyzoZA5CtZ8CsUNvOCBk8Z32acFg_AvE0` | 266111 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/unnamed_002_AvE0.png` | 436 | socle tri-système |
| `knowledge/03_systeme_cor/Annexe_Architecture_dsh-core - Google Docs_files/unnamed_AvE0.png` | 927 | socle tri-système |
| `knowledge/03_systeme_cor/architecture_v2_roadmap.md` | 5025 | socle tri-système |
| `knowledge/03_systeme_cor/Blueprint Architecture Systemique Agentique 3.pdf` | 494825 | socle tri-système |
| `knowledge/03_systeme_cor/Blueprint Architecture Systemique Agentique final.pdf` | 106663 | socle tri-système |
| `knowledge/03_systeme_cor/Blueprint Architecture Systemique Agentique.pdf` | 260839 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs.htm` | 612665 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/1773621574-KixCss_ltr_xK51.css` | 4116656 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/2_data/api_xK51.js` | 14576 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/2_data/cb=gapi_xK51.loaded_0` | 106527 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/2_data/css2_xK51.css` | 30496 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/2_data/m=_b,_tp_xK51` | 184357 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/2_xK51.htm` | 369353 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/a_002_xK51.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/a_xK51.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/bscframe_xK51.htm` | 125 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/building-blocks-sidebar-promo_xK51.svg` | 8262 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/cb=gapi_xK51.loaded_0` | 326543 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/cb=gapi_xK51.loaded_1` | 55020 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/cb=gapi_xK51.loaded_2` | 62 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/client_xK51.js` | 14582 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/edit_002_xK51.htm` | 108 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/edit_xK51.htm` | 662 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/lazy.min_xK51.js` | 129011 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/m=kix_core_xK51` | 3586183 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/pr_32px_asknjuyerc_xK51.png` | 4764 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/proxy_data/cb=gapi_xK51.loaded_0` | 80168 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/proxy_data/googleapis.proxy_xK51.js` | 14598 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/proxy_xK51.htm` | 506 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/RotateCookiesPage_data/m=hfcr_xK51` | 10627 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/RotateCookiesPage_xK51.htm` | 277 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/rs=AA2YrTt998CdWnSLmb9zS1V16Pmj71mO1w_xK51.css` | 7335 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/rs=AA2YrTuyzoZA5CtZ8CsUNvOCBk8Z32acFg_xK51` | 266111 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/unnamed_002_xK51.png` | 436 | socle tri-système |
| `knowledge/03_systeme_cor/Document de Suivi - Phase 2 - Google Docs_files/unnamed_xK51.png` | 927 | socle tri-système |
| `knowledge/03_systeme_cor/gemini-code-1789840110891.html` | 22281 | socle tri-système |
| `knowledge/03_systeme_cor/gemini-code-1789840482162.html` | 22475 | socle tri-système |
| `knowledge/03_systeme_cor/gemini-code-1789841510580.html` | 24766 | socle tri-système |
| `knowledge/03_systeme_cor/JOURNAL_INSTALLATION_V2.md` | 2743 | socle tri-système |
| `knowledge/03_systeme_cor/Master Blueprint V2.3 - Architecture Agentique Autonome.pdf` | 222291 | socle tri-système |
| `knowledge/03_systeme_cor/MASTER BLUEPRINT V4.0 - ARCHITECTURE SYSTÉMIQUE DSH-CORE.txt` | 0 | socle tri-système |
| `knowledge/03_systeme_cor/Master Blueprint V4.0 - DSH-CORE.htm` | 20675 | socle tri-système |
| `knowledge/rules/common/location_spectrophotometre.md` | 744 | socle tri-système |
| `mcp_config.json` | 250 | socle tri-système |
| `mcp_scrape.py` | 871 | socle tri-système |
| `mcp_skills.py` | 1750 | socle tri-système |
| `memory/chroma_db/76cde451-fcd5-4380-bcb3-114051382ab1/data_level0.bin` | 321200 | socle tri-système |
| `memory/chroma_db/76cde451-fcd5-4380-bcb3-114051382ab1/header.bin` | 100 | socle tri-système |
| `memory/chroma_db/76cde451-fcd5-4380-bcb3-114051382ab1/length.bin` | 400 | socle tri-système |
| `memory/chroma_db/76cde451-fcd5-4380-bcb3-114051382ab1/link_lists.bin` | 0 | socle tri-système |
| `memory/chroma_db/chroma.sqlite3` | 188416 | socle tri-système |
| `memory/cold_storage/invoice_archive_20260920_142857.json` | 248 | socle tri-système |
| `memory/cold_storage/invoice_archive_20260920_143153.json` | 1517 | socle tri-système |
| `memory/cold_storage/invoice_archive_20260920_143407.json` | 298 | socle tri-système |
| `memory/memory_manager.py` | 1395 | socle tri-système |
| `memory_manager.py` | 7204 | socle tri-système |
| `model_router.py` | 9199 | socle tri-système |
| `models/huggingface/.agent_harnesses.json` | 5778 | socle tri-système |
| `models/huggingface/hub/models--PaddlePaddle--PP-LCNet_x1_0_doc_ori/refs/main` | 40 | racine / non classé |
| `n8n_api.py` | 3670 | socle tri-système |
| `NEO4J_INVENTORY.md` | 6851 | socle tri-système |
| `ocr_engine.py` | 3081 | socle tri-système |
| `orchestrator.py` | 1985 | socle tri-système |
| `PATCH_PLAN.md` | 15590 | racine / non classé |
| `QDRANT_INVENTORY.md` | 6346 | socle tri-système |
| `README.md` | 7236 | racine / non classé |
| `repositories/canonical_skill_repository.py` | 12484 | socle tri-système |
| `repositories/graph_repository.py` | 6594 | socle tri-système |
| `repositories/memory_repository.py` | 4776 | socle tri-système |
| `repositories/skills_repository.py` | 4804 | socle tri-système |
| `repositories/supplier_identity_repository.py` | 6736 | socle tri-système |
| `repositories/supplier_registration_repository.py` | 5694 | socle tri-système |
| `repositories/teacher_student_repository.py` | 2843 | socle tri-système |
| `router.py` | 6042 | socle tri-système |
| `schemas/accounting_choices.py` | 5560 | socle tri-système |
| `schemas/banking_schema.py` | 26591 | socle tri-système |
| `schemas/declarative_task_schema.py` | 54860 | socle tri-système |
| `schemas/document_perception.py` | 10392 | socle tri-système |
| `schemas/document_result.py` | 408 | socle tri-système |
| `schemas/folder_state_schema.py` | 11585 | socle tri-système |
| `schemas/invoice_schema.py` | 26998 | socle tri-système |
| `schemas/master_tables_schema.py` | 40771 | socle tri-système |
| `schemas/obsidian_vault_schema.py` | 11626 | socle tri-système |
| `schemas/profile_schema.py` | 344 | socle tri-système |
| `schemas/rule_registry_schema.py` | 2590 | socle tri-système |
| `schemas/skill_manifest_schema.py` | 9007 | socle tri-système |
| `schemas/supplier_identity_schema.py` | 10751 | socle tri-système |
| `schemas/supplier_registration_schema.py` | 3758 | socle tri-système |
| `schemas/vector_payload_schema.py` | 2544 | socle tri-système |
| `scripts/audit_dsh_vs_compta_map.py` | 10147 | socle tri-système |
| `scripts/run_batch_audit.py` | 62162 | socle tri-système |
| `scripts/run_document_watcher.py` | 4649 | outillage du socle |
| `scripts/run_e2e_p14_p16.py` | 17467 | outillage du socle |
| `services/annotation_perception.py` | 18778 | socle tri-système |
| `services/business_extraction_manager.py` | 4209 | racine / non classé |
| `services/canonical_skill_creation_service.py` | 4230 | racine / non classé |
| `services/cloud_vision_extractor.py` | 11031 | socle tri-système |
| `services/declarative_pdf_service.py` | 32983 | socle tri-système |
| `services/declarative_tarif_service.py` | 44102 | socle tri-système |
| `services/document_engine.py` | 34062 | racine / non classé |
| `services/document_extractors.py` | 33200 | racine / non classé |
| `services/document_geometry.py` | 23349 | racine / non classé |
| `services/document_pipeline.py` | 7710 | socle tri-système |
| `services/document_profiler.py` | 3941 | socle tri-système |
| `services/document_router.py` | 25753 | socle tri-système |
| `services/extraction_manager.py` | 4231 | racine / non classé |
| `services/ingestion_manager.py` | 9375 | racine / non classé |
| `services/instructions_task_engine.py` | 2610 | socle tri-système |
| `services/invoice_embedding_service.py` | 1876 | socle tri-système |
| `services/invoice_skill_creator.py` | 9437 | socle tri-système |
| `services/local_master_writer.py` | 54610 | racine / non classé |
| `services/memory_manager.py` | 4658 | socle tri-système |
| `services/neo4j_service.py` | 2490 | socle tri-système |
| `services/obsidian_vault_service.py` | 46178 | socle tri-système |
| `services/ollama_vision_extractor.py` | 13340 | socle tri-système |
| `services/persistence_manager.py` | 32788 | socle tri-système |
| `services/routing_engine.py` | 5901 | racine / non classé |
| `services/rule_registry_service.py` | 18165 | socle tri-système |
| `services/skill_router.py` | 3260 | socle tri-système |
| `services/tax_classification_engine.py` | 1458 | racine / non classé |
| `services/template_skill_store.py` | 25853 | racine / non classé |
| `services/vault_accounting_bridge.py` | 10350 | socle tri-système |
| `skills/ping_qdrant.py` | 24 | socle tri-système |
| `skills/plv_generator/run.py` | 2682 | socle tri-système |
| `skills/plv_generator/SKILL.md` | 288 | socle tri-système |
| `skills/plv_inspector/run.py` | 1307 | socle tri-système |
| `skills/plv_modifier/run.py` | 1616 | socle tri-système |
| `skills/registry.json` | 251 | socle tri-système |
| `SKILLS_DUPLICATES_INVENTORY.md` | 11861 | socle tri-système |
| `SKILLS_REGISTRY_ARCHITECTURE.md` | 4845 | socle tri-système |
| `temporaires.txt` | 69767 | racine / non classé |
| `test.html` | 46 | racine / non classé |
| `TODO_CONVERGENCE.md` | 7034 | racine / non classé |
| `tools/audit_qdrant_collections.py` | 14454 | socle tri-système |
| `tools/validate_supplier_identity.py` | 26247 | socle tri-système |
| `VALIDATION FINALE DE L'ARCHITECTURE CIBLE DSH-CORE.md` | 5846 | racine / non classé |
| `validators/accounting_validator.py` | 4608 | socle tri-système |
| `WEB_TOR_AUDIT.md` | 4239 | socle tri-système |
| `workspace/bonjour.py` | 25 | racine / non classé |
| `workspace/imports/tariffs/indumix_comparison_report.json` | 128795 | racine / non classé |
| `workspace/imports/tariffs/indumix_complete_pdf_import_audit.json` | 2492 | socle tri-système |
| `workspace/imports/tariffs/indumix_final_closure_audit.json` | 3605 | socle tri-système |
| `workspace/imports/tariffs/indumix_final_validation_audit.json` | 1824 | socle tri-système |
| `workspace/imports/tariffs/indumix_remaining_anomalies.json` | 3939 | racine / non classé |
| `workspace/imports/tariffs/indumix_review.xlsx` | 25506 | racine / non classé |
| `workspace/index.html` | 4208 | racine / non classé |
| `workspace/invoices/audit/20261030-023255-f10de1e/manifest.json` | 1762 | socle tri-système |
| `workspace/invoices/audit/20261030-023255-f10de1e/records.jsonl` | 155760 | socle tri-système |
| `workspace/invoices/audit/20261030-023255-f10de1e/report.md` | 14202 | socle tri-système |
| `workspace/invoices/audit/20261030-023255-f10de1e/summary.json` | 4091 | socle tri-système |
| `workspace/invoices/audit/20261030-134203-f10de1e/manifest.json` | 1738 | socle tri-système |
| `workspace/invoices/audit/20261030-134548-f10de1e/manifest.json` | 1880 | socle tri-système |
| `workspace/invoices/audit/20261030-134548-f10de1e/records.jsonl` | 13059 | socle tri-système |
| `workspace/invoices/audit/20261030-134548-f10de1e/report.md` | 2987 | socle tri-système |
| `workspace/invoices/audit/20261030-134548-f10de1e/summary.json` | 2412 | socle tri-système |
| `workspace/invoices/audit/20261030-142245-f10de1e/manifest.json` | 2079 | socle tri-système |
| `workspace/invoices/audit/20261030-142245-f10de1e/records.jsonl` | 16019 | socle tri-système |
| `workspace/invoices/audit/20261030-142245-f10de1e/report.md` | 2786 | socle tri-système |
| `workspace/invoices/audit/20261030-142245-f10de1e/summary.json` | 2673 | socle tri-système |
| `workspace/invoices/audit/20261030-143237-f10de1e/manifest.json` | 2079 | socle tri-système |
| `workspace/invoices/audit/20261030-143237-f10de1e/records.jsonl` | 19408 | socle tri-système |
| `workspace/invoices/audit/20261030-143237-f10de1e/report.md` | 2343 | socle tri-système |
| `workspace/invoices/audit/20261030-143237-f10de1e/summary.json` | 2261 | socle tri-système |
| `workspace/invoices/audit/20261030-155907-f10de1e/manifest.json` | 2076 | socle tri-système |
| `workspace/invoices/audit/20261030-155907-f10de1e/records.jsonl` | 195992 | socle tri-système |
| `workspace/invoices/audit/20261030-155907-f10de1e/report.md` | 14217 | socle tri-système |
| `workspace/invoices/audit/20261030-155907-f10de1e/summary.json` | 4356 | socle tri-système |
| `workspace/invoices/audit/baseline.json` | 1474 | socle tri-système |
| `workspace/invoices/audit/p8-1-final/manifest.json` | 2515 | socle tri-système |
| `workspace/invoices/audit/p8-1-final/records.jsonl` | 201505 | socle tri-système |
| `workspace/invoices/audit/p8-1-final/report.md` | 15379 | socle tri-système |
| `workspace/invoices/audit/p8-1-final/summary.json` | 4544 | socle tri-système |
| `workspace/invoices/audit/p8-1-final2/manifest.json` | 2515 | socle tri-système |
| `workspace/invoices/audit/p8-1-final2/records.jsonl` | 201577 | socle tri-système |
| `workspace/invoices/audit/p8-1-final2/report.md` | 15380 | socle tri-système |
| `workspace/invoices/audit/p8-1-final2/summary.json` | 4547 | socle tri-système |
| `workspace/invoices/audit/p8-1-off/manifest.json` | 2071 | socle tri-système |
| `workspace/invoices/audit/p8-1-off/records.jsonl` | 186552 | socle tri-système |
| `workspace/invoices/audit/p8-1-off/report.md` | 14482 | socle tri-système |
| `workspace/invoices/audit/p8-1-off/summary.json` | 4265 | socle tri-système |
| `workspace/invoices/audit/p8-1-on/manifest.json` | 2512 | socle tri-système |
| `workspace/invoices/audit/p8-1-on/records.jsonl` | 201300 | socle tri-système |
| `workspace/invoices/audit/p8-1-on/report.md` | 15373 | socle tri-système |
| `workspace/invoices/audit/p8-1-on/summary.json` | 4539 | socle tri-système |
| `workspace/invoices/audit/p8-off/manifest.json` | 2069 | socle tri-système |
| `workspace/invoices/audit/p8-off/records.jsonl` | 186416 | socle tri-système |
| `workspace/invoices/audit/p8-off/report.md` | 14480 | socle tri-système |
| `workspace/invoices/audit/p8-off/summary.json` | 4262 | socle tri-système |
| `workspace/invoices/audit/p8-on-final/manifest.json` | 2516 | socle tri-système |
| `workspace/invoices/audit/p8-on-final/records.jsonl` | 203667 | socle tri-système |
| `workspace/invoices/audit/p8-on-final/report.md` | 15459 | socle tri-système |
| `workspace/invoices/audit/p8-on-final/summary.json` | 4669 | socle tri-système |
| `workspace/invoices/audit/p8-on/manifest.json` | 2481 | socle tri-système |
| `workspace/invoices/audit/p8-on/records.jsonl` | 203252 | socle tri-système |
| `workspace/invoices/audit/p8-on/report.md` | 15441 | socle tri-système |
| `workspace/invoices/audit/p8-on/summary.json` | 4657 | socle tri-système |
| `workspace/qdrant/audit/qdrant-audit-20261031T160418Z.json` | 13139 | socle tri-système |
| `workspace/qdrant/audit/qdrant-audit-20261031T160559Z.json` | 15128 | socle tri-système |
| `workspace/qdrant/audit/qdrant-audit-20261031T165427Z.json` | 13139 | socle tri-système |
| `workspace/qdrant/audit/qdrant-audit-20261031T165442Z.json` | 15127 | socle tri-système |
| `workspace/qdrant/audit/qdrant-audit-20261031T165449Z.json` | 5723 | socle tri-système |
| `workspace/skills/invoice_crisol_color.json` | 2253 | socle tri-système |
| `workspace/skills/invoice_edf.json` | 1878 | socle tri-système |
| `workspace/skills/invoice_smtrt_sas.json` | 2101 | socle tri-système |
| `workspace/somme.py` | 283 | racine / non classé |
| `workspace/vault/_system/schema.yaml` | 1397 | socle tri-système |

## 3. Application métier `apd-compta`

Ingestion factures / OFX, rapprochement, TVA CA12, Cockpit, référentiel
tiers et règles comptables.

| Fichier | Taille (o) | Justification |
|---|---:|---|
| `BANKING_ARCHITECTURE.md` | 10166 | métier comptable APD |
| `BANKING_RECORD_SPEC.md` | 16709 | métier comptable APD |
| `BANKING_RELATIONS_REVIEW.md` | 21355 | métier comptable APD |
| `BANKING_SCHEMA_REVIEW.md` | 11261 | métier comptable APD |
| `data/accounting/accounting.sqlite3` | 548864 | métier comptable APD |
| `data/accounting/accounting.sqlite3.bak_p24` | 475136 | métier comptable APD |
| `Demarrer_Cockpit_DSH_Bureau2.bat` | 2330 | métier comptable APD |
| `docs/PIPELINE_FACTURE_V1.md` | 4993 | métier comptable APD |
| `docs/SUPPLIER_SKILL_MIGRATION_SPEC.md` | 26882 | métier comptable APD |
| `INVOICE_SCHEMA_ORIGIN.md` | 10429 | métier comptable APD |
| `INVOICE_SCHEMA_RECONSTRUCTION.md` | 5713 | métier comptable APD |
| `knowledge/rules/accounting/RULE_TVA_ZERO_DISAMBIGUATION_001.md` | 3616 | métier comptable APD |
| `Lancer_Cockpit_APD.bat` | 1318 | métier comptable APD |
| `PIPELINE_OFFICIEL.md` | 16407 | métier comptable APD |
| `run_site_builder.py` | 466 | métier comptable APD |
| `schemas/bank_statement_schema.py` | 0 | métier comptable APD |
| `schemas/payment_method_schema.py` | 6610 | métier comptable APD |
| `scripts/apd_pipeline_web.py` | 7540 | métier comptable APD |
| `scripts/apply_yucel_opening_statement.py` | 6093 | métier comptable APD |
| `scripts/export_catalog_pdf.py` | 8473 | métier comptable APD |
| `scripts/fetch_apd_emails.py` | 17622 | métier comptable APD |
| `scripts/refresh_cockpit.py` | 15657 | métier comptable APD |
| `scripts/run_apd_four_stage_pipeline.py` | 2250 | métier comptable APD |
| `scripts/run_pipeline_pilot.py` | 9055 | métier comptable APD |
| `scripts/simulate_ca12_apd.py` | 6070 | métier comptable APD |
| `scripts/translate_supplier_catalog.py` | 18627 | métier comptable APD |
| `services/accounting_cleanup_service.py` | 7654 | métier comptable APD |
| `services/accounting_memory_learning_service.py` | 17024 | métier comptable APD |
| `services/accounting_spreadsheet_export_service.py` | 150553 | métier comptable APD |
| `services/acompleter_readable_service.py` | 19021 | métier comptable APD |
| `services/acompleter_workbook_service.py` | 34536 | métier comptable APD |
| `services/apd_control_panel.py` | 19133 | métier comptable APD |
| `services/apd_evidence_rules.py` | 33341 | métier comptable APD |
| `services/apd_four_stage_pipeline.py` | 91530 | métier comptable APD |
| `services/balance_simulation_service.py` | 20888 | métier comptable APD |
| `services/bank_statement_parser_service.py` | 10668 | métier comptable APD |
| `services/banking_invoice_producer.py` | 25807 | métier comptable APD |
| `services/bosch_sia_invoice_parser.py` | 17180 | métier comptable APD |
| `services/customer_invoice_batch_importer.py` | 46013 | métier comptable APD |
| `services/customer_invoice_reconciliation.py` | 34572 | métier comptable APD |
| `services/document_flow_watcher.py` | 33534 | métier comptable APD |
| `services/email_inbox_bridge.py` | 30888 | métier comptable APD |
| `services/fec_export_service.py` | 4849 | métier comptable APD |
| `services/folder_sync_service.py` | 17965 | métier comptable APD |
| `services/folder_tree_service.py` | 12101 | métier comptable APD |
| `services/hermes_accounting_bridge.py` | 23690 | métier comptable APD |
| `services/hotel_expense_parser.py` | 16833 | métier comptable APD |
| `services/inbox_ingestion_service.py` | 69255 | métier comptable APD |
| `services/invoice_party_supplier_identity_adapter.py` | 2534 | métier comptable APD |
| `services/invoice_producer_protocol.py` | 22996 | métier comptable APD |
| `services/invoice_reconciliation_engine.py` | 3009 | métier comptable APD |
| `services/invoice_similarity_service.py` | 4063 | métier comptable APD |
| `services/invoice_skill_applier.py` | 7208 | métier comptable APD |
| `services/lcr_remittance_parser.py` | 28706 | métier comptable APD |
| `services/local_invoice_extractor.py` | 20543 | métier comptable APD |
| `services/ofx_bank_statement_parser.py` | 19681 | métier comptable APD |
| `services/opening_statement_ingestion_service.py` | 18044 | métier comptable APD |
| `services/pdf_splitter_and_linker.py` | 19823 | métier comptable APD |
| `services/settled_invoice_parser.py` | 18454 | métier comptable APD |
| `services/skill_lifecycle_service.py` | 9112 | métier comptable APD |
| `services/spreadsheet_invoice_producer.py` | 26337 | métier comptable APD |
| `services/sqlite_accounting_store.py` | 60275 | métier comptable APD |
| `services/supplier_memory_service.py` | 9493 | métier comptable APD |
| `services/supplier_registration_coordinator.py` | 14731 | métier comptable APD |
| `services/supplier_registration_service.py` | 13067 | métier comptable APD |
| `services/supplier_repository.py` | 9528 | métier comptable APD |
| `services/tier_reference_service.py` | 29515 | métier comptable APD |
| `services/tva_zero_rule_evaluator.py` | 10614 | métier comptable APD |
| `services/vat_simulation_service.py` | 39095 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600023334.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600027786.pdf` | 33631 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600037983.pdf` | 34321 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600051571.pdf` | 34301 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600054034.pdf` | 33825 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600061106.pdf` | 33255 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600064577.pdf` | 33826 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600064630.pdf` | 34275 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600075353.pdf` | 33681 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600080268.pdf` | 33234 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600085151.pdf` | 33903 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600092496.pdf` | 34595 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600129397.pdf` | 34045 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600129518.pdf` | 34035 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600134219.pdf` | 33684 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600154494.pdf` | 34097 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600154543.pdf` | 33614 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600154577.pdf` | 33320 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600154616.pdf` | 34490 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600173334.pdf` | 34297 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600174186.pdf` | 33642 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600191177.pdf` | 33541 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600214417.pdf` | 33433 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600225363.pdf` | 33259 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600225382.pdf` | 33723 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600225397.pdf` | 34470 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600225418.pdf` | 35156 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600235029.pdf` | 33639 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600247831.pdf` | 34024 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600247837.pdf` | 34018 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600252598.pdf` | 33705 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600258748.pdf` | 34033 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600262656.pdf` | 33535 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600262898.pdf` | 33334 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600271537.pdf` | 33854 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600297768.pdf` | 34822 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600314118.pdf` | 33391 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600320515.pdf` | 32560 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600362330.pdf` | 33819 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600399271.pdf` | 34438 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600410844.pdf` | 33755 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600410857.pdf` | 33586 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600418849.pdf` | 34146 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600428183.pdf` | 33928 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600428193.pdf` | 34921 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600452848.pdf` | 34138 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600452856.pdf` | 33805 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600464425.pdf` | 33921 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600477370.pdf` | 34013 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600503410.pdf` | 41128 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600505914.pdf` | 41430 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600506047.pdf` | 41618 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600511180 (1).pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600511180 (2).pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600511180.pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600527437.pdf` | 41498 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600549351.pdf` | 42091 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600551733.pdf` | 42369 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600561148.pdf` | 41414 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600634151.pdf` | 41913 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600642469.pdf` | 41946 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600646907.pdf` | 41770 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600652853.pdf` | 42165 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600670044.pdf` | 41914 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600706582.pdf` | 41624 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600729081.pdf` | 41580 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600729123.pdf` | 42012 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600731887.pdf` | 42266 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/boxtal_2600731983.pdf` | 42145 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/FAC-J2718_SAS-DIFFAM automatisme.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/FAC-J2718_SAS-DIFFAM.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Factura tradeinn 2.pdf` | 139393 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Factura tradeinn.pdf` | 113206 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Facture 381 du 30_06_2025_01.pdf` | 42295 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/facture ang- 2026-11-06T115933.289.Zeendoc.pdf` | 12813 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Facture client - 262105.pdf` | 60203 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/facture le temps des cerises vêtements .pdf` | 298643 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Facture lidle  n° 2613710696.pdf` | 90865 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Facture lidle n° 2613710696.pdf` | 90865 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/FACTURE N°41000190 DU 31_10_2024.eml` | 33181 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/RE_ SMTRT.eml` | 68207 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/receipt_cfa65429-9b73-5640-8e99-f82d095b21f9.pdf` | 141634 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/receipt_fd30ae49-c05e-5a18-a53b-a33b6677733e.pdf` | 136067 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/SMTRT.eml` | 53857 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Y16V_SD_INVOICE202609243956114620.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/00_INBOX/Y16V_SD_ORDER_CONF 20260916 152129 4086518725.pdf` | 57071 | métier comptable APD |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER.xlsx` | 27901 | métier comptable APD |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER_MIS_A_JOUR.xlsx` | 6661 | métier comptable APD |
| `workspace/document_flow/APD/02_A_COMPLETER/F-2026-037.pdf` | 28133 | métier comptable APD |
| `workspace/document_flow/APD/02_A_COMPLETER/Invoice-GENMCYCQ-0001.pdf` | 28768 | métier comptable APD |
| `workspace/document_flow/APD/02_A_COMPLETER/Receipt-2136-4590.pdf` | 29067 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/092026 AR APD 1407.pdf` | 308200 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/_data_-_[archived__IC].pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/_data_-_[archived__IC][1].pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/Facture 381 du 30_06_2025_01.pdf` | 42295 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/Facture client - 262105__1__1.pdf` | 60203 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/facture ha paintcar avril pour juin 16  ILAA0F41__1__1.pdf` | 59769 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/Facture Sia 251430 octobre16__1__1.pdf` | 1665061 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/facture_paintcar.pdf` | 148824 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/ILA193A1__1__1.pdf` | 189164 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/ILA7CC61__1__1.pdf` | 148824 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/ACHATS_MARCHANDISES/Y16V_SD_INVOICE202609243956114620.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/BANQUE_FINANCE/Bordereau_remise_deffet_encaissement.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/BANQUE_FINANCE/operations_01012026_03112026.ofx` | 100436 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/DIVERS_A_CLASSER/Y16V_SD_ORDER_CONF 20260916 152129 4086518725__1__1.pdf` | 57071 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/18417.pdf` | 815527 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/19544__1.pdf` | 850774 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/19544__2__1.pdf` | 850774 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/FAC-J2718_SAS-DIFFAM__1__1.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/facture creation holding__1__1.pdf` | 220663 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202601_30827543_1439374235__1__1.pdf` | 112325 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202602_30827543_1447097480__1__1.pdf` | 112028 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202603_30827543_1454768315__1__1.pdf` | 112206 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202604_30827543_1462284220__1__1.pdf` | 112376 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202605_30827543_1469336723__1__1.pdf` | 101185 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202606_30827543_1476879577__1__1.pdf` | 101078 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202607_30827543_1484427884__1__1.pdf` | 100880 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/FRAIS_GENERAUX/Facture_Free_202608_30827543_1492012280 (1)__1__1.pdf` | 112571 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/boxtal_2600023334.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/Facture 423 du 31_05_2025_01__1.pdf` | 42282 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/Facture 423 du 31_05_2025_01__2__1.pdf` | 42282 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/TRANSPORTS_LOGISTIQUE/W260707W1957907O306043910__1__1.pdf` | 6049 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/avoir cholet 2026__1__1.pdf` | 578059 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/facture carrosserie yucel oct a mai.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/Facture client - 262104.pdf` | 60894 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/Facture client - 263075.pdf` | 61518 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/Facture client - 263075__1.pdf` | 61785 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/factures clients 011225 a 311026.pdf` | 19042945 | métier comptable APD |
| `workspace/document_flow/APD/03_VALIDE_PRET_FEC/VENTES_CLIENTS/factures clients.pdf` | 5658978 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT.xlsx` | 7650 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/FRAIS_REPRESENTATION_PRESIDENT/TABLEAU_FRAIS_REPRESENTATION_PRESIDENT.xlsx` | 5566 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HONORAIRES_JURIDIQUES/TABLEAU_HONORAIRES_JURIDIQUES.xlsx` | 5490 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE.xlsx` | 5343 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/INFORMATIQUE_WEB/TABLEAU_INFORMATIQUE_WEB.xlsx` | 5548 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION.xlsx` | 6424 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX.xlsx` | 5790 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING.xlsx` | 6404 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/GRAND_LIVRE_APD_TOUS_FOURNISSEURS.xlsx` | 61656 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/GRAND_LIVRE_CLIENTS_DETAILLES.xlsx` | 12224 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ASSISTANCE_PEINTURE_DIFFUSION_INVOICE_DE5BD79D_ADCF_573E_ADE0_7844BBC61F29_2025-06-16.pdf` | 42295 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ASSISTANCE_PEINTURE_DIFFUSION_SAS_INVOICE_BA9E8480_FFE0_5669_9D2D_1D24860D8276_2026-09-28.pdf` | 308200 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/BOSCH_SIA_AVOIR_3956106092_2026-09-14.pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/BOSCH_SIA_AVOIR_3956114620_2026-09-24.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/BOSCH_SIA_FACTURE_3956106093_2026-09-14.pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/BOXTAL_FACTURE_2600023334_2026-01-12.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/BOXTAL_FACTURE_REGLEE_605898AD_6F15_5613_BB8E_F5F5B73F26BA.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/CAISSE_D_EPARGNE_BORDEREAU_32000028917640.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/CAISSE_D_EPARGNE_BORDEREAU_LCR_391CE287_877C_57D1_BF3D_02A31E1B2E11.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/CAISSE_D_EPARGNE_SOURCE_OFX_267B4C9E_97B1_582C_9D2D_DFC9D5074AB4.pdf` | 100436 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/CASSIS41V_FACTURE_FACTURE_423_DU_31_05_2025_01_2025-05-19.pdf` | 42282 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/CHRONOPOST_FACTURE_W260707W1957907O306043910_2026-07-07.pdf` | 6049 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/EI_ATIA_SONIA_TALENT_CO_FACTURE_F_2026_037_2026-10-19.pdf` | 28133 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/EM_A_LES_EMAUX_DE_L_ATLANTIQUE_FACTURE_262105_2026-06-30.pdf` | 60203 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1439374235_2026-01-02.pdf` | 112325 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1447097480_2026-02-02.pdf` | 112028 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1454768315_2026-03-02.pdf` | 112206 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1462284220_2026-04-02.pdf` | 112376 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1469336723_2026-05-02.pdf` | 101185 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1476879577_2026-06-02.pdf` | 101078 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1484427884_2026-07-02.pdf` | 100880 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/FREE_TELECOM_FREE_MOBILE_FACTURE_1492012280_2026-08-02.pdf` | 112571 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/HABITAT_AUTOMATISME_SAS_DIFFAM_FACTURE_FAC_J2718_2026-07-27.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/MISE_SUR_ORBITE_FACTURE_18417_2026-05-22.pdf` | 815527 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/MISE_SUR_ORBITE_FACTURE_19544_2026-10-02.pdf` | 850774 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/OPENROUTER_INC_FACTURE_INVOICE_GENMCYCQ_0001.pdf` | 28768 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/OPENROUTER_INC_FACTURE_RECEIPT_2136_4590.pdf` | 29067 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/PAINTCAR_FACTURE_ILA193A1_2026-09-14.pdf` | 189164 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/PAINTCAR_FACTURE_ILA7CC61_2026-09-14.pdf` | 148824 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/PAINTCAR_FACTURE_ILAA0F41_2016-04-22.pdf` | 59769 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ROBERT_BOSCH_SIA_ABRASIVES_AVOIR_0ED76450_126B_5FFE_9535_57423B6C820B.pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ROBERT_BOSCH_SIA_ABRASIVES_AVOIR_385AC041_BABE_564D_9864_988611FB3F08.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ROBERT_BOSCH_SIA_ABRASIVES_BON_DE_COMMANDE_NON_COMPTABLE_4086518725_2026-09-21.pdf` | 57071 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/ROBERT_BOSCH_SIA_ABRASIVES_FACTURE_DFB0CA78_3895_54BF_ACDA_F3B98D0CAF85.pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/SIA_ABRASIVES_FRANCE_FACTURE_91E3FD7A_297D_598F_8521_B0366EEFBBA8_2016-08-02.pdf` | 1665061 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/TRANSPORTS_CATROUX_FACTURE_423_2025-05-31.pdf` | 42282 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/YASMIN_CONSULTING_FACTURE_1025_001_2025-10-05.pdf` | 220663 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/ACHATS/YASMIN_CONSULTING_FACTURE_FACTURE_CREATION_HOLDING.pdf` | 220663 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/49365BA4_1084_558A_BDAD_8292BD97C127_2026-06-30_LES_EMAUX_DE_L_ATLANTIQUE.pdf` | 60894 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/4FDEF1F4_5EAA_5095_B685_20711F67CCCE_CLIENTS.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/57398026_CF70_53CA_97E2_868D10EBC625_CLIENTS.pdf` | 19042945 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/85155D43_D2F2_5880_967B_2B4E3829CEC8_CLIENTS.pdf` | 5658978 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/AVOIR_CHOLET_2026_2026-01-16_CHOLLET.pdf` | 578059 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/BB5EC735_57BC_569E_B2AC_59E54CD7CFE1_2026-09-30_LES_EMAUX_DE_L_ATLANTIQUE.pdf` | 61785 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/BF967099_CC46_50E4_ADAB_1E2D7016CFDC_2026-09-30_LES_EMAUX_DE_L_ATLANTIQUE.pdf` | 61518 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16222_2024-10-31_CARROSSERIE_YUCELL.pdf` | 530118 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16247_2024-11-30_CARROSSERIE_YUCELL.pdf` | 453388 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16262_2024-12-31_CARROSSERIE_YUCELL.pdf` | 715873 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16277_2025-01-31_CARROSSERIE_YUCELL.pdf` | 528025 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16292_2025-02-28_CARROSSERIE_YUCELL.pdf` | 639174 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16306_2025-03-31_CARROSSERIE_YUCELL.pdf` | 526076 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16325_2025-04-30_CARROSSERIE_YUCELL.pdf` | 528528 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16400_2025-11-28_SPPV.pdf` | 401791 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16402_2025-11-28_CHOLLET.pdf` | 478515 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16403_2025-11-28_CHOLLET.pdf` | 398797 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16405_2025-11-30_CARS_18.pdf` | 398908 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16406_2025-11-30_CARROSSERIE_YUCELL.pdf` | 680102 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16407_2025-11-30_GP_AUTO_36_DP2C.pdf` | 478084 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16408_2025-11-30_GP_AUTO_NEVERS.pdf` | 559424 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16409_2025-11-30_CALANDRE_18.pdf` | 400705 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16410_2025-11-30_CALANDRE_18.pdf` | 398786 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16411_2025-11-30_SOLFA_CARBURANT.pdf` | 399501 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16412_2025-11-30_YDES_AUTO_ACCESSOIRES_GP15.pdf` | 480626 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16413_2025-12-31_CARROSSERIE_YUCELL.pdf` | 679833 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16413_2025-12-31_CARROSSERIE_YUCELL__1.pdf` | 716231 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16414_2025-12-31_CALANDRE_18.pdf` | 476352 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16414_2025-12-31_CALANDRE_18__1.pdf` | 515403 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16415_2025-12-31_CARS_18.pdf` | 398917 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16415_2025-12-31_CARS_18__1.pdf` | 438452 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16416_2025-12-31_CHOLLET.pdf` | 402332 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16416_2025-12-31_CHOLLET__1.pdf` | 514498 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16417_2025-12-31_CHOLLET.pdf` | 398795 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16417_2025-12-31_CHOLLET__1.pdf` | 438358 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16418_2025-12-31_CALANDRE_18.pdf` | 398781 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16418_2025-12-31_CALANDRE_18__1.pdf` | 438339 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16419_2025-12-31_GP_AUTO_NEVERS.pdf` | 555826 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16419_2025-12-31_GP_AUTO_NEVERS__1.pdf` | 594573 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16420_2025-12-31_GP_AUTO_36_DP2C.pdf` | 475572 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16420_2025-12-31_GP_AUTO_36_DP2C__1.pdf` | 514660 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16421_2026-01-30_CHOLLET.pdf` | 481778 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16421_2026-01-30_CHOLLET__1.pdf` | 594372 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16422_2026-01-30_CHOLLET.pdf` | 398796 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16422_2026-01-30_CHOLLET__1.pdf` | 438352 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16423_2026-01-31_CARROSSERIE_YUCELL.pdf` | 475491 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16423_2026-01-31_CARROSSERIE_YUCELL__1.pdf` | 514423 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16424_2026-01-31_CALANDRE_18.pdf` | 476706 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16424_2026-01-31_CALANDRE_18__1.pdf` | 515768 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16425_2026-01-31_CALANDRE_18.pdf` | 398787 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16425_2026-01-31_CALANDRE_18__1.pdf` | 438343 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16426_2026-01-31_CARS_18.pdf` | 398903 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16426_2026-01-31_CARS_18__1.pdf` | 438435 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16427_2026-01-31_GP_AUTO_NEVERS.pdf` | 480921 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16427_2026-01-31_GP_AUTO_NEVERS__1.pdf` | 520113 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16428_2026-01-31_GP_AUTO_36_DP2C.pdf` | 476098 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16428_2026-01-31_GP_AUTO_36_DP2C__1.pdf` | 515227 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16429_2026-01-31_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 399035 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16429_2026-01-31_CARROSSERIE_CHANTEREAU_STEPHANO__1.pdf` | 438585 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16430_2026-01-31_CARROSSERIE_RIGAL.pdf` | 600866 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16430_2026-01-31_CARROSSERIE_RIGAL__1.pdf` | 637440 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16431_2026-01-31_SOLFA_CARBURANT.pdf` | 401698 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16431_2026-01-31_SOLFA_CARBURANT__1.pdf` | 441247 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16432_2026-01-31_GP_AUTO_03100.pdf` | 400953 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16432_2026-01-31_GP_AUTO_03100__1.pdf` | 440586 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16433_2026-01-31_TECHNI_WHEEL.pdf` | 399267 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16433_2026-01-31_TECHNI_WHEEL__1.pdf` | 438823 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16434_2026-02-10_TECHNI_WHEEL_OSTWALD.pdf` | 399836 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16434_2026-02-10_TECHNI_WHEEL_OSTWALD__1.pdf` | 439413 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16435_2026-01-31_SCTI.pdf` | 401035 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16435_2026-01-31_SCTI__1.pdf` | 440638 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16436_2026-01-31_YDES_AUTO_ACCESSOIRES_GP15.pdf` | 757754 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16436_2026-01-31_YDES_AUTO_ACCESSOIRES_GP15__1.pdf` | 793365 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16437_2026-02-28_CHOLLET.pdf` | 476047 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16437_2026-02-28_CHOLLET__1.pdf` | 515139 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16438_2026-02-28_CHOLLET.pdf` | 398798 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16438_2026-02-28_CHOLLET__1.pdf` | 438355 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16439_2026-02-28_CARS_18.pdf` | 476342 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16439_2026-02-28_CARS_18__1.pdf` | 515573 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16440_2026-02-28_CALANDRE_18.pdf` | 476565 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16440_2026-02-28_CALANDRE_18__1.pdf` | 515719 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16441_2026-02-28_CALANDRE_18.pdf` | 398784 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16441_2026-02-28_CALANDRE_18__1.pdf` | 438341 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16442_2026-02-28_GP_AUTO_NEVERS.pdf` | 477687 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16442_2026-02-28_GP_AUTO_NEVERS__1.pdf` | 516949 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16443_2026-02-28_GP_AUTO_36_DP2C.pdf` | 477752 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16443_2026-02-28_GP_AUTO_36_DP2C__1.pdf` | 516786 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16444_2026-02-28_CARROSSERIE_YUCELL.pdf` | 603016 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16444_2026-02-28_CARROSSERIE_YUCELL__1.pdf` | 639571 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16445_2026-02-28_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 600973 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16445_2026-02-28_CARROSSERIE_CHANTEREAU_STEPHANO__1.pdf` | 637548 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16446_2026-02-28_CARROSSERIE_RIGAL.pdf` | 398809 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16446_2026-02-28_CARROSSERIE_RIGAL__1.pdf` | 438367 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16447_2026-02-28_SCTI.pdf` | 601908 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16447_2026-02-28_SCTI__1.pdf` | 638450 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16453_2026-03-31_CHOLLET.pdf` | 398813 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16454_2026-03-31_GP_AUTO_NEVERS.pdf` | 561582 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16455_2026-03-31_GP_AUTO_36_DP2C.pdf` | 480057 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16456_2026-03-31_CARS_18.pdf` | 401389 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16457_2026-03-31_CALANDRE_18.pdf` | 477046 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16458_2026-03-31_CALANDRE_18.pdf` | 398783 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16459_2026-03-31_CARROSSERIE_YUCELL.pdf` | 676956 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16460_2026-03-31_TECHNI_WHEEL_OSTWALD.pdf` | 473916 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16461_2026-03-31_TECHNI_WHEEL.pdf` | 474275 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16462_2026-03-31_SAVOIE_JANTES.pdf` | 399238 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16463_2026-03-31_CARROSSERIE_RIGAL.pdf` | 398783 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16464_2026-03-31_CARROSSERIE_VINOLIENNE.pdf` | 400066 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16464_2026-03-31_CARROSSERIE_VINOLIENNE__1.pdf` | 439657 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16465_2026-04-15_CHOLLET.pdf` | 399940 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16466_2026-04-30_GP_AUTO_NEVERS.pdf` | 481295 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16467_2026-04-30_GP_AUTO_36_DP2C.pdf` | 477501 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16468_2026-04-30_CALANDRE_18.pdf` | 401585 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16469_2026-04-30_CALANDRE_18.pdf` | 398787 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16470_2026-04-30_CARS_18.pdf` | 400750 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16471_2026-04-30_CARROSSERIE_YUCELL.pdf` | 676912 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16472_2026-04-30_CARROSSERIE_VINOLIENNE.pdf` | 399252 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16473_2026-04-30_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 398799 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16474_2026-04-30_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 398817 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16475_2026-04-30_CHOLLET.pdf` | 400020 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16476_2026-04-30_YDES_AUTO_ACCESSOIRES_GP15.pdf` | 719415 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16477_2026-04-30_SCTI.pdf` | 399531 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16478_2026-04-30_SPPV.pdf` | 402240 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16479_2026-04-30_GP_AUTO_MOULINS_03.pdf` | 400160 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16480_2026-04-30_GP_AUTO_18.pdf` | 399463 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16481_2026-05-30_GP_AUTO_NEVERS.pdf` | 477500 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16482_2026-05-30_GP_AUTO_36_DP2C.pdf` | 479911 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16483_2026-05-30_CARS_18.pdf` | 603288 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16484_2026-05-30_CARROSSERIE_YUCELL.pdf` | 475820 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16485_2026-05-30_SOLFA_CARBURANT.pdf` | 400092 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16486_2026-05-30_CARROSSERIE_RIGAL.pdf` | 398993 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16487_2026-05-30_CALANDRE_18.pdf` | 401249 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16488_2026-05-30_CALANDRE_18.pdf` | 398783 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16492_2026-06-30_CHOLLET.pdf` | 402048 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16493_2026-06-30_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 399716 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16494_2026-06-30_SOLFA_CARBURANT.pdf` | 400333 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16495_2026-06-30_CARROSSERIE_YUCELL.pdf` | 677332 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16496_2026-06-30_CALANDRE_18.pdf` | 475522 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16497_2026-06-30_CALANDRE_18.pdf` | 398788 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16498_2026-06-30_CARS_18.pdf` | 400558 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16499_2026-06-30_CARROSSERIE_RIGAL.pdf` | 399280 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16500_2026-06-30_GP_AUTO_36_DP2C.pdf` | 554936 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16501_2026-06-30_GP_AUTO_NEVERS.pdf` | 402334 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16502_2026-06-30_GP_AUTO_18.pdf` | 402098 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16504_2026-06-30_CARROSSERIE_VINOLIENNE.pdf` | 400083 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16505_2026-06-30_TECHNI_WHEEL_OSTWALD.pdf` | 475176 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16506_2026-06-30_TECHNI_WHEEL.pdf` | 474113 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16507_2026-07-29_MAIRIE_DE_AFA.pdf` | 497016 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16508_2026-07-31_CARROSSERIE_YUCELL.pdf` | 602981 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16509_2026-07-31_CALANDRE_18.pdf` | 401662 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16510_2026-07-31_CALANDRE_18.pdf` | 398788 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16511_2026-07-31_GP_AUTO_36_DP2C.pdf` | 480503 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16512_2026-07-31_GP_AUTO_NEVERS.pdf` | 557758 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16513_2026-07-31_YDES_AUTO_ACCESSOIRES_GP15.pdf` | 677698 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16514_2026-07-31_CHOLLET.pdf` | 401944 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16515_2026-07-31_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 399938 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16516_2026-07-31_CARROSSERIE_RIGAL.pdf` | 398749 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16517_2026-07-31_CARS_18.pdf` | 400722 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16518_2026-07-31_GP_AUTO_18.pdf` | 475311 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16519_2026-08-20_SAVOIE_JANTES.pdf` | 600826 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16520_2026-08-20_MAIRIE_DE_AFA.pdf` | 496574 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16521_2026-08-31_GP_AUTO_36_DP2C.pdf` | 478775 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16522_2026-08-31_CARROSSERIE_VINOLIENNE.pdf` | 677085 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16523_2026-08-31_GP_AUTO_NEVERS.pdf` | 558420 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16524_2026-08-31_CALANDRE_18.pdf` | 400112 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16525_2026-08-31_GP_AUTO_18.pdf` | 475862 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16526_2026-08-31_CALANDRE_18.pdf` | 398784 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16527_2026-09-30_CHOLLET.pdf` | 401710 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16528_2026-09-30_CARROSSERIE_YUCELL.pdf` | 677575 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16529_2026-09-30_CARROSSERIE_YUCELL.pdf` | 399115 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16530_2026-09-30_CALANDRE_18.pdf` | 477455 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16531_2026-09-30_CARROSSERIE_VINOLIENNE.pdf` | 400214 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16532_2026-09-30_CARROSSERIE_RIGAL.pdf` | 399840 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16533_2026-09-30_CALANDRE_18.pdf` | 398787 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16534_2026-09-30_SOLFA_CARBURANT.pdf` | 400090 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16535_2026-09-30_SCTI.pdf` | 602229 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16536_2026-09-30_GP_AUTO_36_DP2C.pdf` | 555586 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16537_2026-09-30_GP_AUTO_18.pdf` | 400819 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16538_2026-09-30_GP_AUTO_NEVERS.pdf` | 558264 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16539_2026-09-30_CARROSSERIE_CHANTEREAU_STEPHANO.pdf` | 398798 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16540_2026-09-30_SPPV.pdf` | 477384 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16541_2026-10-30_CHOLLET.pdf` | 401902 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/PIECES_JOINTES/VENTES/FA16543_2026-10-12_CARROSSERIE_VINOLIENNE.pdf` | 399109 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/RAPPORT_COHERENCE_PIECES.xlsx` | 4915 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/REFERENTIEL_TIERS_APD.xlsx` | 6951 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_BILAN_RESULTAT_2026.xlsx` | 20471 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_CA12_2026.xlsx` | 27636 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_ACHATS_MARCHANDISES.xlsx` | 8198 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_BANQUE_FINANCE.xlsx` | 8355 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_DIVERS_A_CLASSER.xlsx` | 7514 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_FRAIS_GENERAUX.xlsx` | 18621 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_TRANSPORTS_LOGISTIQUE.xlsx` | 11281 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_APD_VENTES_CLIENTS.xlsx` | 21056 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_CATEGORIES.xlsx` | 10940 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_BANQUE_TVA.xlsx` | 34920 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_PIECES_COMPTABLES.xlsx` | 25006 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_VENTES_CLIENTS.xlsx` | 23703 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT.xlsx` | 5279 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE.xlsx` | 5323 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION.xlsx` | 5294 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX.xlsx` | 5583 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING.xlsx` | 5319 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/Bordereau_remise_deffet_encaissement.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/factures clients.pdf` | 5658978 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/Liste des factures clients.csv` | 4493 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/operations_01012026_03112026 (1).ofx` | 100436 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/RAPPORT_P20_REEL.json` | 15633 | métier comptable APD |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/SOURCES_REEL/RAPPORT_P20_REEL_RECON.json` | 17953 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC].pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC][1].pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC][1]__1.pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC][1]__2.pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC][1]__3.pdf` | 73151 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC]__1.pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC]__2.pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/_data_-_[archived__IC]__3.pdf` | 72921 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/ACCR_ACCRACQT_8e1a303a-10e6-445a-b029-97536aaca9b4_2026-10-12_AG_10_MySys_.pdf` | 161681 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/ACCR_ACCRACQT_db8182da-75b5-4183-843a-b2c17481c135_2026-10-12_AG_10_MySys_.pdf` | 161678 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/AOUT 26.pdf` | 90475 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Bordereau_remise_deffet_encaissement.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Bordereau_remise_deffet_encaissement__1.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Bordereau_remise_deffet_encaissement__2.pdf` | 211538 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/boxtal_2600023334.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/boxtal_2600023334__1.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/boxtal_2600023334__2.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/boxtal_2600023334__3.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/BRW44FA6645219F_001113.pdf SUITE BILAN 2025.pdf` | 164295 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/CMR ASS P.pdf..pdf` | 274512 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/DOCB2ZPOISEEEEC.pdf` | 9251 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/DOCE2ZPYI4LQP1X.pdf` | 9289 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/DOCKLGPKHW3KSXY.pdf` | 9227 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/facture carrosserie yucel oct a mai.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/facture carrosserie yucel oct a mai__1.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/facture carrosserie yucel oct a mai__2.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/facture carrosserie yucel oct a mai__3.pdf` | 1672597 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Facture client - 263075.pdf` | 61518 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/facture juridique modification.pdf` | 198594 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Facture_CT26090094.pdf` | 71402 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/factures clients 011225 a 311026.pdf` | 19042945 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/factures clients 011225 a 311026__1.pdf` | 19042945 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/factures clients 011225 a 311026__2.pdf` | 19042945 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE AVRIL 26.pdf` | 90544 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE FEVRIER 26.pdf` | 90500 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE JANVIER 26.pdf` | 90146 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE JUIN 26.pdf` | 90315 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE MAI 26.pdf` | 90325 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE MARS 26.pdf` | 90458 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE OCTOBRE 26.pdf` | 90712 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/FREE SEPT 26.pdf` | 90522 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/image.png` | 144309 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/image002.png` | 11217 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/image__1.png` | 405759 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/image__2.png` | 64247 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/JUILLET 26.pdf` | 90632 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Psr3_PSR3ACQT_b45510bb-e4dd-4cac-abb5-3d2810213bda_2026-10-24_AG_10_MySys_.pdf` | 158094 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Psr_PSR1ACQT_17b5a0d3-0301-40e2-b97c-a35bc329e139_2026-10-22_AG_10_MySys_ (1).pdf` | 159410 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Psr_PSR1ACQT_17b5a0d3-0301-40e2-b97c-a35bc329e139_2026-10-22_AG_10_MySys_.pdf` | 159408 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/XP_CT2604000396-1__1.PDF` | 695826 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/XPEM_CT2604000396-2__2EM.PDF` | 512279 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Y16V_SD_INVOICE202609243956114620.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Y16V_SD_INVOICE202609243956114620__1.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Y16V_SD_INVOICE202609243956114620__2.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Y16V_SD_INVOICE202609243956114620__3.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/05_ARCHIVE_ERREUR/Y16V_SD_INVOICE202609243956114620__4.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/APD/_A_COMPLETER.xlsx` | 44743 | métier comptable APD |
| `workspace/document_flow/APD/RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.xlsx` | 6231 | métier comptable APD |
| `workspace/document_flow/default/02_A_COMPLETER/_A_COMPLETER.xlsx` | 5568 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT.xlsx` | 5279 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/FRAIS_REPRESENTATION_PRESIDENT/TABLEAU_FRAIS_REPRESENTATION_PRESIDENT.xlsx` | 5300 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/HONORAIRES_JURIDIQUES/TABLEAU_HONORAIRES_JURIDIQUES.xlsx` | 5293 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE.xlsx` | 5323 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/INFORMATIQUE_WEB/TABLEAU_INFORMATIQUE_WEB.xlsx` | 5280 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION.xlsx` | 5294 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX.xlsx` | 5659 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING.xlsx` | 5318 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/GRAND_LIVRE_CLIENTS_DETAILLES.xlsx` | 4831 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/GRAND_LIVRE_default_TOUS_FOURNISSEURS.xlsx` | 5713 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/RAPPORT_COHERENCE_PIECES.xlsx` | 4916 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/SIMULATION_BILAN_RESULTAT_2026.xlsx` | 8024 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/SIMULATION_CA12_2026.xlsx` | 10376 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/SYNTHESE_CATEGORIES.xlsx` | 5364 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/TABLEAU_MERE_BANQUE_TVA.xlsx` | 6054 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/TABLEAU_MERE_PIECES_COMPTABLES.xlsx` | 5482 | métier comptable APD |
| `workspace/document_flow/default/04_EXPORT_COMPTA/default/TABLEAU_VENTES_CLIENTS.xlsx` | 5690 | métier comptable APD |
| `workspace/document_flow/default/_A_COMPLETER.xlsx` | 5727 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600023334.pdf` | 33785 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600027786.pdf` | 33631 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600037983.pdf` | 34321 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600051571.pdf` | 34301 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600054034.pdf` | 33825 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600061106.pdf` | 33255 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600064577.pdf` | 33826 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600064630.pdf` | 34275 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600075353.pdf` | 33681 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600080268.pdf` | 33234 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600085151.pdf` | 33903 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600092496.pdf` | 34595 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600129397.pdf` | 34045 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600129518.pdf` | 34035 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600134219.pdf` | 33684 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600154494.pdf` | 34097 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600154543.pdf` | 33614 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600154577.pdf` | 33320 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600154616.pdf` | 34490 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600173334.pdf` | 34297 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600174186.pdf` | 33642 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600191177.pdf` | 33541 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600214417.pdf` | 33433 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600225363.pdf` | 33259 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600225382.pdf` | 33723 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600225397.pdf` | 34470 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600225418.pdf` | 35156 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600235029.pdf` | 33639 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600247831.pdf` | 34024 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600247837.pdf` | 34018 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600252598.pdf` | 33705 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600258748.pdf` | 34033 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600262656.pdf` | 33535 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600262898.pdf` | 33334 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600271537.pdf` | 33854 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600297768.pdf` | 34822 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600314118.pdf` | 33391 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600320515.pdf` | 32560 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600362330.pdf` | 33819 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600399271.pdf` | 34438 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600410844.pdf` | 33755 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600410857.pdf` | 33586 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600418849.pdf` | 34146 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600428183.pdf` | 33928 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600428193.pdf` | 34921 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600452848.pdf` | 34138 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600452856.pdf` | 33805 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600464425.pdf` | 33921 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600477370.pdf` | 34013 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600503410.pdf` | 41128 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600505914.pdf` | 41430 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600506047.pdf` | 41618 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600511180 (1).pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600511180 (2).pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600511180.pdf` | 37032 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600527437.pdf` | 41498 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600549351.pdf` | 42091 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600551733.pdf` | 42369 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600561148.pdf` | 41414 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600634151.pdf` | 41913 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600642469.pdf` | 41946 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600646907.pdf` | 41770 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600652853.pdf` | 42165 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600670044.pdf` | 41914 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600706582.pdf` | 41624 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600729081.pdf` | 41580 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600729123.pdf` | 42012 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600731887.pdf` | 42266 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/boxtal_2600731983.pdf` | 42145 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/FAC-J2718_SAS-DIFFAM automatisme.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/FAC-J2718_SAS-DIFFAM.pdf` | 68955 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Factura tradeinn 2.pdf` | 139393 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Factura tradeinn.pdf` | 113206 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Facture 381 du 30_06_2025_01.pdf` | 42295 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Facture client - 262105.pdf` | 60203 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/facture le temps des cerises vêtements .pdf` | 298643 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Facture lidle  n° 2613710696.pdf` | 90865 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Facture lidle n° 2613710696.pdf` | 90865 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/FACTURE N°41000190 DU 31_10_2024.eml` | 33181 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/RE_ SMTRT.eml` | 68207 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/receipt_cfa65429-9b73-5640-8e99-f82d095b21f9.pdf` | 141634 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/receipt_fd30ae49-c05e-5a18-a53b-a33b6677733e.pdf` | 136067 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/SMTRT.eml` | 53857 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Y16V_SD_INVOICE202609243956114620.pdf` | 39967 | métier comptable APD |
| `workspace/document_flow/default/_DOUBLONS_IDENTIQUES/00_INBOX/Y16V_SD_ORDER_CONF 20260916 152129 4086518725.pdf` | 57071 | métier comptable APD |
| `workspace/document_flow/default/MIGRE_VERS_APD.md` | 144 | métier comptable APD |
| `workspace/document_flow/e2e_manifest_bosch_sia.json` | 1302 | métier comptable APD |
| `workspace/document_flow/SCI APD/Facture-4173286-20-07-2026_1602.pdf` | 175656 | métier comptable APD |
| `workspace/imports/clients/client_xml_structure_report.json` | 3506 | métier comptable APD |
| `workspace/imports/clients/Liste des clients.xml` | 196656 | métier comptable APD |
| `workspace/invoices/input/05 SEPT 27.78.pdf` | 88071 | métier comptable APD |
| `workspace/invoices/input/05 SEPT 45.81.pdf` | 171308 | métier comptable APD |
| `workspace/invoices/input/06 OCTOBRE 14,99.pdf` | 86522 | métier comptable APD |
| `workspace/invoices/input/10 NOVEMBRE 25 19,90.pdf` | 86443 | métier comptable APD |
| `workspace/invoices/input/12 DECE 199.99.pdf` | 88254 | métier comptable APD |
| `workspace/invoices/input/14octobre18tick1362280.38142200.pdf` | 647455 | métier comptable APD |
| `workspace/invoices/input/15.04.25.W250415W1957907O274065381.pdf` | 6650 | métier comptable APD |
| `workspace/invoices/input/17.01.25.W250117W1957907O267468286.pdf` | 6651 | métier comptable APD |
| `workspace/invoices/input/18 MAI 2025.pdf` | 251269 | métier comptable APD |
| `workspace/invoices/input/19 DEC 185.91.pdf` | 96581 | métier comptable APD |
| `workspace/invoices/input/19.08.25 52,22.pdf` | 86065 | métier comptable APD |
| `workspace/invoices/input/21 SEPT 25 16,19 EUR.pdf` | 85804 | métier comptable APD |
| `workspace/invoices/input/23.01.25;W250123W1957907O267912252.pdf` | 6654 | métier comptable APD |
| `workspace/invoices/input/23dec17 tick9263237.30015922.pdf` | 546430 | métier comptable APD |
| `workspace/invoices/input/25 NOV 74.33.pdf` | 85422 | métier comptable APD |
| `workspace/invoices/input/28 janv 25 Factura.pdf` | 74202 | métier comptable APD |
| `workspace/invoices/input/28 novembre 25 195,95 eur.pdf` | 166823 | métier comptable APD |
| `workspace/invoices/input/28.02.25.W250228W1957907O270618214.pdf` | 6654 | métier comptable APD |
| `workspace/invoices/input/2aout18tick5227857.45809776.pdf` | 523318 | métier comptable APD |
| `workspace/invoices/input/30 JUILLET 25 128;19.pdf` | 86540 | métier comptable APD |
| `workspace/invoices/input/4 FEV 25 4398283-order-invoice-0abc8d4db78d3665d0c3e9e43d97135c.pdf` | 36914 | métier comptable APD |
| `workspace/invoices/input/4 FEV 25 4398283-order-invoice-6e6c0ed50000e4b80f686b6f541accf6.pdf` | 38581 | métier comptable APD |
| `workspace/invoices/input/4 FEV 25 96511490_130684960.pdf` | 57881 | métier comptable APD |
| `workspace/invoices/input/4 FEV 25 OVO1698014_20250204.pdf` | 37719 | métier comptable APD |
| `workspace/invoices/input/59.89 16 MAY 2025.pdf` | 171740 | métier comptable APD |
| `workspace/invoices/input/68,22 DU 17 MARS 25.pdf` | 83539 | métier comptable APD |
| `workspace/invoices/input/689 EUR DU 25 FEVRIER 25.pdf` | 85132 | métier comptable APD |
| `workspace/invoices/input/7juillet18tick2789169.38666940.pdf` | 386040 | métier comptable APD |
| `workspace/invoices/input/96,56 11 FEV 25 67ac6ba8a6de6_invoice_70100_20250212_103915.pdf` | 101187 | métier comptable APD |
| `workspace/invoices/input/ABONNEMENT.pdf` | 219519 | métier comptable APD |
| `workspace/invoices/input/AMENDE 144 EUR 30.06.26.pdf` | 78608 | métier comptable APD |
| `workspace/invoices/input/Factura.pdf traddin 21 janv 25.pdf` | 77953 | métier comptable APD |
| `workspace/invoices/input/FACTURE 16014.pdf` | 435729 | métier comptable APD |
| `workspace/invoices/input/facture accompagnement.pdf` | 381436 | métier comptable APD |
| `workspace/invoices/input/facture annonce legale.pdf` | 584806 | métier comptable APD |
| `workspace/invoices/input/Facture APD 210744[28] general paint.pdf` | 333876 | métier comptable APD |
| `workspace/invoices/input/FACTURE BILAN 2024 - 2511-F-2025-007-EI-ATIA-SONIA-TALENT-CO.pdf` | 21867 | métier comptable APD |
| `workspace/invoices/input/FACTURE DANIEL OCTOBRE MISE EN AVRIL 25.pdf` | 419919 | métier comptable APD |
| `workspace/invoices/input/facture ha paintcar avril pour juin 16  ILAA0F41.pdf` | 59769 | métier comptable APD |
| `workspace/invoices/input/Facture n° 2609649432.pdf` | 34609 | métier comptable APD |
| `workspace/invoices/input/FACTURE PAINTCAR ILA49722 SEPT POUR 16 NOV 16.pdf` | 110437 | métier comptable APD |
| `workspace/invoices/input/facture paintcar mai 2018.pdf` | 344377 | métier comptable APD |
| `workspace/invoices/input/Facture Sia 251430 2OAUT16 POUR 2octobre16.pdf` | 1665061 | métier comptable APD |
| `workspace/invoices/input/Facture Sia 251430 octobre16.pdf` | 1665061 | métier comptable APD |
| `workspace/invoices/input/facture électricité.pdf` | 176335 | métier comptable APD |
| `workspace/invoices/input/facture-1.pdf` | 135149 | métier comptable APD |
| `workspace/invoices/input/Facture_000003 ASSISTANCE_PEINTURES_DIFFUSION.pdf` | 163316 | métier comptable APD |
| `workspace/invoices/input/facture_creation_holding.pdf` | 220663 | métier comptable APD |
| `workspace/invoices/input/facture_emaux.pdf` | 61785 | métier comptable APD |
| `workspace/invoices/input/facture_free.pdf` | 249069 | métier comptable APD |
| `workspace/invoices/input/Facture_lidle.pdf` | 49040 | métier comptable APD |
| `workspace/invoices/input/facture_location5.pdf` | 324702 | métier comptable APD |
| `workspace/invoices/input/facture_paintcar (2).pdf` | 148824 | métier comptable APD |
| `workspace/invoices/input/facture_paintcar.pdf` | 148824 | métier comptable APD |
| `workspace/invoices/input/facture_transport.PDF` | 550124 | métier comptable APD |
| `workspace/invoices/input/facture_transport2.PDF` | 71402 | métier comptable APD |
| `workspace/invoices/input/Factures FA2123 DIP¨AOUT POUR OCT 16.pdf` | 141491 | métier comptable APD |
| `workspace/invoices/input/Factures FA2126 DIP AOPUT POUR OCT 16.pdf` | 217500 | métier comptable APD |
| `workspace/invoices/input/Factures FA2192 DIP SEPT POUR NOV 16.pdf` | 139200 | métier comptable APD |
| `workspace/invoices/input/FACTURES JANVIER ET FEVRIER.pdf` | 643440 | métier comptable APD |
| `workspace/invoices/input/ILADBF12 paintcar janv pour fev.pdf` | 59767 | métier comptable APD |
| `workspace/invoices/input/JANV 24 BOX.pdf` | 249069 | métier comptable APD |
| `workspace/invoices/input/OVO1735289_20250211.pdf` | 38382 | métier comptable APD |
| `workspace/invoices/input/paintcar mars pour AVRIL 17 ILA23FB2.pdf` | 59516 | métier comptable APD |
| `workspace/invoices/input/recap frais transport livraison chrono boxtal.pdf` | 547530 | métier comptable APD |
| `workspace/invoices/input/ticket patapain 180.pdf` | 98358 | métier comptable APD |
| `workspace/invoices/input/Votre document edf.pdf` | 1932263 | métier comptable APD |
| `workspace/invoices/input/W250228W1957907O270618214.pdf` | 6655 | métier comptable APD |
| `workspace/invoices/input/W250505W1957907O275367852.pdf` | 6652 | métier comptable APD |
| `workspace/invoices/input/W250604W1957907O277534980.pdf` | 6648 | métier comptable APD |
| `workspace/invoices/input/W250609W1957907O277794510.pdf` | 6652 | métier comptable APD |
| `workspace/invoices/iput effectués/Facture client - 263075.pdf` | 61785 | métier comptable APD |
| `workspace/invoices/iput effectués/ticket_resto.pdf` | 56058 | métier comptable APD |
| `workspace/suppliers/suppliers.json` | 3662 | métier comptable APD |
| `workspace/vault/01-contexte/cockpit_compta.md` | 92105 | métier comptable APD |
| `workspace/vault/07-compta/Cockpit_Compta.md` | 90464 | métier comptable APD |

## 4. Tests et artefacts temporaires obsolètes à purger

| Fichier | Taille (o) | Justification |
|---|---:|---|
| `benchmarks/data/ocr_baseline_texts.json.bak` | 31832 | test / artefact obsolète |
| `docs/test_nuextract_v8.6.1_before_free_smtrt.py` | 7415 | test / artefact obsolète |
| `gmail_dryrun_inbox_20.log` | 4618 | test / artefact obsolète |
| `gmail_dryrun_inbox_72.log` | 31348 | test / artefact obsolète |
| `gmail_fetch_pass1.log` | 1394 | test / artefact obsolète |
| `gmail_fetch_pass2.log` | 1394 | test / artefact obsolète |
| `gmail_fetch_pass3_limit20.log` | 1598 | test / artefact obsolète |
| `gmail_fetch_pass4_limit72.log` | 3794 | test / artefact obsolète |
| `models/huggingface/xet/logs/xet_20261024T074048617+0200_219192.log` | 35806 | test / artefact obsolète |
| `ofx_err.log` | 1646 | test / artefact obsolète |
| `Ollama` | 21 | test / artefact obsolète |
| `recon_err.log` | 0 | test / artefact obsolète |
| `refresh_cockpit_output.log` | 766 | test / artefact obsolète |
| `Sans titre.base` | 37 | test / artefact obsolète |
| `test_experimental_multitva.py` | 4717 | test / artefact obsolète |
| `test_ingestion.py` | 1494 | test / artefact obsolète |
| `test_learning_loop.py` | 3072 | test / artefact obsolète |
| `test_llamaindex_qdrant.py` | 1478 | test / artefact obsolète |
| `test_mem0_qdrant.py` | 1186 | test / artefact obsolète |
| `test_nuextract.py` | 5458 | test / artefact obsolète |
| `test_profiler.py` | 1886 | test / artefact obsolète |
| `test_skill_creator.py` | 1374 | test / artefact obsolète |
| `tests/fixtures/banking/sample.csv` | 208 | test / artefact obsolète |
| `tests/fixtures/banking/sample.ofx` | 859 | test / artefact obsolète |
| `tests/fixtures/orientation_corpus_manifest.json` | 6103 | test / artefact obsolète |
| `tests/fixtures/spreadsheet/sample_journal.csv` | 280 | test / artefact obsolète |
| `tests/fixtures/spreadsheet/sample_journal.xlsx` | 5096 | test / artefact obsolète |
| `tests/test_accounting_spreadsheet_export_exhaustive.py` | 7877 | test / artefact obsolète |
| `tests/test_acompleter_workbook_exhaustive.py` | 16578 | test / artefact obsolète |
| `tests/test_agents_invoice_agent_schema.py` | 6917 | test / artefact obsolète |
| `tests/test_annotation_perception.py` | 10024 | test / artefact obsolète |
| `tests/test_bank_statement_import_exhaustive.py` | 20097 | test / artefact obsolète |
| `tests/test_banking_producer.py` | 5403 | test / artefact obsolète |
| `tests/test_banking_producer_exhaustive.py` | 29942 | test / artefact obsolète |
| `tests/test_banking_schema.py` | 36198 | test / artefact obsolète |
| `tests/test_canonical_skill_creation_service.py` | 5493 | test / artefact obsolète |
| `tests/test_canonical_skill_repository.py` | 13993 | test / artefact obsolète |
| `tests/test_canonical_skills_api.py` | 16719 | test / artefact obsolète |
| `tests/test_canonical_skills_api_integration.py` | 4524 | test / artefact obsolète |
| `tests/test_category_summary_export_exhaustive.py` | 21884 | test / artefact obsolète |
| `tests/test_declarative_workflows_exhaustive.py` | 36127 | test / artefact obsolète |
| `tests/test_document_engine.py` | 12599 | test / artefact obsolète |
| `tests/test_document_extractors.py` | 11369 | test / artefact obsolète |
| `tests/test_document_flow_watcher_exhaustive.py` | 18834 | test / artefact obsolète |
| `tests/test_document_geometry.py` | 2923 | test / artefact obsolète |
| `tests/test_document_geometry_fastpath.py` | 5188 | test / artefact obsolète |
| `tests/test_document_perception.py` | 5026 | test / artefact obsolète |
| `tests/test_document_pipeline.py` | 16806 | test / artefact obsolète |
| `tests/test_document_router.py` | 16697 | test / artefact obsolète |
| `tests/test_document_router_exhaustive.py` | 16860 | test / artefact obsolète |
| `tests/test_e2e_spectrophotometre_reconciliation.py` | 4852 | test / artefact obsolète |
| `tests/test_extraction_manager_invoice_schema.py` | 5241 | test / artefact obsolète |
| `tests/test_extractor_benchmark_runner.py` | 17826 | test / artefact obsolète |
| `tests/test_extractor_ocr_dataset_schema.py` | 4388 | test / artefact obsolète |
| `tests/test_fec_export_exhaustive.py` | 8766 | test / artefact obsolète |
| `tests/test_folder_state_model_exhaustive.py` | 14899 | test / artefact obsolète |
| `tests/test_folder_sync_service_exhaustive.py` | 30241 | test / artefact obsolète |
| `tests/test_inbox_ingestion_exhaustive.py` | 26731 | test / artefact obsolète |
| `tests/test_ingestion_manager_invoice_schema.py` | 7628 | test / artefact obsolète |
| `tests/test_invoice_graph.py` | 6721 | test / artefact obsolète |
| `tests/test_invoice_graph_perception.py` | 6373 | test / artefact obsolète |
| `tests/test_invoice_party_supplier_identity_adapter.py` | 6819 | test / artefact obsolète |
| `tests/test_invoice_pipeline_chain.py` | 8279 | test / artefact obsolète |
| `tests/test_invoice_producers.py` | 27951 | test / artefact obsolète |
| `tests/test_invoice_schema.py` | 14364 | test / artefact obsolète |
| `tests/test_local_master_writer.py` | 113402 | test / artefact obsolète |
| `tests/test_master_tables_schema.py` | 41667 | test / artefact obsolète |
| `tests/test_model_router.py` | 10241 | test / artefact obsolète |
| `tests/test_multitenant_isolation.py` | 18429 | test / artefact obsolète |
| `tests/test_n8n_api_canonical_invoice.py` | 7325 | test / artefact obsolète |
| `tests/test_obsidian_vault_service_exhaustive.py` | 50777 | test / artefact obsolète |
| `tests/test_orientation_benchmark.py` | 6379 | test / artefact obsolète |
| `tests/test_p17_apd_business_rules_exhaustive.py` | 49846 | test / artefact obsolète |
| `tests/test_p19_ofx_lcr_reconciliation_exhaustive.py` | 34010 | test / artefact obsolète |
| `tests/test_p20_customer_batch_and_gmail_filter_exhaustive.py` | 45664 | test / artefact obsolète |
| `tests/test_p21_bosch_sia_and_unified_inbox_exhaustive.py` | 19772 | test / artefact obsolète |
| `tests/test_p22_four_stage_pipeline_and_hermes_bridge_exhaustive.py` | 25227 | test / artefact obsolète |
| `tests/test_p24_encaissement_yucel_multigroup_and_apd_inbox_exhaustive.py` | 19408 | test / artefact obsolète |
| `tests/test_p25_tier_directory_aliases_ledger_direction_and_clean_exports_exhaustive.py` | 12486 | test / artefact obsolète |
| `tests/test_persistence_contracts.py` | 46960 | test / artefact obsolète |
| `tests/test_persistence_manager.py` | 35510 | test / artefact obsolète |
| `tests/TEST_PLAN_INVOICE_AGENT.md` | 1499 | test / artefact obsolète |
| `tests/TEST_PLAN_RESEARCH_AGENT.md` | 1215 | test / artefact obsolète |
| `tests/test_qdrant_audit_service.py` | 6536 | test / artefact obsolète |
| `tests/test_reconciliation_arbitration_exhaustive.py` | 15326 | test / artefact obsolète |
| `tests/test_root_invoice_agent_schema.py` | 6144 | test / artefact obsolète |
| `tests/test_routing_engine_model_selection.py` | 3660 | test / artefact obsolète |
| `tests/test_rule_registry_exhaustive.py` | 44764 | test / artefact obsolète |
| `tests/test_rule_registry_multitenant_exhaustive.py` | 8750 | test / artefact obsolète |
| `tests/test_rule_registry_service.py` | 12528 | test / artefact obsolète |
| `tests/test_run_batch_audit.py` | 11521 | test / artefact obsolète |
| `tests/test_run_pipeline_pilot.py` | 4854 | test / artefact obsolète |
| `tests/test_skill_distillation.py` | 15714 | test / artefact obsolète |
| `tests/test_skill_lifecycle_service.py` | 13900 | test / artefact obsolète |
| `tests/test_skill_manifest_schema.py` | 14115 | test / artefact obsolète |
| `tests/test_spreadsheet_producer.py` | 6344 | test / artefact obsolète |
| `tests/test_spreadsheet_producer_exhaustive.py` | 26601 | test / artefact obsolète |
| `tests/test_sqlite_accounting_store_exhaustive.py` | 20001 | test / artefact obsolète |
| `tests/test_supplier_enrichment.py` | 10508 | test / artefact obsolète |
| `tests/test_supplier_identity_repository.py` | 10395 | test / artefact obsolète |
| `tests/test_supplier_identity_schema.py` | 13487 | test / artefact obsolète |
| `tests/test_supplier_ledger_export_exhaustive.py` | 20797 | test / artefact obsolète |
| `tests/test_supplier_registration_coordinator.py` | 18789 | test / artefact obsolète |
| `tests/test_supplier_registration_repository.py` | 12748 | test / artefact obsolète |
| `tests/test_supplier_registration_service.py` | 20753 | test / artefact obsolète |
| `tests/test_tenant_middleware_exhaustive.py` | 8292 | test / artefact obsolète |
| `tests/test_translate_supplier_catalog.py` | 6081 | test / artefact obsolète |
| `tests/test_tva_zero_rule_evaluator.py` | 30494 | test / artefact obsolète |
| `tests/test_validate_ocr_ground_truth.py` | 14771 | test / artefact obsolète |
| `tests/test_validate_supplier_identity.py` | 19941 | test / artefact obsolète |
| `tests/test_vat_simulation_exhaustive.py` | 26774 | test / artefact obsolète |
| `tests/test_vault_accounting_bridge_exhaustive.py` | 20085 | test / artefact obsolète |
| `tests/test_vector_payload_schema.py` | 3107 | test / artefact obsolète |
| `tests/test_vision_budget_supervisor_exhaustive.py` | 22053 | test / artefact obsolète |
| `tests/test_vision_supervisor.py` | 8733 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__1.xlsx` | 5989 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__2.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__3.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__4.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__5.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__6.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER__7.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/02_A_COMPLETER/_A_COMPLETER_MIS_A_JOUR__1.xlsx` | 6661 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT__1.xlsx` | 5279 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT__2.xlsx` | 5303 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/CARBURANT/TABLEAU_CARBURANT__3.xlsx` | 5303 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/FRAIS_REPRESENTATION_PRESIDENT/TABLEAU_FRAIS_REPRESENTATION_PRESIDENT__1.xlsx` | 5300 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/FRAIS_REPRESENTATION_PRESIDENT/TABLEAU_FRAIS_REPRESENTATION_PRESIDENT__2.xlsx` | 5321 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/FRAIS_REPRESENTATION_PRESIDENT/TABLEAU_FRAIS_REPRESENTATION_PRESIDENT__3.xlsx` | 5320 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HONORAIRES_JURIDIQUES/TABLEAU_HONORAIRES_JURIDIQUES__1.xlsx` | 5294 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HONORAIRES_JURIDIQUES/TABLEAU_HONORAIRES_JURIDIQUES__2.xlsx` | 5315 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HONORAIRES_JURIDIQUES/TABLEAU_HONORAIRES_JURIDIQUES__3.xlsx` | 5314 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE__1.xlsx` | 5323 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE__2.xlsx` | 5344 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/HOTELLERIE/TABLEAU_HOTELLERIE__3.xlsx` | 5343 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/INFORMATIQUE_WEB/TABLEAU_INFORMATIQUE_WEB__1.xlsx` | 5281 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/INFORMATIQUE_WEB/TABLEAU_INFORMATIQUE_WEB__2.xlsx` | 5302 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/INFORMATIQUE_WEB/TABLEAU_INFORMATIQUE_WEB__3.xlsx` | 5301 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION__1.xlsx` | 5294 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION__2.xlsx` | 5315 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/RESTAURATION/TABLEAU_RESTAURATION__3.xlsx` | 5315 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX__1.xlsx` | 5659 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX__2.xlsx` | 5660 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/SYNTHESE_FRAIS_GENERAUX__3.xlsx` | 5659 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING__1.xlsx` | 5319 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING__2.xlsx` | 5342 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/FRAIS_GENERAUX/TRANSPORT_PARKING/TABLEAU_TRANSPORT_PARKING__3.xlsx` | 5341 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/GRAND_LIVRE_CLIENTS_DETAILLES__1.xlsx` | 4832 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/GRAND_LIVRE_CLIENTS_DETAILLES__2.xlsx` | 4831 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/GRAND_LIVRE_CLIENTS_DETAILLES__3.xlsx` | 4831 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/RAPPORT_COHERENCE_PIECES__1.xlsx` | 4917 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/RAPPORT_COHERENCE_PIECES__2.xlsx` | 4916 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/RAPPORT_COHERENCE_PIECES__3.xlsx` | 4916 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/REFERENTIEL_TIERS_APD__1.xlsx` | 6951 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/REFERENTIEL_TIERS_APD__2.xlsx` | 6952 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_BILAN_RESULTAT_2026__1.xlsx` | 8024 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_BILAN_RESULTAT_2026__2.xlsx` | 8024 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_BILAN_RESULTAT_2026__3.xlsx` | 8023 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_CA12_2026__1.xlsx` | 10378 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_CA12_2026__2.xlsx` | 10377 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SIMULATION_CA12_2026__3.xlsx` | 10376 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_CATEGORIES__1.xlsx` | 5364 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_CATEGORIES__2.xlsx` | 5364 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/SYNTHESE_CATEGORIES__3.xlsx` | 5364 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_BANQUE_TVA__1.xlsx` | 6055 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_BANQUE_TVA__2.xlsx` | 6055 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_BANQUE_TVA__3.xlsx` | 6054 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_PIECES_COMPTABLES__1.xlsx` | 5482 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_PIECES_COMPTABLES__2.xlsx` | 5503 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_MERE_PIECES_COMPTABLES__3.xlsx` | 5502 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_VENTES_CLIENTS__1.xlsx` | 5691 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_VENTES_CLIENTS__2.xlsx` | 5691 | test / artefact obsolète |
| `workspace/document_flow/APD/04_EXPORT_COMPTA/APD/TABLEAU_VENTES_CLIENTS__3.xlsx` | 5691 | test / artefact obsolète |
| `workspace/document_flow/APD/_A_COMPLETER__1.xlsx` | 6220 | test / artefact obsolète |
| `workspace/document_flow/APD/_A_COMPLETER__2.xlsx` | 6354 | test / artefact obsolète |
| `workspace/document_flow/APD/_A_COMPLETER__3.xlsx` | 6351 | test / artefact obsolète |
| `workspace/exports/accounting/default/GRAND_LIVRE_default_sia.xlsx` | 7470 | test / artefact obsolète |
| `workspace/exports/accounting/default/GRAND_LIVRE_default_TOUS_FOURNISSEURS.xlsx` | 248306 | test / artefact obsolète |
| `workspace/exports/accounting/default/SIMULATION_CA12_2026.xlsx` | 14965 | test / artefact obsolète |
| `workspace/exports/accounting/default/SYNTHESE_default_ACHATS_MARCHANDISES.xlsx` | 7289 | test / artefact obsolète |
| `workspace/exports/accounting/default/TABLEAU_VENTES_CLIENTS.xlsx` | 9682 | test / artefact obsolète |
| `workspace/inbox/test_image.jpg` | 99160 | test / artefact obsolète |
| `workspace/invoices/input/test_image.jpg` | 97913 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_excel.xlsx` | 13397 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_image.jpg` | 104106 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_image2.jpg` | 95654 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_natif.pdf` | 186522 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_scan.pdf` | 440837 | test / artefact obsolète |
| `workspace/invoices/iput effectués/test_tableau.pdf` | 61518 | test / artefact obsolète |
| `workspace/invoices/output_json/test_facture.json` | 673 | test / artefact obsolète |
| `workspace/invoices/processed/test_facture.png` | 14773 | test / artefact obsolète |
| `workspace/test_escalade.py` | 141 | test / artefact obsolète |
| `workspace/test_escalade_2.py` | 209 | test / artefact obsolète |

## 5. Recommandations de découpage

1. Extraire `services/accounting_*`, `services/apd_*`, `services/*invoice*`,
   `services/*bank*`, `services/*tier*`, `services/*vat*` et
   `services/opening_statement_*` dans un paquet `apd_compta/`.
2. Conserver dans `dsh_core/` le harnais, Hermes, Codex, la mémoire 4D,
   la perception documentaire, OptSkill et l'audit.
3. Déplacer les tests métier dans `apd_compta/tests/` et les tests du socle
   dans `dsh_core/tests/`.
4. Purger les fichiers listés en section 4 (logs, sauvegardes, doublons
   `__N.xlsx`, caches) après validation du dirigeant.
5. Garder `workspace/document_flow/APD/` comme point de montage unique des
   données métier, jamais comme code.
