# 01 - ARCHITECTURE CIBLE ET TOPOLOGIE (DSH-CORE)

## 1. PRINCIPES DIRECTEURS
* **Orchestration Déterministe :** Utilisation de LangGraph comme colonne vertébrale (machine à états). AutoGen est réservé à des sous-nœuds isolés (ex: débats entre agents).
* **Clients Aveugles & Interconnectés :** Cherry Studio, Open WebUI, VS Code, n8n et **AnythingLLM** ne gèrent aucune logique métier ni mémoire locale. AnythingLLM n'utilise plus sa base interne mais interroge le bus LlamaIndex centralisé.
* **Protocole d'Outils (MCP) :** Utilisation exclusive du Model Context Protocol (MCP Hub) pour interagir avec l'OS, les fichiers locaux et n8n.

## 2. SCHÉMA GLOBAL DU SYSTÈME

              ┌────────────────────────────────────────────────────┐
              │          CLIENTS D'AFFICHAGE UNIQUES               │
              │ Cherry Studio | Open WebUI | AnythingLLM | VS Code │
              └──────────────────────┬─────────────────────────────┘
                                     │ (Standard API OpenAI /v1)
                                     ▼
              ┌────────────────────────────────────────────────────┐
              │            CERVEAU CENTRAL (FastAPI)               │
              │          LangGraph (State & Orchestration)         │
              └──────────────┬────────────────┬───────────────┬────┘
                             │                │               │
        ┌────────────────────┴───┐        ┌───┴───────────┐   │    ┌─────────────────────────┐
        ▼                        ▼        ▼               ▼   └───►│ OBSERVABILITÉ (ÉCRAN 2) │
┌─────────────────────────┐ ┌───────────────┐ ┌───────────────┐    │ - Langfuse (Tokens)     │
│    CASCADE DE RÔLES     │ │    MCP HUB    │ │    MÉMOIRE    │    │ - LangGraph Studio      │
│ Base Model (Qwen 9B)    │ │ Outils réels  │ │ - Qdrant      │    │   (Animation temps réel)│
│   │                     │ │ Disk, Tor, Git│ │ - Neo4j       │    └─────────────────────────┘
│   ▼                     │ │  Drive, n8n   │ │ - Mem0        │
│ Expert Model (Qwen 27B) │ └───────────────┘ └───────────────┘
│   │                     │
│   ▼                     │
│ Supervisor (Claude 3.5) │
└─────────────────────────┘

## 3. GESTION DE LA MÉMOIRE (TRIPTYQUE)
* **Qdrant (Vectoriel - Port 6333) :** 
  * `mem0_memories` : Faits persistants extraits par Qwen 9B.
  * `document_bus` : Indexation LlamaIndex des dossiers (factures, code).
  * `skills_catalog` : Indexation des compétences pour exécution via le MCP Hub.
* **Neo4j (Graphe - À déployer) :** Cartographie des relations (Knowledge Graph). Indispensable pour lier un Fournisseur ↔ Facture ↔ Avoir ↔ Ligne bancaire.

## 4. OBSERVABILITÉ ET CONTRÔLE (ÉCRAN 2)
* **Langfuse :** Traçabilité exacte des nœuds LangGraph, comptage des jetons, et suivi des coûts (déployé via Docker).
* **React Flow / LangGraph Studio :** Interface graphique permettant de visualiser en temps réel le passage des données entre les agents et les boucles.

## 5. PIPELINES MÉTIER (USINE DE TRAITEMENT)

**Pipeline Factures & Documents (Phase 8 - Évoluée) :**
1. **Triage Initial (Triage Node) :** 
   - *Image (Ticket, carburant, resto) :* Routage vers `qwen2.5-vl:7b` pour extraction géométrique directe.
   - *PDF (Facture fournisseur) :* Routage vers `Surya OCR` pour extraction massive de texte brut.
2. **Structuration (Cerveau) :** L'agent réparateur (`qwen3.5:9b`) valide l'arithmétique (HT + TVA = TTC) et convertit les données brutes via un schéma Pydantic.
3. **Routage Conditionnel :**
   - *Si standard :* Envoi vers le nœud d'Ingestion (Qdrant) pour déclencher n8n.
   - *Si complexe ou inconnu :* Escalade vers le graphe d'apprentissage.

**Pipeline Lettrage SIA BOSH (Cible) :**
* Utilisation conjointe de Qdrant (recherche de la facture) et Neo4j (arbre de résolution des avoirs) pour déterminer le solde net avant rapprochement bancaire.

## 6. BOUCLE D'AUTO-APPRENTISSAGE (PATTERN TEACHER-STUDENT)
Le véritable moteur de création du système est géré en coulisse par LangGraph. Lorsque le système fait face à un nouveau format de fournisseur ou une impasse, il déclenche la cascade suivante :

1. **Tier Rapide (Qwen 9B) :** Tente d'extraire la donnée, mais échoue sur la mise en page.
2. **Tier Expert (Qwen 27B) :** Escalade pour la résolution de la logique comptable complexe.
3. **Tier Superviseur (Claude 3.5 via OpenRouter) :** Audite la structure du document inconnu et rédige un fichier d'apprentissage `SKILL_[NOM_FOURNISSEUR].md`. Ce fichier est injecté dans Qdrant pour que Qwen 9B devienne totalement autonome lors de la prochaine facture de ce même fournisseur.