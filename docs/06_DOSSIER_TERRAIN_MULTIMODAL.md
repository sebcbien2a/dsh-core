---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe historique — Archives numérotées
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: []
downstream_consumers: []
verification_tests: []
---

# DossierTerrain multimodal générique

Statut : SPÉCIFICATION CIBLE À IMPLÉMENTER
Implémentation : NON COMMENCÉE
Architecture parente : DSH_CORE_AI_ARCHITECTURE_V2.md
Validation : aucun envoi, suppression ou action externe sans confirmation humaine

## 1. Schéma générique

`DossierTerrain` est un dossier durable, indépendant d’un client et extensible à tout sujet terrain.

```text
DossierTerrain {
  dossier_id: UUID
  created_at: datetime
  updated_at: datetime
  created_by: string
  source: "telegram" | autre
  status: "captured" | "enrichment_needed" | "ready" | "action_pending" | "closed"
  title: string
  description: string
  category: string
  category_history: CategoryChange[]
  client: EntityReference | null
  subject: string | null
  product_or_reference: EntityReference | null
  contacts: EntityReference[]
  requested_action: ActionRequest | null
  attachments: Attachment[]
  tags: string[]
  provenance: Provenance[]
  human_review: ReviewState
}
```

`category` est une classification modifiable, pas un type de dossier fermé. Les exemples — colorimétrie, défaut, livraison, tarif, chantier, commande, référence inconnue, rappel commercial — sont des catégories ou tags initiaux, non une liste exhaustive.

## 2. Champs obligatoires et facultatifs

**Obligatoires :** `dossier_id`, dates, `created_by`, `source`, `status`, `title`, `description`, `category`, `category_history`, `attachments`, `provenance`, `human_review`.

**Facultatifs :** client, sujet libre distinct du titre, produit/référence, contacts, action demandée et tags. Une capture peut commencer sans client, contact, catégorie précise ni action demandée. Les éléments non identifiés restent explicitement inconnus ou à compléter ; le système ne les invente pas.

`EntityReference` contient au minimum un identifiant stable et un libellé observé ; la résolution vers une entité canonique est facultative et traçable. `ActionRequest` contient une intention, un statut, un échéancier facultatif et les références concernées. `ReviewState` distingue « non examiné », « à valider », « validé » et « refusé ».

## 3. Fichiers originaux

- Stocker les originaux dans le stockage documentaire, hors Mem0 et hors Git.
- Utiliser un chemin stable sous le dossier, par exemple `dossiers-terrain/<dossier_id>/originals/`.
- Chaque élément `Attachment` conserve `attachment_id`, chemin, nom source, type MIME, taille, date de réception et hash SHA-256.
- Autoriser zéro à plusieurs pièces jointes, notamment plusieurs photos et plusieurs messages vocaux.
- Préserver les originaux ; dérivés OCR, transcriptions, miniatures et descriptions visuelles sont des artefacts distincts, référencés et régénérables.
- Ne pas supprimer ni remplacer un original sans validation humaine.

## 4. Métadonnées Qdrant

Indexer des passages textuels utiles : description, transcription, OCR, légendes visuelles et notes de suivi. Chaque point contient au minimum :

`dossier_id`, `attachment_id` facultatif, `chunk_id`, `text`, `category`, `client_id` facultatif, `product_id` facultatif, `contact_ids`, `created_at`, `updated_at`, `source`, `content_hash`, `embedding_model`.

BGE-M3 produit les embeddings. Qdrant est un index reconstructible, non la source canonique du dossier ni des fichiers. Les filtres de métadonnées permettent de restreindre la recherche par client, catégorie, date ou entité liée.

## 5. Relations Neo4j

Représenter les entités et liens explicitement, avec des identifiants stables :

- `(DossierTerrain)-[:CONCERNE]->(Client)` — zéro, une ou plusieurs relations si nécessaire ;
- `(DossierTerrain)-[:MENTIONNE]->(Produit|Référence|Contact)` ;
- `(DossierTerrain)-[:CONTIENT]->(Attachment)` ;
- `(DossierTerrain)-[:DEMANDE]->(Action)` ;
- `(DossierTerrain)-[:SUIVIT|REMPLACE|EST_LIE_A]->(DossierTerrain)`.

Une entité non résolue reste une mention du dossier, pas un nœud canonique créé sur simple supposition. Les changements de catégorie, résolution et liens conservent provenance et historique. Neo4j complète les fichiers canoniques ; il ne les remplace pas.

## 6. Mem0

Mémoriser uniquement le contexte conversationnel léger et utile : préférences de suivi, alias confirmés, contexte de conversation et rappels non canoniques. Référencer le `dossier_id` lorsque pertinent.

Ne pas y stocker les originaux, les données comptables faisant autorité, les versions officielles du dossier, ni des décisions ou identités non validées. Toute information durable doit rester vérifiable dans le dossier canonique.

## 7. Routage automatique des modèles

Les rôles globaux des modèles, le routage, les fallbacks et les responsabilités
des harnesses relèvent uniquement de
[`AI_MODEL_ROUTING_AND_HARNESSES.md`](AI_MODEL_ROUTING_AND_HARNESSES.md).
Les propositions ci-dessous sont une cible métier locale et ne doivent pas
créer une affectation de modèle concurrente.

- Telegram/Hermes recueillent le message, les fichiers et les précisions ; LangGraph orchestre.
- Nemotron 3 Ultra `:free` peut contribuer au RAG et à la recherche de gros documents non confidentiels sous Hermes ; ses propositions restent révisables.
- DeepSeek V4.1 Flash 1017 est le profil d'exécution de niveau moyen sous OpenCode ; il n'est pas désigné ici comme modèle de vision ou fallback.
- LightOnOCR intervient pour lire les petits textes difficiles, notamment sur étiquettes ou documents photographiés.
- BGE-M3 vectorise les contenus textuels ; Qdrant sert à la recherche sémantique.
- Si le client, la référence ou l’intention reste ambiguë, demander une précision ou conserver l’élément comme non résolu.
- DeepSeek Harness reste un harness spécialisé à finaliser ; son rôle précis n'est pas encore validé et il ne doit pas dupliquer Hermes ou OpenCode.
- Git est la source canonique des Skills.

## 8. Recherche depuis le PC principal

Depuis le PC principal, rechercher par texte, client, contact, produit/référence, catégorie, période, statut et pièces jointes. Combiner filtres Qdrant et relations Neo4j ; retourner les dossiers pertinents avec leurs identifiants, résumés, liens documentaires et niveau de résolution. Ouvrir les originaux depuis le stockage documentaire. Une recherche inter-machine utilise les identifiants et manifestes logiques versionnés, jamais une copie physique des bases Qdrant ou Neo4j.

## 9. Préparation d’un mail ou d’une autre action

À partir d’un dossier, produire un brouillon d’action avec objectif, destinataire éventuel, texte proposé, pièces jointes référencées et informations manquantes. Les originaux ne sont attachés qu’après sélection explicite. Une action non-mail peut être préparée sous forme de tâche ou de rappel, liée au dossier. Conserver le brouillon et son historique dans le système documentaire/workflow, avec son statut et sa provenance.

## 10. Validation humaine

Une validation humaine explicite est requise avant tout envoi de mail ou message, suppression ou remplacement de fichier, modification d’un enregistrement canonique, confirmation d’une association client/contact/produit contestable, ou exécution d’une action externe. Avant validation, les propositions de modèle restent des suggestions ; aucune action ne doit être présentée comme exécutée.

## 11. Skills génériques

- **`capturer-dossier-terrain`** — Créer ou compléter un dossier depuis texte, photos et notes vocales Telegram ; accepter les entités facultatives et plusieurs pièces ; préserver originaux et provenance ; demander les informations manquantes sans bloquer la capture.
- **`retrouver-dossier-client`** — Rechercher par client confirmé, alias, sujet, produit, catégorie ou période ; présenter les correspondances et ambiguïtés sans fusion automatique de dossiers.
- **`preparer-action-depuis-dossier`** — Transformer une demande en brouillon de tâche, rappel ou prochaine étape ; signaler prérequis et incertitudes ; ne rien exécuter sans validation humaine.
- **`preparer-mail-avec-pieces-jointes`** — Préparer destinataire, objet, corps et liste de pièces proposées ; vérifier les références et demander confirmation avant tout envoi ou ajout de pièces.

Les définitions de ces Skills sont versionnées dans Git ; leur publication et toute évolution suivent un cycle explicite avec validation humaine.

## 12. Critères d’acceptation

1. Un texte Telegram sans client crée un dossier exploitable avec client non renseigné.
2. Plusieurs photos d’un même envoi sont conservées comme pièces distinctes avec hashes et provenance.
3. Une note vocale est conservée à l’identique ; sa transcription est un dérivé relié à l’original.
4. Une étiquette à petits caractères déclenche l’usage ciblé de LightOnOCR et conserve son résultat comme dérivé.
5. Un défaut produit est classé et indexé sans imposer de client ni de catégorie prédéfinie.
6. Une erreur de livraison peut être complétée plus tard par un contact, sans recréer le dossier ni perdre l’historique.
7. Une demande de tarif avec référence inconnue conserve la référence comme non résolue et permet son enrichissement ultérieur.
8. Un changement de catégorie est historisé et les recherches retrouvent le dossier par sa catégorie actuelle.
9. Depuis une autre machine, une recherche retrouve plusieurs dossiers d’un même client et ouvre leurs originaux via leurs références logiques, sans copier les bases.
10. Un mail ou une action peut être préparé à partir du dossier, mais l’envoi, la suppression ou la modification reste impossible sans validation humaine explicite.
