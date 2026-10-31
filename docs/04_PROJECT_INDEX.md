---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe historique — Archives numérotées
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: []
downstream_consumers: []
verification_tests: []
---

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

## Index documentaire de l'échafaudage IA
- `docs/PROJECT_HANDOVER.md` : point d'entrée unique d'une nouvelle conversation ; il renvoie vers cet index et la source canonique détaillée.
- `docs/AI_MODEL_ROUTING_AND_HARNESSES.md` : source canonique détaillée pour la matrice des modèles, les profils, les rôles des harnesses et l'état opérationnel confirmé.
- `docs/PROJECT_DECISIONS.md` : registre des décisions durables, dont la décision d'évaluation de l'échafaudage IA du 2026-10-28.