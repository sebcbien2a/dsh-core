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
                                 │                     │                    │
        ┌────────────────────┴───┐         ┌───┴───────────┐    │    ┌─────────────────────────┐
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




NOUVEAU, EST CE QUE LE PROJET PECEDENT EST FINALISE


# ARCHITECTURE CIBLE : DSH-CORE (SIA BOSH)

## 1. Objectifs Métier (Distributeur Corse / Val de Loire)
Le système SIA BOSH doit soulager la charge mentale liée à la comptabilité et à la gestion de stock à travers 3 piliers :
1. **Rapprochement des Achats (Le bouclier anti-comptable) :** Extraction automatique des factures fournisseurs (ex: Paintcar, Corsica Ferries) et lettrage avec les mouvements bancaires dans Neo4j pour justifier chaque dépense.
2. **Gestion des Ventes et LCR :** Modélisation des paiements groupés (Lettres de Change Relevé de fin de mois) liant un seul mouvement bancaire à de multiples factures clients émises.
3. **Réassort Intelligent (Génération Excel) :** Analyse des factures de ventes pour en extraire les articles vendus, triés par fournisseur, et pré-remplissage d'un tableau Excel de réassort (avec validation humaine finale).

## 2. Infrastructure Technique
* **Base de l'orchestrateur :** `A:\dsh-core\` (Python 3.11, `.venv-v3`).
* **Cerveau & Routage :** FastAPI (`api.py`) + LangGraph (`orchestrator.py`).
* **Agents IA :** Centralisés dans LangGraph. Les anciens scripts autonomes (ex: `research_agent.py` avec Llama3.2) sont obsolètes et à archiver.
* **Observabilité :** Langfuse v2 (Docker, port 3001) connecté via un fichier `.env` ultra-sécurisé.
* **Base Graphe (SIA BOSH) :** Neo4j (Docker, ports 7474/7687) pour la modélisation des lettrages (factures -> paiements).
* **Sauvegarde :** Dépôt distant privé sur GitHub (branche `main`).

MAJ ARCHITECTURE V2] - Standardisation des Endpoints Agents

Problème identifié : Instabilité des clients (Open WebUI, requêtes externes) due à un format de retour de l'API (FastAPI) hétérogène (tantôt string brut, tantôt dict). Le débuggage se faisait côté client, violant le principe de centralisation.

Décision (Le Contrat Universel) : Tous les endpoints du Cerveau (dsh-core), et en particulier /api/research, doivent désormais retourner un objet JSON standardisé. Le format d'échange imposé est :
{ "status": "success|error", "agent": "nom_de_lagent", "result": "données brutes ou texte" }

Conséquence : La logique d'exécution, de scraping et de formatage est strictement confinée au backend (FastAPI/LangGraph). Les interfaces (Open WebUI, Cherry, n8n) sont réduites au rôle de "dumb pipes" (tuyaux d'affichage) et ne font qu'extraire la clé result du JSON retourné.

### Contrat d'Interface Universel (API FastAPI)
Pour garantir la compatibilité absolue avec tous les clients (Open WebUI, Cherry Studio, n8n, VS Code), **dsh-core** impose un format de retour strict pour 100% de ses endpoints. 
Aucune route ne doit retourner de texte brut. La logique métier (scraping, formatage) reste dans LangGraph/Agents.

**Format imposé (JSON) :**
```json
{
  "status": "success",
  "agent": "nom_de_lagent",
  "result": "Données brutes, dictionnaire ou texte généré"
}

Devient :

Plain Text
Vision fonctionnelle
+
Agents
+
Mémoire
+
Escalade
+
Interfaces
Afficher plus de lignes

À ajouter :

Gouvernance des modèles
Plain Text
Les modèles sont interchangeables.
 
BaseModel
actuellement : Qwen 9B
 
ExpertModel
actuellement : Qwen 27B
 
VisionModel
actuellement : Qwen2.5-VL
 
SupervisorModel
actuellement : Claude Sonnet
 
Toute évolution future doit remplacer
un rôle et non un nom de modèle.
Afficher moins
Nouveaux agents officiels
Plain Text
RouterAgent
ResearchAgent
InvoiceAgent
MemoryAgent
SkillAgent
SupervisorAgent
Afficher plus de lignes

Action 1 : Ajouter la nouvelle collection Qdrant
Ouvre docs/01_ARCHITECTURE_CIBLE.md et cherche la section "3. GESTION DE LA MÉMOIRE (TRIPTYQUE)".
À l'endroit où tu listes tes collections Qdrant (mem0_memories, document_bus, skills_catalog), ajoute cette nouvelle ligne :   Markdown  * `teacher_student_metrics` : Stocke les métriques d'escalade (ex: `{"task": "facture_inconnue", "base_model_success": false, "supervisor_used": true, "skill_created": true}`) pour tracer les échecs et la création de compétences.
Action 2 : Ajouter les KPI officiels
Tout à la fin de ce même fichier docs/01_ARCHITECTURE_CIBLE.md, crée une nouvelle section dédiée pour officialiser les objectifs chiffrés de ton système :   Markdown## 7. GOUVERNANCE ET KPI (TAUX D'APPRENTISSAGE)
L'objectif ultime de DSH-Core est de transférer progressivement l'expertise des modèles lourds vers le modèle de base via la création de Skills.

**KPI d'Apprentissage (Learning Rate) :**
* **Objectif M+1 :** Qwen 9B autonome (70%), Qwen 27B sollicité (25%), Claude sollicité (5%).
* **Objectif M+6 :** Qwen 9B autonome (90%), Qwen 27B sollicité (9%), Claude sollicité (1%).

**KPI Opérationnels :**
* **ResearchAgent :** Temps moyen Quick Search < 10s | Deep Search < 90s | Cache Hit Ratio > 30%.
* **InvoiceAgent :** Taux de validation directe > 95% | Taux de réparation OCR automatique >
