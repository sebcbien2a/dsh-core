# INVENTAIRE DSH-CORE (Mappage du Code)

## Fichiers de Routage et API
- `api.py` : Point d'entrée principal FastAPI (Ports 8000).
- `orchestrator.py` : Machine à états LangGraph.

## Les Agents (Dossier /agents)
- `research_agent.py` : Moteur de recherche multi-stratégies (DDGS, Playwright, Qdrant).
- `invoice_agent.py` : Pipeline de traitement comptable (OCR, Vision, Validation).
- `router_agent.py` : Cerveau d'aiguillage des requêtes.
- `memory_agent.py` : Gestion des interactions avec Qdrant, LlamaIndex et Mem0.
- `skill_agent.py` : Création et indexation des nouvelles compétences.
- `supervisor_agent.py` : Interface de dernier recours avec Claude 3.5.

## Outils et Connecteurs
- `mcp_skills.py` : Serveur MCP pour l'interaction avec le système local.
- `n8n_api.py` : Connecteur de webhooks pour l'automatisation aval.