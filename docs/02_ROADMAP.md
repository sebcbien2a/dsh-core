# 02 - ROADMAP DES CHANTIERS PRIORITAIRES

Suite à l'audit d'urbanisation, les développements sont priorisés pour interconnecter les briques existantes avant toute nouvelle fonctionnalité.

## CHANTIER 1 : Restructuration du Cerveau (LangGraph)
- **Objectif :** Brancher `api.py` ➔ `router.py` ➔ `orchestrator.py` ➔ `Agents`.
- **Statut :** [PRÉVU]

## CHANTIER 2 : TeacherStudentAgent
- **Objectif :** Créer l'agent qui supervise Langfuse/LangGraph pour détecter les échecs de Qwen 9B, piloter l'escalade vers Claude, et indexer les Skills.
- **Statut :** [PRÉVU]

## CHANTIER 3 : MemoryAgent (Refonte logique)
- **Objectif :** Ajouter une couche de décision au-dessus de `memory_manager.py` pour router les données automatiquement (Mem0 pour conversation, LlamaIndex pour documents, Qdrant pour Skills).
- **Statut :** [PRÉVU]

## CHANTIER 4 : OCRRouterNode
- **Objectif :** Créer un nœud LangGraph décisionnel pour router les documents visuels vers les bons extracteurs (Surya vs Qwen-VL vs Repair).
- **Statut :** [PRÉVU]

## CHANTIER 5 : Dashboard DSH-Core & Metrics
- **Objectif :** Créer la collection `teacher_student_metrics` dans Qdrant et un tableau de bord affichant le taux d'autonomie de Qwen 9B.
- **Statut :** [PRÉVU]


Phase 1 — Consolider V6
1. Tester InvoiceAgent sur de vraies factures

Objectif :

Plain Text
teacher_student_metrics
count : 1 → 5 → 20
Afficher plus de lignes

et vérifier :

Python
repo.get_global_metrics()
Afficher plus de lignes
2. Embeddings des factures

Aujourd'hui :

Python
vector=[0.0] * 768
Afficher plus de lignes

C'est du faux vectoriel.

Je remplacerais par :

Plain Text
nomic-embed-text
Afficher plus de lignes

sur :

Plain Text
fournisseur
date
catégorie
montant
Afficher plus de lignes

C'est le gain le plus rapide.

3. Skills Catalog automatique

Faire que :

Plain Text
Claude
↓
résout problème
↓
skills_catalog
Afficher plus de lignes

soit réellement alimenté.

Ça apportera plus de valeur que Neo4j immédiatement.

Phase 2 — Router V6
Router orienté compétences

Aujourd'hui :

Plain Text
Router
↓
Agent
Afficher plus de lignes

Demain :

Plain Text
Router
↓
skills_catalog
↓
Agent
Afficher plus de lignes

Là tu commences à avoir un système adaptatif.

Phase 3 — MemoryAgent

Quand plusieurs agents produisent des données :

Plain Text
Research
Invoice
Coder
TeacherStudent
Afficher plus de lignes

tu peux créer :

Plain Text
MemoryAgent
Afficher plus de lignes

qui centralise :

Plain Text
Qdrant
skills
documents
conversations
Afficher plus de lignes
Phase 4 — Neo4j

Là seulement.

Parce qu'il faut avoir des relations à stocker.

Par exemple :

Plain Text
Facture
↓
Fournisseur
↓
Projet
Afficher plus de lignes

ou :

Plain Text
Skill
↓
Résout
↓
Erreur
Afficher plus de lignes

ou :

Plain Text
Agent
↓
Produit
↓
Document
Afficher plus de lignes
Ce que je ferais demain matin
Tester 3 à 5 factures réelles.
Vérifier :
Python
repo.get_global_metrics()
Afficher plus de lignes
Ajouter les vrais embeddings dans invoice_documents.
Commencer skills_catalog.

Et honnêtement, oui :

Plain Text
Qdrant
+
Neo4j
Afficher plus de lignes

sera probablement la V7 ou V8.

Mais aujourd'hui ton meilleur retour sur investissement est :

Plain Text
Embeddings factures
+
Skills Catalog
Afficher plus de lignes

avant Neo4j. C'est là que tu gagneras le plus en intelligence réelle du système.

Fournissez vos commentaires sur BizChat

Non, on n'avait pas fini. Et tu as bien fait de demander un point d'étape.

L'intermède sur la GED, OneDrive, les factures et le ledger était utile, mais il ne faut pas nous faire perdre le fil.

Ce qui est terminé

✅ ResearchAgent

✅ InvoiceAgent

✅ AccountingValidator

✅ InvoiceEmbeddingService

✅ TeacherStudentAgent

✅ TeacherStudentRepository

✅ SkillCreatorAgent

✅ SkillsRepository

✅ MemoryRepository

✅ GraphRepository

✅ Qdrant

✅ Neo4j

✅ LangGraph

✅ Langfuse

Ce qui n'est PAS terminé
1. SkillRouter

C'est probablement le plus gros trou actuel.

Aujourd'hui :

Plain Text
Echec
↓
TeacherStudent
↓
SkillCreator
↓
Skill sauvegardé
Afficher plus de lignes

Mais je ne sais pas encore si :

Plain Text
Nouvelle tâche
↓
Recherche skill existant
↓
Injection du skill
↓
Exécution
Afficher plus de lignes

est réellement branché.

Tant que ce n'est pas fait :

Plain Text
Le système apprend
mais n'exploite pas totalement ses apprentissages.
Afficher plus de lignes
2. TeacherStudent → SkillCreator

Je n'ai pas encore vu la partie :

Python
teacher_student_agent
Afficher plus de lignes

qui déclenche réellement :

Python
SkillCreatorAgent.create_and_store_skill()
Afficher plus de lignes

après le seuil d'échec.

Je vois la logique.

Je vois le repository.

Je vois le creator.

Je n'ai pas encore vu le branchement complet.

3. MemoryAgent

Le plus gros morceau restant.

Aujourd'hui :

Plain Text
ResearchAgent
Afficher plus de lignes

utilise certaines mémoires.

Plain Text
InvoiceAgent
Afficher plus de lignes

utilise certaines mémoires.

Plain Text
TeacherStudentAgent
Afficher plus de lignes

utilise certaines mémoires.

Mais je n'ai pas encore vu :

Plain Text
MemoryAgent
Afficher plus de lignes

devenir l'orchestrateur unique.

4. Neo4j métier

Neo4j est installé.

GraphRepository existe.

Mais le graphe métier n'est pas encore réellement exploité.

Par exemple :

Plain Text
Document
↓
Invoice
↓
Supplier
Afficher plus de lignes

commence à exister.

Mais :

Plain Text
Invoice
↔ Skill
 
Invoice
↔ Document
 
Metric
↔ Skill
 
Client
↔ Invoice
Afficher plus de lignes

n'est pas encore branché partout.

5. Remplacement du faux vectoriel

Tu as :

Python
vector=[0.0] * 768
Afficher plus de lignes

dans certains endroits.

Je sais que tu as commencé :

Plain Text
InvoiceEmbeddingService
Afficher plus de lignes

mais je n'ai pas encore vu le remplacement effectif partout.

Mon estimation honnête

Je dirais :

Plain Text
Architecture 95 %
 
Code 85 %
 
Intégration 70 %
 
Tests réels 15 %
Afficher plus de lignes
Si c'était mon projet

La suite stricte serait :

V8
Plain Text
SkillRouter
↓
SkillsRepository
↓
TeacherStudent
Afficher plus de lignes
V8.5
Plain Text
TeacherStudent
↓
SkillCreator
↓
SkillsRepository
↓
réutilisation automatique
Afficher plus de lignes
V9
Plain Text
MemoryAgent
↓
MemoryRepository
↓
GraphRepository
↓
TeacherStudentRepository
↓
SkillsRepository
Afficher plus de lignes
V9.5
Plain Text
InvoiceEmbeddingService
↓
Qdrant réel
Afficher plus de lignes

(remplacement définitif des vecteurs factices)

Donc non, on n'a rien oublié.

Le gros morceau restant est bien :

Plain Text
SkillRouter
+
TeacherStudent
+
MemoryAgent
Afficher plus de lignes

Et c'est probablement là que se situent les derniers 10-15 % de DSH‑Core avant les premiers vrais flux de production.

Voici la version plan d'action exécutable, pas la version philosophique.

C'est celle que je donnerais à Gemini pour qu'il arrête de créer de nouveaux concepts et commence à construire dans le bon ordre.

DSH-Core - Plan d'action officiel
Objectif

Passer de :

Collection de composants


à :

Système de mémoire d'entreprise opérationnel

PHASE 1 - Stabilisation V7
Etat

Code déjà développé :

ResearchAgent
InvoiceAgent
TeacherStudentAgent
TeacherStudentRepository

InvoiceEmbeddingService
InvoiceSimilarityService
AccountingValidator

SkillsRepository
SkillCreatorAgent
SkillRouter

MemoryRepository
MemoryManager

GraphRepository
Neo4jService

Travail restant
Brancher réellement les composants

Aujourd'hui :

SkillCreatorAgent existe


Demain :

TeacherStudent
↓
SkillCreatorAgent

Brancher SkillRouter

Aujourd'hui :

SkillRouter existe


Demain :

ResearchAgent
↓
SkillRouter
↓
SkillsRepository
↓
Qwen9B

KPI V7

Critères de validation :

Création automatique d'un skill
Stockage dans skills_catalog
Réutilisation détectée
Compteur usage > 0

PHASE 2 - V7.8 Ledger Foundation

Objectif :

Créer la vérité métier.

Nouveau dossier
ledgers/

Fichiers à créer
repositories/

document_ledger_repository.py

banking_ledger_repository.py

services/

document_ledger_service.py

banking_ledger_service.py

schemas/

document_record.py

banking_record.py

Base SQLite
DocumentLedger.db
BankingLedger.db

Critères de validation
DocumentLedger

Capable de stocker :

UID
Document
Chemin
Statut
Qdrant ID
Neo4j ID

BankingLedger

Capable de stocker :

Date
Montant
Libellé
Compte
Document associé

PHASE 3 - Banking Domain

Objectif :

Construire la brique bancaire.

Nouveau dossier
agents/

Nouveau composant
banking_agent.py

Sources acceptées
Niveau 1

CSV banque

Exemple :

Crédit Agricole
BP
LCL

Niveau 2

PDF bancaire

Niveau 3

Capture écran

PNG

JPG

Niveau 4

Photo papier

OCR

Validation humaine obligatoire

Workflow
CSV
↓
BankingAgent
↓
BankingLedger

Critères de validation

Importer :

CSV
PDF
Image


dans :

BankingLedger

PHASE 4 - Assets Architecture

Objectif :

Créer le coffre-fort documentaire unique.

Arborescence cible
OneDrive

DSH-Core

├── Assets

├── Documents

├── Banking

├── Catalogs

├── Projects

├── Marketing

├── Knowledge

├── Archives

├── Errors

└── Exports

Critère

Tous les workflows utilisent :

OneDrive


comme vérité physique.

PHASE 5 - Neo4j réel

Objectif :

Passer de Neo4j technique à Neo4j métier.

Relations à alimenter
Facture
→ Fournisseur

Facture
→ Paiement

Facture
→ Avoir

Facture
→ BL

Projet
→ Facture

Produit
→ Catalogue

Produit
→ Amazon

Skill
→ Task

Document
→ Projet

Critère

Pouvoir demander :

Montre-moi tous les documents liés à Rexel

PHASE 6 - Reconciliation Agent

Agent le plus stratégique.

Nouveau fichier
agents/reconciliation_agent.py

Entrées
DocumentLedger

BankingLedger

Qdrant

Neo4j

Sorties
Facture ↔ Paiement

Facture ↔ Avoir

Facture ↔ BL

Facture ↔ Projet

KPI
Taux de rapprochement automatique

PHASE 7 - AnythingLLM Integration

Objectif :

Transformer AnythingLLM en portail d'import universel.

Role

Pas cerveau.

Pas mémoire.

Pas orchestrateur.

Role réel
Portail d'entrée

Flux
AnythingLLM

↓

Classification

↓

DSH-Core

Cas
Facture
AnythingLLM

↓

InvoiceAgent

↓

DocumentLedger

Banque
AnythingLLM

↓

BankingAgent

↓

BankingLedger

Catalogue
AnythingLLM

↓

CatalogAgent

↓

CatalogLedger

PHASE 8 - Agents Métier

Après stabilisation.

CatalogAgent

Gestion produits.

MarketingAgent

Flyers.

Catalogues.

PDF.

WebsiteAgent

Sites vitrines.

AmazonAgent

Fiches Amazon.

SEO.

Descriptions.

PHASE 9 - Vision Finale
Assets
        ↓

Ledgers
        ↓

Qdrant
        ↓

Neo4j
        ↓

MemoryManager
        ↓

Agents
        ↓

n8n

Règle Architecturale Suprême
Assets + Ledgers
=
Vérité absolue

Qdrant
=
Mémoire sémantique

Neo4j
=
Mémoire relationnelle

Skills
=
Mémoire des compétences

MemoryManager
=
Chef d'orchestre

Agents
=
Travailleurs spécialisés

Prochaine action concrète

Si tu veux rester discipliné :

1. Terminer intégration SkillCreatorAgent
2. Terminer intégration SkillRouter
3. Construire DocumentLedger
4. Construire BankingLedger
5. Construire BankingAgent


C'est, à mon avis, le chemin le plus court vers une première version réellement utilisable de DSH-Core.

V7 ✅ VALIDÉE

V7.POST 🔄 Réglage retrieval Skills

V7.8A ⏳ IngestionManager

V7.8B ⏳ DocumentLedger

V7.8C ⏳ BankingLedger

V8 ⏳ Neo4j métier

V8.5 ⏳ ReconciliationAgent

V9 ⏳ Tableau de bord Père + Serveur dédié

La bonne séquence
MAINTENANT
V7.8A
Plain Text
IngestionManager
Afficher plus de lignes

Mission :

Plain Text
Un document arrive
↓
Quel type ?
↓
Quel agent ?
↓
Quel ledger ?
`
Afficher plus de lignes
V7.8B
Plain Text
DocumentLedger
Afficher plus de lignes

Contient :

Plain Text
Factures
 
Avoirs
 
BL
 
Tickets restaurant
 
Carburant
 
Péages
 
Notes de frais
 
Contrats
Afficher plus de lignes
V7.8C
Plain Text
BankingLedger
Afficher plus de lignes

Contient :

Plain Text
Transactions bancaires
Afficher plus de lignes

avec :

Plain Text
Hash de déduplication
Afficher plus de lignes

pour gérer :

Plain Text
CSV
 
PDF
 
Capture écran
 
Photos
 
Import historique
Afficher plus de lignes

même en chevauchement.

Ensuite seulement
V8

Neo4j métier

Là Neo4j devient extrêmement puissant.

Tu pourras créer :

Plain Text
(Facture)
│
▼
 
(Fournisseur)
 
(Facture)
│
▼
 
(Paiement)
 
(Facture)
│
▼
 
(Avoir)
 
(Facture)
│
▼
 
(BL)
 
(Facture)
│
▼
 
(Projet)
Afficher plus de lignes
Puis V8.5

Et c'est là que je rejoins Gemini.

On introduit :

Plain Text
MemoryAgent
``
Afficher plus de lignes

ou :

Plain Text
MemoryManager V2
`
Afficher plus de lignes

qui relie :

Plain Text
Qdrant
 
Neo4j
 
Ledgers
 
Agents

✅ V7 Apprentissage

⏳ V7.8A IngestionManager

⏳ V7.8B DocumentLedger

⏳ V7.8C BankingLedger

⏳ V8 Neo4j Métier

⏳ V8.5 MemoryAgent / Traçabilité IA

⏳ V9 Dashboard Père + Serveur dédié

Backlog V8.5 gelé mais validé
1. Modèle Neo4j des Skills

Nœuds :

Plain Text
(Skill)
(ErrorMetric)
(Agent)
(SkillCreator)
(Workflow)
``
Afficher plus de lignes

Relations :

Plain Text
(Skill)-[:CREATED_FROM]->(ErrorMetric)
 
(Skill)-[:SOLVES]->(ErrorMetric)
 
(Agent)-[:USED]->(Skill)
 
(SkillCreator)-[:GENERATED]->(Skill)
 
(Skill)-[:APPLIES_TO]->(Workflow)
Afficher plus de lignes

Exemple :

Plain Text
ResearchAgent
│
USED
│
▼
 
TVA inversée
│
SOLVES
│
▼
 
Facture HT/TTC inversée
Afficher plus de lignes
2. Plan de migration SkillsRepository → Neo4j

Actuellement :

Plain Text
Qdrant
Afficher plus de lignes

contient :

Plain Text
Skill
Embedding
UsageCount
Métadonnées
Afficher plus de lignes

Demain :

Plain Text
Qdrant
↓
Recherche
 
Neo4j
↓
Traçabilité
 
Afficher plus de lignes

Migration prévue :

Plain Text
Étape 1
Qdrant reste maître
 
Étape 2
Duplication vers Neo4j
 
Étape 3
Audit croisé
 
Étape 4
MemoryAgent
``
Afficher plus de lignes

Aucune migration destructive.

3. GraphRepository V8

À prévoir :

Plain Text
repositories/graph_repository.py
Afficher plus de lignes

Méthodes :

Python
create_skill()
 
create_error_metric()
 
link_skill_to_error()
 
link_agent_to_skill()
 
link_skill_to_workflow()
 
get_skill_usage()
Afficher plus de lignes
4. Tests d'intégration Neo4j

Fichier prévu :

Plain Text
test_graph_repository.py
Afficher plus de lignes

Validation :

Plain Text
Créer Skill
✅
 
Créer ErrorMetric
✅
 
Créer relation CREATED_FROM
✅
 
Relire relation
✅
 
Afficher plus de lignes
5. Tests V8.5

Fichier prévu :

Plain Text
test_memory_agent.py
Afficher plus de lignes

Questions à pouvoir répondre :

Plain Text
Quel skill est le plus utilisé ?
 
Quel agent utilise le plus de skills ?
 
Quel skill corrige le plus d'erreurs ?
 
Quel workflow génère le plus d'apprentissages ?
Afficher plus de lignes
6. MemoryAgent

Fichier prévu :

Plain Text
agents/memory_agent.py
Afficher plus de lignes

Mission :

Plain Text
Qdrant
+
Neo4j
+
Ledgers
Afficher plus de lignes

Répondre à :

Plain Text
Pourquoi ce skill existe ?
 
Combien de fois utilisé ?
 
Par qui ?
 
Pour quel workflow ?
Afficher plus de lignes
✅ Décision officielle

Je note donc dans la roadmap :

Plain Text
V8.5 - TRAÇABILITÉ IA
 
✅ Modèle Neo4j validé
✅ Relations validées
✅ Plan de migration validé
✅ Tests d'intégration définis
✅ MemoryAgent défini
 
⏸ Développements reportés
`✅ V8.5 GELÉ MAIS SPÉCIFIÉ
Neo4j Skills Graph
Nœuds
Plain Text
(Agent)
 
(Skill)
 
(ErrorMetric)
 
(Workflow)
 
(Task)
 
(SkillCreator)
Afficher plus de lignes
Relations
Plain Text
(Agent)-[:USED]->(Skill)
 
(Skill)-[:CREATED_FROM]->(ErrorMetric)
 
(Skill)-[:SOLVES]->(ErrorMetric)
 
(SkillCreator)-[:GENERATED]->(Skill)
 
(Skill)-[:APPLIES_TO]->(Workflow)
 
(Skill)-[:APPLIED_TO]->(Task)
Afficher plus de lignes
✅ Plan de migration SkillsRepository
Phase 1

Aujourd'hui :

Plain Text
Qdrant
=
Source officielle des Skills
Afficher plus de lignes
Phase 2

Demain :

Plain Text
SkillCreator
↓
Qdrant
 
+
↓
 
Neo4j
Afficher plus de lignes

Double écriture.

Aucun risque.

Phase 3

MemoryAgent

Plain Text
Recherche
↓
Qdrant
 
Traçabilité
↓
Neo4j
Afficher plus de lignes
✅ GraphRepository prévu
Plain Text
repositories/graph_repository.py
Afficher plus de lignes

Méthodes prévues :

Python
log_skill_creation()
 
log_skill_usage()
 
create_error_metric()
 
get_skill_usage()
 
get_agent_skills()
 
get_skill_history()
Afficher plus de lignes
✅ Tests d'intégration Neo4j

Fichier :

Plain Text
test_graph_repository.py
Afficher plus de lignes

Vérifications :

Plain Text
Connexion Neo4j
✅
 
Création Skill
✅
 
Création ErrorMetric
✅
 
Création relation CREATED_FROM
✅
 
Création relation USED
✅
 
Lecture graphe
✅
 
Afficher plus de lignes
✅ Tests V8.5

Fichier :

Plain Text
test_memory_agent.py
Afficher plus de lignes

Questions métier :

Plain Text
Quel skill est le plus utilisé ?
 
Quel agent utilise le plus de skills ?
 
Quelle erreur a généré le plus de compétences ?
 
Quel workflow produit le plus d'apprentissages ?
Afficher plus de lignes
✅ MemoryAgent cible
Plain Text
agents/memory_agent.py
Afficher plus de lignes

Rôle :

Plain Text
Qdrant
+
Neo4j
+
Ledgers
Afficher plus de lignes

Capable de répondre :

Plain Text
Pourquoi ce skill existe ?
 
Quel agent l'utilise ?
 
Combien de fois ?
 
Quelle erreur l'a créé ?
Afficher plus de lignes
✅ Statut officiel
Plain Text
V7 ✅ Validé
 
V7.8A ✅ Validé
 
V7.8B ⏳ À faire
 
V7.8C ⏳ À faire
 
V8 ⏳ Neo4j Métier
 
V8.5 ✅ Spécifié
⏸ Gelé
 
V9 ⏳ Dashboard Père



1 octobre 2026

DSH-Core – Feuille de route consolidée (Révision Septembre 2026)
✅ V8 TERMINÉE
Boucle d'apprentissage autonome
Erreur
↓
TeacherStudentAgent
↓
EXPERT_MODEL
↓
SkillCreatorAgent
↓
SkillsRepository
↓
Qdrant
↓
SkillRouter
↓
Réutilisation automatique du Skill


Statut :

✅ VALIDÉ EN TEST RÉEL


Validation obtenue :

Création du Skill
✅

Stockage Qdrant
✅

Recherche vectorielle
✅

Réutilisation automatique
✅

Injection de contexte
✅

✅ Modèles agnostiques

L'architecture n'est plus liée à des modèles spécifiques.

BASE_MODEL

EXPERT_MODEL

VISION_MODEL

EMBEDDING_MODEL


Statut :

✅ VALIDÉ


Objectif atteint :

Qwen
DeepSeek
Gemma
Mistral
OpenAI


peuvent être remplacés sans refonte du code métier.

✅ API DSH-Core ↔ n8n

Architecture validée :

n8n
↓
HTTP Request
↓
host.docker.internal:8000
↓
DSH-Core API


Résultat observé :

POST /api/v1/ingest
200 OK


Validation :

PDF transmis par n8n
✅

Réception par FastAPI
✅

Invocation InvoiceAgent
✅


Le flux n8n → DSH-Core est donc opérationnel.

✅ Décision majeure : IngestionManager devient la porte d'entrée universelle

Tous les documents, quelle que soit leur origine, devront passer par :

IngestionManager


Sources prévues :

Gmail

OneDrive

Android

n8n

AnythingLLM

DSH-Core Desktop

APIs futures

Import manuel


Architecture cible :

Toutes les sources
          ↓
   IngestionManager
          ↓
Classification
          ↓
Pipeline adapté
          ↓
JSON métier

✅ Décision majeure : Source de vérité

La source de vérité n'est PAS Qdrant.

La source de vérité n'est PAS Neo4j.

La source de vérité devient :

A:\DSH-Core


ou

OneDrive\DSH-Core


Architecture retenue :

Documents physiques
        ↓

Ledger SQLite
        ↓

Qdrant
        ↓

Neo4j

✅ Architecture Gmail

Structure créée :

DSH

├── A_TRAITER
├── EN_COURS
├── TRAITE
└── ERREUR


Flux cible :

Email facture
        ↓

DSH/A_TRAITER
        ↓

n8n
        ↓

DSH-Core
        ↓

Traitement
        ↓

DSH/TRAITE


ou :

DSH/ERREUR

✅ Décision AnythingLLM

AnythingLLM ne doit PAS posséder son propre pipeline documentaire.

Architecture validée :

AnythingLLM
        ↓
IngestionManager


Exactement comme :

Gmail
OneDrive
Android
n8n


Objectif :

1 seul pipeline documentaire

1 seul OCR

1 seul Ledger

1 seul Qdrant

1 seul Neo4j

1 seule validation métier

🔄 Nouveau chantier : Pipeline documentaire universel

La séance n8n a révélé un défaut architectural important :

Erreur actuelle :

Failed to load image or audio file


Cause :

PDF envoyé directement à Qwen VL


alors que tous les PDF ne doivent pas être traités par Vision.

Cas n°1 : PDF texte natif

Exemples :

Facture EDF

Facture Rexel

Facture fournisseur PDF

Relevé bancaire téléchargé

Export ERP

Export Sage


Pipeline retenu :

PDF
↓
PyMuPDF
↓
Texte
↓
BASE_MODEL
↓
JSON


Pourquoi :

Plus rapide

Plus précis

Pas d'OCR

Pas de perte d'information

Cas n°2 : PDF scanné / PDF photo

Exemples :

Ticket restaurant photographié

Ticket carburant

Ticket péage

Facture papier scannée

Relevé photographié


Pipeline retenu :

PDF
↓
Détection absence de texte
↓
Extraction images
↓
Surya OCR
↓
VISION_MODEL
↓
JSON


ou :

PDF
↓
Images
↓
VISION_MODEL
↓
JSON


selon les résultats des futurs tests.

Cas n°3 : Photo smartphone

Exemples :

JPG

PNG

HEIC


Pipeline :

Image
↓
VISION_MODEL
↓
JSON

Cas n°4 : CSV

Exemples :

Relevé bancaire CSV

Export logiciel


Pipeline :

CSV
↓
Pandas
↓
JSON


Aucune IA nécessaire.

Cas n°5 : Excel

Exemples :

Export Sage

Balance

Grand Livre

Liste clients


Pipeline :

XLSX
↓
OpenPyXL / Pandas
↓
JSON


Aucune IA nécessaire.

✅ Architecture documentaire cible
Document reçu
        ↓

DocumentClassifier
        ↓

PDF texte ?
        ↓
      PyMuPDF

PDF image ?
        ↓
 Surya + Vision

Image ?
        ↓
 Vision

CSV ?
        ↓
 Pandas

Excel ?
        ↓
 Pandas


Puis :

JSON métier
        ↓
Validation Pydantic
        ↓
Ledger
        ↓
Qdrant
        ↓
Neo4j

🔄 V8.5
Skills ↔ Neo4j

Objectif :

Skill
    ↓
Neo4j


afin de répondre à :

Pourquoi ce skill existe ?

Quel échec l'a créé ?

Combien de fois a-t-il été utilisé ?

Quel agent l'utilise ?


Relations prévues :

Skill
↓
CREATED_FROM
↓
Metric

Skill
↓
USED_BY
↓
Agent

Skill
↓
SOLVES
↓
Problem

🔄 V9
MemoryAgent

Objectif :

MemoryAgent


devient l'orchestrateur principal de :

MemoryRepository

SkillsRepository

TeacherStudentRepository

GraphRepository

Qdrant

Neo4j

🔄 Ledger

Objectif final :

Date
Montant
Catégorie
Fournisseur
Client
Chemin PDF
Hash
Statut
Rapprochement bancaire


Piloté par :

SQLite


et non par Qdrant.

🔄 DSH-Core Desktop

Décision validée :

Le serveur permanent est repoussé.

Une application locale sera créée.

Vision cible :

DSH-Core Desktop


avec :

[Démarrer Agents]

[Traiter Inbox]

[Documents]

[Comptabilité]

[Recherche]

[Skills]

[Administration]

État d'avancement global
Architecture globale        95 %

V8 Learning Loop          100 %

API n8n                    95 %

IngestionManager           90 %

Pipeline documentaire      60 %

Neo4j Skills              20 %

MemoryAgent               20 %

Ledger                    10 %

DSH-Core Desktop           0 %

Priorités immédiates
P1

Créer :

DocumentClassifier

P2

Distinguer automatiquement :

PDF texte
vs
PDF image

P3

Brancher :

SkillsRepository
↔
Neo4j

P4

Commencer le Ledger SQLite.

Conclusion : DSH-Core n'est plus un simple système OCR de factures. Il évolue vers une plateforme documentaire et comptable unifiée, où Gmail, Android, n8n, OneDrive, AnythingLLM et les futures interfaces utilisent tous le même point d'entrée : IngestionManager. 🚀

Je prends en compte cette réflexion. Elle ajoute un élément très important à la feuille de route : la gestion incrémentale des Skills dans Qdrant, qui devient désormais un composant d'architecture à part entière.

🔄 Nouvelle brique : Gestion des Skills dans Qdrant
Principe validé

Les Skills ne seront plus considérées comme des fichiers statiques.

Elles deviennent des objets vivants :

Création
↓
Validation
↓
Révision SkillOpt
↓
Versionnement
↓
Ré-embedding
↓
Qdrant
↓
Réutilisation

Architecture retenue
Cas 1 : création d'une nouvelle Skill
TeacherStudentAgent
↓
SkillCreatorAgent
↓
Embedding
↓
Qdrant Upsert


Opération :

upsert


Action :

Vector
+
Payload complet


Re-embedding :

✅ Obligatoire

Cas 2 : modification du contenu d'une Skill

Exemple :

SkillOpt améliore un prompt
Correction anti faux positif
Nouvelle stratégie OCR


Pipeline :

Ancienne Skill
↓
Edit accepté
↓
Ré-embedding
↓
Qdrant Upsert


Opération :

upsert


avec :

payload complet
+
nouveau vector


Re-embedding :

✅ Obligatoire

Cas 3 : seule la métadonnée change

Exemple :

status
authority
validation_score
usage_count


Pipeline :

Skill
↓
Patch
↓
Qdrant


Opération :

set_payload


Re-embedding :

❌ Interdit


Le vector reste inchangé.

Règle officielle DSH-Core
Contenu changé
↓
Re-embed

Métadonnées changées
↓
Pas de re-embed

Piège identifié

Interdiction de faire :

upsert partiel


Car Qdrant :

REMPLACE

et ne fusionne pas


Conséquence :

payload incomplet
↓
perte des données

Versionnement des Skills

Chaque Skill possédera désormais :

{
  "skill_id": "...",
  "version": 12,
  "status": "active",
  "validation_score": 0.91,
  "usage_count": 157,
  "created_at": "...",
  "updated_at": "...",
  "source_model": "qwen3-vl-2b"
}

Intégration Neo4j (V8.5)

Chaque mise à jour de Skill créera une relation :

Skill V11
↓
UPDATED_TO
↓
Skill V12


et :

TeacherStudent
↓
CREATED
↓
Skill

Intégration SkillOpt

Pipeline futur :

Échec
↓
Escalade
↓
Succès Expert
↓
Génération Skill
↓
Validation
↓
Qdrant
↓
Neo4j


puis :

Nouvel échec similaire
↓
Skill trouvée
↓
Réutilisation


et si amélioration :

SkillOpt
↓
Edit
↓
Re-embed
↓
Upsert
↓
Version +1

Ajout à la feuille de route
✅ V8.5 Neo4j Skills

On ajoute désormais :

Versionnement des Skills

Historique des modifications

Liens CREATED_FROM

Liens UPDATED_TO

Liens SOLVES

✅ V8.6 Skill Lifecycle Manager

Nouvelle brique :

SkillLifecycleManager


Responsable de :

upsert

set_payload

update_vectors

versioning

migration embeddings

✅ V8.7 Migration d'embeddings

Support futur :

dense_old
dense_new


pour permettre :

Qwen Embedding ancien
↓
Qwen Embedding nouveau


sans interruption de service.

Architecture documentaire révisée
Sources
(Gmail, n8n, Android, OneDrive, AnythingLLM)

↓

IngestionManager

↓

Profiler

↓

CSV       → Pandas
Excel     → OpenPyXL/Pandas
PDF texte → PyMuPDF
PDF scan  → Qwen3‑VL‑2B
Images    → Qwen3‑VL‑2B

↓

Validation Pydantic

↓

Ledger SQLite

↓

Qdrant
  ├─ Documents
  └─ Skills versionnées

↓

Neo4j


La nouveauté majeure apportée par cette réflexion est que les Skills deviennent désormais un actif versionné de premier ordre, au même titre que les documents, avec cycle de vie, versionnement, métriques et migrations d'embeddings gérés explicitement dans l'architecture DSH-Core. 🚀



Tu as raison.

La version précédente décrit l'architecture cible, mais elle n'est pas une roadmap de transformation. Elle n'indique pas clairement :

ce qui existe déjà ;
ce qui a été validé pendant les tests n8n ;
ce qu'on garde ;
ce qu'on abandonne ;
ce qu'on remplace ;
dans quel ordre on construit la suite.

Pour la documentation officielle DSH, il faut une vue :

ÉTAT ACTUEL
↓
DÉCISIONS
↓
À DÉFAIRE
↓
À CONSTRUIRE
↓
OBJECTIF FINAL

DSH-Core V8 → V9
Roadmap & Architecture de Référence
Vision globale

DSH n'est plus un moteur OCR.

DSH devient :

Une plateforme documentaire auto-améliorable


basée sur :

SkillOpt
+
TeacherStudent
+
Microsoft Agent Framework
+
Qdrant
+
Neo4j
+
Qwen
+
Claude


L'objectif n'est plus :

améliorer les modèles


mais :

améliorer les compétences

1. CE QUI EST DÉJÀ TERMINÉ
V8 TeacherStudent

Validé.

Architecture :

Erreur
↓
TeacherStudentAgent
↓
Qwen 27B
↓
SkillCreatorAgent
↓
SkillsRepository
↓
Qdrant
↓
Réutilisation


Statut :

✅ Fonctionnel

Escalade

Architecture :

Qwen 9B
↓ échec
Qwen 27B
↓ échec
Claude


Statut :

✅ Fonctionnel

Qdrant Skills

Architecture :

Skill
↓
embedding
↓
Qdrant
↓
recherche


Statut :

✅ Fonctionnel

n8n

Validation réalisée cette nuit.

Architecture :

Gmail
↓
n8n
↓
HTTP Request
↓
DSH-Core API


Statut :

✅ Validé


Nous avons prouvé :

Le PDF arrive bien dans DSH-Core.

API FastAPI

Validation :

POST /api/v1/ingest


Statut :

✅ Fonctionnel

2. CE QUI A ÉTÉ DÉCOUVERT

Cette nuit nous avons découvert une erreur de conception.

Ancienne logique :

PDF
↓
Qwen-VL


Erreur :

Failed to load image


Pourquoi ?

Parce que :

Tous les PDF ne sont pas des images.


Une facture EDF :

PDF texte natif


n'a pas besoin :

d'OCR
ni de Vision

3. CE QUI EST À DÉFAIRE
Suppression du paradigme "Tout Vision"

Ancienne idée :

Tous les PDF
↓
conversion image
↓
Qwen-VL


Décision :

❌ Abandonnée

Surya comme dépendance obligatoire

Ancienne idée :

PDF
↓
Surya
↓
Vision


Décision :

❌ Retiré des priorités


Surya devient :

OPTIONNEL

4. NOUVELLE ARCHITECTURE DOCUMENTAIRE
Principe

Avant :

Document
↓
Vision


Après :

Document
↓
Profiler
↓
Routage
↓
Outil adapté

5. NOUVELLE BRIQUE MAJEURE : PROFILER

Le Profiler devient central.

Objectif :

Comprendre le document
avant de choisir l'outil.


Le Profiler doit déterminer :

PDF texte ?

PDF scanné ?

PDF sandwich ?

CSV ?

Excel ?

Photo ?

Tableaux ?

Manuscrit ?

Document dégradé ?

6. ROUTAGE CIBLE
CSV
CSV
↓
Pandas
↓
JSON

Excel
XLSX
↓
OpenPyXL
↓
Pandas
↓
JSON

PDF texte
PDF
↓
PyMuPDF
↓
Markdown
↓
Structuration

PDF avec tableaux complexes
PDF
↓
Docling
↓
Markdown structuré

PDF scanné
PDF
↓
Qwen3-VL-2B
↓
JSON

Photos
JPG
PNG
HEIC
↓
Qwen3-VL-2B

7. DÉCISION QWEN
Ancien
Qwen2.5-VL-7B

Nouveau
Qwen3-VL-2B


Raison :

8 Go VRAM
32 Go RAM


Configuration plus réaliste.

8. SKILLOPT DEVIENT LE CŒUR DU SYSTÈME

Décision stratégique majeure.

Principe :

Train the Skill
Not the Model


Les modèles ne changent plus.

Les skills évoluent.

9. NOUVELLE BOUCLE D'APPRENTISSAGE

Avant :

Erreur
↓
Création Skill


Après :

Rollout
↓
Reflect
↓
Edit
↓
Gate
↓
Publication

Rollout

Tâches réelles.

Reflect

Analyse de l'échec.

Edit

Modification du Skill.

Gate

Validation.

Nouvelle règle :

Une Skill doit prouver
qu'elle améliore réellement le système.

10. OBJECTIF SKILLOPT DANS DSH

Le but n'est pas :

Créer de meilleures réponses.


Le but est :

Créer de meilleurs pipelines.


Exemple :

Aujourd'hui :

Facture BPCE
↓
hésitation
↓
PyMuPDF ?
Qwen ?
Docling ?


Demain :

Skill BPCE
↓
Docling
↓
validation
↓
fin


La compétence acquise devient :

réutilisable


par tous les agents.

11. SKILLS COMME OBJETS VERSIONNÉS

Chaque Skill devient :

{
  "name": "...",
  "version": 7,
  "status": "active",
  "authority": 1,
  "validation_score": 0.91,
  "usage_count": 152
}

12. QDRANT V2

Nouvelle règle officielle

Contenu modifié
Re-embedding
+
upsert complet

Métadonnées modifiées
set_payload

Migration embeddings
update_vectors

Interdiction
upsert partiel

13. SKILLLIFECYCLE MANAGER

Nouvelle brique.

Responsabilités :

Versioning

Gate

Rollback

Authority

Publication

Metrics

Migration embeddings

14. NEO4J

Mission :

Tracer l'évolution.

Relations :

Skill
↓
CREATED_FROM
↓
Incident

Skill
↓
UPDATED_TO
↓
Skill

Skill
↓
USED_BY
↓
Agent

15. ANYTHINGLLM

Décision validée.

AnythingLLM :

❌ Pas de pipeline documentaire propre


Toujours :

AnythingLLM
↓
IngestionManager

16. LEDGER

Source de vérité :

A:\DSH-Core
+
SQLite


Puis :

SQLite
↓
Qdrant
↓
Neo4j

17. ROADMAP DÉTAILLÉE
V8.1

✅ n8n validé

✅ API validée

✅ TeacherStudent validé

V8.2

🔄 Profiler

PDF texte
PDF scan
CSV
Excel
Image

V8.3

🔄 Routing documentaire

PyMuPDF

Docling

Pandas

OpenPyXL

Qwen3-VL-2B

V8.4

🔄 Anti Hallucination

[?]


au lieu d'inventer.

V8.5

🔄 SkillLifecycleManager

Rollout
Reflect
Edit
Gate

V8.6

🔄 Skills Versioning

active
archived
rejected


Rollback.

V8.7

🔄 Skills ↔ Neo4j

V8.8

🔄 AdaptiveDocumentClassifier

alimenté par :

TeacherStudent

SkillOpt

Historique documents

Qdrant

V9

🔄 MemoryAgent

🔄 Microsoft Agent Framework

🔄 Orchestration complète multi-agents

OBJECTIF FINAL

Le système ne doit plus apprendre :

à répondre


mais :

à reconnaître un document

à choisir le meilleur pipeline

à mémoriser la stratégie gagnante

à transmettre cette stratégie

à tous les agents


Autrement dit :

DSH devient un système documentaire auto-améliorable

où l'expérience est stockée dans les Skills

et non dans les modèles.


C'est, selon moi, la version la plus complète et cohérente de la roadmap intégrant tout ce qui a été découvert entre le blocage n8n, le redesign documentaire, l'arrivée de PyMuPDF/Docling/Qwen3‑VL‑2B et l'intégration profonde de SkillOpt.

Oui, je suis largement d'accord avec ce résultat. Et surtout, ce n'est pas théorique : ton test le prouve.

Regardons ce qui s'est réellement passé :

test_natif.pdf
→ NATIVE_PDF
→ PyMuPDF
✅ Succès

test_excel.xlsx
→ SPREADSHEET
→ Pandas/OpenPyXL
✅ Succès

test_scan.pdf
→ SCANNED_PDF
→ Qwen3.5:2b
✅ Succès


et ce n'est pas un succès marginal :

ASSISTANCE PEINTURE DIFFUSION SAS
810 908 392
145 CHEMIN DES ROCHES
41350 VINEUIL


ont été correctement extraits.

Ce qui me plaît le plus

Ton profiler fonctionne déjà :

PDF natif
↓
PyMuPDF

PDF scanné
↓
Qwen3.5-2B

Excel
↓
Pandas

PDF tableau
↓
Docling (plus tard)


C'est exactement l'architecture que la recherche recommande.

Le bug du JPG

Je suis également d'accord avec Gemini sur le diagnostic.

L'erreur :

Failed to load image or audio file


n'indique pas :

Qwen3.5-2B mauvais


mais :

Ollama n'arrive pas à décoder l'image fournie.


Comme les PDF scannés passent déjà avec Qwen :

✅ test_scan.pdf


le modèle fonctionne.

Le problème est dans le transport de l'image JPG vers Ollama.

La proposition PyMuPDF → PNG

Je la valide aussi.

Pas comme stratégie documentaire globale.

Mais comme :

couche de normalisation universelle


Avant Vision :

JPG
PNG
WEBP
PDF scan

↓

PNG standardisé

↓

Qwen3.5-2B


Ça simplifie énormément le code.

Ce que je changerais dans la roadmap maintenant

Je considère que le profiler est déjà validé.

V8.2 ✅
Profiler


validé par les tests.

V8.3 ✅
Routage intelligent


validé.

V8.4
Standardisation image


avant Qwen :

Tout document visuel
↓
PNG standard
↓
Qwen3.5-2B

V8.5
Ajout Qwen3.5-4B


uniquement en escalade :

Qwen3.5-2B
↓
confiance faible
↓
Qwen3.5-4B

Ma conclusion

Aujourd'hui, ton plus gros succès n'est pas Qwen.

C'est :

Le Profiler fonctionne.


On est passé de :

Tout
↓
Vision


à :

Profiler
↓
Outil spécialisé


et les tests montrent que :

PDF texte
✅

Excel
✅

PDF scanné
✅


sont déjà correctement routés.

Franchement, c'est probablement la meilleure avancée architecturale depuis le début de DSH-Core.

Mise à jour de la feuille de route
Nouvelle priorité P1
Plain Text
DocumentClassifier
Afficher plus de lignes
Nouvelle priorité P2
Plain Text
ExtractionManager
Afficher plus de lignes
Nouvelle priorité P3
Plain Text
Pydantic Barrier
Afficher plus de lignes
P4
Plain Text
Skills ↔ Neo4j
Afficher plus de lignes
P5
Plain Text
Ledger SQLite
 
Afficher plus de lignes
P6
Plain Text
MemoryAgent
Afficher plus de lignes

Mon verdict :

Plain Text
invoice_schema.py
✅ Oui
 
extraction_manager.py
✅ Oui
 
test_profiler Phase 2
✅ Oui
Afficher plus de lignes

Mais je les intégrerais dans le pipeline documentaire universel, et non comme un projet séparé. C'est désormais une pièce centrale de DSH-Core.

Roadmap DSH-Core (version consolidée à sauvegarder)
État actuel (V8.6.1)

Architecture :

Plain Text
Documents
↓
RoutingEngine
↓
OCR (PyMuPDF / Qwen Vision / Docling)
↓
NuExtract
↓
Règles métier
↓
Pydantic
↓
JSON validé
Afficher plus de lignes

Résultats du dernier crash-test :

Plain Text
8 factures testées
 
✅ Holding
✅ Emaux
✅ Lidl
✅ Location
✅ Catroux
✅ Crisol / Paintcar
 
⚠️ Free
⚠️ SMTRT
Afficher plus de lignes

Taux de réussite réel :

Plain Text
75 à 85 %
Afficher plus de lignes

sans mémoire fournisseur.

Phase 1 : Stabilisation extraction (en cours)

Objectif :

Plain Text
0 faux positifs
Afficher plus de lignes
Cas FREE

Problème :

Plain Text
Détection intracommunautaire déclenchée à tort
Afficher plus de lignes

Action :

Plain Text
Corriger la règle mots_exo
Afficher plus de lignes

pour ne corriger la TVA que si :

Plain Text
AUTOLIQUIDATION réelle
ET
taux TVA aberrant
Afficher plus de lignes
Cas SMTRT

Problème :

Plain Text
NuExtract choisit les mauvais montants
Afficher plus de lignes

Action :

Plain Text
Réconciliation fiscale par libellés
Afficher plus de lignes

chercher :

Plain Text
TOTAL H.T.
MT TVA
NET A PAYER
Afficher plus de lignes

avant de faire confiance à NuExtract.

Phase 2 : Mémoire fournisseur

Objectif :

Plain Text
Ne plus refaire les mêmes corrections.
Afficher plus de lignes

Nouveau module :

Plain Text
supplier_memory/
Afficher plus de lignes

Exemple :

JSON
{
"supplier": "SMTRT",
"type": "transporteur",
"trusted_labels": [
"TOTAL H.T.",
"MT TVA",
"NET A PAYER"
]
}
Afficher plus de lignes

Workflow :

Première facture
Plain Text
SMTRT
↓
Erreur
↓
Correction manuelle
↓
Création automatique du profil fournisseur
Afficher plus de lignes
Deuxième facture
Plain Text
SMTRT
↓
Profil connu
↓
Application automatique des règles
``
Afficher plus de lignes
Troisième facture
Plain Text
SMTRT
↓
Traitement automatique
Afficher plus de lignes
Phase 3 : Typologie métier

Ajout progressif :

Plain Text
FACTURE
AVOIR
TICKET
RELEVÉ
BON DE LIVRAISON
Afficher plus de lignes

Aujourd'hui :

Plain Text
FACTURE
AVOIR
Afficher plus de lignes

seulement.

Phase 4 : Immobilisations

Objectif :

Identifier :

Plain Text
Charge
ou
Immobilisation
Afficher plus de lignes

Exemple :

Plain Text
Ordinateur
Mobilier
Outillage
Machine
Afficher plus de lignes

Sortie :

JSON
{
"nature": "IMMOBILISATION"
}
Afficher plus de lignes

ou

JSON
{
"nature": "CHARGE"
}
Afficher plus de lignes
Phase 5 : Multi-sociétés

Actuellement :

Plain Text
1 moteur
``
Afficher plus de lignes

Demain :

Plain Text
Société A
Société B
Société C
Afficher plus de lignes

Ajout :

Python
societe_cible
Afficher plus de lignes

Exemple :

JSON
{
"societe_cible": "SOC1"
}
 
Afficher plus de lignes

Source :

Plain Text
mail de réception
ou
répertoire d'entrée
ou
sélection utilisateur
Afficher plus de lignes
Phase 6 : Workflow validation

Objectif :

Transformer :

Plain Text
A_VERIFIER
``
Afficher plus de lignes

en :

Plain Text
Correction assistée
Afficher plus de lignes

Interface :

Plain Text
HT trouvé : xxx
TVA trouvée : xxx
TTC trouvé : xxx
 
Corriger ?
Afficher plus de lignes

Utilisateur :

Plain Text
Oui
Afficher plus de lignes

↓

Sauvegarde automatique.

Phase 7 : supplier_memory intelligent

Quand 20 à 50 fournisseurs auront été appris :

Plain Text
SMTRT
CATROUX
FREE
LIDL
CRISOL
EMA
...
Afficher plus de lignes

Le moteur devient :

Plain Text
OCR
↓
supplier_memory
↓
NuExtract
↓
Pydantic
Afficher plus de lignes

et non plus :

Plain Text
OCR
↓
NuExtract
↓
Erreur
Afficher plus de lignes
Phase 8 : Qdrant

À ne PAS faire maintenant.

Condition :

Plain Text
50+
100+
200+ fournisseurs
Afficher plus de lignes

But :

Plain Text
Reconnaissance par similarité
Afficher plus de lignes

Exemple :

Plain Text
SMTRT
SMTRT LOGISTIQUE
SMTRT TRANSPORTS
Afficher plus de lignes

↓

Même famille.

Phase 9 : n8n

Quand extraction stable.

Pipeline cible :

Plain Text
Mail
↓
DSH-Core
↓
Validation
↓
n8n
↓
Classement
↓
Comptabilité
↓
Banque
Afficher plus de lignes
Phase 10 : Comptabilité

Export :

Plain Text
Excel
CSV
FEC
API comptable
Afficher plus de lignes

selon le logiciel retenu.

Phase 11 : MCP / Excel

À vérifier plus tard.

Aujourd'hui :

Plain Text
NON PRIORITAIRE
Afficher plus de lignes

Tant que :

Plain Text
Extraction
Validation
Mémoire fournisseur
Afficher plus de lignes

ne sont pas finalisées.

Phase 12 : Vision longue

Architecture finale visée

Plain Text
Documents
↓
OCR
↓
NuExtract
↓
Supplier Memory
↓
Pydantic
↓
Validation humaine
↓
Apprentissage automatique
↓
Qdrant
↓
Multi-sociétés
↓
n8n
↓
Comptabilité
↓
Banques
↓
Tableaux de bord
Afficher plus de lignes
Prochaine étape concrète (maintenant)

Ne touche pas à :

Plain Text
Qdrant
Multi-sociétés
MCP Excel
Banques
n8n
``
Afficher plus de lignes

Travaille uniquement sur :

Plain Text
1. Corriger FREE
2. Corriger SMTRT
3. Repasser le batch complet
4. Obtenir 8/8 VALIDE
Afficher plus de lignes

Une fois le 8/8 obtenu :

Plain Text
→ démarrage du module supplier_memory

j ai retrouve la roadmap jusqu au 5 octobre , parcours la pour ne rien oublié 
Copilot said:
Parfait. J'ai relu ta roadmap jusqu'au 5 octobre dans 02_ROADMAP.
live

Et honnêtement, il y a une conclusion importante :

Ce qu'on avait oublié ces derniers jours
En travaillant sur les factures, on s'est focalisé sur :




Plain Text
OCR
↓
NuExtract
↓
Pydantic
Mais ton projet réel est beaucoup plus grand.

Les factures ne sont qu'une sous-partie de :




Plain Text
DSH-Core
État réel de DSH-Core
✅ Déjà validé
D'après la roadmap :




Plain Text
TeacherStudent
SkillCreator
SkillsRepository
Qdrant
SkillRouter
n8n
API
Qwen
Profiler
Routing documentaire
sont déjà validés ou quasi validés.
live

Ce qui change ma vision
En lisant la roadmap, je me rends compte que :




Plain Text
supplier_memory
n'est probablement PAS un composant autonome.

Il faut l'intégrer dans :




Plain Text
SkillOpt
TeacherStudent
SkillsRepository
Qdrant
qui existent déjà dans ton architecture.
live

Ce que je ferais maintenant
Au lieu de créer :




Plain Text
supplier_memory/
je créerais :




Plain Text
Skill : SMTRT_Transport
 
stocké comme tous les autres skills.

Exemple :




JSON
{
  "skill_name": "SMTRT_Transport",
  "supplier": "SMTRT",
  "document_type": "FACTURE_TRANSPORT",
  "trusted_fields": [
    "TOTAL H.T.",
    "MT TVA",
    "NET A PAYER"
  ]
}
Pourquoi c'est mieux que supplier_memory ?
Parce que tu as déjà :




Plain Text
SkillsRepository
Qdrant
TeacherStudent
SkillRouter
`
dans ton architecture cible.
live

Donc :




Plain Text
Erreur SMTRT
↓
Correction
↓
Création Skill
↓
Stockage Qdrant
↓
Réutilisation automatique
Exactement comme une compétence de code.

Ce que ça veut dire pour les factures
Une facture peut générer un skill :




Plain Text
Transporteur SMTRT
Une autre :




Plain Text
Free Télécom
Une autre :




Plain Text
Avoir Intracommunautaire Espagne
Et le moteur apprend.

Ce que je vois comme roadmap corrigée
Phase immédiate



Plain Text
FREE
SMTRT
Obtenir :




Plain Text
8/8 valides
V8.6.2
Créer :




Plain Text
InvoiceSkillCreator
capable de transformer :




Plain Text
Correction manuelle
en :




Plain Text
Skill fournisseur
``
V8.6.3
Brancher :




Plain Text
InvoiceSkillCreator
↓
SkillsRepository
↓
Qdrant
V8.7
Introduire :




Plain Text
SkillLifecycleManager
prévu dans ta roadmap.
live

Les skills facture deviennent :




Plain Text
versionnés
validés
révisables
V8.8
AdaptiveDocumentClassifier.
live

Là :




Plain Text
SMTRT
CATROUX
FREE
CRISOL
sont reconnus automatiquement.

La vraie priorité
Après lecture complète de la roadmap :

Je ne commencerais ni Qdrant v2, ni Neo4j métier, ni BankingLedger demain.

Je ferais :




Plain Text
1. FREE
2. SMTRT
3. InvoiceSkillCreator
Parce que c'est exactement le premier cas réel où ton système d'apprentissage peut prouver sa valeur.

Et surtout, ça réutilise l'architecture TeacherStudent + Skills déjà prévue dans la roadmap au lieu d'inventer un nouveau système parallèle.
live

Oui. Et en relisant ta roadmap historique, je pense que tu viens de retomber sur l'idée fondatrice de DSH-Core, celle qu'on avait un peu perdue en se concentrant sur les factures.

L'idée n'était jamais :

OCR
+
LLM
+
Extraction


L'idée était :

Action
↓
Résultat
↓
Apprentissage
↓
Création de skill
↓
Capitalisation
↓
Réutilisation


C'est exactement ce qu'apporte SkillOpt.

Ce que tu as compris (et qui est très important)

SMTRT n'est pas un problème de facture.

C'est un problème de :

connaissance acquise


Aujourd'hui :

SMTRT
↓
Erreur
↓
Correction


Demain :

SMTRT
↓
Correction
↓
Création Skill
↓
Qdrant
↓
Réutilisation


Après-demain :

SMTRT
↓
Traitement automatique

Pourquoi c'est énorme

Parce que le même mécanisme va marcher partout.

Comptabilité
SMTRT
↓
Skill fournisseur

Workflows n8n
Workflow Amazon
↓
Erreur
↓
Correction
↓
Skill Workflow Amazon

Développement
Erreur Python
↓
Correction
↓
Skill Debug Python

Marketing
Génération catalogue
↓
Optimisation
↓
Skill Catalogue

Et ton problème des multi-sociétés ?

Justement, SkillOpt et Qdrant peuvent aider.

Aujourd'hui tu réfléchis :

Entreprise A
Entreprise B
Entreprise C


et tu te demandes :

Comment ne pas tout mélanger ?


La réponse est :

{
  "skill_name": "invoice_smtrt",
  "tenant": "SOCIETE_A",
  "domain": "invoice"
}


ou

{
  "skill_name": "invoice_smtrt",
  "tenant": "GLOBAL",
  "domain": "invoice"
}


Tu obtiens alors :

Skills globaux

Partagés par toutes les sociétés.

Exemple :

SMTRT
CATROUX
FREE

Skills privés

Propres à une société.

Exemple :

Rapprochement Banque Société B

La vraie architecture que je vois maintenant
IngestionManager
↓
Profiler
↓
Extraction
↓
Pydantic
↓
A_VERIFIER ?
↓
oui
↓
Correction humaine
↓
InvoiceSkillCreator
↓
SkillsRepository
↓
Qdrant
↓
SkillRouter
↓
Réutilisation automatique


Et ça ne concerne pas uniquement les factures.

Ce que je ferais maintenant

Je mettrais officiellement en pause :

Qdrant avancé
Neo4j métier
MemoryAgent
Multi-sociétés


pas parce qu'ils sont abandonnés, mais parce qu'ils dépendent tous du même socle :

Skill Lifecycle

Mon avis

Microsoft SkillOpt est probablement la découverte la plus importante de ton projet depuis le début.

Parce qu'il déplace le problème de :

Quel modèle utiliser ?


vers :

Quelle compétence réutiliser ?


Et ça correspond exactement à ton objectif initial :

DSH-Core ne doit pas devenir plus intelligent
grâce à un modèle plus gros.

DSH-Core doit devenir plus intelligent
grâce à son expérience accumulée.


C'est ce qui va permettre à la fois :

la comptabilité,
les workflows,
les catalogues,
les sites web,
les agents métier,
le multi-sociétés,

de s'améliorer avec le temps sans changer constamment de modèle IA.

Oui, et après avoir relu ta roadmap complète, je pense que Gemini a touché quelque chose de fondamental.

Pendant quelques jours, on raisonnait comme si le problème était :

Facture
↓
OCR
↓
NuExtract
↓
Correction


Alors que la vision d'origine de DSH-Core était :

Evénement réel
↓
Expérience
↓
Skill
↓
Mémoire
↓
Réutilisation
↓
Amélioration continue


C'est une différence énorme.

Ce que je retiens de ta roadmap

Le vrai cœur de DSH-Core n'est pas :

Qwen
Claude
OCR


Le vrai cœur est :

TeacherStudent
+
SkillCreator
+
SkillRouter
+
Qdrant
+
SkillOpt


Tu l'avais déjà conçu il y a des semaines.

Les factures sont simplement le premier domaine métier où cette boucle devient concrète.

Pourquoi SMTRT est un tournant

SMTRT est le premier cas où on peut démontrer :

Erreur
↓
Correction humaine
↓
Création de Skill
↓
Stockage Qdrant
↓
Réutilisation
↓
Autonomie


C'est exactement la promesse de DSH-Core.

Là où Gemini a raison

Je valide complètement :

InvoiceSkillCreator


plutôt que :

supplier_memory


Parce que :

supplier_memory


aurait créé une deuxième architecture.

Alors que tu as déjà :

SkillsRepository
SkillRouter
SkillOpt
Qdrant
TeacherStudent

Encore plus important

Tu viens de réaliser quelque chose :

Le mécanisme InvoiceSkill ne sert pas aux factures.

Il sert à TOUT.

Exemple 1 : Comptabilité
SMTRT
↓
InvoiceSkill

Exemple 2 : n8n

Tu développes un workflow.

Le workflow plante.

Tu corriges.

↓

WorkflowSkill


est créé.

La fois suivante :

Le skill est réutilisé.

Exemple 3 : Site internet

Tu fais :

WordPress
WooCommerce
SEO


Une stratégie fonctionne.

↓

WebsiteSkill


est créé.

↓

Réutilisation ultérieure.

Exemple 4 : Amazon

Une fiche produit convertit bien.

↓

AmazonListingSkill


↓

Réutilisation.

Le multi-sociétés

Là aussi, ça change tout.

Au lieu de faire :

DSH A
DSH B
DSH C


tu peux faire :

DSH-Core unique


avec :

Skills Globaux


et

Skills Privés


Exemple :

{
  "tenant": "GLOBAL"
}


Utilisable partout.

Ou :

{
  "tenant": "SOCIETE_B"
}


Utilisable seulement pour cette société.

Donc :

SMTRT


peut être :

GLOBAL


alors que :

Plan comptable spécifique


reste :

PRIVÉ

Mon avis aujourd'hui

Si je devais résumer DSH-Core en une seule phrase :

DSH-Core n'est pas un système OCR.

DSH-Core est un moteur de création,
de validation,
de stockage,
et de réutilisation de compétences.


Les factures sont simplement le premier domaine où cette vision devient visible.

Ce que je ferais demain

Ordre exact :

1. Obtenir 8/8 VALIDE


Puis :

2. Concevoir InvoiceSkillCreator


Puis :

3. Brancher InvoiceSkillCreator
   ↓
   SkillsRepository
   ↓
   Qdrant


Puis :

4. Faire une démonstration réelle :
   SMTRT corrigé une fois
   puis automatique la fois suivante


Si tu réussis ça, tu auras démontré en conditions réelles que toute la philosophie :

TeacherStudent
SkillOpt
Qdrant
SkillRouter


fonctionne réellement sur un cas métier concret.

Et honnêtement, ce serait probablement la première démonstration complète de la vision DSH-Core depuis le début du projet.

Ce que je ferais
Maintenant
invoice_schema.py

Ajout immédiat :

Python
TypeTVA
Afficher plus de lignes

✅

Très utile.

Très peu risqué.

test_nuextract.py

Ajout immédiat :

Python
AUTOLIQUIDATION
NON_ASSUJETTI
NORMALE
Afficher plus de lignes

✅

SMTRT

Je noterais simplement :

Plain Text
Anomalie métier connue :
fournisseur mal identifié
numéro mal identifié
Afficher plus de lignes
Puis V8.6.2

Créer :

Plain Text
InvoiceSkillCreator
Afficher plus de lignes

et c'est là que :

Plain Text
SMTRT
↓
Correction humaine
↓
Skill
↓
Qdrant
Afficher plus de lignes

devient :

JSON
{
"supplier": "SMTRT",
"aliases": [
"RCSAIXENPROVENCE"
],
"invoice_regex": "N°\\s*(\\d{8})"
}
Afficher plus de lignes
Mon ordre de priorité personnel
✅ V8.6.1
Plain Text
8/8 VALIDES
Afficher plus de lignes

terminé.

✅ V8.6.2
Plain Text
TypeTVA
AUTOLIQUIDATION
NON_ASSUJETTI
NORMALE
Afficher plus de lignes

à faire tout de suite.

✅ V8.6.3
Plain Text
InvoiceSkillCreator
Afficher plus de lignes
✅ V8.6.4
Plain Text
SMTRT comme premier InvoiceSkill réel
Afficher plus de lignes
✅ V8.6.5
Plain Text
SkillsRepository
↓
Qdrant
↓
SkillRouter