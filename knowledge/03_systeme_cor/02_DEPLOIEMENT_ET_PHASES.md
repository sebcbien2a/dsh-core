# 02 - BIBLE DE RECONSTRUCTION ET PHASES DE DÉPLOIEMENT

## 1. PRÉREQUIS ET STOCKAGE
* **Machine :** ASUS ROG Flow X16 (RTX 4070 8 Go VRAM, 32 Go RAM) sur Windows 11 Pro.
* **Sanctuarisation :** Tout l'écosystème réside sur le disque `A:\dsh-core\`.
* **Variables globales :** `os.environ["HF_HOME"] = r"A:\dsh-core\models\huggingface"`.

## 2. ENVIRONNEMENT PYTHON
* **Version :** Python 3.11.9 (isoler dans `.venv-v3`). Éviter 3.14.
* **Clé API Cloud (Superviseur) :** Une clé API OpenRouter est requise dans l'environnement (`OPENROUTER_API_KEY`) pour permettre à LangGraph d'invoquer Claude 3.5 Sonnet lors de la phase finale d'auto-apprentissage.
* **Commandes vitale :**
```powershell
pip install torch torchvision torchaudio --index-url [https://download.pytorch.org/whl/cu121](https://download.pytorch.org/whl/cu121)
pip install ollama qdrant-client llama-index mem0ai pydantic pillow surya-ocr fastapi[standard] uvicorn python-multipart neo4j langfuse


3. INFRASTRUCTURE DOCKER & MODÈLES OLLAMA
Conteneur Qdrant : docker run -p 6333:6333 -p 6334:6334 -v A:\dsh-core\qdrant_storage:/qdrant/storage:z qdrant/qdrant

Conteneur Neo4j (Prévu) : Graphe relationnel pour la comptabilité complexe.

Conteneur Langfuse (Prévu) : Moteur d'observabilité.

Modèles locaux vitaux : qwen2.5-vl:7b, qwen3.5:9b, nomic-embed-text:latest.

4. REGISTRE DES POINTS D'APPUI (API N8N)
[Point d'appui 1] Extraction Facture : Port 8000 | Route POST /api/v1/process-invoice (n8n_api.py).

[Point d'appui 2] Lettrage SIA BOSH : Port 8000 | Route /api/v1/ledger-siabosh (À construire).

[Point d'appui 3] Requêtes Qdrant : Port 6333 | invoice_indexer.py.
5. FEUILLE DE ROUTE (PHASES)
Phase 1-3 : Qdrant, Mem0, LlamaIndex (Validées).

Phase 4 : Intégration LangGraph dans FastAPI piloté par agents_registry.json.

Phase 5 : Centralisation serveurs MCP (Filesystem, Drive, n8n).

Phase 6 : Déploiement de Langfuse local (Docker) pour le traçage fin des jetons et coûts.

Phase 7 : Connexion du tableau de bord animé (LangGraph Studio / React Flow) sur écran secondaire.

Phase 8 : Pipeline Factures & API locale (Validée).

Phase 9 : Intégration Neo4j & Modélisation du graphe comptable