\# 03 - JOURNAL DE SUIVI ET DE VALIDATION



\## ASSAINISSEMENT SYSTÈME (Septembre 2026)

\* Suppression des anciens environnements conflictuels (`.venv`, `.venv-1`, `.venv-v2` sous Python 3.14).

\* Stabilisation de l'environnement `.venv-v3` (Python 3.11.9) validée.

\* Remplacement de Llama Vision par `qwen2.5vl:7b` (optimisation VRAM).



\## VALIDATIONS TECHNIQUES RÉALISÉES

\* \*\*Mémoire Vectorielle :\*\* Collections `mem0` et `document\_bus` (768 dims) opérationnelles sur Docker port 6333.

\* \*\*Extraction Épisodique :\*\* Le stockage et la restitution de faits via Mem0 + `qwen3.5:9b` sont confirmés.

\* \*\*Phase 8 (Factures) :\*\* Le script `invoice\_agent.py` utilisant Surya OCR + Qwen 9B génère avec succès des JSON validés mathématiquement via Pydantic. Indexation dans Qdrant testée.

\* \*\*Point d'accès n8n :\*\* Serveur Webhook FastAPI (`n8n\_api.py`) créé et fonctionnel sur le port 8000.



\## PROCHAINES ÉTAPES IMMÉDIATES

1\. \*\*Atelier de développement IA :\*\* Configurer l'environnement VS Code (avec serveurs MCP et agents) pour que l'IA locale puisse coder la suite de l'architecture.

2\. \*\*Construction de la Phase 4 :\*\* Coder le routeur LangGraph dans le backend FastAPI.

3\. \*\*Module SIA BOSH :\*\* Démarrer le développement du point d'appui de lettrage complexe.

