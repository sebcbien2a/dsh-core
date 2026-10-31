---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 4 — Audit de Lot, Baselines & Gouvernance Cloud/Local
status: ACTIVE
upstream_dependencies: [model_router.py, .env.example]
downstream_consumers: [services/cloud_vision_extractor.py, services/local_invoice_extractor.py, services/ollama_vision_extractor.py]
verification_tests: [tests/test_model_router.py, tests/test_vision_supervisor.py]
contract_version: 1.0.1
---

﻿# Routage des modÃ¨les et rÃ´les des harnesses

## Objet et statut

Ce document est la source de vÃ©ritÃ© lisible pour la matrice initiale des
modÃ¨les, les profils OpenCode, la rÃ©partition des rÃ´les entre OpenCode, Hermes
et DeepSeek Harness, et l'audit des tÃ¢ches rÃ©elles. Il dÃ©crit une affectation
cible validÃ©e et l'Ã©tat opÃ©rationnel confirmÃ© au 28/10/2026.

VS Code reste le cockpit humain. Copilot 365 aide Ã  cadrer et arbitre les
dÃ©cisions d'architecture. Gemini 4.0 Argon peut contre-auditer les dÃ©cisions
importantes. OpenCode est l'environnement principal d'exÃ©cution ; Hermes
orchestre les tÃ¢ches complexes. Copilot VS Code reste disponible en secours,
mais ses crÃ©dits sont Ã  Ã©conomiser.

Les performances hors du harness prÃ©vu ne sont pas considÃ©rÃ©es comme
suffisamment reprÃ©sentatives pour dÃ©cider seules d'une affectation. Les
synergies entre modÃ¨le et harness seront observÃ©es pendant les tÃ¢ches rÃ©elles.
Les coÃ»ts par tÃ¢che sont des estimations variables selon les tokens, les outils,
le raisonnement et la route fournisseur ; aucun montant par tÃ¢che n'est garanti.

## Ã‰tat opÃ©rationnel confirmÃ© â€” 2026-10-28

- OpenCode est installÃ© et authentifiÃ© auprÃ¨s d'OpenRouter. Les cinq profils
  listÃ©s dans la matrice ci-dessous sont configurÃ©s globalement.
- Hermes est installÃ© et configurÃ© avec OpenRouter. Le skill intÃ©grÃ© `opencode`
  est activÃ© et disponible. ParamÃ¨tres confirmÃ©s : fournisseur `openrouter`,
  modÃ¨le principal `xiaomi/mimo-v2.6-pro`, `base_url`
  `https://openrouter.ai/api/v1`, `api_mode` `chat_completions`,
  `smart_model_routing.sort: price` et
  `smart_model_routing.require_parameters: true`. La clÃ© reste dans le fichier
  secret Hermes appropriÃ© ; sa valeur ne doit jamais Ãªtre affichÃ©e ou copiÃ©e.
- Le quota quotidien gratuit est commun aux routes `:free`. Le contrÃ´leur local
  interroge l'API OpenRouter sans appel de modÃ¨le. DerniÃ¨re lecture observÃ©e :
  limite 1 000, 33 utilisÃ©es, 967 restantes ; rÃ©initialisation Ã  minuit UTC.
- DeepSeek Harness est installÃ© globalement en version `0.2.1-rc.2`
  (commande `dsh`), avec Node.js `24.21.0` et npm `11.19.0`. npm a signalÃ©
  cinq scripts d'installation non autorisÃ©s : `@deepseek-ai/dsh-subprocess-local`,
  `koffi`, `node-pty`, `@google/genai` et `protobufjs`. La finalisation
  technique et son rÃ´le spÃ©cialisÃ© restent Ã  valider.
- Aucun benchmark artificiel supplÃ©mentaire ne doit Ãªtre lancÃ©. Les
  performances seront auditÃ©es pendant les tÃ¢ches rÃ©elles avec le harness
  cible. Un seul exÃ©cutant agit sur un worktree donnÃ© ; aucun fallback payant
  silencieux n'est autorisÃ©. Les installations globales ne modifient pas
  DSH-Core.

## Matrice initiale validÃ©e

| Niveau | ModÃ¨le et identifiant OpenRouter | Profil OpenCode | Harness principal | RÃ´le et coÃ»t |
|---|---|---|---|---|
| Facile | Thinking Machines Inkling â€” `openrouter/thinkingmachines/inkling:free` | `facile-inkling` | OpenCode | Exploration, comprÃ©hension du dÃ©pÃ´t, code lÃ©ger et petites propositions. Gratuit dans les limites du quota OpenRouter. |
| Fallback gratuit | Cohere North Mini Code â€” `openrouter/cohere/north-mini-code:free` | `fallback-north` | OpenCode | Remplacement explicite d'Inkling, correctifs simples, terminal, petits tests et tÃ¢ches agentiques courtes. Gratuit dans les limites du quota. |
| Moyen | DeepSeek V4.1 Flash 1017 â€” `openrouter/deepseek/deepseek-v4.1-flash-1017` | `moyen-deepseek` | OpenCode | ExÃ©cutant principal pour le code existant, correctifs, tests, refactorisations, migrations et tÃ¢ches multi-fichiers moyennes. CoÃ»t variable, non garanti par tÃ¢che. |
| Difficile | Xiaomi MiMo-V2.6-Pro standard â€” `openrouter/xiaomi/mimo-v2.6-pro` | `difficile-mimo` | Hermes | Architecture, planification et coordination des tÃ¢ches complexes. Sous Hermes, MiMo planifie et prÃ©pare le travail des exÃ©cutants. CoÃ»t variable, non garanti par tÃ¢che. |
| RAG / gros documents | NVIDIA Nemotron 3 Ultra 550B A55B â€” `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | `rag-nemotron` | Hermes | Recherche et synthÃ¨se de gros corpus non confidentiels, avec traÃ§abilitÃ© et aide contextuelle Ã  Hermes. Gratuit dans les limites applicables au compte. |

### Contraintes de sÃ©lection

- Inkling est rÃ©servÃ© aux tÃ¢ches non confidentielles et seulement si les
  conditions du fournisseur autorisent l'exploitation des donnÃ©es. Aucune
  modification importante ne lui est confiÃ©e sans validation humaine ; il
  n'est pas l'architecte final d'une grosse brique.
- North Mini Code est un fallback sÃ©lectionnable, mais aucun basculement
  automatique silencieux n'est permis avant validation d'un routeur contrÃ´lÃ©.
  Le modÃ¨le effectivement utilisÃ© doit rester visible.
- DeepSeek est un modÃ¨le payant : le sÃ©lectionner explicitement, sans coÃ»t
  garanti par tÃ¢che.
- MiMo dÃ©signe uniquement la version standard. **MiMo-V2.6-Pro-UltraSpeed est
  exclu en raison de son coÃ»t** : ne pas le configurer, l'appeler ni le proposer
  comme fallback automatique.
- Nemotron ne reÃ§oit aucun document confidentiel, donnÃ©e personnelle sensible,
  secret, mot de passe ou donnÃ©e client rÃ©elle. Lecture seule par dÃ©faut ;
  obtenir une validation humaine avant toute transformation ou Ã©criture.

Les routes `:free` partagent les limites applicables au compte OpenRouter.
Le quota confirmÃ© est commun Ã  ces routes, et non attribuÃ© sÃ©parÃ©ment Ã  chaque
modÃ¨le. La derniÃ¨re lecture est consignÃ©e dans l'Ã©tat opÃ©rationnel ci-dessus ;
elle ne constitue pas une garantie de coÃ»t par tÃ¢che.

## Profils et agents OpenCode

Les profils logiques sÃ©lectionnables sont `facile-inkling`,
`fallback-north`, `moyen-deepseek`, `difficile-mimo` et `rag-nemotron`.
Ils s'ajoutent aux agents intÃ©grÃ©s `build`, `plan`, `explore`, `general`,
`summary`, `title` et `compaction` ; ils ne les remplacent pas.

- **EXPLORE** : exploration rapide en lecture seule, recherche de fichiers ou
  symboles et comprÃ©hension initiale du dÃ©pÃ´t. ModÃ¨le privilÃ©giÃ© : Inkling ;
  fallback North Mini Code, sÃ©lection explicite.
- **PLAN** : analyse, plan initial, revue et dÃ©composition sans modification
  du code. Choix explicite selon la difficultÃ© : Inkling pour facile,
  DeepSeek pour moyen, MiMo sous Hermes pour difficile ou grosse brique.
- **BUILD** : modifications effectives, tests, correctifs et refactorisations.
  ModÃ¨le privilÃ©giÃ© : DeepSeek. Inkling est possible pour une tÃ¢che simple et
  peu risquÃ©e. Une grosse brique attend la validation du plan Hermes/MiMo.
- **GENERAL** : sous-tÃ¢ches multiÃ©tapes ou unitÃ©s de travail dÃ©lÃ©guÃ©es ; le
  modÃ¨le est choisi explicitement selon la sous-tÃ¢che, sans sÃ©lection cachÃ©e.
- **RAG-NEMOTRON** : recherche et synthÃ¨se en lecture seule sur des documents
  non confidentiels, comme apport de contexte Ã  Hermes.

Aucun agent ne choisit silencieusement un modÃ¨le payant. Aucun fallback ne doit
masquer le modÃ¨le rÃ©ellement utilisÃ©. L'objectif est cinq profils fonctionnels,
quatre niveaux (facile, moyen, difficile et RAG), un fallback gratuit explicite
et un modÃ¨le principal par rÃ´le â€” pas une multiplication d'agents.

## RÃ´les des harnesses

### OpenCode

Environnement principal d'exÃ©cution : exploration, travail sur les fichiers,
terminal, tests et profils sÃ©lectionnables. Il remplace progressivement
Copilot VS Code pour l'exÃ©cution, sans retirer le cockpit humain de VS Code ni
la solution de secours Copilot.

### Hermes

Orchestrateur des tÃ¢ches complexes : planification, coordination, suivi des
tÃ¢ches longues, appel d'OpenCode comme exÃ©cutant et gestion des handovers et de
la reprise. MiMo planifie et coordonne ; Nemotron peut apporter du contexte
documentaire non confidentiel.

Hermes ne duplique pas l'exÃ©cution qu'OpenCode rÃ©alise dÃ©jÃ , ne lance pas
plusieurs exÃ©cutants sur le mÃªme worktree et ne fait pas modifier une grosse
brique avant validation de son plan.

### DeepSeek Harness

Harness spÃ©cialisÃ© Ã  utiliser uniquement lorsque sa spÃ©cialisation apporte un
avantage rÃ©el. Il est installÃ©, mais sa finalisation technique et son rÃ´le
prÃ©cis restent Ã  valider en exploitation. Il ne devient pas un environnement
universel et ne duplique pas OpenCode ou Hermes.

## Chemin standard d'une tÃ¢che

1. L'humain dÃ©finit l'objectif depuis le cockpit VS Code.
2. Copilot 365 aide Ã  formuler l'architecture ou le ticket.
3. Gemini 4.0 Argon peut contre-auditer les dÃ©cisions importantes.
4. L'humain choisit explicitement le niveau : facile, moyen, difficile ou
   RAG/gros documents.
5. Le profil modÃ¨le-harness correspondant est sÃ©lectionnÃ© sans fallback
   silencieux.
6. Un seul exÃ©cutant agit sur une copie de travail donnÃ©e.
7. Les tests et validations appropriÃ©s sont exÃ©cutÃ©s.
8. Un compte rendu et un handover consignent le rÃ©sultat et l'Ã©tat de reprise.
9. L'humain valide avant commit, fusion ou dÃ©ploiement.
10. Les observations des tÃ¢ches rÃ©elles alimentent progressivement la matrice.

## Chemin Â« grosse brique Â»

Une grosse brique est une fonctionnalitÃ© structurante impliquant plusieurs
composants, des dÃ©cisions d'architecture, une migration, des dÃ©pendances
importantes ou un risque Ã©levÃ© pour DSH-Core.

1. Copilot 365 clarifie l'objectif et les contraintes, puis rÃ©dige le ticket.
2. Gemini 4.0 Argon contre-audite le besoin, les omissions et les risques
   (niveau de rÃ©flexion recommandÃ© : Ã©levÃ©).
3. Hermes avec MiMo standard conÃ§oit l'architecture, dÃ©compose le travail,
   attribue les sous-tÃ¢ches, fixe tests, critÃ¨res d'acceptation et points
   d'arrÃªt, puis prÃ©pare les handovers.
4. OpenCode avec DeepSeek 1017 exÃ©cute les sous-tÃ¢ches techniques, modifie les
   fichiers, lance les tests et rend compte.
5. DeepSeek Harness intervient seulement sur les catÃ©gories oÃ¹ sa
   spÃ©cialisation est utile ; son rÃ´le est ajustÃ© en tÃ¢che rÃ©elle.
6. Nemotron apporte au besoin du contexte RAG ou documentaire non confidentiel,
   mais ne devient pas automatiquement exÃ©cutant de code.
7. L'humain valide les dÃ©cisions importantes, arbitre les conflits et autorise
   commits, fusions et dÃ©ploiements.
8. Git et les handovers prÃ©servent les sources de vÃ©ritÃ© et permettent la
   reprise sans perdre l'Ã©tat du projet.

La sÃ©paration planification/exÃ©cution est explicite : pas de duplication entre
Hermes, OpenCode et DeepSeek Harness, pas de plusieurs exÃ©cutants actifs sur le
mÃªme worktree et pas de sÃ©lection de modÃ¨le cachÃ©e.

## Garde-fous et audit en situation rÃ©elle

- Confirmer que les conditions de donnÃ©es du fournisseur conviennent avant
  d'envoyer des informations. Les donnÃ©es confidentielles et personnelles
  sensibles ne vont jamais Ã  Nemotron gratuit ; les secrets et donnÃ©es client
  rÃ©elles ne sont pas transmis sans autorisation et garanties adaptÃ©es.
- Respecter les permissions et le pÃ©rimÃ¨tre d'une tÃ¢che ; privilÃ©gier la
  lecture seule pour l'analyse documentaire.
- Rendre visibles profil, modÃ¨le et harness rÃ©ellement utilisÃ©s, y compris
  tout fallback explicitement acceptÃ©.
- Maintenir une validation humaine avant toute Ã©criture importante, commit,
  fusion ou dÃ©ploiement.
- ArrÃªter les benchmarks artificiels. Ne pas crÃ©er de campagne comparative
  sÃ©parÃ©e : auditer les modÃ¨les et harnesses pendant le travail rÃ©el.

Pour chaque tÃ¢che significative, consigner les informations disponibles :

| DonnÃ©e | Valeur |
|---|---|
| Date et tÃ¢che | Date observÃ©e et rÃ©sumÃ© |
| DifficultÃ© prÃ©vue | Facile, moyen, difficile ou RAG |
| Harness, modÃ¨le, profil | Identifiants effectivement utilisÃ©s |
| DurÃ©e et coÃ»t | Mesures disponibles ; sinon `NON DISPONIBLE` |
| Tests et rÃ©sultat | Commandes, rÃ©sultats, rÃ©ussite ou Ã©chec |
| Corrections et interventions humaines | Nombre observÃ© |
| QualitÃ© du handover | Reprise possible et limites |
| Recommandation | Maintenir ou rÃ©affecter le rÃ´le, avec justification |

Ne pas inventer ni imposer la collecte des informations absentes. Une
observation hors du harness cible reste historique et ne suffit pas Ã  fixer
tous les rÃ´les futurs.

## Surfaces Hermes : Web Admin (9119) vs Desktop natif

Hermes expose deux surfaces d'interaction complémentaires :

- **Hermes Web Admin (port 9119)** : interface d'administration et de supervision accessible via navigateur. Elle permet de visualiser les tâches, les handovers, les métriques d'exécution, les logs structurés et l'état des agents. C'est la vue opérateur pour le pilotage, l'audit et le débogage des flux complexes. Elle ne remplace pas l'exécution locale ; elle l'observe et la commande à distance.

- **Hermes Desktop App** : application native (Electron/Tauri) installée localement, qui s'intègre au cockpit VS Code. Elle fournit un panneau latéral persistant pour lancer des plans, suivre l'avancement, recevoir les notifications de points d'arrêt et basculer entre les profils OpenCode sans quitter l'IDE. Le Desktop partage la même configuration OpenRouter que le Web Admin ; la clé API reste dans le secret Hermes et n'est jamais affichée.

Les deux surfaces lisent/écrivent le même journal de tâches et le même état de reprise. Le Web Admin est privilégié pour la supervision multi-projets ; le Desktop pour l'exécution quotidienne depuis VS Code.

## Combinaisons 100 % gratuites : Cloud et Offline

Deux parcours entièrement gratuits sont validés, sans clé payante ni quota garanti par tâche :

### Cloud gratuit (OpenRouter :free)
- **Facile / Exploration** : 	hinkingmachines/inkling:free (1 M tokens contexte) via profil 
acile-inkling / agent 
apide OpenCode.
- **Fallback gratuit** : cohere/north-mini-code:free via profil 
allback-north / agent secours OpenCode.
- **RAG / Gros documents** : 
vidia/nemotron-3-ultra-550b-a55b:free via profil 
ag-nemotron / agent 
ag Hermes.
- **Architecture / Planification** : xiaomi/mimo-v2.6-pro (standard, pas UltraSpeed) via profil difficile-mimo / agent plan ou rchitecte Hermes.

Ces quatre modèles partagent le quota quotidien OpenRouter commun aux routes :free (dernière lecture : limite 1 000, 33 utilisées, 967 restantes, reset minuit UTC). Aucun basculement automatique silencieux n'est permis ; le modèle réellement utilisé doit rester visible.

### Offline (exosquelette local Qwen + Skills)
- **Qwen 27B Expert** (qwen3.6:27b via Ollama) : t?ches lourdes de code, refactorisation, g?n?ration de tests, migration. Mod?le principal du chemin ? grosse brique ? en mode hors-ligne.
- **Qwen 9B Base** (qwen3.5:9b via Ollama) : exploration, compr?hension de d?p?t, petites propositions, t?ches rapides. ?quivalent local d'Inkling.
- **Skills locales** : les skills OpenCode/Hermes (ex. invoice_smtrt_sas, supplier_registration, extraction OCR) s'exécutent sur ces modèles sans appel cloud. Elles encapsulent la logique métier, la validation canonique et les contrôles ; le modèle ne fournit que le raisonnement et la génération de code.

L'exosquelette Offline s'active via le lanceur Demarrer_Cockpit_DSH_Bureau2.bat (bascule Bureau Virtuel Windows n°2, démarrage Ollama, Docker Compose, LangGraph API 8000, DeepSeek Harness 3080, Hermes 9119, VS Code). Aucune donnée ne sort du poste ; le quota OpenRouter n'est pas consommé.

## Attelages Chemin 3 : Standard vs Double Architecte

Le « Chemin 3 » (grosse brique) admet deux configurations d'attelage explicites :

| Attelage | Planificateur | Exécutant principal | RAG / Contexte | Usage |
|---|---|---|---|---|
| **Standard** | MiMo-V2.6-Pro (Hermes, profil difficile-mimo / agent plan ou rchitecte) | DeepSeek V4.1 Flash 1017 (OpenCode, profil moyen-deepseek / agent uild ou codeur) | Nemotron 3 Ultra 550B (Hermes, profil 
ag-nemotron / agent 
ag) | Brique standard : architecture validée, exécution déléguée, contexte documentaire à la demande. |
| **Double Architecte** | MiMo-V2.6-Pro (Hermes) **+** MiMo-V2.6-Pro (OpenCode agent rchitecte en lecture seule) | DeepSeek V4.1 Flash 1017 (OpenCode agent codeur) | Nemotron 3 Ultra 550B (Hermes agent 
ag) | Brique à risque élevé / multi-composants : deux passes d'architecture indépendantes (Hermes planifie, OpenCode audite sans modifier) avant exécution. L'agent rchitecte OpenCode a permission.edit: deny, permission.bash: deny. |

Le choix d'attelage est décidé par l'humain au ticket (Copilot 365 + Gemini 4.0 Argon) et consigné dans le handover. Aucun basculement implicite.

## Audit de non-régression continu via benchmarks/ et Langfuse

- **benchmarks/** : dataset OCR figé (enchmarks/data/ocr_baseline_texts.json, 10 cas, 60 champs, hashes SHA-256 conformes, sources inchangées). Interface de validation humaine locale (enchmarks/validate_ocr_ground_truth.py, Tkinter, hors ligne). Aucun benchmark artificiel ne doit être lancé ; les métriques sont collectées pendant les tâches réelles. Le runner d'extraction (enchmarks/extractor_benchmark_runner.py, 42 tests ciblés passés) reste un artefact de préparation sans résultat comparatif réel.
- **Langfuse** : traçabilité complète des exécutions (prompts, réponses, outils, coûts, latences, erreurs). Les tableaux de bord (port 3000) permettent de corréler modèle, harness, profil, durée, tokens et résultat par tâche. L'audit de non-régression compare les exécutions successives sur les mêmes entrées (même source_sha256 pour les propositions fournisseur, même corpus OCR pour l'extraction). Les seuils d'alerte sont définis par tâche (ex. régression > 5 % sur l'exactitude champ-à-champ, ou coût > 2× la médiane historique).
- **Règle** : toute tâche significative produit une entrée dans le tableau de bord d'audit (date, difficulté, harness/modèle/profil, durée/coût, tests/résultat, corrections humaines, qualité handover, recommandation). Les observations hors harness cible restent historiques et ne fixent pas les rôles futurs.
## Observation historique OCR

Un essai rÃ©el du ticket OCR sous OpenCode avec DeepSeek 1017 a obtenu 94/100 ;
Copilot VS Code avait obtenu 93/100 sur le mÃªme ticket. Conserver ces scores
comme observations historiques uniquement : ils ne relancent pas de benchmark
artificiel, ne dÃ©terminent pas tous les rÃ´les futurs et ne remplacent pas
l'audit des tÃ¢ches rÃ©elles avec leurs harnesses respectifs.

## Sources de vÃ©ritÃ© et continuitÃ©

Ordre d'aiguillage : dÃ©pÃ´t Git DSH-Core ; `docs/PROJECT_HANDOVER.md` comme
point d'entrÃ©e exÃ©cutif ; ce document pour le routage des modÃ¨les et harnesses ;
journaux et dÃ©cisions d'architecture ; handovers de tÃ¢ches ; mÃ©moire des outils
comme aide secondaire. Le prÃ©sent document sert d'aiguillage vers la mÃ©moire
unifiÃ©e, qui reste secondaire par rapport aux sources versionnÃ©es.






