# 04 - INVENTAIRE DSH-CORE (MAPPAGE ET DÉPENDANCES)

Ce document dresse l'inventaire exact des fichiers actuels du projet `dsh-core`, de leurs interdépendances et de leurs méthodes publiques, afin de préparer l'audit de refactoring et d'éliminer la dette technique (fusion/suppression d'agents obsolètes).

---

## 1. POINTS D'ENTRÉE ET ORCHESTRATION

### `api.py`
- **Responsabilité :** Serveur web central (FastAPI, Port 8000). Reçoit les requêtes des clients aveugles (WebUI, Cherry, VS Code).
- **Imports :** `fastapi`, `pydantic`, `agents.research_agent`, `agents.coder_agent`, `memory_manager`, `orchestrator.dsh_graph`.
- **Utilisé par :** Clients externes (Open WebUI, Cherry Studio, n8n, VS Code).
- **Appelle :** `research_agent.search_and_structure`, `coder_agent.write_code/execute_code`, `memory_manager.register_skill/find_skill`, `router.router_intelligent`, `dsh_graph.invoke`.
- **Méthodes publiques :** `@app.post("/api/research")`, `/api/code`, `/api/execute`, `/api/autocode`, `/api/router`, `/api/scrape`, `/api/skills/*`, `/api/orchestrator`.

### `orchestrator.py`
- **Responsabilité :** Machine à états LangGraph censée diriger le flux. Actuellement sous-exploitée (utilise un routage par mots-clés simples au lieu du LLM).
- **Imports :** `langgraph`, `typing`.
- **Utilisé par :** `api.py` (via la route `/api/orchestrator`).
- **Appelle :** Les nœuds internes simulés. N'utilise pas encore `router.py`.
- **Méthodes publiques :** `dsh_graph.invoke(state)`.

---

## 2. LOGIQUE CŒUR (LES "CERVEAUX")

### `router.py`
- **Responsabilité :** Contient la logique d'aiguillage LLM la plus avancée du système. Détecte les besoins de vision, d'escalade (Claude/27B) et de mémoire.
- **Imports :** `json`, `requests`, Ollama API.
- **Utilisé par :** `api.py` (via la route `/api/router`). Devrait idéalement être utilisé par `orchestrator.py`.
- **Appelle :** `memory_manager.find_skill`.
- **Méthodes publiques :** `router_intelligent(prompt, image_path, complexite_haute)`, `route_prompt()`.

### `memory_manager.py`
- **Responsabilité :** Gère le triptyque de la mémoire (Mem0, LlamaIndex, Qdrant) et le catalogue de compétences.
- **Imports :** `qdrant_client`, `mem0`, `llama_index`.
- **Utilisé par :** `api.py`, `router.py`.
- **Appelle :** Qdrant DB (port 6333).
- **Méthodes publiques :** `register_skill(name, description, triggers, code)`, `find_skill(query)`, `list_all_skills()`.

---

## 3. LES AGENTS MODERNES (V2 / LANGGRAPH)

### `agents/research_agent.py` (V5)
- **Responsabilité :** Moteur de renseignement multi-stratégies (Quick, Deep, Document, Tor) avec routage interne et cache Qdrant.
- **Imports :** `duckduckgo_search`, `playwright`, `qdrant_client`, `llama_index`, `langfuse`, `pydantic`.
- **Utilisé par :** `api.py`.
- **Appelle :** Qdrant (cache et documents), DuckDuckGo, API Ollama.
- **Méthodes publiques :** `search_and_structure(query)`.

### `agents/invoice_agent.py` (Phase 8)
- **Responsabilité :** Pipeline LangGraph strict pour le traitement comptable (OCR, Vision, Validation Pydantic, n8n).
- **Imports :** `langgraph`, `surya-ocr`, `pydantic`, `requests`, `qdrant_client`.
- **Utilisé par :** Prévu pour être appelé par `api.py` (via `/api/v1/process-invoice`).
- **Appelle :** Surya OCR, Qwen-VL, API n8n Webhook, Qdrant.
- **Méthodes publiques :** `process_invoice(file_path)`.

---

## 4. LES AGENTS HISTORIQUES (SUSPICIONS DE REDONDANCE)

### `agents/coder_agent.py`
- **Responsabilité :** Rédaction et exécution de code Python.
- **Imports :** `subprocess`, Ollama API.
- **Utilisé par :** `api.py` (`/api/code`, `/api/execute`).
- **Méthodes publiques :** `write_code()`, `execute_code()`, `auto_code_and_fix()`.

### `agents/invoice_parser_agent.py`
- **Responsabilité :** Ancien extracteur de factures.
- **Statut suspecté :** Obsolète. Totalement englobé par le nouveau `agents/invoice_agent.py`.

### `agents/pdf_extractor_agent.py`
- **Responsabilité :** Extraction de texte des PDF.
- **Statut suspecté :** Obsolète. Remplacé par `Surya OCR` au sein de `invoice_agent.py` et par `LlamaIndex` au sein de `memory_manager.py`.

### `agents/summary_agent.py`
- **Responsabilité :** Résumé de textes longs.
- **Statut suspecté :** Obsolète. La fonctionnalité est désormais absorbée par la stratégie `_strategy_playwright` (Deep Search) du `research_agent.py`.

---

## 5. OUTILS MCP ET INTÉGRATIONS TIERCES

### `mcp_skills.py`
- **Responsabilité :** Serveur MCP exposant les compétences (skills) du backend vers l'extérieur (VS Code / Continue.dev).
- **Imports :** `mcp` SDK, `json`.
- **Utilisé par :** Clients compatibles MCP.

### `mcp_scrape.py`
- **Responsabilité :** Serveur MCP dédié au scraping.
- **Statut suspecté :** Partiellement redondant avec les capacités internes de `research_agent.py`.

### `n8n_api.py`
- **Responsabilité :** Déclaration des webhooks FastAPI pour recevoir ou envoyer des requêtes vers n8n.
- **Imports :** `fastapi`, `requests`.
- **Utilisé par :** `invoice_agent.py` (envoi), `api.py`.
- **Méthodes publiques :** `POST /api/v1/process-invoice` (écoute).
# 04 - INVENTAIRE DSH-CORE RÉEL

État exact des fichiers présents dans `A:\dsh-core\` et de leur utilité démontrée.

## 1. ORCHESTRATION & API
- `api.py` : [ACTIF] Point d'entrée principal. Gère le routage HTTP de force.
- `orchestrator.py` : [EN COURS] Moteur LangGraph. Actuellement sous-exploité (routage basique).
- `router.py` : [ACTIF] Contient la logique d'analyse LLM complexe, mais mal intégrée à l'orchestrateur.

## 2. AGENTS PRINCIPAUX (VALIDÉS)
- `agents/research_agent.py` : [ACTIF] Recherche multi-stratégies (DDGS, Playwright, Cache).
- `agents/invoice_agent.py` : [ACTIF] Pipeline LangGraph pour factures (Validation Pydantic).
- `agents/coder_agent.py` : [ACTIF] Exécution de code et réparation locale.
- `memory_manager.py` : [ACTIF] Gère les interactions RAG (Mem0, LlamaIndex, Qdrant). Fait office de MemoryAgent de facto.

## 3. AGENTS SUSPECTS (EN QUARANTAINE)
*Aucun de ces fichiers n'est supprimé, mais leur redondance est avérée.*
- `agents/invoice_parser_agent.py` : [ACTIF - REDONDANT] Englobé par `invoice_agent.py`.
- `agents/pdf_extractor_agent.py` : [ACTIF - REDONDANT] À fusionner avec les nœuds Surya OCR.
- `agents/summary_agent.py` : [ACTIF - REDONDANT] Absorbé par les capacités Deep Search de `research_agent.py`.

## 4. OUTILS (MCP & EXTERNES)
- `mcp_skills.py` : [ACTIF] Expose les compétences via Model Context Protocol.
- `mcp_scrape.py` : [ACTIF] Outil MCP de scraping.
- `n8n_api.py` : [ACTIF] Endpoints pour les webhooks d'automatisation.