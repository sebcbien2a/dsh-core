\# 📕 BIBLE DE RECONSTRUCTION - SYSTÈME IA LOCAL (DSH-CORE)

\*\*Date de dernière mise à jour :\*\* Septembre 2026

\*\*Objectif :\*\* Recréer le système complet à l'identique en cas de perte de la machine.



\---



\## 1. PRÉREQUIS MATÉRIELS ET SYSTÈME

\*   \*\*Machine Cible :\*\* ASUS ROG Flow X16 (ou équivalent)

\*   \*\*OS :\*\* Windows 11 Pro

\*   \*\*RAM :\*\* 32 Go DDR5

\*   \*\*GPU :\*\* NVIDIA GeForce RTX 4070 Laptop (8 Go VRAM) - Accélération CUDA indispensable.

\*   \*\*Règle d'or du stockage :\*\* L'intégralité du système, des bases de données et des caches modèles doit vivre sur le disque secondaire de travail (Drive `A:\\`). Rien sur `C:\\`.



\---



\## 2. INFRASTRUCTURE \& ENVIRONNEMENT DE BASE

\### 2.1. Arborescence stricte (`A:\\dsh-core`)

```text

A:\\dsh-core\\

&#x20;├── .venv-v3\\                  # Environnement virtuel Python 3.11

&#x20;├── models\\

&#x20;│    └── huggingface\\          # Cache sanctuarisé pour Surya OCR et autres poids HF

&#x20;├── workspace\\

&#x20;│    └── invoices\\             # Arborescence Phase 8

&#x20;│         ├── input\\           # Dépôt brut depuis n8n

&#x20;│         ├── processed\\       # Images traitées

&#x20;│         └── output\_json\\     # JSON validés

&#x20;└── \[Scripts Python (cf. section 6)]



2.2. Python et Dépendances

Version stricte : Python 3.11 (Ne pas utiliser 3.14, instable avec les bibliothèques IA actuelles).



Variables d'environnement vitales :

os.environ\["HF\_HOME"] = r"A:\\dsh-core\\models\\huggingface" (À inclure en haut de chaque script manipulant des modèles Hugging Face).



Dépendances principales à installer via pip :

pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 (Pour activer la RTX 4070)

pip install ollama qdrant-client llama-index mem0ai pydantic pillow surya-ocr fastapi\[standard] uvicorn python-multipart



3\. INFRASTRUCTURE DOCKER \& BASES DE DONNÉES

Outil : Docker Desktop (configuré avec WSL 2).



Conteneur Mémoire (Qdrant) :



Image : qdrant/qdrant



Commande de lancement : docker run -p 6333:6333 -p 6334:6334 -v A:\\dsh-core\\qdrant\_storage:/qdrant/storage:z qdrant/qdrant



Collections actives : mem0 (Mémoire générale), invoices (Factures, 768 dimensions, distance Cosine).



4\. RÉGISTRE DES MODÈLES IA (OLLAMA LOCAL)

Modèles à télécharger impérativement via ollama run \[nom\_du\_modele] lors d'une réinstallation :



qwen2.5vl:7b : Modèle de vision et routeur cognitif (Analyse visuelle et prise de décision).



qwen3.5:9b : Agent de structuration JSON (Déterministe, température à 0).



nomic-embed-text:latest : Moteur de vectorisation (768 dimensions pour la base Qdrant).



(Modèles additionnels testés/disponibles : deepseek-r1:32b, mistral-small, hermes3, qwen2.5-coder).



5\. ARCHITECTURE DES SCRIPTS (LES BRIQUES DU SYSTÈME)

Liste des fichiers constituant le cœur du système :



Mémoire \& Routage (Phases 1 à 3) :



skills.py : Outils à disposition de l'IA (get\_time, etc.).



memory\_manager.py : Connexion LlamaIndex + Mem0 + Qdrant.



router.py : Cascade de décision (Qwen VL -> Claude/Qwen).



Pipeline Comptable / Factures (Phase 8) :



schemas/invoice\_schema.py : Contrat de données Pydantic (Validations arithmétiques HT+TVA=TTC).



ocr\_engine.py : Moteur déterministe d'extraction textuelle (Surya OCR v0.22, force le GPU).



invoice\_agent.py : Le coordinateur qui lie Surya, Qwen 9B et Pydantic.



invoice\_indexer.py : Injecte les JSON valides dans Qdrant pour la recherche en langage naturel.



6\. REGISTRE DES POINTS D'APPUI (PASSERELLES WEBHOOK N8N)

Ces points d'API locaux sont les "guichets" permettant aux flux externes (n8n, mails, dossiers surveillés) d'interroger l'IA locale.



\[Point d'appui 1] - Extraction \& Validation Facture



Port d'écoute : 8000



Fichier : n8n\_api.py



Route : POST http://localhost:8000/api/v1/process-invoice



Utilisation n8n : Nœud "HTTP Request" (Method: POST, Send Body: True, Body Content Type: Form-Data, Parameter: file).



\[Point d'appui 2] - Lettrage \& Rapprochement Spécialisé (SIA BOSH) (En construction)



Rôle : Gérer les cascades d'avoirs et de correctifs pour déterminer le solde net avant rapprochement bancaire.



Route prévue : /api/v1/ledger-siabosh



\[Point d'appui 3] - Requêtes Sémantiques Qdrant (Via Base)



Port d'écoute : 6333



Accès : Directement via l'API Qdrant ou le script invoice\_indexer.py.

