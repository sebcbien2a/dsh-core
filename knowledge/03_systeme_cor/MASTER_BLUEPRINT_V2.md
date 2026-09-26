Markdown
# MASTER BLUEPRINT V2 — ARCHITECTURE DSH-CORE

## 1. Principes Directeurs & Abstraction Matérielle
- **Environnement cible :** Station locale RTX 4070 (8 Go VRAM) + 32 Go RAM + Windows Pro (WSL 2 / Docker).
- **Règle d'or :** Élimination des boucles conversationnelles libres et imprévisibles au profit d'une machine à états déterministe (LangGraph).
- **Indépendance vis-à-vis des modèles :** L'architecture raisonne par rôles abstraits et non par noms de modèles figés.
- **Découplage strict :** Les interfaces (Cherry Studio, Open WebUI, VS Code, n8n) sont des clients légers sans logique métier. Toute l'intelligence et la mémoire sont gérées par le backend central FastAPI.

---

## 2. Topologie du Système

              ┌──────────────────────────────────────────────┐
              │          CLIENTS D'AFFICHAGE UNIQUES         │
              │  Cherry Studio | Open WebUI | VS Code | n8n  │
              └──────────────────────┬───────────────────────┘
                                     │ (Standard API OpenAI /v1)
                                     ▼
              ┌──────────────────────────────────────────────┐
              │            CERVEAU CENTRAL (FastAPI)         │
              │         LangGraph (State & Orchestration)    │
              └──────────────┬────────────────┬──────────────┘
                             │                │
        ┌────────────────────┴───┐        ┌───┴────────────────────┐
        ▼                        ▼        ▼                        ▼
┌─────────────────────────┐ ┌───────────────┐ ┌────────────────┐ ┌───────────────┐
│     CASCADE DE RÔLES    │ │    MCP HUB    │ │  BUS DOCUMENTS │ │    MÉMOIRE    │
│ Base / Fast Model       │ │ Outils réels  │ │   LlamaIndex   │ │     Mem0      │
│   │                     │ │ Disk, Tor, Git│ │        +       │ │       +       │
│   ▼                     │ │  Drive, n8n   │ │     Qdrant     │ │    Qdrant     │
│ Expert Model            │ └───────────────┘ └────────────────┘ └───────────────┘
│   │                     │
│   ▼                     │
│ Supervisor Model        │
└─────────────────────────┘


### Rôles des Modèles (Pool Dynamique)
- **Base / Fast Model** *(actuellement Qwen 9B)* : conversations courantes, tâches simples, exécution rapide, économie de VRAM.
- **Expert Model** *(actuellement Qwen 27B)* : raisonnement approfondi, résolution de tâches dépassant le Base Model, code complexe.
- **Document Vision Model** *(actuellement meilleur candidat disponible)* : analyse spatiale, géométrie documentaire, découpage de zones.
- **Supervisor Model** *(actuellement Claude Sonnet)* : ingénierie de skills, analyse des échecs locaux, validation critique.

### Composants Clés
1. **Orchestrateur (LangGraph + FastAPI) :** Moteur d'état centralisé. Intercepte les requêtes, injecte le contexte Mem0, gère les escalades de rôles et déclenche les outils.
2. **Double Registre Déterministe :**
   - `registry.json` (`A:\dsh-core\skills\`) : Catalogue des actions et scripts exécutables.
   - `agents_registry.json` (`A:\dsh-core\agents\`) : Catalogue dynamique des postes/agents (rôles, outils autorisés, modèles attribués).
3. **Moteur Vectoriel Unifié (Qdrant Local Docker :6333) :**
   - Collection `mem0_memories` : Faits persistants et préférences utilisateur (Mem0 natif sur Qdrant).
   - Collection `document_bus` : Documents, devis, fiches techniques (LlamaIndex).
   - Collection `skills_catalog` : Indexation sémantique des fichiers `SKILL.md`.
4. **MCP Hub Centralisé :** Point d'accès unique vers le système de fichiers, le tunnel Tor (port 9150), les connecteurs Cloud (Drive, OneDrive) et les déclencheurs n8n.
5. **Observabilité (Écran 2) :**
   - **Langfuse (Docker) :** Traçabilité exacte des nœuds, comptage des jetons et suivi des coûts.
   - **React Flow / LangGraph Studio :** Animation temps réel du passage des données entre les boucles.

---

## 3. Pipeline Comptable & Tri de Documents (Factures & Tickets)

[Photo / Scan Facture]
│
▼
┌─────────────────────────────────────────────────────────────┐
│ 1. OCR DÉTERMINISTE (Surya OCR local)                       │
│    Extraction brute sans hallucination : texte et chiffres  │
└──────────┬──────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ANALYSE GÉOMÉTRIQUE (Document Vision Model)              │
│    Slot interchangeable : meilleur candidat disponible      │
│    Segmentation : En-tête, Tableau articles, Totaux         │
└──────────┬──────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ 3. CONTRÔLE ARITHMÉTIQUE (OCR Repair Agent - Base Model)    │
│    Vérification stricte : Total HT + TVA == Total TTC       │
│    Sortie JSON validée par schéma Pydantic                  │
└──────────┬──────────────────────────────────────────────────┘
│
▼
┌─────────────────────────────────────────────────────────────┐
│ 4. INGESTION & AUTOMATISATION (LlamaIndex + Qdrant + n8n)   │
│    Indexation métadonnées (fournisseur, date, acquitté)     │
│    Déclenchement du workflow n8n de classement comptable    │
└─────────────────────────────────────────────────────────────┘


---

## 4. Feuille de Route d'Implémentation (8 Phases)

| Phase | Brique | Objectif Technique |
| :--- | :--- | :--- |
| **Phase 1** | **Qdrant (Docker)** | Déploiement du conteneur Qdrant local sur le port `6333` avec stockage persistant sur `A:\`. |
| **Phase 2** | **Mem0 $\rightarrow$ Qdrant** | Configuration de Mem0 pour utiliser Qdrant comme backend vectoriel unique. |
| **Phase 3** | **LlamaIndex** | Montage du bus documentaire unifié au-dessus de Qdrant. |
| **Phase 4** | **LangGraph & Agents** | Intégration de LangGraph dans `api.py` piloté par `agents_registry.json`. |
| **Phase 5** | **MCP Hub Unifié** | Centralisation des serveurs MCP (`dsh-skills-hub`, Tor, Filesystem, Drive). |
| **Phase 6** | **Langfuse (Docker)** | Déploiement de Langfuse local pour le traçage fin des jetons et la maîtrise des coûts. |
| **Phase 7** | **Visualisation Écran 2** | Connexion du tableau de bord animé (LangGraph Studio / React Flow). |
| **Phase 8** | **Pipeline Factures** | Montage de la chaîne Surya OCR $\rightarrow$ Docum


# 🏗️ MASTER BLUEPRINT V2 - SYSTÈME IA LOCAL & AUTOMATISATION (DSH-CORE)
**Propriétaire :** Distributeur Paintcar (Région Corse / Val de Loire)
**Machine :** ASUS ROG Flow X16 (2024) - RTX 4070 (8 Go VRAM) - 32 Go RAM
**Objectif Principal :** Créer un système IA local 100% autonome et privé, capable d'agir comme un assistant de codage avancé pour concevoir, structurer et déployer des modules d'automatisation (n8n, facturation, comptabilité).

---

## 1. CAHIER DES CHARGES & PLAN DE CONCEPTION GLOBAL
*(Issu des conceptions initiales avec Copilot & Qwen)*

Le système n'est pas un simple chatbot, mais une "usine à créer des applications". Il est divisé en deux grandes strates :
1. **La Strate de Développement (L'Atelier) :** Un environnement VS Code piloté par des agents IA locaux (via l'extension Continue.dev ou similaire) capables de coder, débugger et créer les futurs projets d'automatisation.
2. **La Strate de Production (L'Usine n8n) :** Les workflows d'automatisation d'entreprise qui s'appuient sur l'IA pour trier, extraire et traiter la donnée métier (boîtes mails, factures, rapprochements).

### 1.1. Projets Cibles (Roadmap Métier)
*   **Projet A : Gestion centralisée des factures (Phase 8 en cours)**
    *   Boîte mail dédiée (`factures@`).
    *   Traitement des tickets (carburant, resto) via vision IA.
    *   Extraction PDF (fournisseurs).
    *   Tri intelligent via OCR et LLM.
*   **Projet B : Rapprochement bancaire complexe (Cas "SIA BOSH")**
    *   Création d'un module de lettrage spécifique pour gérer les cascades d'avoirs et de factures rectificatives avant l'intégration bancaire.
*   **Projet C : Automatisation des devis/commandes** (À définir).

---

## 2. L'ENVIRONNEMENT DE DÉVELOPPEMENT IA (L'Atelier)
*C'est la priorité absolue pour permettre à l'IA de coder la suite du projet.*
*   **IDE :** Visual Studio Code.
*   **Outils Agents :** Intégration de serveurs MCP (Model Context Protocol) pour donner accès au système de fichiers, à la recherche web, et à n8n directement depuis l'éditeur.
*   **Modèles de Code (Ollama) :** `qwen2.5-coder`, `deepseek-r1:32b`.

---

## 3. ARCHITECTURE TECHNIQUE V2 (Validée)
*Sanctuarisation totale sur le disque A:\. Rien sur C:\.*

### 3.1. Infrastructure de base
*   **Python :** Version stricte 3.11 (Environnement `.venv-v3`).
*   **Stockage Modèles :** `os.environ["HF_HOME"] = r"A:\dsh-core\models\huggingface"`
*   **Base Vectorielle (Mémoire) :** Qdrant via Docker Desktop (Port 6333), volumes sur `A:\dsh-core\qdrant_storage`.
*   **Modèles Locaux Actifs (Ollama) :** 
    *   `qwen2.5vl:7b` (Vision globale)
    *   `qwen3.5:9b` (Structuration JSON déterministe)
    *   `nomic-embed-text:latest` (Embeddings 768 dimensions pour Qdrant)

### 3.2. Le Moteur de Traitement (DSH-Core)
*   `skills.py` & `memory_manager.py` : Accès aux outils et à la mémoire Qdrant.
*   `router.py` : Routeur cognitif pour aiguiller les requêtes vers le bon modèle.

---

## 4. MODULES DE PRODUCTION (Points d'Appui n8n)
L'orchestrateur n8n délègue l'intelligence au moteur Python local via des webhooks locaux (FastAPI).

### 4.1. Module Factures Standard (Phase 8 - Validé)
*   **Script :** `invoice_agent.py` + `ocr_engine.py` (Surya OCR v0.22) + `schemas/invoice_schema.py`
*   **Logique :** Extraction Image -> Qwen 9B -> Validation Pydantic (`HT+TVA=TTC`) -> JSON.
*   **Point d'Appui n8n :** API sur le port `8000` (`n8n_api.py` -> Route : `/api/v1/process-invoice`).
*   **Indexation :** `invoice_indexer.py` injecte le résultat dans Qdrant pour la recherche sémantique.

### 4.2. Module Lettrage (Futur - Cas SIA BOSH)
*   **Logique (à coder avec les agents VS Code) :** Registre de lettrage fournisseur, assignation des avoirs aux factures ouvertes, calcul du solde net.

# 🏗️ MASTER BLUEPRINT V2 — ARCHITECTURE DSH-CORE & PROJETS

## 1. CAHIER DES CHARGES & PROJETS MÉTIER (Conception initiale)
L'objectif est de créer un système IA local, privé et déterministe pour coder et automatiser la comptabilité de l'entreprise (distribution Paintcar).
- **Projet A (Factures & Tickets) :** Surveillance d'une boîte mail dédiée (`factures@`), traitement visuel des petits frais (tickets restaurant, carburant), et extraction OCR des factures PDF fournisseurs.
- **Projet B (Cas "SIA BOSH") :** Création d'un module de lettrage spécifique capable de gérer les cascades d'avoirs et de factures rectificatives avant le rapprochement bancaire.

## 2. PRINCIPES DIRECTEURS & TOPOLOGIE
- **Environnement cible :** Station locale RTX 4070 (8 Go VRAM), 32 Go RAM, Windows Pro (WSL 2 / Docker)[cite: 9].
- **Orchestration centrale :** Élimination des boucles conversationnelles libres au profit d'une machine à états déterministe via LangGraph[cite: 9]. AutoGen est réservé à des usages encapsulés spécifiques[cite: 11].
- **Clients aveugles :** Cherry Studio, VS Code (Continue) et Open WebUI n'ont aucune logique métier ; ils se connectent comme de simples terminaux à l'API FastAPI centrale[cite: 9, 11].
- **Protocole unifié :** Abandon des protocoles A2A au profit exclusif du Model Context Protocol (MCP Hub) pour les interactions avec l'OS (fichiers, n8n, Drive)[cite: 11].

## 3. INFRASTRUCTURE & ENVIRONNEMENT DE BASE
- **Python :** Version stricte 3.11.9 isolée dans l'environnement `.venv-v3`[cite: 8].
- **Stockage Sanctuarisé :** Déport complet des modèles Ollama, des caches Hugging Face et des conteneurs sur le lecteur `A:\`[cite: 8].
- **Moteur Vectoriel (Docker) :** Qdrant déployé localement sur le port `6333` avec persistance sur `A:\`[cite: 8, 9].
  - Collection `mem0` / `mem0_memories` : Stockage des faits persistants[cite: 8, 9].
  - Collection `document_bus` : Indexation documentaire LlamaIndex pour le RAG[cite: 8, 9].

## 4. CASCADE DE RÔLES & MODÈLES (OLLAMA LOCAL)
- **Base / Fast Model :** `qwen3.5:9b` (ou équivalent) pour les tâches simples, l'extraction de faits et l'exécution directe[cite: 8, 9].
- **Expert Model :** `qwen3.6:27b` pour le raisonnement profond et la logique complexe[cite: 8, 9].
- **Vision Model :** `qwen2.5vl:7b` spécialisé dans la géométrie documentaire et l'analyse de tableaux[cite: 8, 9].
- **Embeddings :** `nomic-embed-text:latest` calibré sur 768 dimensions pour la base Qdrant[cite: 8].
- **Supervisor Model :** `Claude 3.5 Sonnet` (Cloud) réservé à l'arbitrage d'architecture et l'ingénierie de compétences[cite: 8, 9].

## 5. PIPELINE MÉTIER COMPTABLE (PHASE 8)
- **Étape 1 - Vision :** Utilisation de `qwen2.5vl:7b` pour segmenter la structure du document (zones d'en-tête, tableaux, totaux)[cite: 8, 11].
- **Étape 2 - OCR Pur :** Extraction déterministe avec Surya OCR / Docling pour récupérer le texte brut sans hallucination[cite: 8, 9, 11].
- **Étape 3 - Contrôle Pydantic :** Un agent réparateur (Qwen 9B) structure le JSON et valide strictement l'arithmétique (`Total HT + TVA == Total TTC`)[cite: 8, 9, 11].
- **Étape 4 - Ingestion & n8n :** Indexation des métadonnées (fournisseur, date) dans Qdrant via LlamaIndex, déclenchant le workflow de classement dans n8n[cite: 9, 11].

## 6. FEUILLE DE ROUTE D'IMPLÉMENTATION (LES 8 PHASES)
- **Phases 1 à 3 (Validées) :** Conteneur Qdrant déployé (`:6333`), mémoire Mem0 connectée, et bus LlamaIndex opérationnel[cite: 8, 9].
- **Phase 4 :** Intégration de LangGraph dans FastAPI piloté par `agents_registry.json`[cite: 9].
- **Phase 5 :** Centralisation des outils locaux via le MCP Hub[cite: 9, 11].
- **Phases 6 & 7 :** Observabilité avec Langfuse et monitoring visuel via LangGraph Studio[cite: 9].
- **Phase 8 (Validée) :** Pipeline d'extraction factures déterministe achevé[cite: 8, 9].