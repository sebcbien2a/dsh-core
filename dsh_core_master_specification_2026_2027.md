# DSH-Core — spécification technique et architecturale maîtresse V2

**État de référence : 20 octobre 2026**  
**Statut :** spécification de la vision fonctionnelle canonique, des décisions
validées comme cible et de l'état vérifié du dépôt.  
**Source canonique principale :** [dsh core complet.md](./dsh%20core%20complet.md)

Cette spécification ne transforme pas une architecture cible en fonctionnalité
existante. Les composants et flux sont qualifiés selon les statuts définis à la
section 2. Aucun secret, mot de passe ni clé API n'est documenté ici.

## Sommaire

1. [Objet, autorité et vision](#1-objet-autorité-et-vision)
2. [Statuts et règles de lecture](#2-statuts-et-règles-de-lecture)
3. [Responsabilités des composants](#3-responsabilités-des-composants)
4. [Architecture locale, Oracle et interfaces](#4-architecture-locale-oracle-et-interfaces)
5. [Contrats des trois tableaux de travail](#5-contrats-des-trois-tableaux-de-travail)
6. [Documents et pipeline facture](#6-documents-et-pipeline-facture)
7. [Contrats facture et gel d'InvoiceSchema](#7-contrats-facture-et-gel-dinvoiceschema)
8. [Architecture bancaire](#8-architecture-bancaire)
9. [MasterData et mémoires](#9-masterdata-et-mémoires)
10. [Skills, apprentissage et validation](#10-skills-apprentissage-et-validation)
11. [Modèles d'intelligence artificielle](#11-modèles-dintelligence-artificielle)
12. [Indexation documentaire et manifeste Oracle](#12-indexation-documentaire-et-manifeste-oracle)
13. [Staging, synchronisation et conflits](#13-staging-synchronisation-et-conflits)
14. [Sécurité, traçabilité et validation humaine](#14-sécurité-traçabilité-et-validation-humaine)
15. [État constaté au 20 octobre 2026](#15-état-constaté-au-20-octobre-2026)
16. [Ordre de réalisation et dépendances](#16-ordre-de-réalisation-et-dépendances)
17. [Architecture cible — diagramme](#17-architecture-cible--diagramme)
18. [Décisions bloquées et limites](#18-décisions-bloquées-et-limites)
19. [Sources de contrôle](#19-sources-de-contrôle)

## 1. Objet, autorité et vision

DSH-Core est une plateforme locale, souveraine, documentaire et comptable,
autonome dans ses traitements déterministes et assistés, auto-améliorante sous
contrôle, et multi-entreprises. Elle couvre la réception et la compréhension
des pièces, l'extraction vérifiable, les opérations comptables, la recherche
documentaire, la proposition de décisions et l'apprentissage à partir des
corrections validées.

La boucle fonctionnelle visée est :

```text
Observation
→ compréhension et extraction
→ contrôles déterministes et recherche de contexte
→ proposition explicable
→ validation humaine si requise
→ décision métier locale et traçable
→ skill candidat éventuel
→ test de non-régression
→ publication contrôlée
→ réutilisation déterministe
```

### Source de vérité

**DSH-Core local est l'unique source de vérité métier.** Les référentiels,
écritures officielles, décisions validées, allocations comptables et skills
publiés sont sous contrôle local. Ni un modèle, ni une mémoire vectorielle, ni
un service distant ne peuvent remplacer cette autorité.

**Hermes** fournit l'interface et l'encapsulation de DSH-Core. Il ne remplace
pas DSH-Core, ses services métier ni LangGraph. **LangGraph** est l'orchestrateur
métier unique de l'architecture cible; les routes ou agents existants ne sont
pas des orchestrateurs métier concurrents dans la cible.

L'autonomie se développe par étapes et par règles éprouvées. Un résultat
probable, une similarité ou une décision passée ne sont pas, seuls, une preuve
comptable ou juridique.

## 2. Statuts et règles de lecture

Chaque mention d'état dans cette spécification utilise l'une des catégories
suivantes :

- **EXISTANT** : code, service, donnée ou interface repéré dans le dépôt ou au
  runtime. La présence ne signifie pas que le composant est raccordé ou valide.
- **PARTIEL** : composant raccordé à une partie du flux, exercé par test, ou
  utilisable avec des limites significatives.
- **NON RACCORDE** : composant présent sans appelant du flux applicatif courant
  identifié.
- **CIBLE** : décision fonctionnelle ou architecture validée à construire.
  N'implique ni fichier présent ni déploiement.
- **BLOQUÉ** : contrat ou décision métier non arrêté; ne pas inventer de
  comportement ni figer l'implémentation concernée.
- **FUTUR** : capacité souhaitée ou étape ultérieure non active à la date de
  référence.

Les statuts **ACTIF**, **PARTIELLEMENT ACTIF**, **NON RACCORDE** et **OBSOLETE**
des audits sont traduits ici respectivement par **EXISTANT**,
**PARTIEL**, **NON RACCORDE** et, selon le cas, **NON RACCORDE**/**FUTUR**.
« Pipeline officiel » signifie le chemin existant le plus raccordé, pas le
flux cible complet ni une garantie de réussite en production.

Les comptages de bases et les états de service sont des observations ponctuelles
au 20 octobre 2026. Ils ne constituent pas des garanties permanentes.

## 3. Responsabilités des composants

| Composant | Responsabilité spécifiée | État au 20 octobre 2026 |
|---|---|---|
| DSH-Core local | Autorité métier, orchestration des flux, validation et publication officielle. | **CIBLE** dans son rôle unifié; plusieurs briques locales sont **EXISTANTES** ou **PARTIELLES**. |
| Hermes Desktop | Interface générale locale qui expose DSH-Core sans reprendre sa logique métier. | **CIBLE**; configuration Hermes non repérée dans le dépôt. |
| Hermes Oracle | Satellite disponible 24/7, capable de préparer des analyses et propositions dans un staging distant/réversible. | **CIBLE**; intégration non repérée. |
| Telegram | Interface mobile répartie en cinq canaux : `PRESSE`, `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL`, `DOCUMENTS`. | **CIBLE**; raccordement non attesté. |
| LangGraph | Orchestrateur métier unique de la cible; coordonne états, services, reprise et points de validation humaine. | **PARTIEL** : un graphe est présent, mais les nœuds observés sont simulés et ne forment pas l'orchestration métier complète. |
| APIs, n8n et connecteurs | Entrées/sorties et automatisations; ils transmettent les demandes au cœur sans devenir autorités métier concurrentes. | **EXISTANT/PARTIEL** selon les routes; les intégrations ne sont pas toutes reliées. |
| Modèles IA | Assistance à l'extraction, à la classification, à la recherche et aux propositions selon un rôle contrôlé. | Ollama Vision est **EXISTANT** dans le flux facture actif; le reste est variable ou **CIBLE**. |

Les services de domaine exécutent leurs responsabilités dans les workflows
LangGraph. Hermes n'abrite pas une seconde implémentation de ces règles. Les
connecteurs ne publient jamais directement des faits non validés dans les
registres locaux.

## 4. Architecture locale, Oracle et interfaces

### 4.1 Fonctionnement local

Le poste local héberge DSH-Core et demeure le seul lieu d'approbation et de
publication métier. Les fonctions locales disponibles doivent continuer sans
connexion Internet, sous réserve des modèles et dépendances déjà présents.
Une dépendance distante indisponible doit être signalée explicitement, sans
retour simulé donnant l'apparence d'un succès.

### 4.2 Traitements Oracle

Oracle peut, comme **CIBLE** :

- traiter les emails et les documents accessibles sur OneDrive;
- indexer les documents préparés à la recherche;
- analyser les pièces comptables;
- calculer et soumettre des propositions de rapprochement nocturne.

Oracle écrit uniquement dans un **staging réversible**. Les propositions ne
sont ni des écritures comptables ni des décisions publiées. Elles sont
transmises localement selon le manifeste versionné décrit aux sections 12 et
13. Toute validation définitive et toute promotion dans les données
officielles sont locales.

### 4.3 Interfaces validées comme cible

- **Hermes Desktop** : interface générale locale.
- **Telegram** : interface mobile; les cinq canaux sont exactement `PRESSE`,
  `RECHERCHE`, `COMPTABILITÉ`, `GÉNÉRAL`, `DOCUMENTS`.
- **VS Code + Copilot** : développement, tests, audit et maintenance du code;
  ce n'est pas une source de vérité d'exécution.
- **Gemini** : mémoire de sécurité du projet et sous-traitant pour les missions
  confiées; pas d'autorité de publication métier.
- **Claude** : superviseur/Teacher, en particulier pour le bootstrap d'un skill.
- **AnythingLLM** : interface facultative pour des usages intensifs de RAG; ne
  remplace pas DSH-Core, MasterData, Neo4j ou Qdrant.

La configuration Hermes, l'intégration Telegram et le satellite Oracle ne sont
pas constatés comme installés ou raccordés dans le dépôt audité.

## 5. Contrats des trois tableaux de travail

Les tableaux ci-dessous spécifient les vues métier validées comme **CIBLE**.
Ils ne sont pas des interfaces déjà livrées.

### 5.1 Tableau général des pièces comptables

**Objet :** suivre les pièces et leurs états, sans confondre un fichier source,
une extraction, une décision ou une écriture.

**Périmètre :**

- factures clients et avoirs clients;
- factures fournisseurs et avoirs fournisseurs;
- frais et justificatifs, y compris avance personnelle;
- pièces justificatives liées aux paiements ou rapprochements.

**Informations par pièce :**

- identifiant stable de pièce et hash du fichier source;
- origine/canal, emplacement ou référence du document, dates de réception;
- société concernée et état de résolution de cette identité;
- type de pièce et sens économique (facture, avoir, frais);
- tiers identifié ou libellé source non résolu;
- dates, devises et montants extraits, sans remplacer les valeurs de la pièce;
- contrôles réalisés, anomalies et erreurs bloquantes;
- modèle, schéma, skill/version éventuelle et preuves de décision;
- statut de traitement et références aux objets métier associés.

Les valeurs brutes, les valeurs normalisées et les corrections doivent être
distinguables. Le tableau ne valide pas lui-même l'extraction et ne déclenche
pas une ventilation sans contrôle prévu.

### 5.2 Tableau de validation locale avant ventilation

**Objet :** revue locale préalable à l'affectation comptable, à la ventilation
ou à toute écriture officielle.

**Informations et actions visibles :**

- données extraites face à la pièce source;
- société, type de pièce, fournisseur/client et catégorie proposés;
- montants HT, TVA, TTC et devise ainsi que le résultat des contrôles;
- éléments de preuve, score éventuel, modèle et version de skill utilisés;
- erreurs, incertitudes, conflits et explication de la proposition;
- décisions disponibles : accepter, corriger, rejeter ou demander une revue;
- acteur, horodatage, motif et valeur avant/après de chaque décision.

Une proposition Oracle reste en staging jusqu'à validation locale. Une
incertitude significative bloque la ventilation. Toute acceptation entraîne
une trace d'audit et peut alimenter un skill candidat, sans promouvoir ce
skill automatiquement.

### 5.3 Tableau général de rapprochement bancaire

**Objet :** suivre relevés, mouvements et allocations vers les documents
comptables.

**Informations visibles :**

- identifiant du relevé et du mouvement, hash/source et références bancaires;
- compte métier résolu et société propriétaire via le compte;
- date de transaction et date d'inscription si distincte;
- montant signé, devise, libellé et informations de paiement source;
- contrepartie brute et identité résolue, si vérifiée;
- allocations individuelles, documents cibles, montants et devises;
- état de chaque allocation, motif, score éventuel et historique d'approbation;
- montants alloués et solde restant, selon les règles comptables validées;
- cas non rapprochés, partiels, groupés, ambigus ou en conflit.

Un paiement partiel ou groupé est représenté par plusieurs allocations
distinctes. Une paire mouvement-document correspond à une allocation. Le
tableau n'utilise pas un unique champ de facture associée pour représenter une
relation N↔N.

### 5.4 Conventions de signe communes

- Les montants source d'une facture ou d'un avoir sont conservés tels
  qu'extraits, avec le type documentaire. L'effet sur la créance ou la dette
  dépend du type de pièce; la valeur source n'est pas silencieusement inversée.
- Dans `BankingRecord`, un débit/sortie est négatif et un crédit/entrée est
  positif. Le signe ou statut de source est conservé pour audit.
- Le montant affecté à une allocation doit être cohérent avec le mouvement,
  le document, les devises et les règles de frais/écarts à définir.
- Les sommes et calculs monétaires utilisent une précision décimale exacte,
  pas une approximation binaire en virgule flottante.
- Une donnée inconnue n'est jamais inventée pour équilibrer une pièce ou un
  rapprochement.

## 6. Documents et pipeline facture

### 6.1 Chemin facture observé

Le seul chemin de réception facture reliant une route HTTP à un agent réel
identifié est :

```text
n8n_api.py — POST /api/v1/ingest
→ agents.invoice_agent.InvoiceAgent.process_invoice
→ traitement Vision via Ollama
→ JSON validé avec l'InvoiceData locale de cet agent
→ réponse à n8n
```

Ce chemin est **EXISTANT**, mais minimal. Il ne persiste pas la facture dans
Qdrant/Neo4j et ne raccorde pas le profiler, les moteurs de contrôle métier ou
les skills facture.

### 6.2 Composants et statut

| Composant | Statut | Portée observée |
|---|---|---|
| `n8n_api.py` — `POST /api/v1/ingest` | **EXISTANT** | Appelle l'agent facture actif. |
| `agents/invoice_agent.py::InvoiceAgent` | **EXISTANT** | Vision/Ollama, extraction JSON et validation avec sa classe `InvoiceData` locale. |
| `services/ingestion_manager.py::IngestionManager` | **PARTIEL** | Exercé par test, non appelé par le webhook facture; la branche bancaire reste `pending_implementation`. |
| `DocumentProfiler` | **PARTIEL** | Présent et testé, non raccordé au webhook facture. |
| `RoutingEngine` | **PARTIEL** | Présent et testé avec le profiler; non appelé par le webhook actif. |
| `BusinessExtractionManager` | **PARTIEL** | Utilisé dans des tests, non raccordé au webhook. |
| `InvoiceReconciliationEngine` | **PARTIEL** | Appelé par un script/test, non raccordé au webhook actif. |
| `TaxClassificationEngine` | **PARTIEL** | Appelé par un script/test, non raccordé au webhook actif. |
| `schemas/invoice_schema.py::InvoiceData` | **PARTIEL** | Modèle plat importé par des services/tests, distinct de l'`InvoiceData` locale de l'agent. |
| `InvoiceSkillCreator` / `InvoiceSkillApplier` | **PARTIEL** | Exercés séparément, non appelés par le webhook actif. |
| `services/extraction_manager.py::ExtractionManager` | **NON RACCORDE / BLOQUÉ** | Non appelé par le webhook; importe le symbole absent `InvoiceSchema`. |
| `invoice_agent.py` à la racine | **NON RACCORDE** | Voie distincte de l'agent actif; son import de `InvoiceSchema` absent la bloque. |
| Graphe facture backend | **PARTIEL** | Route alternative et étapes d'extraction/validation simulées. |
| `InvoiceIndexer`, services embedding/similarity et writers facture Neo4j | **NON RACCORDE** | Aucun raccordement au webhook actif repéré. |

### 6.3 Pipeline cible

Le pipeline fonctionnel cible doit coordonner :

1. réception, attribution d'une origine et détection de type;
2. profilage documentaire par `DocumentProfiler`;
3. routage par `RoutingEngine` vers le traitement adapté;
4. extraction texte, tableau ou image par les outils disponibles et validés;
5. validation de la structure et conservation de la provenance;
6. contrôle arithmétique par `InvoiceReconciliationEngine`;
7. qualification fiscale par `TaxClassificationEngine`;
8. résolution du tiers et de la société avec les référentiels, sans déduire une
   identité non prouvée;
9. proposition de catégorie/ventilation et application éventuelle d'un skill
   publié;
10. staging, validation locale lorsque requise et publication contrôlée vers
    les objets métier officiels.

Ce pipeline complet est **CIBLE/FUTUR** et dépend de contrats compatibles. Les
étapes 2 à 9 ne doivent pas être annoncées comme exécutées dans le webhook
actuel. Aucune utilisation de PyMuPDF, Docling, OCR, Vision ou NuExtract n'est
attribuée au webhook au-delà de ce qui est constaté dans l'agent actif.

### 6.4 Corrections déjà appliquées

Les trois corrections relevées au journal sont :

1. `api.py` appelle `router.route_prompt` au lieu du symbole absent
   `router_intelligent`, avec l'indicateur d'image attendu.
2. `agents/research_agent.py` appelle
   `SkillRouter.retrieve_skill_for_task` et consomme son résultat
   `str | None` comme chaîne optionnelle.
3. `services/ingestion_manager.py` renseigne l'erreur depuis le champ
   `message` renvoyé par l'agent, plutôt que depuis `last_error`.

Elles corrigent ces raccordements; elles ne valident pas le pipeline cible et
ne modifient pas le contrat `InvoiceSchema`.

## 7. Contrats facture et gel d'InvoiceSchema

### 7.1 État prouvé

Aucune déclaration de classe `InvoiceSchema` n'a été retrouvée dans les
sources auditées. `schemas/invoice_schema.py` expose une classe `InvoiceData`
plate. `agents/invoice_agent.py` définit une autre classe `InvoiceData` locale,
elle aussi de forme différente. Les deux classes ne sont pas interchangeables
par leur seul nom.

Des prompts et exemples décrivent un format imbriqué comprenant notamment :

- racine : `numero_facture`, `date_emission`, `fournisseur`, `client`,
  `lignes`, `total_ht`, `montant_tva`, `total_ttc`, `devise`;
- fournisseur/client : objet comportant `nom`;
- ligne : `description`, `quantite`, `prix_unitaire_ht`, `total_ht`,
  `taux_tva`.

Ces exemples sont des indices du JSON demandé par certains appelants. Ils ne
prouvent pas les annotations Python, champs obligatoires, contraintes,
validateurs, valeurs par défaut ni règles arithmétiques d'un modèle absent.

### 7.2 Décision

`InvoiceSchema` est **BLOQUÉ/GELÉ**. Ne pas le recréer par supposition, ne pas
le remplacer par `InvoiceData`, ne pas renommer les modèles, et ne pas déclarer
les contrats compatibles sans arbitrage et validation explicite du JSON de
bout en bout. Le modèle exact reste à établir avant de raccorder les
gestionnaires qui le demandent.

## 8. Architecture bancaire

### 8.1 Entrée et formats

`IngestionManager` reconnaît `AssetType.BANK_STATEMENT`, mais retourne
actuellement `pending_implementation` avec la destination logique
`banking_bus`. Aucun BankingAgent, BankingRecord exécutable, ledger ni graphe
de rapprochement bancaire n'est raccordé. `schemas/bank_statement_schema.py`
est vide.

| Format | Traitement spécifié | État |
|---|---|---|
| OFX | `ofxparse` pour lire les transactions et métadonnées OFX. | **CIBLE**; dépendance et parseur non observés dans le code courant. |
| MT940 | `mt940` (distribution connue sous `mt-940`) pour lire ce format. | **CIBLE**; dépendance et parseur non observés dans le code courant. |
| CSV bancaire | Voie distincte avec format et mapping propres. | **BLOQUÉ/FUTUR**; pas de parseur ni mapping validé. |
| PDF bancaire | Voie distincte à spécifier; l'agent facture Vision n'est pas son parseur par défaut. | **FUTUR**; pas de pipeline bancaire PDF établi. |

Le choix du parseur repose sur le format détecté; OFX et MT940 ne sont pas des
fallbacks l'un pour l'autre. Ces parseurs extraient des données et ne
rapprochent pas les transactions ni ne certifient les soldes. XGrammar, si
utilisé, ne contraint que la forme d'une sortie générée; sa compatibilité avec
le moteur d'inférence reste **BLOQUÉE À VALIDER** et il ne remplace pas les
contrôles métier.

### 8.2 Contrat cible `BankingRecord`

Le contrat ci-dessous est conceptuel. Il ne prétend pas être un schéma présent
ou exécutable.

**Champs nécessaires au mouvement normalisé :**

| Champ logique | Règle |
|---|---|
| `record_id` | Identifiant interne stable, distinct d'un identifiant bancaire source. |
| `transaction_date` | Date transactionnelle/de valeur, avec sa sémantique source préservée. |
| `booking_date` | Date d'inscription si elle est distincte et fournie. |
| `amount` | Décimal exact signé dans la devise du mouvement; débit négatif, crédit positif. |
| `currency` | Devise du montant; résoudre depuis un contexte fiable si nécessaire, sinon bloquer le record final. |
| `label` | Libellé lisible et traçable aux éléments source. |
| `account_id` | Identifiant stable du compte DSH-Core résolu; obligatoire avant publication au ledger. |
| `account_reference` | Référence brute du compte dans la source, distincte de `account_id`. |
| `document_id` | Identifiant du relevé/document d'origine; peut être lié à l'`asset_id` d'ingestion si transmis. |
| `source_format` | Format réellement parsé (`OFX` ou `MT940` dans le périmètre actuel de la cible). |
| Références source | Selon disponibilité : `source_transaction_id`, `bank_reference`, `customer_reference`, `statement_id`, `transaction_type`, `source_status`, `source_payload`. |
| Partie source | `counterparty_name` et `remittance_information` si fournis; le texte brut ne vaut pas identité maître. |
| Chèque | `check_number` s'il est fourni; distinct de l'identifiant de transaction. |

Les métadonnées de relevé — période, soldes d'ouverture/clôture, devise,
institution et référence — appartiennent au relevé ou au contexte documentaire
et ne sont pas répétées sur chaque transaction sans nécessité. L'équation
solde d'ouverture + mouvements = solde de clôture constitue un contrôle
distinct du parsing et du rapprochement.

### 8.3 Comptes, sociétés et contreparties

- Le référentiel Compte porte l'identifiant stable, les références bancaires
  qualifiées par institution, la devise ou les devises, le statut et le lien
  de propriété.
- Chaque `BankingRecord` normalisé pointe vers un compte métier résolu. La
  société est déterminée par ce compte; elle n'est pas recopiée dans chaque
  mouvement sans décision spécifique.
- La règle recommandée est un compte appartenant à une société à un instant
  donné. **BLOQUÉ :** comptes partagés/conjoints et historique de propriété
  doivent être confirmés avant de figer les cardinalités.
- Fournisseurs et clients ont leurs identités stables dans les référentiels
  correspondants. Un nom de relevé est une observation, pas une résolution.
  Les rôles payeur/bénéficiaire ne sont pas déduits du seul signe.
- Les détails de virement, prélèvement, mandat, LCR, retours et autres
  paiements ne sont structurés que s'ils sont exposés par la source et définis
  par le contrat; aucune donnée absente n'est inventée.

### 8.4 Allocations N↔N

Le rapprochement est porté par une **allocation**, et non par des champs
singuliers du mouvement :

- une allocation lie exactement un `record_id` à une cible métier identifiée
  (type et identifiant);
- elle porte son identifiant stable, montant décimal exact, devise, état,
  méthode/motif/score éventuel et audit de création, validation, modification
  ou annulation;
- un mouvement a zéro à plusieurs allocations;
- un document peut être payé par zéro à plusieurs mouvements;
- un paiement partiel utilise une allocation inférieure au solde dû ou
  plusieurs allocations cumulées;
- un paiement groupé ventile un mouvement en plusieurs allocations distinctes;
- un état agrégé « rapproché » est dérivé des allocations, sans remplacer
  leur historique.

Les sommes doivent être cohérentes avec les montants et soldes rapprochables,
en traitant explicitement frais, retenues, écarts et conversions autorisés.
Ne pas dépasser les soldes ouverts sauf correction ou écart autorisé et tracé.

**BLOQUÉ :** les types de cibles métier, la convention de signe des montants
alloués, les règles de devise/conversion/arrondi/tolérance, les frais et écarts,
les statuts définitifs, les annulations, les rôles structurés des parties et
le compte partagé restent à arrêter. `matched_document_id` et `matched_amount`
ne suffisent pas pour figer le N↔N.

### 8.5 Chèques, remises, LCR et cas réels

Un chèque individuel reste un mouvement bancaire s'il est fourni comme tel.
Une remise agrégée est un objet de regroupement différent, avec référence,
date, montant et composition lorsque les preuves sont disponibles. La relation
de composition d'une remise n'est pas une allocation de rapprochement vers une
facture. Si le relevé ne donne qu'un crédit agrégé, ne pas inventer ses chèques
constituants; garder le détail en revue ou hors périmètre.

Le modèle cible doit pouvoir traiter chèques, remises de chèques, virements,
prélèvements, LCR, paiements groupés, paiements partiels et retours/contre-
passations, mais le contrat de remise et certains détails de paiement restent
**BLOQUÉS**. Les algorithmes métier ou seuils chiffrés décrits dans les
anciennes versions sont des propositions, pas des comportements actifs ou des
règles validées ici.

Oracle peut préparer une proposition nocturne d'allocation dans le staging;
validation et publication du rapprochement demeurent locales.

## 9. MasterData et mémoires

### 9.1 MasterData

**CIBLE :** les classeurs Excel du MasterData sont l'interface humaine des
données de référence. Les familles décrites dans les spécifications sont :
sociétés, contreparties, comptes bancaires, alias bancaires, catégories de
dépenses, journal des avances en compte courant (CCA) et erreurs de validation.

Le MasterData est administré localement. Les référentiels portent des
identifiants stables, contrôlent les relations et leurs statuts, et alimentent
les usages métier après vérification. Les classeurs, services de chargement et
synchronisation décrits comme cibles ne sont pas tous présents ou raccordés.

### 9.2 Responsabilités des mémoires

| Système | Source de données/rôle | Ce qu'il ne doit pas faire | État |
|---|---|---|---|
| MasterData (Excel) | Configuration métier humaine et référentiels. | Ne pas se substituer au journal d'exécution ou inventer des relations. | **CIBLE** |
| Neo4j | Relations déterministes validées : sociétés, comptes, tiers, alias, documents et liens métier. | Ne pas être considéré comme vérité courante sans provenance et accès de lecture vérifiés. | Service **EXISTANT**; repositories du dépôt **NON RACCORDE**/schéma en divergence. |
| Qdrant | Similarité vectorielle, candidats sémantiques et index RAG. | Ne pas certifier une identité, une décision comptable ou une écriture à partir du score seul. | Service **EXISTANT**; collections et writers **PARTIELS/INCONNUS**. |
| Mem0 | Mémoire épisodique de décisions/préférences et contexte passé. | Ne pas remplacer MasterData, Neo4j ni validation métier. | Façade **PARTIELLE**; collection observée vide. |

Ordre conceptuel : vérité structurée/référentiels et relations validées,
recherche vectorielle pour suggérer, mémoire épisodique pour rappeler un
précédent, puis décision humaine ou règle métier validée. Une suggestion
Qdrant ou un souvenir Mem0 ne remplace jamais une relation Neo4j vérifiée.

## 10. Skills, apprentissage et validation

### 10.1 État réel des skills

- `skills/registry.json` contient `ping_qdrant`; le script associé affiche un
  message fixe et ne prouve pas une vérification réseau.
- `workspace/skills/` contient `invoice_smtrt_sas`, version `1.0.0`, compteur
  `usage_count=2`.
- Qdrant `agent_skills` contient quatre contenus distincts visant la même
  tâche d'extraction de TVA en cas d'inversion HT/TTC : redondance
  fonctionnelle probable, égalité stricte non établie.
- Qdrant `skills_catalog` est vide.
- `teacher_student_metrics` contient 16 métriques, pas des définitions de
  skills; aucune création ni réutilisation confirmée par les marqueurs
  `skill_created=true` et `skill_reused=true` au relevé.
- Aucun `SKILL.md` métier de premier niveau n'a été trouvé. La configuration
  Hermes et SkillOpt n'est pas présente dans le dépôt audité.

Le registre JSON racine est la voie de skills la plus directement exposée par
l'API. Le routeur/harness retourne un skill/contexte mais ne l'exécute pas
comme un workflow complet. La voie Qdrant `agent_skills` et Teacher–Student
est distincte, partielle et ne prouve pas un apprentissage autonome.

### 10.2 Registre canonique et cycle de vie cible

Le local conserve le **registre canonique des skills publiés**. Oracle peut
produire un candidat, mais ne modifie pas le registre officiel et ne publie
jamais un skill.

Cycle de vie validé :

```text
CANDIDATE → TESTING → APPROVED → PUBLISHED
                 ↘ REJECTED
PUBLISHED → RETIRED
```

Un candidat ne progresse qu'après test et décision. `REJECTED` et `RETIRED`
sont des états terminaux ou de retrait, pas des alias pour publication.
L'historique de versions et les preuves de chaque transition sont conservés.

### 10.3 SkillOpt et superviseur Claude

**SkillOpt** optimise et teste un candidat sur un jeu de validation; une
validation de non-régression est requise avant `APPROVED` puis publication
locale. Aucune amélioration de prompt ne doit dégrader les cas déjà validés ni
créer de collision connue.

**Claude** supervise systématiquement le bootstrap d'un nouveau skill.
Lorsque la maturité et les résultats de régression sont établis, la supervision
peut devenir partielle ou se déclencher sur exception. Cette supervision ne
remplace pas la validation locale et n'autorise pas une publication directe.

La gouvernance est **CIBLE validée**, mais registre canonique, SkillOpt et
transfert contrôlé des skills ne sont pas attestés comme intégrés. Les quatre
variantes existantes ne doivent pas être fusionnées ou supprimées sans examen
et autorisation explicites.

## 11. Modèles d'intelligence artificielle

### 11.1 Sélection par rôle

La sélection est par rôle, pas par un unique `AI_MODE` global :

| Rôle | Usage général |
|---|---|
| Vision | Pièces image/scannées et compréhension visuelle, lorsqu'un tel modèle est disponible. |
| Raisonnement de base | Classification, normalisation et décisions courantes. |
| Expert | Cas complexes, ambiguïtés et exceptions nécessitant une analyse approfondie. |
| Superviseur | Bootstrap et supervision du cycle de skills; Claude selon la politique validée. |

Les modèles locaux Ollama constituent le socle de repli souverain. Des modèles
gratuits OpenRouter peuvent être employés pour les rôles vision, base ou
expert; aucun modèle gratuit n'est considéré comme permanent, stable ou
garanti. Le modèle actif dans l'agent facture vérifié est un appel Ollama
Vision; cela ne valide pas les autres allocations de rôle.

### 11.2 Contrôles avant déploiement

Pour chaque modèle/version affecté à un rôle, vérifier et tracer :

- disponibilité, identifiant, version et limites de service;
- capacité vision quand elle est requise;
- appels d'outils et format structuré/JSON lorsque le workflow en dépend;
- fenêtre de contexte, taille des pièces et comportement de troncature;
- qualité et validité de sortie, erreurs, délais et limites de débit;
- confidentialité, destination des données et autorisation d'envoi hors poste;
- disponibilité d'un modèle Ollama de fallback pour le rôle concerné.

Un modèle incompatible n'est pas activé. L'absence de réseau ou du fournisseur
externe déclenche le fallback local disponible ou un échec explicite; elle
n'autorise pas une sortie simulée donnant un succès trompeur. Le fonctionnement
local hors Internet reste un objectif de conception.

## 12. Indexation documentaire et manifeste Oracle

### 12.1 Sources et traitement

Oracle peut préparer l'indexation des emails et contenus OneDrive ainsi que :
documentation technique, procédures, fiches produits, catalogues, tarifs,
contrats et anciennes factures. Les connecteurs et ce pipeline Oracle sont
**CIBLE**, non actifs dans le dépôt audité.

Le résultat par document doit garder la source et son hash, les métadonnées,
le texte extrait, les chunks, les artefacts nécessaires à l'examen et les
informations de modèle d'embedding. La recherche résultante alimente Qdrant
comme moteur de similarité/RAG; elle ne rend pas les documents ou leurs
conclusions automatiquement vrais.

### 12.2 Manifeste logique

Le manifeste Oracle → local est **décidé**. Il identifie le lot et sa version
et décrit le contenu logique transporté; ce n'est pas une copie de base.

Champs d'enveloppe :

- `manifest_id`;
- `version`;
- `origine`;
- hashes;
- version du schéma;
- identifiant du modèle d'embedding et dimension.

Le manifeste contient **une entrée par document**, comprenant :

- identité/source du document et hash;
- chunks;
- texte extrait;
- métadonnées;
- artefacts nécessaires à l'audit, la reprise et la validation.

Le code du manifeste, le transport concret des lots et les tests de reprise
restent à implémenter; leur absence n'annule pas les règles déjà décidées.

## 13. Staging, synchronisation et conflits

### 13.1 Staging et décisions locales

Tout résultat Oracle est stocké dans un staging réversible. Il ne peut ni
modifier ni remplacer un objet officiel local. La validation locale évalue
l'identité, les hashes, le schéma, l'embedding, les doublons, le contenu, la
provenance et les conflits avant toute publication.

Statuts d'entrée prévus :

- `PENDING` : reçu, à examiner;
- `ACCEPTED` : compatible et accepté localement;
- `ACCEPTED_REEMBED` : accepté après ré-embedding local;
- `DUPLICATE_SKIPPED` : doublon reconnu, aucune nouvelle publication;
- `HUMAN_REVIEW_REQUIRED` : revue humaine requise;
- `REJECTED` : rejet local, aucune publication;
- `PUBLISHED` : publié localement après validation.

Un conflit de version ou d'identité est bloqué sous `SYNC_CONFLICT`; seule sa
résolution locale permet de poursuivre. Une version locale `PUBLISHED` reste
prioritaire sur une proposition Oracle `CANDIDATE`.

### 13.2 Synchronisation logique

La synchronisation est logique et versionnée :

- **Neo4j :** appliquer des opérations logiques `MERGE` à partir
  d'identifiants métier stables, après validation locale; ne pas importer un
  état brut de base.
- **Qdrant :** transférer des objets documentaires/logiques compatibles. Un
  snapshot ne peut être utilisé que pour une migration explicitement contrôlée.
- Ne jamais copier directement les répertoires `qdrant_storage` ou
  `neo4j_data`.
- N'effectuer aucun `DELETE` automatique; les retraits nécessitent une décision
  et un traitement explicites.
- Ne pas confondre synchronisation logique validée et fusion physique de
  stockages.

### 13.3 Idempotence, reprise et travaux d'implémentation

Chaque entrée est identifiée et vérifiée par manifeste/version/hash afin
d'éviter qu'une retransmission crée des doublons ou applique deux fois une
promotion. Les réessais doivent conserver le statut, le résultat antérieur et
la trace d'audit. Une reprise ne contourne jamais la validation locale.

Le format et les règles sont décidés. Restent **FUTURS** : le code du
manifeste, le transport concret, les tests d'idempotence et de reprise ainsi
que l'interface locale de résolution des conflits. Aucun de ces travaux
n'autorise une synchronisation physique ou une promotion distante.

## 14. Sécurité, traçabilité et validation humaine

### Invariants

1. Aucun secret, mot de passe ou clé API ne doit être documenté dans cette
   spécification ni ajouté aux fichiers versionnés. Les secrets éventuellement
   déjà présents dans les configurations existantes doivent faire l'objet d'un
   audit et d'une migration vers des variables d'environnement ou un gestionnaire
   de secrets.
2. Pas de suppression, fusion ou migration de stockage sans audit de
   provenance, validation et autorisation explicite.
3. Oracle n'écrit jamais directement dans les données officielles locales;
   toute promotion est locale.
4. Pas de promotion automatique d'un skill; le test de non-régression et la
   validation locale sont requis.
5. Aucune donnée de pièce, identité, compte, chèque ou allocation n'est
   inventée pour compléter un contrat.
6. Les contenus bruts et normalisés, hashes, origine, modèle/rôle/version,
   schéma, skill/version, contrôles, scores et décisions sont traçables selon
   leur disponibilité.
7. Toute ambiguïté matérielle déclenche une revue ou un état bloqué, pas une
   validation silencieuse.
8. Les données et références externes sont soumises au moindre accès utile;
   les pièces ne sont envoyées à un fournisseur externe que si la politique
   de confidentialité l'autorise.
9. Les journaux ne doivent pas exposer des secrets ou des données sensibles
   inutiles au diagnostic.
10. La promotion est versionnée, contrôlée et auditable; l'absence d'une
    dépendance produit une erreur explicite.
11. Le mode local reste utilisable sans Internet pour les fonctions et modèles
    disponibles localement.

### Validation humaine

Une validation humaine est requise lorsqu'une identité, un compte, une
ventilation, une allocation, une remise ou une sortie de modèle demeure
ambiguë, en conflit ou insuffisamment prouvée. L'événement de validation
conserve au minimum l'objet concerné, la décision, l'auteur/processus, la date,
le motif et les valeurs avant/après nécessaires à l'audit.

Des seuils numériques de score décrits dans une version antérieure ne sont pas
retenus ici comme règles actives ou validées : les audits bancaires signalent
que les scores, tolérances et règles de décision restent à définir.

## 15. État constaté au 20 octobre 2026

### 15.1 Corrections et pipelines

- Trois corrections de code sont consignées : routage API vers `route_prompt`,
  consultation de skill par `retrieve_skill_for_task`, et remontée de
  l'erreur d'agent depuis `message` dans `IngestionManager`.
- Le webhook facture `n8n_api.py` → `agents.invoice_agent.InvoiceAgent` est le
  chemin HTTP actif identifié; il utilise Ollama Vision et le modèle local de
  l'agent.
- `DocumentProfiler`, `RoutingEngine`, `BusinessExtractionManager`,
  `InvoiceReconciliationEngine` et `TaxClassificationEngine` existent et sont
  partiellement exercés, mais ne sont pas raccordés au webhook facture.
- `IngestionManager` est testé mais non appelé par le webhook facture.
- `InvoiceSchema` n'a pas été retrouvé; ses appelants sont bloqués ou non
  raccordés. Les deux `InvoiceData` sont distincts.
- La voie facture backend est simulée en partie. L'indexation facture et les
  écritures Qdrant/Neo4j ne sont pas raccordées au webhook.
- Le graphe LangGraph présent ne réalise pas l'orchestration métier complète;
  des nœuds actuels sont simulés.
- La branche bancaire de `IngestionManager` retourne
  `pending_implementation`. Le schéma est vide; aucun parseur bancaire,
  BankingAgent, ledger ou workflow bancaire n'est raccordé.

### 15.2 Stockages

- **Qdrant :** une instance racine est active et joignable; huit collections
  ont été observées et l'audit rapporte 23 vecteurs. `agent_skills` en
  contient quatre, `teacher_student_metrics` seize, `invoice_documents` trois
  selon le relevé, `skills_catalog` zéro et `mem0` zéro. Les comptages de
  `document_bus` et `research_cache` ne sont pas établis par la cartographie.
- Deux répertoires Qdrant existent : le stockage racine correspond à l'instance
  active auditée; celui sous `docker/` n'est rattaché à aucune instance active.
  Une relation ou génération commune est probable, mais les contenus ne sont
  pas certifiés comme copies identiques. Ne pas les fusionner ni supprimer.
- **Neo4j :** le service/conteneur est actif et répond en HTTP; l'accès de
  lecture authentifié est à sécuriser. La cartographie de stockage a reçu
  `401` et ne certifie pas le contenu. Un autre relevé rapporte trois nœuds et
  deux relations, avec provenance non attribuée; cette différence de preuve
  demeure explicite. Aucun repository applicatif du dépôt n'est établi comme
  auteur de ces données.
- La disponibilité d'un service ou la présence d'une collection ne démontre
  pas un raccordement applicatif ni la validité de son contenu.

### 15.3 Skills et cibles

- Le registre JSON racine contient une entrée `ping_qdrant`; un fichier
  `invoice_smtrt_sas` existe dans `workspace/skills/`.
- Les quatre points `agent_skills` sont distincts, mais probablement
  redondants fonctionnellement; aucune fusion n'a été réalisée.
- `skills_catalog` est vide. Les 16 éléments `teacher_student_metrics` sont
  des métriques, et ne confirment aucune création ou réutilisation de skill.
- Hermes, SkillOpt et leur configuration ne sont pas présents dans le dépôt
  audité. L'architecture Oracle, Telegram, staging, synchronisation logique,
  tableaux et gouvernance des skills est **CIBLE**, pas un flux opérationnel
  vérifié.
- Aucune suppression, fusion ou migration physique n'est autorisée par ces
  constats.

## 16. Ordre de réalisation et dépendances

Cet ordre respecte les dépendances des contrats et les priorités des audits.
Il décrit le travail à faire; il ne prétend pas que les étapes sont déjà
réalisées.

### Étape 1 — préserver et vérifier les raccordements existants

- Valider les trois corrections appliquées et leurs tests ciblés.
- Conserver le webhook facture actif comme référence de l'état courant.
- Sécuriser l'accès de lecture Neo4j avant d'interpréter ou modifier son contenu.
- Ne pas supprimer ou fusionner les répertoires/collections en l'absence de
  provenance vérifiée.

### Étape 2 — résoudre les contrats bloquants

- Comparer les attentes des appelants facture et arbitrer le contrat
  `InvoiceSchema`; ne pas substituer `InvoiceData` par commodité.
- Définir les références et statuts nécessaires à la traçabilité d'une pièce,
  sans confondre cible de conception et modèle existant.
- Arrêter les décisions bancaires bloquées : comptes partagés, cibles
  rapprochables, signe/devise/tolérances, rôles des parties et remise de
  chèques.

### Étape 3 — consolider les référentiels et les stockages de façon non destructive

- Définir et vérifier les sources MasterData et identifiants stables.
- Authentifier et inventorier Neo4j en lecture, puis confronter les données
  runtime au schéma visé.
- Comparer les deux stockages Qdrant et déterminer leur provenance avant tout
  transfert.
- Aucune migration ne précède l'approbation du contrat, la sauvegarde et un
  plan vérifiable de reprise.

### Étape 4 — réaliser les contrats et validations déterministes

- Formaliser le schéma de pièce et les contrôles arithmétiques/fiscaux sans
  prétendre que le webhook actuel les exécute.
- Formaliser le BankingRecord et les allocations après résolution des décisions
  bloquées; tester des relevés représentatifs anonymisés.
- Définir les contrats des trois tableaux à partir des entités validées.

### Étape 5 — raccorder le pipeline documentaire et facture

- Raccorder les composants existants dans le flux métier après validation des
  contrats : ingestion, profilage, routage, extraction, contrôles, validation
  locale et persistance.
- Utiliser LangGraph comme orchestration métier unique de la cible, avec
  interruptions humaines explicites.
- Vérifier les flux de bout en bout avant de marquer les composants comme
  **EXISTANT** au sens opérationnel.

### Étape 6 — construire le pipeline bancaire

- Ajouter les parseurs OFX et MT940 séparés, puis normaliser vers le contrat
  BankingRecord approuvé.
- Traiter CSV et PDF comme voies distinctes après définition de leurs formats.
- Implémenter le ledger et les allocations N↔N, puis le rapprochement et sa
  validation locale.
- Ne pas implémenter la remise de chèques détaillée tant que son périmètre et
  ses preuves source restent bloqués.

### Étape 7 — publier les capacités documentaires et d'apprentissage

- Construire l'indexation Oracle et le staging, puis le manifeste et le
  transport logique versionné; tester hashes, reprises, idempotence et
  conflits.
- Implémenter le registre local canonique des skills et son cycle de vie.
- Intégrer SkillOpt et ses tests de non-régression; faire superviser le
  bootstrap par Claude et ne distribuer à Oracle que les versions publiées
  localement.
- Vérifier la politique des modèles par rôle, leurs capacités et le fallback
  Ollama avant activation.

### Étape 8 — interfaces et mise en service

- Intégrer Hermes Desktop, Telegram (cinq canaux) et Hermes Oracle à la surface
  de DSH-Core sans déplacer l'autorité métier.
- Déployer les trois tableaux, les workflows de validation et les exports
  après tests de sécurité, de reprise, d'audit et de fonctionnement local sans
  Internet.

Chaque jalon est clos sur preuves (tests, audit de raccordement, traçabilité et
validation métier), pas sur l'existence d'un fichier ou d'un service seul.

## 17. Architecture cible — diagramme

```text
Utilisateur
   │
   ├── Hermes Desktop — interface générale locale
   └── Telegram — PRESSE / RECHERCHE / COMPTABILITÉ / GÉNÉRAL / DOCUMENTS
          │
          ▼
DSH-Core local — unique autorité métier
   │
   ├── APIs / connecteurs / services de domaine
   │
   ▼
LangGraph — orchestrateur métier unique (CIBLE)
   │
   ├── Document / facture
   │      ingestion → profilage → routage → OCR/Vision/extraction
   │      → contrôles → identité/skill → proposition
   │
   ├── Banque
   │      OFX → ofxparse | MT940 → mt940
   │      CSV/PDF : voies distinctes à définir
   │      → BankingRecord → allocations N↔N proposées
   │
   └── Recherche documentaire / apprentissage de skills candidats
          ▲
          │ analyses nocturnes, emails, OneDrive, pièces
Hermes Oracle — satellite, aucune autorité de publication
          │
          ▼
Staging Oracle réversible + manifeste logique versionné
          │
          ▼
Validation locale / contrôle hashes, schéma, embedding et conflits
          │
          ├── PENDING / HUMAN_REVIEW_REQUIRED / SYNC_CONFLICT
          │       → revue, rejet ou reprise; pas de promotion
          │
          └── ACCEPTED / ACCEPTED_REEMBED
                  │ validation locale et publication contrôlées
                  ▼
         Référentiels / ledger / registres officiels locaux
                  │
                  ├── MasterData : faits de référence
                  ├── Neo4j : relations métier validées
                  ├── Qdrant : vecteurs et RAG
                  ├── Mem0 : décisions épisodiques autorisées
                  └── registre des skills : versions PUBLISHED
                          │
                          ▼
            Ventilation, rapprochement final et exports comptables
```

Tout chemin Oracle vers une écriture officielle passe par le staging, puis par
une validation et une publication locales. Le diagramme décrit la **CIBLE**,
pas un déploiement constaté de bout en bout.

## 18. Décisions bloquées et limites

### Bloqués

- Contrat exact de `InvoiceSchema`, ses types/validateurs et sa relation aux
  deux modèles `InvoiceData`.
- Compte bancaire partagé ou propriété exclusive, unicité et résolution des
  références source, comptes multidevises.
- Types d'objets rapprochables, signe/devise des allocations, conversions,
  tolérances, frais, écarts, dépassements, annulations et états.
- Cardinalité/résolution des parties et rôle payeur/bénéficiaire.
- Périmètre des remises de chèques, composition et disponibilité des preuves
  constituantes.
- Accès authentifié de lecture Neo4j, origine de son contenu et rapprochement
  des deux stockages Qdrant.
- Disponibilité, compatibilité et conditions de confidentialité des modèles et
  connecteurs externes au déploiement.

### Futurs, mais règles déjà décidées

Le code du manifeste Oracle, le transport concret des lots, les tests
d'idempotence/reprise, l'interface de conflit, les parseurs et ledger
bancaires, le raccordement complet LangGraph, les interfaces Hermes/Telegram,
la gouvernance des skills et SkillOpt restent à réaliser ou valider. Ces
travaux n'autorisent ni copie physique des bases, ni écriture Oracle directe,
ni suppression automatique, ni promotion non locale.

### Limites de preuve

La spécification décrit des responsabilités et des contrats de cible; elle
n'est pas elle-même un schéma exécutable. Un service accessible ne prouve pas
la provenance de ses données. Les quatre skills Qdrant sont des variantes
différentes et probablement redondantes, non des copies strictement
identiques. Les mesures runtime peuvent évoluer après les audits.

## 19. Sources de contrôle

- [dsh core complet.md](./dsh%20core%20complet.md) — source canonique principale.
- [03_JOURNAL.md](./03_JOURNAL.md) — chronologie, corrections et décisions.
- [PIPELINE_OFFICIEL.md](./PIPELINE_OFFICIEL.md) — raccordements applicatifs
  constatés.
- [04_AUDIT_ARCHITECTURE.md](./04_AUDIT_ARCHITECTURE.md) — architecture
  observée.
- [05_DECISIONS_ARCHITECTURE.md](./05_DECISIONS_ARCHITECTURE.md) — décisions et
  priorités de convergence.
- [SKILLS_DUPLICATES_INVENTORY.md](./SKILLS_DUPLICATES_INVENTORY.md) —
  inventaire des skills.
- [DATABASE_STORAGE_MAP.md](./DATABASE_STORAGE_MAP.md) — état runtime Qdrant et
  Neo4j.
- [BANKING_ARCHITECTURE.md](./BANKING_ARCHITECTURE.md) — pipeline bancaire et
  limites actuelles.
- [BANKING_RECORD_SPEC.md](./BANKING_RECORD_SPEC.md) — contrat bancaire
  conceptuel.
- [BANKING_SCHEMA_REVIEW.md](./BANKING_SCHEMA_REVIEW.md) — lacunes du contrat
  bancaire.
- [BANKING_RELATIONS_REVIEW.md](./BANKING_RELATIONS_REVIEW.md) — cardinalités,
  responsabilités et décisions bloquées.
