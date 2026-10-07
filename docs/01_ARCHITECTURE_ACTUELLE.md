# 01 - ARCHITECTURE RÉELLEMENT BRANCHÉE

Ce document décrit le flux de données *réel* exécuté par le code existant à ce jour, sans présumer des intentions futures.

## 1. FLUX D'ENTRÉE (FASTAPI)
[ACTIF] Le fichier `api.py` est le véritable chef d'orchestre actuel. Il reçoit les requêtes et les distribue directement aux agents, court-circuitant majoritairement LangGraph.
- `/api/research` ➔ `research_agent.py`
- `/api/code` ➔ `coder_agent.py`
- `/api/router` ➔ `router.py`
- `/api/skills/*` ➔ `memory_manager.py`

## 2. L'ORCHESTRATEUR LANGGRAPH
[EN COURS] Le fichier `orchestrator.py` existe mais sa logique de décision (`node_triage`) repose sur des mots-clés hardcodés (ex: "facture", "lettrage") au lieu d'utiliser l'intelligence de `router.py`.

## 3. LE SOUS-SYSTÈME OCR (FACTURES)
[EN COURS] Le flux facture est codé dans `invoice_agent.py` (Validation Pydantic, Structure LangGraph interne), mais :
- Le routage intelligent entre Surya (PDF texte), Qwen-VL (Photos) et le module de réparation (Tickets) est implicite.
- [PRÉVU] Un `OCRRouterNode` spécifique doit être créé pour rendre ce tri explicite.

## 4. LA BOUCLE D'APPRENTISSAGE
[PRÉVU] La création de Skills et l'escalade Qwen9B -> Qwen27B -> Claude sont documentées mais aucun composant de code (comme un `TeacherStudentAgent`) ne pilote automatiquement ce flux.