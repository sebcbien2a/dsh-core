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
Phase 4 : Intégration LangGraph dans FastAPI (Validée - orchestrator.py).
Phase 5 : Centralisation serveurs MCP (Validée).
Phase 6 : Déploiement de Langfuse local via Docker (Validée - Port 3000).
Phase 7 : Connexion du tableau de bord animé (LangGraph Studio / React Flow) sur écran secondaire.
Phase 8 : Pipeline Factures & API locale (Validée).
Phase 9 : Intégration Neo4j via Docker (Validée - Port 7474).
Phase 10
Plain Text
ResearchAgent V5
Afficher plus de lignes

Objectif :

Plain Text
DDGS
Playwright
Qdrant
TOR
Cache Qdrant
Langfuse
Afficher plus de lignes
Phase 11
Plain Text
InvoiceAgent V2
Afficher plus de lignes

Objectif :

Plain Text
Surya OCR
Vision
Validation
Repair Agent
Qdrant
n8n
``
Afficher plus de lignes
Phase 12
Plain Text
Agent Registry
Afficher plus de lignes

Objectif :

Plain Text
agents_catalog
dans Qdrant
``
Afficher plus de lignes


NOUVEAUX 

# PHASES DE DÉPLOIEMENT

- [x] **Phase 1 à 5 :** Environnement virtuel, Serveur FastAPI, Outils MCP, Agents IA, Orchestrateur LangGraph. (Terminées)
- [x] **Phase 6 : Base de données de Traces (Langfuse)**
  - [x] Remplacement de l'image `latest` par la version `2` dans le `docker-compose.yml`.
  - [x] Démarrage réussi sur `http://localhost:3001` (contournement des bloqueurs de navigateur).
  - [x] Création des clés API (Secret/Public).
  - [x] Sécurisation des clés dans `.env`.
  - [x] Branchement du callback Langfuse dans `api.py`.
- [x] **Phase 8 : Sauvegarde Distante (GitHub)**
  - [x] Création du dépôt privé.
  - [x] Synchronisation sécurisée (`git push -u origin main --force`).
- [~] **Phase 9 : Modélisation des Lettrages et Base Graphe (Neo4j)**
  - [x] Test de connexion et d'exécution Cypher sur le port 7474.
  - [ ] Création du modèle "Rapprochement Achats" (Factures fournisseurs).
  - [ ] Création du modèle "Rapprochement Ventes" (Clients et LCR).
  - [ ] Outil d'extraction des articles pour export Excel.