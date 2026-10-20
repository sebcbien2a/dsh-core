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

DSH-Core 2027
Roadmap Architecturale Référence (Version Consolidée)

Objectif principal

DSH-Core n'est plus considéré comme un moteur de comptabilité.

DSH-Core devient :

Une plateforme autonome capable :

- d'analyser
- de mémoriser
- de relier
- d'apprendre
- de créer des workflows
- de créer des agents
- de s'auto-améliorer


La comptabilité est simplement le premier domaine métier utilisé pour construire cette plateforme.

Vision globale
                     DSH-Core

┌─────────────────────────────────────────┐
│                Interface                │
└─────────────────┬───────────────────────┘
                  │
                  ▼

┌─────────────────────────────────────────┐
│                Agents                   │
└─────────────────┬───────────────────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼

 Neo4j         Qdrant       Langfuse

 Vérité        Mémoire      Observabilité
 métier        sémantique   et audit

Rôle de chaque composant
Neo4j

Neo4j est :

La vérité métier.


Neo4j sait :

Qui ?
Quoi ?
Quand ?
Comment ?
Pourquoi ?


Neo4j stocke :

Sociétés
Fournisseurs
Documents
Factures
Paiements
Agents
Workflows
Skills
Outils
Relations

Qdrant

Qdrant est :

La mémoire sémantique.


Qdrant sait :

A quoi cela ressemble ?


Exemples :

Facture similaire

Fournisseur similaire

Workflow similaire

Agent similaire

Skill similaire

Langfuse

Langfuse est :

La mémoire d'exécution.


Langfuse sait :

Ce qui s'est passé.


Exemples :

Quel agent a pris la décision ?

Quel workflow a été exécuté ?

Pourquoi a-t-il échoué ?

Combien de tokens ?

Combien de temps ?

Quel coût ?

Architecture Multi-Sociétés
Principe

Aucune donnée ne doit être globale.

Chaque objet doit appartenir à une société.

Company
class Company


Champs :

company_id
legal_name
siret
vat_number

currency

accounting_folder

status

created_at
updated_at


Exemple :

COMP_001
COMP_002
COMP_003
COMP_004


correspondant aux 4 sociétés.

Supplier
class Supplier


Champs :

supplier_id

raison_sociale

siret

tva_intra

status

trust_score

verification_level

documents_seen

aliases

created_at
updated_at

Statuts fournisseurs
DISCOVERED
PENDING
VERIFIED
TRUSTED
REJECTED
ARCHIVED

Skill
class Skill


Champs :

skill_id

skill_name

version

status

confidence

usage_count

supplier_id

created_at
updated_at

Document
class Document


Champs :

document_id

company_id

supplier_id

skill_id

file_name

file_hash

document_type

status

created_at
updated_at

Types de documents
FACTURE

AVOIR

DEVIS

BON_COMMANDE

BON_LIVRAISON

CONTRAT

RIB

MAIL

DOCUMENT_DIVERS

Invoice
class Invoice


Champs :

invoice_id

document_id

supplier_id

company_id

invoice_number

invoice_date

due_date

currency

amount_ht

amount_tva

amount_ttc

vat_type

payment_status

created_at
updated_at

Paiement
class Payment


Champs :

payment_id

company_id

bank_account_id

payment_date

amount

currency

bank_reference

status

created_at
updated_at

Compte bancaire
class BankAccount


Champs :

account_id

company_id

iban

bic

bank_name

currency

status

Rapprochement bancaire
class Reconciliation


Champs :

reconciliation_id

invoice_id

payment_id

company_id

status

confidence

rule_used

validated_by

validated_at

Statuts
NOT_MATCHED

CANDIDATE

MATCHED

VALIDATED

REJECTED

Workflow
class Workflow


Champs :

workflow_id

name

description

trigger

status

version

created_at
updated_at

Agent
class Agent


Champs :

agent_id

name

role

description

status

memory_scope

created_at
updated_at

Tool
class Tool


Champs :

tool_id

name

category

description

version

status

Graphe Neo4j complet
Sociétés
Company
  └── OWNS
          BankAccount

Company
  └── RECEIVES
          Invoice

Company
  └── USES
          Workflow

Fournisseurs
Supplier
   └── USES_SKILL
               Skill

Document
    └── IDENTIFIED_AS
                     Supplier

Invoice
   └── ISSUED_BY
                 Supplier

Comptabilité
Invoice
    └── MATCHED_TO
                    Payment

Payment
    └── COMES_FROM
                    BankAccount

Apprentissage
Skill
   └── LEARNED_FROM
                    Document

Supplier
    └── VALIDATED_BY
                      Document

Workflows
Workflow
    └── CALLS
               Tool

Agent
   └── EXECUTES
                 Workflow

Flux Neo4j / Qdrant / Langfuse
Cas standard
Facture PDF
↓
OCR
↓
Extraction
↓
Neo4j
    fournisseur connu ?
↓
oui
↓
Skill
↓
Réconciliation
↓
Archivage

Cas ambigu
Facture PDF
↓
OCR
↓
Extraction
↓
Neo4j
↓
fournisseur inconnu
↓
Qdrant
↓
suggestion
↓
validation
↓
Neo4j
↓
création fournisseur

Cas audit
Agent
↓
Workflow
↓
Outil
↓
Résultat


Tout est historisé dans :

Langfuse

Workflow Fournisseur final

Objectif :

Zéro script manuel.


Aujourd'hui

Erreur
↓
test_skill_creator.py


Demain

Erreur
↓
Interface utilisateur
↓
Validation
↓
SupplierRepository
↓
Skill
↓
Fin

Interface Validation Fournisseur
━━━━━━━━━━━━━━━━━━━━━━━
NOUVEAU FOURNISSEUR
━━━━━━━━━━━━━━━━━━━━━━━

Nom détecté

CATROUX TRANSPORTS

SIRET

12345678901234

TVA

FRXXXXXXXXXXXX

Documents vus

3

Statut

DISCOVERED

[Valider]

[Modifier]

[Ignorer]

[Voir PDF]

Messages guidés
Conflit SIRET
⚠ Conflit détecté

Le SIRET :

67162081300103

est déjà enregistré pour :

SMTRT SAS

[Voir fiche]

[Corriger]

[Annuler]

Conflit TVA
⚠ TVA déjà utilisée

FR82671620813

est liée à

SMTRT SAS

Nouveau fournisseur
ℹ Fournisseur inconnu détecté

Créer une fiche ?

[Oui]

[Non]

Roadmap de développement
Phase 0

✅ Extraction

✅ TVA

✅ OCR

✅ Skills

✅ SupplierRepository

✅ Rapprochement

✅ 8/8 VALIDE

Phase 1
Finalisation comptabilité

Objectif :

Ne laisser aucune dette technique.


Livrables :

Company Repository

Workflow fournisseur

Historique fournisseur

Rapprochement complet

Séparation des 4 sociétés

Phase 2
Neo4j

Livrables :

Installation

Modèle complet

Relations

Synchronisation comptabilité

Phase 3
Qdrant

Collections :

documents_memory

supplier_memory

skill_memory

workflow_memory

agent_memory

Phase 4
Langfuse

Livrables :

Traces

Observabilité

Audit

Coûts

Performance

Phase 5
Toolbox

Catalogue d'actions :

OCR

Extraction

Export Excel

Export PDF

Email

Qdrant Search

Neo4j Query

Facturation

Phase 6
Workflow Builder

Objectif :

Créer des workflows en langage naturel.


Exemple :

Quand une facture arrive :

extraire

classifier

rapprocher

archiver


↓

Workflow généré

Phase 7
Agent Builder

Agents :

Agent Comptable

Agent Fournisseur

Agent Banque

Agent Archivage

Agent Workflow

Agent Qdrant

Agent Neo4j

Phase 8
Système autonome

Objectif final :

Décrire un besoin.

DSH-Core :

conçoit le workflow,

crée les agents,

connecte les outils,

mémorise les résultats,

s'améliore.

Phrase directrice du projet
Neo4j sait ce qui est vrai.

Qdrant sait ce qui ressemble.

Langfuse sait ce qui s'est passé.

Les Agents savent quoi faire.

Les Workflows savent dans quel ordre le faire.

DSH-Core orchestre l'ensemble.


Priorité immédiate : finir proprement le socle comptable et multi-sociétés, puis installer Neo4j. Qdrant et Langfuse viennent juste après.

DSH-Core 2027
Roadmap Architecturale Référence (Version Consolidée)

Objectif principal

DSH-Core n'est plus considéré comme un moteur de comptabilité.

DSH-Core devient :

Plain Text
Une plateforme autonome capable :
 
- d'analyser
- de mémoriser
- de relier
- d'apprendre
- de créer des workflows
- de créer des agents
- de s'auto-améliorer

La comptabilité est simplement le premier domaine métier utilisé pour construire cette plateforme.

Vision globale
Plain Text
DSH-Core
 
┌─────────────────────────────────────────┐
│ Interface │
└─────────────────┬───────────────────────┘
│
▼
 
┌─────────────────────────────────────────┐
│ Agents │
└─────────────────┬───────────────────────┘
│
┌─────────────┼─────────────┐
▼ ▼ ▼
 
Neo4j Qdrant Langfuse
 
Vérité Mémoire Observabilité
métier sémantique et audit
Rôle de chaque composant
Neo4j

Neo4j est :

Plain Text
La vérité métier.

Neo4j sait :

Plain Text
Qui ?
Quoi ?
Quand ?
Comment ?
Pourquoi ?

Neo4j stocke :

Plain Text
Sociétés
Fournisseurs
Documents
Factures
Paiements
Agents
Workflows
Skills
Outils
Relations
Qdrant

Qdrant est :

Plain Text
La mémoire sémantique.

Qdrant sait :

Plain Text
A quoi cela ressemble ?

Exemples :

Plain Text
Facture similaire
 
Fournisseur similaire
 
Workflow similaire
 
Agent similaire
 
Skill similaire
Langfuse

Langfuse est :

Plain Text
La mémoire d'exécution.

Langfuse sait :

Plain Text
Ce qui s'est passé.

Exemples :

Plain Text
Quel agent a pris la décision ?
 
Quel workflow a été exécuté ?
 
Pourquoi a-t-il échoué ?
 
Combien de tokens ?
 
Combien de temps ?
 
Quel coût ?
Architecture Multi-Sociétés
Principe

Aucune donnée ne doit être globale.

Chaque objet doit appartenir à une société.

Company
Python
class Company

Champs :

Python
company_id
legal_name
siret
vat_number
 
currency
 
accounting_folder
 
status
 
created_at
updated_at

Exemple :

Plain Text
COMP_001
COMP_002
COMP_003
COMP_004

correspondant aux 4 sociétés.

Supplier
Python
class Supplier

Champs :

Python
supplier_id
 
raison_sociale
 
siret
 
tva_intra
 
status
 
trust_score
 
verification_level
 
documents_seen
 
aliases
 
created_at
updated_at
Statuts fournisseurs
Python
DISCOVERED
PENDING
VERIFIED
TRUSTED
REJECTED
ARCHIVED
Skill
Python
class Skill

Champs :

Python
skill_id
 
skill_name
 
version
 
status
 
confidence
 
usage_count
 
supplier_id
 
created_at
updated_at
Document
Python
class Document

Champs :

Python
document_id
 
company_id
 
supplier_id
 
skill_id
 
file_name
 
file_hash
 
document_type
 
status
 
created_at
updated_at
Types de documents
Python
FACTURE
 
AVOIR
 
DEVIS
 
BON_COMMANDE
 
BON_LIVRAISON
 
CONTRAT
 
RIB
 
MAIL
 
DOCUMENT_DIVERS
Invoice
Python
class Invoice

Champs :

Python
invoice_id
 
document_id
 
supplier_id
 
company_id
 
invoice_number
 
invoice_date
 
due_date
 
currency
 
amount_ht
 
amount_tva
 
amount_ttc
 
vat_type
 
payment_status
 
created_at
updated_at
Paiement
Python
class Payment

Champs :

Python
payment_id
 
company_id
 
bank_account_id
 
payment_date
 
amount
 
currency
 
bank_reference
 
status
 
created_at
updated_at
Compte bancaire
Python
class BankAccount

Champs :

Python
account_id
 
company_id
 
iban
 
bic
 
bank_name
 
currency
 
status
Rapprochement bancaire
Python
class Reconciliation

Champs :

Python
reconciliation_id
 
invoice_id
 
payment_id
 
company_id
 
status
 
confidence
 
rule_used
 
validated_by
 
validated_at
Statuts
Python
NOT_MATCHED
 
CANDIDATE
 
MATCHED
 
VALIDATED
 
REJECTED
Workflow
Python
class Workflow

Champs :

Python
workflow_id
 
name
 
description
 
trigger
 
status
 
version
 
created_at
updated_at
Agent
Python
class Agent

Champs :

Python
agent_id
 
name
 
role
 
description
 
status
 
memory_scope
 
created_at
updated_at
Tool
Python
class Tool

Champs :

Python
tool_id
 
name
 
category
 
description
 
version
 
status
Graphe Neo4j complet
Sociétés
Plain Text
Company
└── OWNS
BankAccount
Plain Text
Company
└── RECEIVES
Invoice
Plain Text
Company
└── USES
Workflow
Fournisseurs
Plain Text
Supplier
└── USES_SKILL
Skill
Plain Text
Document
└── IDENTIFIED_AS
Supplier
Plain Text
Invoice
└── ISSUED_BY
Supplier
Comptabilité
Plain Text
Invoice
└── MATCHED_TO
Payment
Plain Text
Payment
└── COMES_FROM
BankAccount
Apprentissage
Plain Text
Skill
└── LEARNED_FROM
Document
Plain Text
Supplier
└── VALIDATED_BY
Document
Workflows
Plain Text
Workflow
└── CALLS
Tool
Plain Text
Agent
└── EXECUTES
Workflow
Flux Neo4j / Qdrant / Langfuse
Cas standard
Plain Text
Facture PDF
↓
OCR
↓
Extraction
↓
Neo4j
fournisseur connu ?
↓
oui
↓
Skill
↓
Réconciliation
↓
Archivage
Cas ambigu
Plain Text
Facture PDF
↓
OCR
↓
Extraction
↓
Neo4j
↓
fournisseur inconnu
↓
Qdrant
↓
suggestion
↓
validation
↓
Neo4j
↓
création fournisseur
Cas audit
Plain Text
Agent
↓
Workflow
↓
Outil
↓
Résultat

Tout est historisé dans :

Plain Text
Langfuse
Workflow Fournisseur final

Objectif :

Plain Text
Zéro script manuel.

Aujourd'hui

Plain Text
Erreur
↓
test_skill_creator.py

Demain

Plain Text
Erreur
↓
Interface utilisateur
↓
Validation
↓
SupplierRepository
↓
Skill
↓
Fin
Interface Validation Fournisseur
Plain Text
━━━━━━━━━━━━━━━━━━━━━━━
NOUVEAU FOURNISSEUR
━━━━━━━━━━━━━━━━━━━━━━━
 
Nom détecté
 
CATROUX TRANSPORTS
 
SIRET
 
12345678901234
 
TVA
 
FRXXXXXXXXXXXX
 
Documents vus
 
3
 
Statut
 
DISCOVERED
 
[Valider]
 
[Modifier]
 
[Ignorer]
 
[Voir PDF]
Messages guidés
Conflit SIRET
Plain Text
⚠ Conflit détecté
 
Le SIRET :
 
67162081300103
 
est déjà enregistré pour :
 
SMTRT SAS
 
[Voir fiche]
 
[Corriger]
 
[Annuler]
Conflit TVA
Plain Text
⚠ TVA déjà utilisée
 
FR82671620813
 
est liée à
 
SMTRT SAS
Nouveau fournisseur
Plain Text
ℹ Fournisseur inconnu détecté
 
Créer une fiche ?
 
[Oui]
 
[Non]
Roadmap de développement
Phase 0

✅ Extraction

✅ TVA

✅ OCR

✅ Skills

✅ SupplierRepository

✅ Rapprochement

✅ 8/8 VALIDE

Phase 1
Finalisation comptabilité

Objectif :

Plain Text
Ne laisser aucune dette technique.

Livrables :

Plain Text
Company Repository
 
Workflow fournisseur
 
Historique fournisseur
 
Rapprochement complet
 
Séparation des 4 sociétés
Phase 2
Neo4j

Livrables :

Plain Text
Installation
 
Modèle complet
 
Relations
 
Synchronisation comptabilité
Phase 3
Qdrant

Collections :

Plain Text
documents_memory
 
supplier_memory
 
skill_memory
 
workflow_memory
 
agent_memory
Phase 4
Langfuse

Livrables :

Plain Text
Traces
 
Observabilité
 
Audit
 
Coûts
 
Performance
Phase 5
Toolbox

Catalogue d'actions :

Plain Text
OCR
 
Extraction
 
Export Excel
 
Export PDF
 
Email
 
Qdrant Search
 
Neo4j Query
 
Facturation
Phase 6
Workflow Builder

Objectif :

Plain Text
Créer des workflows en langage naturel.

Exemple :

Plain Text
Quand une facture arrive :
 
extraire
 
classifier
 
rapprocher
 
archiver

↓

Plain Text
Workflow généré
Phase 7
Agent Builder

Agents :

Plain Text
Agent Comptable
 
Agent Fournisseur
 
Agent Banque
 
Agent Archivage
 
Agent Workflow
 
Agent Qdrant
 
Agent Neo4j
Phase 8
Système autonome

Objectif final :

Plain Text
Décrire un besoin.
 
DSH-Core :
 
conçoit le workflow,
 
crée les agents,
 
connecte les outils,
 
mémorise les résultats,
 
s'améliore.
Phrase directrice du projet
Plain Text
Neo4j sait ce qui est vrai.
 
Qdrant sait ce qui ressemble.
 
Langfuse sait ce qui s'est passé.
 
Les Agents savent quoi faire.
 
Les Workflows savent dans quel ordre le faire.
 
DSH-Core orchestre l'ensemble.

Priorité immédiate : finir proprement le socle comptable et multi-sociétés, puis installer Neo4j. Qdrant et Langfuse viennent juste après.

Fournissez vos commentaires sur BizChat
You said:
dans ton dsh core , tu n as pas oublié langraph et mem0 ? je ne les vois pas .souviens toi de cette conversation : " Oui, c'est exactement ton stack qui te permet de *ne pas envoyer l'historique*. Voici le rôle de chaque composant : 
 ## Qui fait quoi 
 | Composant | Rôle dans la gestion du contexte | |---|---| | *Mem0* | *Le cœur du système.* Il extrait les faits pertinents des conversations (préférences, décisions, contexte projet), les stocke de façon compressée, et ne récupère que les 2-3 mémoires pertinentes par requête. → *~80% de réduction de tokens* vs historique complet. | | *Qdrant* | Stockage vectoriel : sert à la fois comme *vector store de Mem0* (recherche sémantique des mémoires) et comme *base RAG* (chunks de tes PDFs). | | *Neo4j* | Optionnel (Mem0^g) : stocke les *relations entre entités* (ex: user → PREFERS → Python, user → WORKED_ON → ProjetX). Permet des requêtes en graphe que le vector seul ne peut pas faire. | | *LangGraph* | *Orchestration uniquement.* C'est là que tu appelles mem0.search() dans un nœud du graphe avant d'envoyer au LLM. Il ne stocke pas de mémoire durable — son état est éphémère par thread_id. | | *Langfuse* | *Observabilité uniquement.* Tracing, coût, latence. Zéro impact sur la longueur de contexte. | 
 ## Le flux concret 
 
 Question utilisateur │ ▼ ┌─────────────────────────────────────────┐ │ LangGraph (orchestration) │ │ │ │ 1. mem0.search(question) │ ← Qdrant (vector) + Neo4j (graph) │ → "User préfère Python, travaille │ │ sur projet X, a choisi FastAPI" │ │ │ │ 2. qdrant_rag.search(question) │ ← Chunks PDF pertinents │ → 3-5 chunks (~1500 tokens) │ │ │ │ 3. Construit le prompt : │ │ [system] + [mémoires Mem0] │ │ + [chunks RAG] + [question] │ │ → ~3000-5000 tokens total │ │ │ │ 4. Envoie au LLM (9B → 27B → Sonnet) │ │ │ │ 5. mem0.add(réponse) │ ← Stocke nouvelles mémoires └─────────────────────────────────────────┘ 
 
 ## En résumé 
 *C'est Mem0 qui remplace l'historique.* Sans lui, tu aurais besoin d'envoyer [msg1, réponse1, msg2, réponse2, ...] à chaque tour. Avec lui, tu envoies juste 2-3 faits compressés ("l'utilisateur a choisi FastAPI", "le projet utilise PostgreSQL") → *8 192 tokens de contexte suffisent* même après 50 sessions. 
 Qdrant + Neo4j sont les *moteurs de stockage* de Mem0 (vector + graphe). LangGraph est le *circuit de commande* qui orchestre le tout. Langfuse est juste le *tableau de bord* par-dessus. 
 Donc oui : c'est bien ton architecture qui te libère de la contrainte d'historique, et c'est pour ça que 8 192 de contexte est largement suffisant." il faudra dailleurs bien fixer cette question du contexte en fonction des tches et des modeles
Copilot said:

Tu as raison, j'ai effectivement oublié deux composants centraux de la vision initiale :

Plain Text
Mem0
LangGraph

Or sans eux, DSH-Core redevient simplement :

Plain Text
Neo4j
+
Qdrant
+
Langfuse

ce qui est une excellente plateforme de données...

mais pas encore un système autonome.

Correction de l'architecture globale

La vraie architecture cible est plutôt :

Plain Text
DSH-Core
 
UI
│
▼
┌─────────────────┐
│ LangGraph │
│ Orchestrateur │
└────────┬────────┘
│
┌─────────────────────┼─────────────────────┐
▼ ▼ ▼
 
Mem0 Neo4j Langfuse
 
Mémoire long Vérité métier Observabilité
terme et audit
 
│
▼
 
Qdrant
 
Mémoire vectorielle
/
Rôle réel de chaque composant
LangGraph

LangGraph devient :

Plain Text
Le cerveau opérationnel.

Il décide :

Plain Text
Quel agent appeler ?
 
Quel workflow lancer ?
 
Quelle mémoire consulter ?
 
Quel outil utiliser ?
 
Quand terminer ?
 

Exemple :

Plain Text
Facture reçue
↓
LangGraph
↓
Agent_Comptable
↓
Mem0
↓
Qdrant
↓
Neo4j
↓
Skill
↓
Réconciliation
↓
Archivage
Mem0

Mem0 devient :

Plain Text
La mémoire personnelle du système.
``

C'est probablement le composant le plus critique pour ton objectif final.

Sans Mem0 :

Plain Text
Conversation
↓
Historique complet envoyé au LLM
↓
Contexte explose
``

Avec Mem0 :

Plain Text
Conversation
↓
Extraction des faits utiles
↓
Compression
↓
Stockage

Exemple :

Mémoire stockée :

Plain Text
Sebastien préfère FastAPI.
Plain Text
Projet DSH-Core utilise Neo4j.
Plain Text
SMTRT est associé au SIRET 67162081300103.
``

Puis récupération :

Plain Text
mem0.search(question)

renvoie :

Plain Text
uniquement
les 2 à 5 souvenirs utiles
Qdrant

Je nuance maintenant.

Qdrant n'est plus directement un composant métier.

Il devient :

Plain Text
Le moteur de stockage de Mem0

et

Plain Text
Le moteur RAG global.

Concrètement :

Plain Text
Mem0
↓
Qdrant

Collections prévues :

Plain Text
memories
 
documents
 
skills
 
suppliers
 
workflows
 
agents
Neo4j

Neo4j garde son rôle.

Il ne remplace pas Mem0.

Il complète Mem0.

Exemple :

Mem0 sait :

Plain Text
Sebastien travaille sur DSH-Core.

Neo4j sait :

Plain Text
DSH-Core
↓
utilise
↓
Neo4j
 
DSH-Core
↓
utilise
↓
Qdrant
 
DSH-Core
↓
possède
↓
Workflow Comptable
Langfuse

Langfuse reste :

Plain Text
Le journal de bord.

Il stocke :

Plain Text
traces
latence
coûts
erreurs
raisonnements
échecs
succès
La question du contexte

Tu l'as très bien rappelé.

C'est probablement l'un des objectifs principaux du projet.

Aujourd'hui beaucoup font :

Plain Text
Conversation complète
→ envoyée à chaque tour

DSH-Core doit faire :

Plain Text
Question
↓
LangGraph
↓
Mem0.search()
↓
Qdrant.search()
↓
Neo4j.query()
↓
assemblage dynamique
↓
LLM

Donc :

Plain Text
Contexte envoyé
=
uniquement
ce qui est utile
Gestion des modèles

Je te propose déjà la règle d'architecture suivante.

Petit modèle
Plain Text
Qwen 7B
Qwen 14B

Contexte :

Plain Text
4k à 8k

Usage :

Plain Text
Tâches simples
Extraction
Classification
Modèle intermédiaire
Plain Text
Qwen 32B
DeepSeek

Contexte :

Plain Text
8k à 32k

Usage :

Plain Text
Analyse
Workflow
Planning
Grand modèle
Plain Text
Claude
GPT

Contexte :

Plain Text
sur demande

Usage :

Plain Text
Architecture
Débogage complexe
Décision stratégique

L'objectif n'est jamais :

Plain Text
augmenter le contexte

mais :

Plain Text
réduire le contexte envoyé

grâce à :

Plain Text
Mem0
+
Qdrant
+
Neo4j
Architecture DSH-Core finale (corrigée)
Couche 1
Vérité Métier
Plain Text
Neo4j

Contient :

Plain Text
Companies
Suppliers
Invoices
Payments
Skills
Agents
Workflows
Tools
Couche 2
Mémoire IA
Plain Text
Mem0
+
Qdrant

Contient :

Plain Text
Préférences
 
Décisions
 
Apprentissages
 
Connaissances
 
RAG
Couche 3
Orchestration
Plain Text
LangGraph

Contient :

Plain Text
Agents
 
Workflow Engine
 
State Machine
 
Routing
Couche 4
Observabilité
Plain Text
Langfuse

Contient :

Plain Text
Traces
 
Métriques
 
Coûts
 
Échecs
Nouvelle priorité de roadmap

Je modifierais désormais l'ordre :

Plain Text
1. Finir le modèle métier comptable
2. Finir les 4 sociétés
3. Neo4j
4. Qdrant
5. Mem0
6. LangGraph
7. Langfuse
8. Toolbox
9. Workflow Builder
10. Agent Builder

et non plus :

Plain Text
Neo4j
Qdrant
Langfuse

car dans la vision initiale que tu viens de rappeler :

Plain Text
Mem0 + LangGraph

sont en réalité les deux briques qui transformeront DSH-Core en système autonome capable d'apprendre durablement sans exploser la fenêtre de contexte des modèles.

Oui, et c'est une excellente question parce qu'elle révèle où en est réellement DSH-Core.

État réel du projet aujourd'hui

Si je fais un audit honnête de tout ce qu'on a évoqué :

✅ Déjà réalisé
Moteur documentaire
OCR
PyMuPDF
Qwen Vision
Docling
AutoRotate


Statut :

OPÉRATIONNEL

Extraction comptable
Facture
Avoir
TVA
Montants
Dates
Numéros


Statut :

OPÉRATIONNEL

Réconciliation fiscale
NORMALE
AUTOLIQUIDATION
NON_ASSUJETTI


Statut :

OPÉRATIONNEL

SupplierRepository
Supplier
SIRET
TVA
Aliases
Validation


Statut :

DÉMARRÉ ET FONCTIONNEL

Skills documentaires
InvoiceSkillCreator
InvoiceSkillApplier


Statut :

FONCTIONNEL

Auto-apprentissage documentaire
Correction
↓
Skill
↓
Réutilisation


Statut :

VERSION 1

Résultat
8/8 VALIDE


donc :

le socle comptable fonctionne.

❌ Ce qui n'existe pas encore
Neo4j

Statut :

0 %


On n'a défini que le modèle.

Aucun nœud.

Aucune relation.

Aucune base.

Qdrant

Statut :

0 %


(sauf si tu as déjà installé quelque chose en local ailleurs)

Dans DSH-Core :

pas branché

Mem0

Statut :

0 %


Et c'est important.

Aujourd'hui :

SupplierRepository
≠
Mem0


Ce sont deux choses totalement différentes.

SupplierRepository :

vérité métier


Mem0 :

mémoire conversationnelle
mémoire décisionnelle
mémoire utilisateur


Aujourd'hui aucun morceau de DSH-Core n'utilise :

mem0.add()


ou

mem0.search()

LangGraph

Statut :

0 %


Aucun graphe.

Aucun agent.

Aucune state machine.

Aucune orchestration.

Aujourd'hui le pipeline est :

script python
↓
fonction
↓
fonction
↓
fonction


pas :

LangGraph

Langfuse

Statut :

0 %


Pas de traces.

Pas d'observabilité.

Pas de monitoring.

Réponse à Gemini

Maintenant on répond à sa question.

Gemini demande :

On attaque :

1. CompanyRepository
ou

2. SupplierDiscovery
ou

3. Document / Invoice

Mon arbitrage d'architecte
1er chantier
CompanyRepository

OBLIGATOIRE

À faire en premier.

Pourquoi ?

Parce que :

Neo4j arrive ensuite.


Et dans Neo4j :

Tout appartient à une société.


Aujourd'hui :

Facture
↓
globale


Demain :

Facture
↓
COMP_001


ou :

Facture
↓
COMP_004


C'est la fondation.

2e chantier
Document / Invoice

IMMÉDIATEMENT après.

Aujourd'hui :

InvoiceData


mélange :

fichier
+
comptabilité


Demain :

Document


contient :

hash
nom
source
statut

Invoice


contient :

TVA
montants
dates
fournisseur


Ça prépare directement :

Neo4j

3e chantier
SupplierDiscovery

APRÈS.

Pourquoi ?

Parce que SupplierDiscovery doit créer :

Supplier


Et ce Supplier devra être lié :

Company
Document
Invoice


Donc si :

Company
Document
Invoice


n'existent pas encore proprement,

SupplierDiscovery reposera sur des fondations incomplètes.

Ordre que je donnerais à Gemini
Phase 1A
CompanyRepository

Phase 1B
Document Entity

Invoice Entity

Phase 1C
Refonte InvoiceData


vers :

Document
+
Invoice

Phase 1D
SupplierDiscoveryService

Phase 1E
Suppression définitive de
test_skill_creator.py
dans les workflows métier.


Puis seulement :

Phase 2
Neo4j

Phase 3
Qdrant

Phase 4
Mem0


(qui reposera d'ailleurs sur Qdrant)

Phase 5
LangGraph

Phase 6
Langfuse

Résumé ultra-court

Si je devais donner une seule phrase à Gemini :

Ne commence pas SupplierDiscovery.

Commence par CompanyRepository puis sépare définitivement Document et Invoice.

Ce sont les deux prérequis indispensables avant Neo4j.

Mem0, LangGraph et Langfuse n'existent pas encore dans DSH-Core ; ils sont prévus pour les phases ultérieures et dépendront directement de la qualité du modèle métier que nous figeons maintenant.


Et surtout : non, le schéma LangGraph + Mem0 n'a pas encore commencé. Nous en sommes encore à la construction du socle métier qui leur permettra d'exister proprement plus tard.

Là, pour une fois, je pense qu'il faut prendre 2 ou 3 jours d'architecture pure avant d'ajouter Qdrant, Langfuse ou des workflows.

Pourquoi ?

Parce que tu viens de découvrir quelque chose de fondamental :

Les workflows futurs dépendront entièrement
du modèle de données comptable.


Si le modèle est mauvais :

Qdrant
Langfuse
Agent Builder
Workflow Builder


hériteront d'une fondation bancale.

Si le modèle est propre :

Qdrant
Langfuse
Agents
Workflow autonome


se grefferont naturellement.

Principe directeur

DSH-Core ne doit pas être :

Logiciel de comptabilité


mais :

Système autonome de traitement documentaire
et décisionnel.


Donc les objets métiers doivent être très propres.

ENUMS À FIGER MAINTENANT
Statut Fournisseur
class SupplierStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    TRUSTED = "TRUSTED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"

Rôle

DISCOVERED

Créé automatiquement


PENDING

Vu plusieurs fois


VERIFIED

Validé humainement


TRUSTED

Historique fiable


REJECTED

Faux fournisseur


ARCHIVED

Ne plus utiliser

Statut Skill
class SkillStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    LEARNING = "LEARNING"
    TRUSTED = "TRUSTED"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"

Statut Pièce
class DocumentStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    RECONCILED = "RECONCILED"
    EXPORTED = "EXPORTED"
    ERROR = "ERROR"

Statut Rapprochement Bancaire
class ReconciliationStatus(str, Enum):
    NOT_MATCHED = "NOT_MATCHED"
    CANDIDATE = "CANDIDATE"
    MATCHED = "MATCHED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"

LES 4 SOCIÉTÉS

Je créerais immédiatement une table maîtresse.

class Company:
    company_id
    legal_name
    siret
    vat_number
    accounting_folder


Exemple :

{
  "COMP_001": {
    "name": "Société 1"
  },
  "COMP_002": {
    "name": "Société 2"
  },
  "COMP_003": {
    "name": "Société 3"
  },
  "COMP_004": {
    "name": "Société 4"
  }
}

Pourquoi ?

Parce qu'une facture doit appartenir à :

UNE société


et pas :

au système global

Modèle Document
Document


Champs :

document_id
company_id
supplier_id
skill_id

file_name
file_hash

date_document

date_extraction

document_type

status

created_at
updated_at

Modèle Facture
Invoice


Champs :

invoice_id

document_id

supplier_id

company_id

invoice_number

invoice_date

due_date

currency

amount_ht

amount_tva

amount_ttc

vat_type

payment_status

Modèle Paiement
Payment


Champs :

payment_id

company_id

bank_account_id

payment_date

amount

currency

description

source_file

status

Modèle Rapprochement

Très important.

Reconciliation


Champs :

reconciliation_id

invoice_id

payment_id

status

confidence

matched_amount

matched_date

rule_used

validated_by

validated_at

Cas concret

Facture :

SMTRT
193.56 €


Paiement banque :

VIR SMTRT
193.56 €


Le rapprochement crée :

{
  "status": "MATCHED",
  "confidence": 0.98
}

Modèle Workflow

Je te conseille de le créer maintenant même s'il reste vide.

WorkflowDefinition


Champs :

workflow_id

name

description

trigger

steps

status

created_at

Exemple futur
{
  "name": "Facture fournisseur",

  "trigger": "nouvelle_facture",

  "steps": [
    "extract",
    "classify",
    "reconcile",
    "archive"
  ]
}

Modèle Agent

À créer maintenant aussi.

AgentDefinition


Champs :

agent_id

name

role

description

tools

memory_scope

status


Exemple :

Agent_Comptable

Agent_Fournisseur

Agent_Banque

Agent_Qdrant

Modèle Qdrant

Je préparerais déjà les collections.

supplier_memory

document_memory

skill_memory

workflow_memory

agent_memory

Ce qui manque encore à DSH-Core

Si on regarde l'objectif final, les gros blocs restants sont :

Bloc validé

✅ OCR

✅ Extraction

✅ TVA

✅ SupplierRepository

✅ Skills

Bloc à construire

🟡 Auto-enrollment fournisseur

🟡 Validation assistée

🟡 Dashboard

Bloc IA

🔴 Qdrant

🔴 Langfuse

🔴 Memory Engine

🔴 Agent Builder

🔴 Workflow Builder

🔴 Toolbox

Bloc long terme

🔴 Génération automatique de workflows

🔴 Création automatique d'agents

🔴 Auto-apprentissage supervisé

Mon avis d'architecte :

avant d'installer Qdrant, je figerais définitivement ces modèles métiers (Company, Supplier, Document, Invoice, Payment, Reconciliation, Workflow, Agent).

Parce que ce sont eux qui vont servir de squelette à tout le reste du système autonome. Une fois ce schéma stabilisé, tu pourras brancher Qdrant, Langfuse et les futurs agents sans avoir à refaire toute la plomberie interne six mois plus tard.

Tu viens de mettre le doigt sur deux points essentiels qui vont probablement changer l'architecture finale de DSH-Core.

1. Oui, le SIRET réduit énormément le problème

Dans ton exemple :

GP AUTOS 18
GP AUTOS 36
GP AUTOS 58


si les factures contiennent :

SIRET
TVA
raison sociale


alors :

Neo4j peut identifier le client
de manière quasi certaine.


Le vrai problème apparaît surtout lors du rapprochement bancaire :

Facture
  GP AUTOS 36

Paiement
  DP2C


Le SIRET n'est souvent plus présent dans le relevé bancaire.

C'est là que le système d'apprentissage devient utile.

2. Je suis plutôt d'accord avec ton idée Excel

Honnêtement, avant même Neo4j, je pense que tu as besoin d'un :

Master Data Manager


simple à éditer.

Et Excel est probablement la meilleure interface utilisateur pour toi aujourd'hui.

Ce que je ferais

Au lieu de :

companies.json
suppliers.json
customers.json
bank_accounts.json


je créerais :

workspace/masterdata/


avec :

companies.xlsx

suppliers.xlsx

customers.xlsx

bank_accounts.xlsx

contacts.xlsx

Avantage

Si demain tu veux ajouter :

GP AUTOS 89


tu ouvres :

customers.xlsx


ajoutes une ligne :

customer_id	nomCUST_004	GP AUTOS 89

et c'est terminé.

Pas de JSON.

Pas de code.

Pas de .env.

Encore mieux

Je rajouterais :

bank_aliases.xlsx


Exemple :

alias bancaire	clientDP2C	GP AUTOS 36
3G AUTOS	GP AUTOS 18
GP AUTOS NEVERS	GP AUTOS 58

Lors du rapprochement :

DP2C
↓
table alias
↓
GP AUTOS 36

Puis Neo4j apprend

Excel devient :

la saisie utilisateur


Neo4j devient :

la connaissance consolidée


Donc :

Excel
↓
Import
↓
Neo4j


et non :

Excel
↓
Système

Là où tu n'arrêtes pas d'oublier LangGraph

Et tu n'es pas le seul, parce que LangGraph apparaît moins concret.

En réalité :

Neo4j

sait :

ce qui est vrai

Qdrant

sait :

ce qui ressemble

Mem0

sait :

ce qu'il faut retenir

Langfuse

sait :

ce qui s'est passé

LangGraph

sait :

quoi faire maintenant


C'est lui le chef d'orchestre.

Exemple concret

Tu déposes une facture.

Aujourd'hui :

script
↓
script
↓
script


Demain :

LangGraph


va exécuter :

Workflow Facture


Étape 1

OCR


Étape 2

Recherche Neo4j


Étape 3

Si inconnu
↓
Qdrant


Étape 4

Demander validation


Étape 5

Créer relation Neo4j


Étape 6

Mem0.add()


Étape 7

Tracer dans Langfuse

Le rôle réel de Mem0

C'est probablement le composant le plus sous-estimé.

Exemple :

Aujourd'hui tu expliques souvent :

GP Autos 18
= 3G Autos

GP Autos 36
= DP2C


Avec Mem0 :

Validation effectuée
↓
Mem0.add()


Des mois plus tard :

mem0.search("DP2C")


renvoie :

DP2C est déjà associé
à GP AUTOS 36


sans relire tout l'historique.

L'architecture qui me paraît la plus cohérente maintenant
Administration
Excel


pour :

Sociétés

Fournisseurs

Clients

Comptes bancaires

Alias bancaires

Vérité métier
Neo4j

Recherche de similarité
Qdrant

Mémoire persistante
Mem0

Orchestration
LangGraph

Audit
Langfuse


La phrase finale devient alors :

Excel permet d'ajouter.

Neo4j sait ce qui est vrai.

Qdrant sait ce qui ressemble.

Mem0 sait ce qu'il faut retenir.

LangGraph décide quoi faire.

Langfuse sait ce qui s'est passé.


Et à mon avis, pour tes 4 sociétés, les clients, les fournisseurs et les comptes bancaires, un import Excel pilotant Neo4j sera beaucoup plus évolutif qu'une accumulation de JSON ou de variables .env.

Oui. En fait, ton exemple fait évoluer l'architecture une nouvelle fois.

Jusqu'ici nous raisonnions :

Facture
↓
Rapprochement
↓
Paiement


Mais dans ton activité réelle, il faut raisonner :

Document
↓
Connaissance métier
↓
Rapprochement
↓
Apprentissage


Les photos montrent clairement que tu as plusieurs couches de références.

1. Nouvelle règle DSH-Core : ne jamais jeter une référence

Je pense que Gemini devra modifier le pipeline d'extraction.

Aujourd'hui :

Facture
↓
numero_facture


Demain :

Facture
↓
Extraire TOUTES les références possibles

Exemple SMTRT

Tu l'as déjà constaté.

Sur une facture on peut avoir :

Numéro facture

Votre référence

Numéro affaire

Numéro client

Bon de livraison

Commande client

Référence transport

Référence dossier


Aujourd'hui :

numero_facture = 60900194


est conservé.

Demain il faudrait :

{
  "invoice_number": "60900194",

  "references": [
    {
      "type": "invoice_number",
      "value": "60900194"
    },
    {
      "type": "customer_reference",
      "value": "..."
    },
    {
      "type": "business_case",
      "value": "..."
    },
    {
      "type": "delivery_note",
      "value": "..."
    }
  ]
}

Pourquoi ?

Parce que dans les banques on retrouve souvent :

Votre ref


et non :

Numéro facture

2. Identification fournisseur / client dès l'entrée

Je pense qu'il faut créer une nouvelle couche :

CounterpartyProfile


Exemple :

{
  "entity_id": "SUP_001",

  "type": "SUPPLIER",

  "name": "SMTRT",

  "usual_payment_method": "TRANSFER",

  "usual_reference_fields": [
    "invoice_number",
    "customer_reference"
  ]
}


Ou :

{
  "entity_id": "CUST_012",

  "type": "CUSTOMER",

  "name": "GP AUTO 36",

  "usual_payment_method": "LCR",

  "bank_aliases": [
    "DP2C"
  ]
}


Cela va orienter l'extraction.

3. Cas LCR / effets / remises à l'encaissement

Tes photos montrent un cas particulier que beaucoup de logiciels gèrent mal.

On voit :

CALANDRE 18
FA16510

GP AUTO 36 DP2C
FA16512

GP AUTO 58 NEVERS
FA16512


Puis :

TOTAL REMISE
10 370,50 €


Cela signifie qu'il faut créer un nouvel objet.

Remittance


Exemple :

{
  "remittance_id": "REM_001",

  "total_amount": 10370.50,

  "lines": [
    {
      "customer": "CALANDRE 18",
      "reference": "FA16510",
      "amount": 144.00
    },
    {
      "customer": "GP AUTO 36",
      "reference": "FA16512",
      "amount": 1546.43
    }
  ]
}


Ensuite sur le relevé bancaire :

+10 370,50 €


DSH-Core devra savoir :

ce crédit bancaire
=
REM_001


et non :

10 lignes indépendantes

4. Gestion des chèques

Tu as raison.

Les chèques sont probablement le pire cas.

Créons une catégorie spéciale :

PaymentMethod.CHEQUE


Le score de rapprochement doit être différent.

Sources possibles :

Montant
100 €

Date
± 15 jours

Tiers
Nom connu


quand la banque le fournit.

Historique
Client paie toujours par chèque

Exemple

Facture :

GP AUTO 36

812.20 €


Chèque :

812.20 €


Score :

Montant exact = +50

Client habituel = +20

Période cohérente = +10

Total = 80


Etat :

MATCH_CANDIDATE


Validation humaine.

5. Paiements cumulés

Exemple Bosch.

Factures :

800 €

650 €

240 €


Paiement :

1690 €


DSH-Core doit tester :

800 + 650 + 240


Puis proposer :

Confiance 98 %


Et apprendre ensuite.

6. Paiements partiels

Très important.

Je pense qu'il faut créer un objet :

Settlement


Exemple :

Facture :

1000 €


Premier virement :

400 €


Deuxième :

600 €


Modèle :

{
  "invoice_id": "...",

  "amount_due": 1000,

  "settlements": [
    {
      "amount": 400
    },
    {
      "amount": 600
    }
  ],

  "balance": 0
}

7. Score de confiance global

Je définirais :

Référence exacte
+100

Numéro facture
+90

Votre référence
+80

Alias bancaire connu
+70

SIRET
+100

Montant exact
+50

Date cohérente
+20

Historique Mem0 déjà vu
+30

Similarité Qdrant
0 à 30

Décision
>= 100
AUTOMATIQUE

70 à 99
PROPOSITION

< 70
MANUEL

Le rôle final de chaque composant
Neo4j

Connaît :

GP AUTO 36

⇄

DP2C

SMTRT

⇄

Compte fournisseur

Facture

⇄

Paiement

Qdrant

Retrouve :

cas similaires

Mem0

Retient :

Sebastien a validé que
DP2C = GP AUTO 36

Bosch est souvent payé
par lots de 3 factures

LangGraph

Orchestre :

Facture
↓
Neo4j
↓
Qdrant
↓
Mem0
↓
Calcul combinaisons
↓
Validation
↓
Apprentissage


Et c'est justement là que LangGraph devient indispensable : ce n'est ni une base de données ni une mémoire, c'est le moteur qui va décider dans quel ordre interroger Neo4j, Qdrant et Mem0 pour résoudre les cas complexes comme les chèques, les paiements partiels ou les virements couvrant plusieurs factures.

Mon avis d'architecte : l'idée est bonne, mais je ne la validerais pas telle quelle sans quelques corrections importantes.

Tu es en train de construire le socle qui va alimenter :

CompanyRepository
↓
Neo4j
↓
Qdrant
↓
Mem0
↓
LangGraph
↓
Agents
↓
Workflows


Donc il faut penser "source de vérité long terme", pas seulement "configuration pratique".

✅ Ce que je valide
Utiliser le .env

Je suis d'accord.

Les 4 sociétés sont :

des paramètres d'exploitation


et non :

des données métier dynamiques


Donc :

COMP_001_NAME=...
COMP_002_NAME=...
...


est pertinent.

Cela évite :

if company == "APD":


un peu partout dans le code.

SCI APD non assujettie

Excellente idée.

COMP_003_VAT_SUBJECT=false


car cela prépare déjà :

TVA non récupérable


et donc :

TTC = charge


pour cette société.

⚠️ Ce que je modifierais
1. Le .env n'est PAS la source de vérité

Je changerais de philosophie.

Gemini semble considérer :

.env
↓
source de vérité


Je ferais :

.env
↓
bootstrap


uniquement.

Pourquoi ?

Dans 2 ans tu auras :

Company
↓
Neo4j


et non :

Company
↓
.env


Je renommerais mentalement :

.env


en :

Configuration de démarrage

2. Les sociétés doivent déjà avoir un UUID

Je ne garderais pas :

COMP_001
COMP_002


comme identifiants techniques.

Je garderais :

short_code = APD


mais je créerais :

{
  "company_id": "cmp_d6f84f11"
}


Pourquoi ?

Parce que :

Neo4j
Qdrant
Mem0
Workflow Engine


n'aiment pas les IDs "métier".

Je stockerais donc :

{
  "company_id": "cmp_d6f84f11",
  "short_code": "APD"
}

3. Il manque la notion de groupe

Tu as :

APD
PRD
SCI APD
HOLDING APD


Je créerais déjà :

{
  "group_id": "GRP_APD"
}


Car dans Neo4j tu vas vouloir :

GROUP APD
│
├── APD
├── PRD
├── SCI APD
└── HOLDING APD

4. Il manque les comptes bancaires

Je les préparerais dès maintenant.

Pas les IBAN si tu ne veux pas.

Mais au moins :

"bank_accounts": []


Puis dans Neo4j :

Company
↓
OWNS
↓
BankAccount

⚠️ La vraie question
Faut-il coder CompanyRepository maintenant ?

Je réponds :

OUI

Faut-il mettre SupplierDiscovery avant ?

Je réponds :

NON


Pourquoi ?

SupplierDiscovery dépendra ensuite de :

Document
↓
Company


Donc l'ordre reste :

Étape 1
CompanyRepository

Étape 2
Document

Étape 3
Invoice

Étape 4
SupplierDiscovery

Où en est Mem0 / LangGraph ?

Très important.

Mem0

Aujourd'hui :

0 %


Aucune implémentation.

Aucune mémoire.

Aucun add()

Aucun search()

LangGraph

Aujourd'hui :

0 %


Aucun graphe.

Aucun workflow.

Aucun agent.

Aucune state machine.

Qdrant

Aujourd'hui :

0 %


dans DSH-Core.

Neo4j

Aujourd'hui :

0 %


également.

Mon état réel du projet
Niveau 1 : terminé

✅ OCR

✅ Extraction

✅ TVA

✅ Skills

✅ Supplier Repository

✅ Batch 8/8

Niveau 2 : à construire

🟡 CompanyRepository

🟡 Document

🟡 Invoice

🟡 SupplierDiscovery

Niveau 3 : non commencé

🔴 Neo4j

🔴 Qdrant

🔴 Mem0

🔴 LangGraph

🔴 Langfuse

Niveau 4 : vision finale

🔴 Toolbox

🔴 Workflow Builder

🔴 Agent Builder

🔴 Auto-amélioration

Ce que je dirais à Gemini maintenant
Valider CompanyRepository.

Le .env sert uniquement à initialiser les sociétés.

CompanyRepository devient ensuite la source de vérité des sociétés.

Ne pas commencer SupplierDiscovery avant la séparation
Document / Invoice.

Ne pas commencer Neo4j avant d'avoir :
- Company
- Supplier
- Document
- Invoice

Mem0, Qdrant, LangGraph et Langfuse n'ont pas encore démarré.

Ils doivent être considérés comme des phases futures et non comme des composants déjà présents dans DSH-Core.


En résumé : oui au CompanyRepository, oui au .env comme bootstrap, mais non à l'idée que le .env deviendra la base métier. La vraie cible reste :

CompanyRepository
↓
Neo4j
↓
Mem0 + Qdrant
↓
LangGraph
↓
Agents autonomes


et nous sommes encore juste avant la construction de ce socle.
Je pense que tu viens d'identifier un point architectural majeur.

Je ne ferais surtout pas :

.env
↓
ajout manuel
↓
JSON statique
↓
ajout manuel
↓
Neo4j plus tard


Ça ressemble à une architecture classique de logiciel métier.

Or ton objectif n'est pas :

Logiciel métier


mais :

Système autonome apprenant

Ce qui me gêne dans le .env

Pour les sociétés, Gemini a raison sur le court terme :

COMP_001_NAME=...


fonctionne.

Mais à long terme :

Une société change de nom
Un SIRET change
Une banque change
Un compte est fermé
Une filiale est créée


Tu vas finir avec :

COMP_001_.....
COMP_002_.....
COMP_003_.....
COMP_004_.....
COMP_005_.....
COMP_006_.....


et le .env deviendra une mini base de données.

Ce n'est pas son rôle.

Ce que je ferais réellement

Je garderais :

DSH_ENV=PROD

NEO4J_URL=...

QDRANT_URL=...

LANGFUSE_URL=...


uniquement pour la configuration technique.

Je ne mettrais pas dedans :

Sociétés
Comptes bancaires
Fournisseurs
Clients

Vision cible

Je créerais un vrai objet :

Entity Registry


Au lieu de :

CompanyRepository


je vois :

Registry Service


capable de gérer :

Company
Supplier
Customer
BankAccount
Contact
LegalEntity

Neo4j comme source de vérité

À terme :

Neo4j
=
registre vivant


Exemple :

(Holding APD)
        │
        │ OWNS
        ▼
(PRD)

(Holding APD)
        │
        │ OWNS
        ▼
(APD)

(APD)
        │
        │ HAS_ACCOUNT
        ▼
(CIC_001)

(APD)
        │
        │ HAS_SUPPLIER
        ▼
(SMTRT)

Peut-on "apprendre" Neo4j ?

Oui.

Et c'est probablement plus intelligent que de remplir du JSON.

Exemple :

Une facture arrive.

Nouveau SIRET
↓
Neo4j


Recherche :

MATCH (s:Supplier)
WHERE s.siret = ...


Aucun résultat.

Le moteur crée :

Supplier
status=DISCOVERED


Après 10 factures :

DISCOVERED
↓
VERIFIED


automatiquement.

Donc le vrai futur n'est pas
.env
↓
companies.json


mais :

.env
↓
boot minimal
↓
Neo4j
↓
registre vivant

Où intervient Qdrant ?

Attention :

Qdrant ne doit pas stocker les sociétés.

Je vois souvent cette erreur.

Qdrant n'est pas fait pour répondre :

Quel est le SIRET de APD ?


Neo4j répond :

vérité


Qdrant répond :

similarité


Exemple :

Utilisateur écrit :

la société peinture résine


alors que :

SAS PEINTURES ET RESINES DIFFUSION


est la vraie raison sociale.

Qdrant peut retrouver :

PRD


puis :

Neo4j confirme

Ce que je proposerais

Créer un composant futur :

Entity Learning Service


Fonctions :

discover_company()

discover_supplier()

discover_customer()

discover_bank_account()

merge_entities()

validate_entity()


Il écouterait :

Factures
Emails
Documents
RIB
Contrats


et enrichirait automatiquement Neo4j.

Ce qui me paraît optimal aujourd'hui
Court terme

Gemini peut faire :

CompanyRepository


mais avec cette règle :

companies.json
=
cache local


et non :

source de vérité définitive

Moyen terme

Créer :

Neo4j Registry


Objets :

Company
Supplier
Customer
BankAccount
Contact
Skill
Workflow
Agent

Long terme

Créer :

Learning Registry


capable de :

Découvrir

Proposer

Fusionner

Valider

Historiser


les entités automatiquement.

Ma recommandation à Gemini serait donc :

Oui au CompanyRepository.

Mais conçu comme une couche de transition.

Ne pas graver les sociétés et comptes bancaires dans du JSON à long terme.

La vraie cible est :

Neo4j = registre vivant des entités.

Qdrant = recherche sémantique et rapprochement.

Mem0 = mémoire utilisateur et mémoire projet.

LangGraph = orchestration.

Les sociétés, comptes bancaires, fournisseurs et clients doivent pouvoir être créés, enrichis et validés automatiquement via un futur Entity Learning Service.


C'est beaucoup plus aligné avec ton objectif initial de plateforme autonome que la simple accumulation de fichiers JSON.

DSH-Core 2026-2027
Plan Directeur (Version de Référence)

Je pense que nous avons maintenant suffisamment de recul pour figer une architecture cohérente.

La règle fondamentale devient :

Excel = administration humaine

Neo4j = vérité métier

Qdrant = similarité

Mem0 = mémoire des validations et décisions

LangGraph = chef d'orchestre

Langfuse = observabilité

1. État réel du projet
Déjà existant
Extraction documentaire
OCR
PyMuPDF
Docling
Qwen Vision
Rotation automatique


Statut :

✅ Opérationnel

Extraction comptable
Factures
TVA
Dates
Montants
Numéros


Statut :

✅ Opérationnel

Skills documentaires
SupplierRepository
InvoiceSkillCreator
InvoiceSkillApplier


Statut :

✅ Opérationnel

Validation
Batch 8/8 valide


Statut :

✅ Stabilisé

2. Architecture cible
                       DSH-Core

                           UI
                            │
                            ▼

                     LangGraph
                  (Orchestrateur)

          ┌──────────┼──────────┐
          ▼          ▼          ▼

       Neo4j      Qdrant    Langfuse

       Vérité     Similarité
       métier     mémoire     Audit

                     │
                     ▼

                    Mem0

           Mémoire des validations
           apprentissages
           préférences

3. Cartographie cible du projet
schemas/

Modèles métier.

Existe
InvoiceData

À créer
core_models.py

counterparty_and_references.py


Contiendra :

Company
Counterparty
Customer
Supplier
Document
Invoice
Payment
Reconciliation
Remittance
CCAEntry
BankAccount

repositories/

Source métier locale.

Existe
supplier_repository.py

À créer
company_repository.py

counterparty_repository.py

bank_account_repository.py

reconciliation_repository.py

services/

Logique métier.

Existe
invoice_skill_creator.py

invoice_skill_applier.py

À créer
mail_routing_service.py

masterdata_import_service.py

sync_masterdata.py

reconciliation_orchestrator.py

supplier_discovery_service.py

memory/

Futures mémoires IA.

À créer
neo4j_service.py

qdrant_service.py

mem0_service.py

workflows/

Définition des flux LangGraph.

À créer
invoice_workflow.py

reconciliation_workflow.py

cca_workflow.py

supplier_learning_workflow.py

agents/

Agents spécialisés.

Plus tard
agent_accounting.py

agent_supplier.py

agent_reconciliation.py

agent_memory.py

4. Master Data Prioritaire
companies.xlsx

Obligatoire.

Colonnes :

company_id

short_code

legal_name

siret

vat_number

is_vat_subject

inbox_pro

inbox_perso


Exemple :

APD
PRD
SCI_APD
HOLDING_APD

counterparties.xlsx

Très important.

Fusionne :

Clients
Fournisseurs


Colonnes :

counterparty_id

party_type

legal_name

siret

vat_number

default_category

usual_payment_method

default_payment_source


Exemple :

SMTRT

CATROUX

TOTAL

MCDONALD'S

GP AUTOS 36

bank_accounts.xlsx

Très important.

Colonnes :

account_id

company_id

iban

bank_name

account_type

status

bank_aliases.xlsx

Critique.

Colonnes :

bank_label_alias

counterparty_id

notes


Exemples :

DP2C

GP AUTOS NEVERS

3G AUTOS

RCSAIXENPROVENCE

cca_journal.xlsx

Compte courant associé.

Colonnes :

date

company_id

supplier

amount_ttc

vat

category

payment_source

status

5. Schémas métier prioritaires
CounterpartyProfile

Devient l'objet central.

Remplace progressivement :

Supplier

Customer


Contient :

counterparty_id

party_type

legal_name

siret

vat_number

bank_aliases

default_expense_category

usual_payment_method

payment_terms_days

category_history

6. Catégories comptables
CARBURANT

RESTAURATION

PEAGE_PARKING

HOTEL

FOURNITURES

OUTILLAGE

TRANSPORT_FRET

SERVICES

MARCHANDISES

DIVERS

7. Sources de paiement
BANK_TRANSFER

BANK_LCR

COMPANY_CARD

PERSONAL_CARD

CASH_PERSONAL

CHECK_COMPANY

8. Cas particuliers obligatoires
CB personnelle

Exemple :

McDonald's


Workflow :

Pas de rapprochement bancaire
↓
CCA
↓
Compte 455

Espèces

Même logique.

CCA

CB Société

Workflow :

Facture
↓
Recherche transaction CB
↓
Rapprochement

9. Neo4j
Rôle

Connaître les vérités métier.

Exemple :

(GP AUTO 36)

HAS_BANK_ALIAS

(DP2C)

(TOTAL ENERGIES)

DEFAULT_CATEGORY

(CARBURANT)

(APD)

OWNS

(Account CIC APD)

Ce qui doit être injecté

Depuis Excel :

Companies

Counterparties

Bank Accounts

Bank Aliases

10. Qdrant
Rôle

Gérer l'imparfait.

Exemples :

TOT ENERG 41

DP2C CHATEAUR

MCDO BLOIS


Qdrant trouve :

TOTAL ENERGIES

DP2C

MCDONALD'S


Puis Neo4j valide.

11. Mem0

Pas prioritaire immédiatement.

Utilisé plus tard pour mémoriser :

GP AUTO 36 = DP2C

Bosch paie souvent par lots

Sebastien valide telle relation

12. LangGraph

Chef d'orchestre.

Workflow futur :

Mail reçu
↓
MailRoutingService
↓
OCR
↓
Extraction
↓
Neo4j
↓
Qdrant
↓
Score de confiance
↓
Validation éventuelle
↓
Apprentissage

13. Langfuse

Dernière couche.

Stocke :

Temps

Coût

Prompts

Latence

Décisions

Erreurs

14. Ordre recommandé
Sprint 1

Créer :

counterparty_and_references.py

company_repository.py

CounterpartyProfile

Sprint 2

Créer :

companies.xlsx

counterparties.xlsx

bank_accounts.xlsx

bank_aliases.xlsx

cca_journal.xlsx

Sprint 3

Créer :

masterdata_import_service.py

sync_masterdata.py

Sprint 4

Installer :

Neo4j

Sprint 5

Installer :

Qdrant

Sprint 6

Créer :

reconciliation_orchestrator.py


et premier workflow LangGraph.

Sprint 7

Installer :

Langfuse

Sprint 8

Installer :

Mem0

Décision finale

À mon avis, nous sommes au point de transition suivant :

Ne plus ajouter de logique dans les Skills.

Ne plus améliorer l'extraction.

Finaliser les schémas métier.

Créer le MasterData Excel.

Puis brancher Neo4j et Qdrant.


C'est la première étape qui prépare réellement DSH-Core à devenir une plateforme autonome plutôt qu'un simple moteur de traitement de factures.

Je vais être un peu à contre-courant de ce qu'on s'est dit jusqu'ici :

Oui, il faut avancer sur Neo4j et Qdrant maintenant. Non, il ne faut pas encore brancher tout l'écosystème (Mem0, agents autonomes, SkillOpt avancé, etc.).

La nuance est importante.

Ce que je vois comme priorité n°1

Tu as en réalité deux projets qui avancent en parallèle :

Projet A : DSH-Compta
Factures
TVA
CCA
LCR
Banques
Clients
Fournisseurs


C'est tout ce qu'on vient de structurer.

Projet B : DSH-Knowledge
Google Drive

OneDrive

PDF

DOCX

RAG

Qdrant

Neo4j

Recherche documentaire


Et celui-là est presque indépendant de la comptabilité.

Là où je pense que tu as raison

Tu ne peux pas attendre 3 mois avant de :

Indexer OneDrive

Indexer Google Drive

Construire ton graphe documentaire

Construire ton RAG


Parce que c'est précisément ce qui va enrichir :

Qdrant


et

Neo4j

Donc je séparerais les chantiers
Chantier 1

MasterData comptable

Excel :

companies.xlsx

counterparties.xlsx

bank_accounts.xlsx

bank_aliases.xlsx

expense_categories.xlsx

cca_journal.xlsx

validation_errors.xlsx


Objectif :

Comptabilité

Chantier 2

Knowledge Graph

Sources :

Google Drive

OneDrive

PDF

DOCX

Notes

Procédures

Documentation


Objectif :

RAG

Ce que j'aurais demandé à Gemini

Pas :

Créer Mem0


Mais :

Créer un pipeline :

Google Drive
↓
Extraction
↓
Chunks
↓
Qdrant

et

Google Drive
↓
Entités
↓
Neo4j

Neo4j va servir à deux choses

Et c'est ça qu'on a découvert un peu tard.

Neo4j comptable
GP AUTO 36
↓
DP2C

Bosch
↓
Paiement groupé

APD
↓
Compte bancaire

Neo4j documentaire
Document
↓
Projet

Projet
↓
Client

Client
↓
Factures

Factures
↓
Paiements


Ce n'est pas le même graphe.

Mais c'est la même base.

Qdrant aussi
Qdrant comptable
DP2C

TOT ENERG

MCDO BLOIS

Qdrant RAG
PDF

Contrats

Procédures

Emails

DOCX


Même moteur.

Deux usages.

Ce que je dirais à Gemini maintenant

Je lui donnerais une mission très précise :

On gèle les modèles Excel définis ci-dessus.

Ne plus modifier la structure tant qu'une incohérence majeure n'est pas découverte.

Commencer maintenant :

1. memory/neo4j_service.py

2. memory/qdrant_service.py

3. services/sync_masterdata.py

4. services/document_ingestion_service.py

Objectif :

- alimenter Neo4j depuis MasterData
- alimenter Qdrant depuis MasterData
- alimenter Qdrant depuis OneDrive
- alimenter Qdrant depuis Google Drive
- préparer le futur RAG documentaire

Mem0 reporté.

Agents reportés.

LangGraph minimal seulement.

Mon avis sur les catégories

Les nouvelles catégories proposées sont bonnes :

✅ LIVRAISON_COLIS

✅ TRANSPORT_VOYAGE

✅ MATERIEL_AMORTISSABLE

Je les garderais.

Mais je ne les laisserais plus uniquement dans :

ExpenseCategory


Je les mettrais aussi dans :

expense_categories.xlsx


qui devient la référence métier.

Si c'était mon projet

Je lancerais maintenant :

Sprint 4
Neo4j

Sprint 5
Qdrant

Sprint 6
Indexation Google Drive

Sprint 7
Indexation OneDrive

Sprint 8
Premier RAG hybride
Qdrant + Neo4j

Sprint 9
LangGraph

Sprint 10
Mem0


Ma conclusion :

Tu as suffisamment travaillé le modèle métier comptable pour commencer Neo4j et Qdrant.

En revanche, je ne brancherais pas encore Mem0 ni les agents autonomes. Je mettrais plutôt l'effort sur :

MasterData Excel
↓
Neo4j
↓
Qdrant
↓
Google Drive
↓
OneDrive
↓
RAG Hybride


car c'est ce qui apportera rapidement une valeur tangible à la fois à DSH-Compta et à DSH-Knowledge.


# ROADMAP V2 — CONVERGENCE LOCAL + ORACLE
Date : 19 octobre 2026


# ROADMAP V2 — CONVERGENCE DSH-CORE LOCAL + ORACLE

**Date de référence : 20 octobre 2026**

**Sources canoniques :**

- `dsh core complet.md`
- `dsh_core_master_specification_2026_2027.md`
- `03_JOURNAL.md`
- `PIPELINE_OFFICIEL.md`
- `04_AUDIT_ARCHITECTURE.md`
- `05_DECISIONS_ARCHITECTURE.md`
- `SKILLS_DUPLICATES_INVENTORY.md`
- `DATABASE_STORAGE_MAP.md`
- `BANKING_ARCHITECTURE.md`
- `BANKING_RECORD_SPEC.md`
- `BANKING_SCHEMA_REVIEW.md`
- `BANKING_RELATIONS_REVIEW.md`

Cette section conserve l'historique de la roadmap précédente. À partir d'ici,
les phases et priorités ci-dessous remplacent les anciennes priorités qui
seraient incompatibles avec les audits et décisions canoniques. C'est une
roadmap à exécuter, pas la déclaration que ses cibles sont déjà livrées.

## 1. État initial de la Roadmap V2

État observé au 20 octobre 2026 :

- Trois corrections de code sont déjà appliquées : `api.py` appelle
  `route_prompt`; `ResearchAgent` utilise `SkillRouter.retrieve_skill_for_task`;
  `IngestionManager` lit le détail d'erreur dans `message`.
- `InvoiceSchema` est gelé et bloqué : sa définition exécutable n'a pas été
  retrouvée; il ne doit pas être remplacé par `InvoiceData` sans décision.
- La branche bancaire de `IngestionManager` retourne
  `pending_implementation`.
- Deux répertoires Qdrant sont présents : celui à la racine correspond à
  l'instance active auditée; celui sous `docker` est secondaire et inactif.
  Leur identité ou équivalence n'est pas prouvée.
- Neo4j est actif; l'audit authentifié du 15 octobre a obtenu l'accès et
  l'inventaire runtime en session `read`. La provenance reste inconnue et un
  rôle exclusivement read-only n'est pas certifié; aucune écriture n'est
  permise avant la définition d'un mécanisme permanent de lecture seule.
- Quatre variantes de `agent_skills` sont fonctionnellement redondantes de
  façon probable; leur identité stricte n'est pas établie.
- `skills_catalog` est vide au relevé.
- Aucune configuration Hermes ou SkillOpt n'a été trouvée dans le dépôt audité.

La présence d'un fichier, d'un service, d'un test isolé ou d'une collection ne
prouve pas un flux de bout en bout. Les statuts des phases ci-dessous sont
initiaux : **EXISTANT**, **PARTIEL**, **NON RACCORDE**, **CIBLE**, **BLOQUÉ** ou
**FUTUR**.

## 2. Principes non négociables

1. DSH-Core local reste l'unique source de vérité métier.
2. Hermes encapsule DSH-Core et ne le remplace pas; LangGraph reste
   l'orchestrateur métier unique de la cible.
3. Oracle est un satellite 24/7; ses écritures restent dans un staging
   réversible. Toute validation et publication définitive est locale.
4. Aucune suppression, fusion ou mise à l'écart destructive sans audit,
   sauvegarde vérifiée et autorisation explicite.
5. Aucune copie physique directe des bases ou répertoires Neo4j/Qdrant pour
   synchroniser les environnements.
6. Aucun `DELETE` automatique, notamment à la réception d'une synchronisation.
7. Aucune écriture Oracle directe dans la vérité locale et aucune promotion
   automatique d'un skill.
8. Une incertitude d'identité, de montant, de société, de compte ou de
   rapprochement entraîne une revue humaine ou un blocage explicite.
9. Le fonctionnement local hors Internet reste requis pour les capacités et
   modèles disponibles localement; une dépendance distante indisponible doit
   être signalée, jamais simulée comme réussie.
10. Chaque décision, transformation, transfert et publication doit être
    traçable et idempotent; les reprises ne doivent pas appliquer deux fois
    une opération.

## 3. Phase 0 — Sauvegarde et preuve de l'état actuel

**Statut initial :** **CIBLE**, préalable à toute convergence.
**Objectif :** établir un état de référence réversible du code, des documents,
des configurations et des bases actives avant tout changement ultérieur.

### Tickets

**P0-1 — Capturer l'état Git du code et de la documentation**

- Dépendances : aucune.
- Livrables : référence de commit/branche, inventaire des changements locaux,
  sauvegarde vérifiable des fichiers de code et de documentation nécessaires.
- Critères d'acceptation : l'état avant travaux peut être identifié et restauré
  sans écraser les modifications préexistantes; les changements de roadmap sont
  distingués du code.
- Risques/interdictions : ne pas nettoyer le worktree, écraser ou committer des
  changements sans autorisation.
- Données/tests : statut Git, empreintes et inventaire des fichiers concernés.

**P0-2 — Sauvegarder de façon contrôlée les bases actives**

- Dépendances : P0-1; accès autorisé aux services.
- Livrables : snapshots/export vérifiés de l'instance Qdrant active et de
  Neo4j, avec date, méthode, empreintes et procédure de restauration.
- Critères d'acceptation : chaque sauvegarde peut être vérifiée et une
  restauration est documentée ou testée sur un environnement contrôlé.
- Risques/interdictions : ne pas exposer de secrets ni copier les répertoires
  physiques comme méthode de synchronisation; ne pas écrire dans les bases
  pendant l'inventaire.
- Données/tests : volumes, collections, contraintes et métadonnées nécessaires
  à la preuve de sauvegarde.

**P0-3 — Vérifier les chemins réellement montés**

- Dépendances : P0-1; P0-2 pour comparer les données.
- Livrables : cartographie read-only des conteneurs, volumes, chemins hôtes et
  chemins de stockage Qdrant/Neo4j.
- Critères d'acceptation : le chemin de chaque service actif est observé; le
  stockage Qdrant secondaire sous `docker` est préservé et marqué inactif tant
  que son rôle n'est pas établi.
- Risques/interdictions : aucune suppression, permutation de montage ou
  démarrage concurrent des stockages.
- Données/tests : inspection Docker et comparaison des chemins effectifs.

**P0-4 — Auditer et sécuriser les secrets existants**

- Dépendances : P0-1.
- Livrables : inventaire confidentiel des emplacements de secrets, plan de
  rotation si nécessaire et migration vers variables d'environnement ou
  gestionnaire de secrets.
- Critères d'acceptation : aucun secret n'est ajouté aux fichiers versionnés;
  la migration et les accès sont vérifiés sans révéler les valeurs dans les
  rapports.
- Risques/interdictions : ne pas afficher, copier dans la roadmap ou committer
  de valeur secrète; ne pas invalider une configuration active sans plan de
  rotation.
- Données/tests : contrôles locaux de fichiers et configurations, rapport
  expurgé des valeurs.

**P0-5 — Tester les trois corrections déjà appliquées**

- Dépendances : P0-1; environnement de test disponible.
- Livrables : résultats reproductibles des tests ciblés API, ResearchAgent et
  IngestionManager.
- Critères d'acceptation : chaque correction a au moins un test ciblé passant;
  les contrats des fonctions appelées et des erreurs sont vérifiés.
- Risques/interdictions : ne pas élargir les changements au-delà de défauts
  découverts directement liés; aucun test ne doit écrire en base réelle.
- Données/tests : fixtures non sensibles et doubles de services externes.

## 4. Phase 1 — Résolution des blocages et contrats

**Statut initial :** majoritairement **BLOQUÉ**; audits non destructifs
**CIBLE**.
**Objectif :** lever les incertitudes qui empêchent de figer les schémas et les
droits de publication.

### Tickets

**P1-1 — Obtenir un accès Neo4j en lecture authentifiée**

- **Statut : audit techniquement exécuté le 15 octobre 2026**
  (`PARTIAL_READ_ONLY`).
- Les dates des 19 et 20 octobre présentes dans les documents antérieurs
  restent des dates documentaires non vérifiées, et ne remplacent pas la date
  réelle de cet audit.
- Accès authentifié obtenu; l'inventaire runtime de la base `neo4j` a été
  obtenu en session `cypher-shell --access-mode read`.
- Résultat : 3 nœuds et 2 relations; voir `NEO4J_INVENTORY.md` pour les seuls
  détails structurels, sans valeurs de propriétés.
- La provenance reste inconnue; aucun writer applicatif n'est attribué.
- Le rôle exclusivement read-only n'est pas certifié par l'édition Community.
- **Interdiction :** aucune écriture Neo4j tant qu'un mécanisme permanent de
  lecture seule n'est pas défini.
- Dépendances : P0-2.
- Livrables : accès authentifié et inventaire read-only; établir l'origine
  seulement dans la mesure vérifiable.
- Critères d'acceptation : accès lecture reproductible; rapport séparant
  explicitement inventaire obtenu, provenance inconnue et privilèges non
  certifiés.
- Risques/interdictions : aucun accès d'écriture ni migration; préserver les
  données existantes.
- Données/tests : schéma runtime et résultats expurgés de toute donnée sensible.

**P1-2 — Comparer sans modification les deux stockages Qdrant**

- **Statut : audit exécuté le 15 octobre 2026 — `DIFFÉRENTS`.**
- Le stockage actif racine et le stockage secondaire ont des collections,
  structures, nombres de fichiers et tailles différents. Seul le stockage
  racine est relié à l'instance active observée; aucune seconde instance n'a
  été démarrée.
- Dépendances : P0-2, P0-3.
- Livrables : comparaison non destructive des collections, compteurs, hashes ou
  empreintes compatibles et paramètres disponibles.
- Critères d'acceptation : le rapport distingue identités prouvées, différences
  et incertitudes; aucune conclusion d'équivalence sans preuve.
- Risques/interdictions : ne pas fusionner, déplacer, réinitialiser ou supprimer
  le stockage secondaire.
- Données/tests : instantanés ou exports read-only de l'instance active et
  artefacts accessibles du stockage inactif.

**P1-3 — Évaluer les quatre variantes `agent_skills`**

- **Statut : audit technique exécuté le 15 octobre 2026.**
- Les quatre variantes sont des doublons fonctionnels probables, non stricts;
  leurs règles ne présentent pas de contradiction observée. Les métadonnées
  disponibles ne justifient pas un choix canonique sans risque de perte
  d'information.
- Dépendances : P0-2, P1-2.
- Livrables : comparaison fonctionnelle, provenance/version si disponible,
  cas d'usage et risques de régression pour chacune.
- Critères d'acceptation : chaque variante est décrite séparément; la
  redondance supposée est confirmée ou infirmée par des éléments concrets.
- Risques/interdictions : aucune fusion ou suppression dans cette phase.
- Données/tests : contenus exportés en lecture seule et corpus de cas
  d'extraction anonymisés.

**P1-4 — Décider le registre canonique des skills**

- **Statut : BLOQUÉ.** Aucun skill canonique ne peut être désigné
  immédiatement sans risque de perte d'information.
- Dépendances : P1-3; revue des stockages et formats locaux existants.
- Livrables : décision validée sur le format, l'identifiant stable, les
  versions, états, transitions, historique et autorité locale du registre.
- Critères d'acceptation : les états `CANDIDATE`, `TESTING`, `APPROVED`,
  `PUBLISHED`, `REJECTED`, `RETIRED` et les permissions Oracle/local sont
  définis sans ambiguïté.
- Risques/interdictions : ne pas déclarer `skills_catalog` existant ou actif;
  ne pas publier un candidat.
- Données/tests : scénarios de création, revue, publication, rejet et retrait.

**P1-5 — Résoudre le contrat `InvoiceSchema`**

- Dépendances : P0-1; inventorier les appelants concernés.
- Livrables : arbitrage métier/technique des champs, types, contraintes,
  compatibilité et stratégie de migration des appelants.
- Critères d'acceptation : contrat documenté et tests de validation du JSON
  retenu; décision explicite sur sa relation aux deux `InvoiceData`.
- Risques/interdictions : ne pas remplacer `InvoiceSchema` par `InvoiceData`
  ni raccorder les appelants bloqués avant l'arbitrage.
- Données/tests : exemples anonymisés facture, avoir, lignes multiples, devise
  et valeurs absentes/ambiguës.

**P1-6 — Résoudre les décisions bancaires bloquantes**

- Dépendances : P0-1; revue métier, comptable et bancaire.
- Livrables : décisions signées sur propriété des comptes, objets
  rapprochables, signe et devise des allocations, conversions, tolérances,
  écarts/frais, rôles des parties, annulations et remises de chèques.
- Critères d'acceptation : chaque point de la liste bloquée de
  `BANKING_RELATIONS_REVIEW.md` a une réponse ou reste explicitement bloqué
  avec propriétaire et condition de résolution.
- Risques/interdictions : ne pas coder de règles supposées; ne pas inventer de
  détails de remise de chèque.
- Données/tests : exemples de mouvements, factures/avoirs et scénarios multi-
  comptes/multi-sociétés représentatifs et anonymisés.

### Ticket différé — Web / scraping / Tor

**WEB-TOR-1 — Sécuriser et restaurer la recherche Web/Tor**

- **Statut : REPORTÉ.** Ce ticket ne modifie pas l'ordre des priorités en
  cours et ne bloque pas P1-4, les décisions bancaires ou `InvoiceSchema`.
- **Dépendances avant implémentation :** validation anti-SSRF, liste blanche
  réseau, contrôle des redirections, authentification de l'API, configuration
  Tor explicite et tests isolés.
- **Périmètre documentaire :** préserver l'état du `ResearchAgent`, de
  `POST /api/scrape` et du wrapper MCP; le stub Tor n'est pas une fonctionnalité
  opérationnelle.
- **Interdictions avant levée du report :** aucune installation, correction,
  activation Tor ou requête vers une adresse `.onion`.

## 5. Phase 2 — MasterData et identifiants stables

**Statut initial :** **CIBLE**; classeurs et raccordements non tous vérifiés.
**Dépendances :** résultats applicables de P1-1 et P1-6.

**P2-1 — Figer référentiels et clés stables**

- Périmètre : sociétés, comptes bancaires, fournisseurs, clients, alias
  bancaires et catégories.
- Dépendances : P1-1, décisions de P1-6.
- Livrables : dictionnaire de données, identifiants stables, contraintes
  d'unicité et règles d'identité/provenance.
- Critères d'acceptation : les collisions, alias, changements de nom et
  références source sont traités explicitement; comptes et sociétés restent
  reliés selon la décision approuvée.
- Risques/interdictions : ne pas fusionner les entités sur une simple
  similarité vectorielle; ne pas créer de références métier fictives.
- Données/tests : cas de doublons, alias, sociétés multiples et comptes
  partageant des références source.

**P2-2 — Définir l'interface Excel MasterData et sa validation locale**

- Dépendances : P2-1.
- Livrables : contrat des feuilles/colonnes, contrôles d'import, erreurs
  actionnables et procédure de validation Excel vers référentiels locaux.
- Critères d'acceptation : import à blanc identifiable, erreurs et changements
  traçables; aucune écriture locale avant validation.
- Risques/interdictions : ne pas considérer Excel comme une base synchronisée
  automatiquement; ne pas appliquer les modifications ambiguës.
- Données/tests : classeurs de validation sans données personnelles réelles,
  champs manquants, doublons et valeurs invalides.

## 6. Phase 3 — Contrats des trois tableaux

**Statut initial :** **CIBLE**; aucun tableau de bout en bout n'est attesté.
**Dépendances :** P1-5, P1-6, P2-1.

**P3-1 — Spécifier les vues et transitions**

- Périmètre : tableau général des pièces comptables; tableau de validation
  locale avant ventilation; tableau général de rapprochement bancaire.
- Dépendances : P2-1, contrats documentaires et bancaires approuvés.
- Livrables : champs, filtres, états, droits, actions et transitions, incluant
  factures/avoirs clients et fournisseurs et frais.
- Critères d'acceptation : chaque donnée affichée a une provenance; chaque
  action est auditable; une proposition Oracle ne peut pas contourner la
  validation locale.
- Risques/interdictions : ne pas présenter une extraction candidate comme
  écriture comptable publiée.
- Données/tests : parcours d'acceptation, correction, rejet, revue et
  publication.

**P3-2 — Figer conventions de signe, corrections et audit**

- Dépendances : P3-1, P1-6.
- Livrables : conventions de signe pour factures, avoirs, frais, mouvements et
  allocations; modèle d'audit des valeurs avant/après, acteur, motif et heure.
- Critères d'acceptation : scénarios de débits/crédits, paiements partiels,
  avoirs et corrections donnent des soldes attendus; toute incertitude conduit
  à revue humaine.
- Risques/interdictions : pas de montant flottant imprécis, correction
  silencieuse ni seuil non validé.
- Données/tests : jeux comptables anonymisés et cas de signe inversé.

## 7. Phase 4 — Convergence du pipeline facture

**Statut initial :** webhook actif minimal; autres composants **PARTIELS**,
**NON RACCORDE** ou **BLOQUÉS**.
**Dépendances :** P0-5, P1-5, P2-1, P3-1.

**P4-1 — Raccorder dans l'ordre validé**

1. `IngestionManager` et réception/idempotence de la pièce.
2. `DocumentProfiler`.
3. `RoutingEngine`.
4. Extraction OCR/Vision adaptée au document.
5. Validation au contrat de schéma décidé; `InvoiceSchema` demeure gelé
   jusqu'à clôture de P1-5.
6. `InvoiceReconciliationEngine`.
7. `TaxClassificationEngine`.
8. Résolution locale de société et de tiers par les référentiels.
9. Application de skills publiés uniquement.
10. Staging, validation locale puis persistance officielle locale.

- Dépendances : étape précédente, contrats de P1/P2 et interfaces de P3.
- Livrables : composants raccordés par étapes dans LangGraph; traces de preuve
  et états d'erreur/revue.
- Critères d'acceptation : tests d'intégration démontrent l'ordre, les
  entrées/sorties et les refus; aucun composant n'est qualifié d'actif avant
  preuve sur flux réel contrôlé.
- Risques/interdictions : ne pas raccorder `InvoiceSchema` gelé ni remplacer
  son contrat par `InvoiceData`; pas de publication automatique d'une
  extraction incertaine.
- Données/tests : factures, avoirs, frais, images de qualité variable,
  multi-lignes et anomalies fiscales anonymisées.

**P4-2 — Valider persistance et reprises du pipeline facture**

- Dépendances : P4-1, P3-2.
- Livrables : preuves, provenance, clés d'idempotence, reprise sur erreur et
  gestion des doublons.
- Critères d'acceptation : retransmettre une même pièce ne crée ni doublon ni
  double écriture; échec intermédiaire reste visible et reprenable.
- Risques/interdictions : pas d'écriture directe aux stockages non validée ni
  d'indexation présentée comme comptabilisation.
- Données/tests : coupures simulées entre extraction, validation et
  persistance.

## 8. Phase 5 — Pipeline bancaire

**Statut initial :** branche `pending_implementation`; schéma bancaire vide;
pipeline bancaire **FUTUR**.
**Dépendances :** P1-6, P2-1, P3-1 et P3-2.

**P5-1 — Arrêter le contrat BankingRecord et les métadonnées de relevé**

- Dépendances : P1-6.
- Livrables : schéma versionné `BankingRecord`, contrat de relevé, provenance,
  compte et montants décimaux signés.
- Critères d'acceptation : date transactionnelle/inscription, devise, montant,
  références source, identifiant stable et relation compte/société sont testés.
- Risques/interdictions : ne pas déclarer le schéma conceptuel comme existant;
  ne pas inventer les champs absents de la source.
- Données/tests : relevés représentatifs anonymisés avec soldes contrôlables.

**P5-2 — Implémenter des parseurs distincts**

- Périmètre : OFX avec `ofxparse`; MT940 avec `mt940`; CSV séparé; PDF séparé.
- Dépendances : P5-1.
- Livrables : parseurs propres à chaque format, détection explicite, erreurs
  et normalisation vers BankingRecord.
- Critères d'acceptation : OFX et MT940 ont chacun des tests dédiés; CSV/PDF
  restent des voies distinctes et ne sont acceptés qu'après définition de
  leurs mappings.
- Risques/interdictions : aucun fallback arbitraire entre formats; ne pas
  traiter un PDF bancaire avec le pipeline facture par défaut.
- Données/tests : jeux anonymisés valides, incomplets, malformés et de
  versions bancaires différentes.

**P5-3 — Construire comptes, mouvements et allocations N↔N**

- Dépendances : P5-1, P5-2, P2-1 et décisions de P1-6.
- Livrables : relations compte/société, mouvements, allocations auditées et
  états dérivés; prise en charge des paiements partiels et groupés.
- Critères d'acceptation : sommes, soldes ouverts, devises et dépassements
  suivent les règles approuvées; une annulation préserve l'historique.
- Risques/interdictions : pas de champs singuliers comme représentation
  canonique d'une relation N↔N; pas de conversion/tolérance non décidée.
- Données/tests : un-à-un, un-à-plusieurs, plusieurs-à-un, N↔N, frais et écarts.

**P5-4 — Couvrir opérations et validation bancaire**

- Périmètre : chèques, remises de chèques, virements, prélèvements, LCR,
  retours/contre-passations, contrôles de soldes et validation locale.
- Dépendances : P5-3; décision explicite sur l'objet et la composition des
  remises.
- Livrables : types source/pratiques effectivement présents, contrôle
  ouverture + mouvements = clôture quand les données le permettent, parcours
  de revue et publication locale.
- Critères d'acceptation : chaque type est testé par des preuves source; les
  remises agrégées ne supposent pas des chèques constituants absents; les
  retours restent auditables.
- Risques/interdictions : ne pas inventer remises, parties ou statut; ne pas
  promouvoir les propositions nocturnes sans validation.
- Données/tests : relevés multi-sociétés, paiements groupés/partiels, chèques,
  remises documentées et contre-passations.

## 9. Phase 6 — Mémoire et skills

**Statut initial :** stockages hétérogènes; registre canonique, SkillOpt et
gouvernance **CIBLES**.
**Dépendances :** P1-3, P1-4, P2-1 et P3-2.

**P6-1 — Construire le registre canonique local et le cycle de vie**

- Livrables : identifiants, versions, stockage de définitions et transitions
  `CANDIDATE`, `TESTING`, `APPROVED`, `PUBLISHED`, `REJECTED`, `RETIRED`.
- Critères d'acceptation : seules les décisions locales autorisées peuvent
  publier; historique d'état/version récupérable.
- Risques/interdictions : aucun candidat Oracle ne modifie le registre publié.
- Données/tests : transitions autorisées/interdites, rollback logique et
  versions concurrentes.

**P6-2 — Relier trajectoires, métriques et skills**

- Dépendances : P6-1, audit P1-3.
- Livrables : liens traçables entre résultat, correction humaine, skill,
  version, essai et métrique, en distinguant mesure et définition de skill.
- Critères d'acceptation : les métriques ne sont pas interprétées comme skills
  et une réutilisation est prouvée par un lien explicite.
- Risques/interdictions : ne pas attribuer rétrospectivement une création ou
  réutilisation non enregistrée.
- Données/tests : trajectoires annotées et cas négatifs sans skill associé.

**P6-3 — Intégrer SkillOpt et superviser le bootstrap**

- Dépendances : P6-1, P6-2.
- Livrables : tests d'optimisation et de non-régression; supervision Claude au
  bootstrap puis partielle/sur exception suivant maturité documentée.
- Critères d'acceptation : un skill ne passe pas `APPROVED` sans résultat de
  non-régression et approbation locale; les exceptions sont bloquantes.
- Risques/interdictions : aucune promotion automatique; confidentialité des
  exemples et corrections.
- Données/tests : corpus de cas approuvés, limites et cas de régression.

**P6-4 — Converger les quatre variantes existantes**

- Dépendances : P1-3, P6-1, P6-3.
- Livrables : plan versionné de conservation, adaptation ou retrait après
  approbation; uniquement les versions publiées sont ensuite distribuables à
  Oracle.
- Critères d'acceptation : chaque variante a une décision et un test de
  non-régression avant toute migration; historique et provenance sont
  conservés.
- Risques/interdictions : aucune suppression/fusion pendant l'audit; aucune
  migration physique Qdrant non autorisée.
- Données/tests : quatre variantes originales sauvegardées et cas de référence.

## 10. Phase 7 — Staging et synchronisation Oracle

**Statut initial :** règles validées, implémentation **FUTURE**.
**Dépendances :** P0-2, P2-1, P3-1, P6-1 et contrats de sécurité.

**P7-1 — Implémenter le manifeste Oracle → local**

- Livrables : enveloppe `manifest_id`, `version`, `origine`, hashes, schéma,
  modèle d'embedding et dimension; une entrée par document avec chunks, texte
  extrait, métadonnées et artefacts.
- Critères d'acceptation : vérification de l'intégrité et de compatibilité
  avant traitement; manifeste versionné reproductible.
- Risques/interdictions : pas de copie de `qdrant_storage` ou `neo4j_data`;
  aucun contenu publié par le seul fait d'être reçu.
- Données/tests : manifeste nominal, incomplet, altéré, doublon et version
  incompatible.

**P7-2 — Construire staging, statuts et ré-embedding**

- Dépendances : P7-1, modèle local d'embedding choisi et testé.
- Livrables : staging réversible et statuts `PENDING`, `ACCEPTED`,
  `ACCEPTED_REEMBED`, `DUPLICATE_SKIPPED`, `HUMAN_REVIEW_REQUIRED`, `REJECTED`,
  `PUBLISHED`, ainsi que `SYNC_CONFLICT`.
- Critères d'acceptation : incompatibilité d'embedding mène au ré-embedding
  local ou à la revue; publication impossible sans décision locale.
- Risques/interdictions : Oracle ne publie jamais; local `PUBLISHED` prévaut
  sur Oracle `CANDIDATE`; pas de `DELETE` automatique.
- Données/tests : hashes identiques/différents, changement de schéma et de
  dimension d'embedding.

**P7-3 — Transporter, reprendre et appliquer de façon idempotente**

- Dépendances : P7-1, P7-2.
- Livrables : transport de lots, reprise après coupure, déduplication et
  interface locale de résolution des conflits.
- Critères d'acceptation : une retransmission du même lot ne crée pas de
  doublon; conflits restent bloqués sous `SYNC_CONFLICT`; une coupure ne
  contourne pas les étapes de validation.
- Risques/interdictions : pas de synchronisation physique; pas de suppression
  de données lors d'une reprise.
- Données/tests : coupures réseau à chaque étape, retransmissions, ordre
  différent et conflits de versions.

**P7-4 — Appliquer la synchronisation logique**

- Dépendances : P7-3, P1-1, P2-1.
- Livrables : `MERGE` Neo4j sur identifiants métier stables et opérations
  Qdrant sur objets logiques; snapshot réservé à une migration explicitement
  contrôlée.
- Critères d'acceptation : résultats traçables et réversibles selon la
  procédure approuvée; aucune écriture n'atteint les données officielles avant
  validation locale.
- Risques/interdictions : pas de copie ou fusion de dossiers de base; aucun
  `DELETE`.
- Données/tests : environnement de préproduction restaurable et lots
  d'essai dédiés.

## 11. Phase 8 — Indexation documentaire Oracle

**Statut initial :** **CIBLE/FUTUR**; pipeline Oracle non attesté.
**Dépendances :** P7-1 à P7-3, politique de confidentialité et modèle
d'embedding vérifié.

**P8-1 — Ingestionner les corpus documentaires autorisés**

- Périmètre : documentation technique, fiches produits, catalogues, tarifs,
  contrats, anciennes factures, emails et OneDrive.
- Livrables : sources autorisées, provenance, hashes, texte extrait, chunks,
  métadonnées, modèle et dimension d'embedding dans le staging Oracle.
- Critères d'acceptation : chaque document est retraçable à sa source; accès,
  droits et exclusions sont appliqués.
- Risques/interdictions : ne pas publier automatiquement une connaissance
  extraite; ne pas indexer une source sans autorisation.
- Données/tests : corpus approuvé et documents avec scans, doublons ou contenu
  incomplet.

**P8-2 — Préparer l'index Oracle et publier localement**

- Dépendances : P8-1, P7-2, P7-3.
- Livrables : index de similarité/RAG côté Oracle, transfert logique et
  publication contrôlée des connaissances acceptées côté local.
- Critères d'acceptation : incompatibilité vectorielle déclenche ré-embedding
  local ou revue; le résultat publié respecte la version locale prioritaire.
- Risques/interdictions : aucun index Oracle n'est une source de vérité;
  aucune copie directe des fichiers internes Qdrant.
- Données/tests : pertinence, doublons, provenance, conflit et publication
  idempotente.

## 12. Phase 9 — Hermes, Telegram et interfaces

**Statut initial :** **CIBLE**; Hermes et Telegram non attestés dans le dépôt.
**Dépendances :** contrats des workflows, validations locales et périmètres de
permission.

**P9-1 — Intégrer Hermes Desktop et ses outils DSH-Core**

- Livrables : interface générale locale et encapsulation des outils et
  services DSH-Core.
- Critères d'acceptation : Hermes ne duplique aucune règle métier et toutes
  les écritures sensibles passent par les workflows locaux.
- Risques/interdictions : ne pas déplacer la source de vérité vers Hermes.
- Données/tests : autorisations, erreurs de service et mode hors Internet.

**P9-2 — Intégrer Hermes Oracle et Telegram**

- Livrables : satellite Oracle 24/7 en staging; Telegram mobile sur exactement
  cinq canaux : `PRESSE`, `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL`, `DOCUMENTS`.
- Critères d'acceptation : permissions local/Oracle distinctes; Oracle ne
  dispose d'aucune route de publication locale; chaque canal respecte son
  périmètre d'accès.
- Risques/interdictions : aucune élévation de privilège par interface et
  aucune publication implicite.
- Données/tests : tests d'accès positifs/négatifs, indisponibilité et journal
  d'audit.

**P9-3 — Définir la place des autres interfaces**

- Livrables : rôles documentés de VS Code + Copilot (développement/audit),
  Gemini (mémoire de sécurité et sous-traitant), AnythingLLM (RAG intensif
  facultatif).
- Critères d'acceptation : aucune de ces interfaces n'est présentée comme
  source de vérité ou autorité de publication.
- Risques/interdictions : respecter confidentialité et moindre privilège.
- Données/tests : revue de permissions et scénario d'indisponibilité externe.

## 13. Phase 10 — Modèles par rôle

**Statut initial :** Ollama Vision actif sur le flux facture identifié; la
politique complète par rôle reste **CIBLE**.
**Dépendances :** P0-4, P4-1, P7-2 et validation de confidentialité.

**P10-1 — Sélectionner et valider les rôles de modèles**

- Rôles : vision, raisonnement de base, expert et superviseur.
- Livrables : registre de déploiement modèle/version/capacités/limites et
  affectation explicite par rôle.
- Critères d'acceptation : disponibilité, vision, appels d'outils, JSON
  structuré, contexte, confidentialité et comportement d'erreur sont testés.
- Risques/interdictions : modèle OpenRouter gratuit facultatif, jamais garanti
  permanent; ne pas envoyer de données hors poste sans autorisation.
- Données/tests : prompts/cas anonymisés par rôle, limites de contexte et
  pannes fournisseur.

**P10-2 — Garantir le fallback Ollama local**

- Dépendances : P10-1.
- Livrables : modèle local validé pour les rôles pouvant utiliser un service
  distant et procédure de bascule.
- Critères d'acceptation : coupure Internet conserve une voie locale testée;
  capacité manquante provoque une erreur explicite, pas un faux succès.
- Risques/interdictions : aucun `AI_MODE` global ne doit masquer une
  incompatibilité par rôle.
- Données/tests : coupure réseau, indisponibilité du modèle et réponse
  structurée invalide.

## 14. Phase 11 — Tests de bout en bout et mise en production

**Statut initial :** **FUTUR**; aucun ensemble complet de validation n'est
attesté.
**Dépendances :** contrats et raccordements des phases précédentes.

**P11-1 — Qualifier les parcours métiers**

- Couverture : factures, banque, multi-sociétés, paiements partiels/groupés,
  avoirs, chèques/remises lorsque décidés, contrôles de soldes et revue locale.
- Livrables : suites E2E, résultats reproductibles, anomalies classées et
  décision de mise en service.
- Critères d'acceptation : états, montants, signes, provenance et écritures
  attendus sont corrects; incertitudes passent en revue humaine.
- Risques/interdictions : pas de données financières réelles non anonymisées
  dans les environnements de test.
- Données/tests : jeux anonymisés et cas limites issus des décisions métier.

**P11-2 — Qualifier Oracle, les skills et la résilience**

- Couverture : staging, coupures/réprise réseau, doublons, conflits,
  idempotence, cycle de vie des skills et non-régression.
- Livrables : rapport d'essais, procédures de rollback et critères de
  surveillance.
- Critères d'acceptation : aucune publication Oracle directe; conflits
  bloqués; aucune reprise en double; aucun skill publié sans approbation.
- Risques/interdictions : ne pas exécuter les essais destructifs sur les bases
  actives.
- Données/tests : manifeste d'essai, versions concurrentes, modèle/embedding
  incompatible et panne pendant chaque transition.

**P11-3 — Qualifier le fonctionnement 100 % local et la mise en production**

- Dépendances : P11-1, P11-2, P10-2.
- Livrables : résultats de test hors Internet, checklist de sécurité,
  validation humaine finale et plan de retour arrière.
- Critères d'acceptation : parcours locaux supportés réussissent sans réseau;
  fonctions distantes indisponibles sont signalées; approbation métier et
  technique enregistrée avant activation.
- Risques/interdictions : ne pas activer une cible dont les contrôles ou
  procédures de reprise ne sont pas vérifiés.
- Données/tests : environnement contrôlé de préproduction et simulation de
  dépendances indisponibles.

## 15. Matrice des livrables

| Phase | Statut initial | Dépendances majeures | Livrables et critères d'acceptation | Risques et interdictions | Données/tests nécessaires |
|---|---|---|---|---|---|
| 0 — Preuve/sauvegarde | CIBLE | Aucune; accès autorisés | État Git/code/documentation et snapshots vérifiés; tests ciblés des trois corrections | Pas d'écrasement, secret exposé ou suppression; conserver Qdrant secondaire | Statut Git, empreintes, sauvegardes restaurables, fixtures non sensibles |
| 1 — Contrats bloqués | PARTIEL/BLOQUÉ | Phase 0 | P1-1 audit Neo4j exécuté; P1-2 stockages Qdrant comparés (`DIFFÉRENTS`); P1-3 audit technique des skills exécuté; P1-4 décision du registre canonique bloquée, ainsi que InvoiceSchema et banque | Aucun write, merge, suppression ou schéma supposé | Provenance et protections, arbitrage canonique des skills, exemples facture/relevé anonymisés, décisions métier |
| 2 — MasterData | CIBLE | P1 et décisions compte/société | Référentiels, clés stables, unicité, Excel vers validation locale | Pas d'identité déduite par similarité seule | Cas alias, doublons, multi-sociétés |
| 3 — Tableaux | CIBLE | P1, P2 | Contrats et transitions des trois tableaux, conventions de signe et audit | Pas de ventilation incertaine ni publication implicite | Parcours de validation/correction/rejet |
| 4 — Facture | PARTIEL/NON RACCORDE/BLOQUÉ | P0, P1-5, P2, P3 | Pipeline LangGraph vérifié de l'ingestion à la persistance locale | Ne pas remplacer InvoiceSchema par InvoiceData | Factures, avoirs, frais, images et anomalies anonymisés |
| 5 — Banque | FUTUR/BLOQUÉ | P1-6, P2, P3 | BankingRecord, parseurs distincts, allocations N↔N, contrôles et revue locale | Pas de règle de signe/devise non décidée; pas d'invention de remises | OFX, MT940, CSV/PDF selon mapping, paiements partiels/groupés |
| 6 — Mémoire/skills | CIBLE/PARTIEL | P1-3/P1-4, P2/P3 | Registre local, versions, trajectoires, SkillOpt, non-régression et distribution contrôlée | Aucune promotion automatique ou fusion avant audit | Variantes sauvegardées, cas skills approuvés et régressions |
| 7 — Synchronisation | CIBLE/FUTUR | P0, P2, P3, P6 | Manifeste, staging, statuts, transport, conflits, reprise/idempotence | Pas de copie physique, DELETE ou écriture Oracle locale directe | Lots valides/altérés/doublons, coupures et incompatibilités |
| 8 — Indexation Oracle | CIBLE/FUTUR | P7, confidentialité | Corpus autorisé, chunks et index, ré-embedding/local publication | Pas de document publié sans validation ni droits | Documentation, produits, contrats, factures et sources autorisées |
| 9 — Interfaces | CIBLE/FUTUR | Workflows et permissions | Hermes Desktop/Oracle, cinq canaux Telegram, interfaces auxiliaires | Hermes ne remplace pas DSH-Core; droits séparés | Tests permissions, pannes et mode local |
| 10 — Modèles | PARTIEL/CIBLE | P0-4, P4, P7 | Modèles par rôle, compatibilité, confidentialité et fallback Ollama | Aucun modèle gratuit réputé permanent; aucun envoi non autorisé | Tests vision, outils, contexte, JSON et panne réseau |
| 11 — E2E/production | FUTUR | Phases précédentes | Tests facture/banque/skills/conflits/local, validation humaine et retour arrière | Pas de mise en production sans preuves ni approbation | Données anonymisées et environnement de préproduction restaurable |

Chaque phase conserve une trace de décision et de résultat. Un statut ne passe
à **EXISTANT** ou **ACTIF** qu'après preuve d'implémentation, de test et de
raccordement au flux attendu.

## 16. Prochain sprint concret

Les tickets ci-dessous sont l'ordre de travail proposé. Les points nécessitant
une décision métier sont marqués **BLOQUÉ** et ne doivent pas être contournés
par une hypothèse technique.

1. **Tester les trois corrections déjà appliquées — P0-5.**
   Dépendances : sauvegarde/référence Git et environnement de test.
   Livrable : tests ciblés et résultat de chaque correction. Acceptation :
   les trois contrats appelant/appelé passent sans écriture en base réelle.
2. **P1-1 — Audit Neo4j en lecture exécuté le 15 octobre (`PARTIAL_READ_ONLY`).**
   Accès authentifié obtenu et inventaire runtime de la base `neo4j` obtenu en
   mode `read`; provenance inconnue et rôle exclusivement read-only non
   certifié. Ne faire aucune écriture avant définition d'un mécanisme permanent
   de lecture seule.
3. **P1-2 — Audit comparatif non destructif des deux stockages Qdrant exécuté :
   `DIFFÉRENTS`.** Seul le stockage racine est relié à l'instance active
   observée; cinq collections lui sont exclusives et deux le sont au stockage
   secondaire. Aucun changement des stockages.
4. **P1-3 — Audit technique des quatre variantes `agent_skills` exécuté.**
   Les entrées sont des doublons fonctionnels probables, mais non stricts;
   aucune contradiction de règle n'a été observée. Aucun skill canonique ne
   peut être choisi immédiatement sans risque de perte d'information.
   Aucune suppression, fusion ou promotion n'a eu lieu.
5. **P1-4 — Décider le registre canonique des skills : BLOQUÉ.**
   Dépendance : résultats P1-3. La décision locale doit établir le stockage,
   les versions, états, transitions et permissions; aucun choix canonique
   immédiat n'est validé. Maintenir les quatre variantes sans suppression ni
   fusion jusqu'à arbitrage.
6. **Résoudre les décisions bancaires bloquantes — P1-6.**
   Statut : **BLOQUÉ** jusqu'à arbitrage métier. Livrable : décisions sur
   propriété des comptes, objets rapprochables, allocations, devises,
   tolérances, rôles des parties et remises de chèques. Acceptation : chaque
   point est décidé ou explicitement maintenu bloqué avec responsable.
7. **Résoudre `InvoiceSchema` avant tout raccordement qui en dépend — P1-5.**
   Statut : **BLOQUÉ** jusqu'à arbitrage. Livrable : contrat vérifiable et
   exemples approuvés. Acceptation : validation de schéma testée; aucune
   substitution par `InvoiceData` non décidée.
8. **Figer les contrats des trois tableaux — P3-1/P3-2.**
   Dépendances : décisions P1 et identifiants MasterData de P2. Livrable :
   champs, statuts, signes, audit, corrections et validations humaines.
   Acceptation : parcours documentés pour pièces, pré-ventilation et
   rapprochement N↔N.
9. **Implémenter seulement ensuite les premiers schémas et services.**
   Dépendances : contrats métier approuvés, preuves/sauvegardes disponibles.
   Livrable : tranche minimale conforme à la priorité retenue. Acceptation :
   tests ciblés, aucune modification destructive des données, et statut
   d'implémentation prouvé avant extension au lot suivant.

**Condition de sortie du sprint :** ne commencer aucune migration, fusion,
suppression ni écriture officielle sur la base de résultats non validés. Les
décisions encore bloquées restent visibles; elles ne sont pas remplacées par
des valeurs par défaut ou des hypothèses.
