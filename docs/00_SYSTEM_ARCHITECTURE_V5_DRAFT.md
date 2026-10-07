# 00 - SYSTEM ARCHITECTURE V5 (DSH-CORE)

Statut : DRAFT VALIDÉ
Date : 2026-09
Version : V5

---

## 1. VISION GLOBALE

DSH-Core est une plateforme agentique locale.

Le système repose sur :

- 1 point d'entrée FastAPI
- 1 RouterAgent
- 1 Orchestrateur LangGraph
- 4 agents métier principaux
- 1 mémoire centralisée
- 1 couche d'observabilité

Principe :

Les interfaces :

- Cherry Studio
- Open WebUI
- VS Code
- AnythingLLM
- n8n

sont des clients aveugles.

Toute la logique réside dans le backend.

---

## 2. AGENTS PRINCIPAUX

### RouterAgent

Implémentation actuelle :

router.py

Mission :

- analyser les requêtes
- choisir le modèle
- choisir l'agent
- injecter la mémoire
- injecter les compétences

Etat :

EXISTANT

---

### ResearchAgent

Implémentation actuelle :

agents/research_agent.py

Mission :

- DDGS
- Playwright
- Recherche documentaire
- TOR
- Cache

Etat :

EXISTANT

---

### InvoiceAgent

Implémentation actuelle :

agents/invoice_agent.py

Mission :

- OCR
- Vision
- Structuration
- Validation
- Archivage
- n8n

Etat :

PARTIELLEMENT IMPLÉMENTÉ

Surya OCR :
PENDING

---

### MemoryAgent

Implémentation actuelle :

memory_manager.py

Mission :

- Mem0
- Qdrant
- LlamaIndex
- Skills

Etat :

EXISTANT

---

### CoderAgent

Implémentation actuelle :

agents/coder_agent.py

Mission :

- génération code
- exécution code
- auto-correction

Etat :

EXISTANT

---

## 3. AGENTS FUTURS

### SupervisorAgent

Mission :

- pilotage Claude
- audit
- validation
- arbitrage

Etat :

PRÉVU

NON IMPLÉMENTÉ

---

### TeacherStudentAgent

Mission :

- apprentissage autonome
- génération de skills
- réduction des appels Claude

Etat :

PRÉVU

NON IMPLÉMENTÉ

---

## 4. AGENTS LEGACY

### invoice_parser_agent.py

Etat :

CANDIDAT SUPPRESSION

Raison :

remplacé par InvoiceAgent

---

### summary_agent.py

Etat :

CANDIDAT SUPPRESSION

Raison :

absorbé progressivement par ResearchAgent

---

### pdf_extractor_agent.py

Etat :

LEGACY CONSERVÉ

Raison :

pdfplumber reste utile sur les PDF texte natifs.

Suppression interdite tant que Surya OCR n'a pas été validé sur tous les scénarios.
Statut : DRAFT VALIDÉ Date : 2026-09 Version : V5

1. VISION GLOBALE
DSH-Core est une plateforme agentique locale.

Le système repose sur :

1 point d'entrée FastAPI
1 RouterAgent
1 Orchestrateur LangGraph
4 agents métier principaux
1 mémoire centralisée
1 couche d'observabilité
Principe :

Les interfaces :

Cherry Studio
Open WebUI
VS Code
AnythingLLM
n8n
sont des clients aveugles.

Toute la logique réside dans le backend.

2. AGENTS PRINCIPAUX
RouterAgent
Implémentation actuelle :

router.py

Mission :

analyser les requêtes
choisir le modèle
choisir l'agent
injecter la mémoire
injecter les compétences
Etat :

EXISTANT

ResearchAgent
Implémentation actuelle :

agents/research_agent.py

Mission :

DDGS
Playwright
Recherche documentaire
TOR
Cache
Etat :

EXISTANT

InvoiceAgent
Implémentation actuelle :

agents/invoice_agent.py

Mission :

OCR
Vision
Structuration
Validation
Archivage
n8n
Etat :

PARTIELLEMENT IMPLÉMENTÉ

Surya OCR : PENDING

MemoryAgent
Implémentation actuelle :

memory_manager.py

Mission :

Mem0
Qdrant
LlamaIndex
Skills
Etat :

EXISTANT

CoderAgent
Implémentation actuelle :

agents/coder_agent.py

Mission :

génération code
exécution code
auto-correction
Etat :

EXISTANT

3. AGENTS FUTURS
SupervisorAgent
Mission :

pilotage Claude
audit
validation
arbitrage
Etat :

PRÉVU

NON IMPLÉMENTÉ

TeacherStudentAgent
Mission :

apprentissage autonome
génération de skills
réduction des appels Claude
Etat :

PRÉVU

NON IMPLÉMENTÉ

4. AGENTS LEGACY
invoice_parser_agent.py
Etat :

CANDIDAT SUPPRESSION

Raison :

remplacé par InvoiceAgent

summary_agent.py
Etat :

CANDIDAT SUPPRESSION

Raison :

absorbé progressivement par ResearchAgent

pdf_extractor_agent.py
Etat :

LEGACY CONSERVÉ

Raison :

pdfplumber reste utile sur les PDF texte natifs.

Suppression interdite tant que Surya OCR n'a pas été validé sur tous les scénarios.
Correction importante de l'architecture

Aujourd'hui :

Plain Text
api.py
|
ResearchAgent
 
api.py
|
CoderAgent
 
api.py
|
MemoryManager
Afficher plus de lignes

Je ne validerais pas cela à long terme.

Architecture cible :

Plain Text
api.py
|
V
router.py
|
V
orchestrator.py
|
+--> ResearchAgent
|
+--> InvoiceAgent
|
+--> MemoryAgent
|
+--> CoderAgent
Afficher plus de lignes
# 00 - SYSTEM ARCHITECTURE V5 (CIBLE VS RÉALITÉ)

Ce document définit l'architecture cible de DSH-Core et son état d'implémentation actuel.

## 1. VISION GLOBALE
DSH-Core est un système autonome unifié : 1 Cerveau (LangGraph), 5 Agents spécialisés, 1 Mémoire centralisée, 1 Observabilité.
- **État d'avancement :** [EN COURS] Le système possède toutes les briques, mais l'orchestration centralisée n'est pas encore stricte.

## 2. LES AGENTS ET COMPOSANTS
- **RouterAgent :** [ACTIF] Implémenté via `router.py`. Gère l'analyse de prompt et le choix du modèle.
- **ResearchAgent :** [ACTIF] Implémenté via `agents/research_agent.py`. Gère DDGS, Playwright et Qdrant.
- **InvoiceAgent :** [ACTIF] Implémenté via `agents/invoice_agent.py`. Gère l'OCR et la validation Pydantic.
- **MemoryAgent :** [ACTIF] Implémenté via `memory_manager.py`. Gère Mem0, LlamaIndex et Skills.
- **CoderAgent :** [ACTIF] Implémenté via `agents/coder_agent.py`. Gère l'exécution locale de code.
- **TeacherStudentAgent :** [PRÉVU] Agent de supervision de l'apprentissage et des escalades.
- **SupervisorAgent :** [PRÉVU] Instance dédiée à l'audit et à la validation complexe via Claude.

## 3. INFRASTRUCTURE & MÉMOIRE
- **Qdrant (Vectoriel) :** [ACTIF] Collections `mem0_memories`, `document_bus`, `skills_catalog`, `research_cache`.
- **Neo4j (Graphe) :** [ACTIF] Déployé mais non connecté aux flux métiers.
- **Langfuse (Observabilité) :** [ACTIF] Connecté et traçant les tokens/latences.
- **Teacher Metrics :** [PRÉVU] Collection Qdrant `teacher_student_metrics` pour mesurer l'autonomie.

Architecture V6

                FastAPI

                    |

              RouterAgent

                    |

      +-------------+-------------+

      |                           |

 ResearchAgent             InvoiceAgent

      |                           |

      +-------------+-------------+

                    |

            TeacherStudentAgent

                    |

            TeacherStudentRepository

                    |

                 Qdrant

       +------------+-------------+

       |            |             |

 document_bus  skills_catalog  teacher_student_metrics

                    |

              MemoryAgent

                    |

             Mem0 + LlamaIndex