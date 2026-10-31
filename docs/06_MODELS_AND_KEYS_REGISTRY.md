---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe historique — Archives numérotées
contract_version: 1.0.1
status: FROZEN
upstream_dependencies: []
downstream_consumers: []
verification_tests: []
---

# Registre des modèles et clés

La matrice globale des modèles et harnesses, les rôles, le routage, les
fallbacks, le quota OpenRouter et l'audit en situation réelle sont définis
uniquement dans [`AI_MODEL_ROUTING_AND_HARNESSES.md`](AI_MODEL_ROUTING_AND_HARNESSES.md).
Le présent registre conserve les observations locales Ollama et les noms ou
emplacements de variables propres à DSH-Core ; il ne définit pas le routage
global OpenRouter ou Hermes.

État observé localement ; aucun modèle n’a été lancé ou supprimé. Les tailles
sont celles affichées par Ollama. Les références Git ont été recherchées par
identifiant exact dans les fichiers suivis ciblés ; l’absence de référence ne
prouve pas qu’un modèle est inutile.

## Règle de sécurité

Les valeurs des clés ne sont jamais documentées ni versionnées. Ce registre
conserve seulement leur nom, fournisseur et emplacement.

## Modèles Ollama installés

Les 16 modèles sont installés ; aucun n’était chargé lors de `ollama ps`.
« Actif » signifie référencé par du code ou un flux de travail suivi, pas
qu’un modèle était en cours d’exécution.

| Identifiant | Taille | Rôle probable | Chargé | Références / statut | Recommandation provisoire |
|---|---:|---|---|---|---|
| `numind/nuextract3:q4_k_m` | 3.4 GB | extraction | non | RÉFÉRENCÉ MAIS RÔLE INCERTAIN (tests/scripts seulement) | évaluer en tâches réelles |
| `qwen3.5:4b` | 3.4 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `qwen3.5:2b` | 2.7 GB | extraction, général léger | non | RÉFÉRENCÉ ET ACTIF (code d’extraction/routage) | conserver |
| `qwen2.5vl:7b` | 6.0 GB | vision, OCR | non | RETIR? / BANNI (motif `qwen2.5*`) | ne plus utiliser |
| `nomic-embed-text:latest` | 274 MB | embeddings | non | RÉFÉRENCÉ ET ACTIF (indexation et mémoire) | conserver |
| `qwen3.6:27b` | 17 GB | coding, raisonnement | non | RÉFÉRENCÉ ET ACTIF (agents/routeur) | conserver |
| `qwen3.5:9b` | 6.6 GB | général, coding | non | RÉFÉRENCÉ ET ACTIF (agents, mémoire et routeur) | conserver |
| `qwen2.5-coder:7b` | 4.7 GB | coding | non | BANNI (motif `qwen2.5*`) | ne plus utiliser |
| `llama3.2:latest` | 2.0 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `llama3.2-vision:latest` | 7.8 GB | vision | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `deepseek-r1:32b` | 19 GB | raisonnement | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `qwen2.5-coder:32b` | 19 GB | coding | non | BANNI (motif `qwen2.5*`) | ne plus utiliser |
| `deepseek-coder-v2:16b` | 8.9 GB | coding | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `mistral-small:latest` | 14 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `hermes3:latest` | 4.7 GB | général, agent | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |
| `deepseek-r1:14b` | 9.0 GB | raisonnement | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | évaluer en tâches réelles |

Aucun des identifiants ci-dessus n’est « référencé mais non installé » selon
la comparaison exacte effectuée.
Les benchmarks artificiels sont arrêtés : toute évaluation future de modèles
se fait pendant des tâches réelles, dans leur harness cible.

## Modèles cloud et routage global

Ce registre ne définit pas les modèles cloud ni leurs rôles. Consulter
[`AI_MODEL_ROUTING_AND_HARNESSES.md`](AI_MODEL_ROUTING_AND_HARNESSES.md), seule
source canonique pour la matrice globale OpenCode/Hermes, les fallbacks, le
quota gratuit partagé du compte OpenRouter et l'évaluation pendant les tâches
réelles. Les coûts par tâche sont variables selon les tokens, les outils, le
raisonnement et la route fournisseur ; aucun coût n'est garanti.

Les variables locales DSH-Core présentes dans `.env.example` concernent le
fonctionnement applicatif de ce dépôt ; elles ne définissent pas les profils
globaux OpenCode ou Hermes. Les calculs tarifaires et les écritures comptables
restent hors LLM.

Migration future de Nomic vers BGE-M3 : à évaluer uniquement pendant des tâches
réelles ; aucun benchmark artificiel ne sera lancé.

### Identifiants encore codés en dur, migration ultérieure vers model_router.py

La recherche exacte dans les fichiers suivis a relevé :

- `qwen3.5:2b` : `services/business_extraction_manager.py`,
  `services/extraction_manager.py`, `services/routing_engine.py`,
  `test_experimental_multitva.py`, `test_nuextract.py`,
  `test_profiler.py` et deux scripts/tests sous `backup/` et `docs/`.
- `qwen3.5:9b` : `agents/coder_agent.py`, `agents/research_agent.py`,
  `generate_journal.py`, `memory_manager.py`, `router.py`, tests et documents
  d’architecture/déploiement ciblés.
- `qwen3.6:27b` : `agents/coder_agent.py`, `generate_journal.py`, `router.py`
  et un journal d’installation.
- `qwen2.5vl:7b` : RETIR? (banni par le motif `qwen2.5*`).
- `numind/nuextract3:q4_k_m` : tests/scripts sous `backup/`, `docs/` et racine.
- `nomic-embed-text:latest` : `invoice_indexer.py`, `memory_manager.py`,
  `generate_journal.py`, tests et documents de déploiement ciblés.

Cette liste inclut aussi des tests et de la documentation ; elle ne signifie
pas que chaque occurrence est une configuration active.

## Emplacement des clés

Présence = variable définie avec une valeur non vide dans le fichier dotenv
indiqué ; les valeurs n’ont pas été affichées. `.env.example` est un modèle
sans secrets, créé mais non indexé à la date de ce relevé. La variable
`OPENROUTER_API_KEY` y est attendue mais laissée vide. Utiliser des
emplacements séparés :

- `A:\dsh-core\.env` : LangGraph et DSH-Core.
- `%LOCALAPPDATA%\hermes\.env` : clés et tokens Hermes (secrets).
- `%USERPROFILE%\.dsh\.env` : chemin cible envisagé pour DeepSeek Harness, non confirmé comme existant.

Ne pas partager ni copier automatiquement des clés entre ces fichiers. Les
noms Hermes ci-dessous sont ceux observés dans sa configuration ; leurs
valeurs se trouvent uniquement dans `%LOCALAPPDATA%\hermes\.env`.

| Variable | Fournisseur / rôle | Fichier attendu ou observé | Présence |
|---|---|---|---|
| `LANGFUSE_SECRET_KEY` | Langfuse, traçage DSH | `A:\dsh-core\.env` | oui |
| `OPENROUTER_API_KEY` | OpenRouter, modèles cloud DSH | `A:\dsh-core\.env` | non dans les dotenv inspectés ; exemple vide |
| `OPENROUTER_API_KEY` | OpenRouter, fournisseur distant Hermes | `%LOCALAPPDATA%\hermes\.env` (fichier observé) | configurée ; valeur jamais affichée ni documentée |
| `ANTHROPIC_API_KEY` | Anthropic | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `OPENAI_API_KEY` | OpenAI | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Google Gemini | `%LOCALAPPDATA%\hermes\.env` (noms attendus) | non observée |
| `NVIDIA_API_KEY` | NVIDIA | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `NOUS_API_KEY` | Nous | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `ARCEEAI_API_KEY` | Arcee AI | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `DEEPINFRA_API_KEY` | DeepInfra | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `AI_GATEWAY_API_KEY` | AI Gateway | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `GLM_API_KEY` | GLM | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `GROQ_API_KEY` | Groq | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `KIMI_API_KEY` | Kimi | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `MINIMAX_API_KEY`, `MINIMAX_CN_API_KEY` | MiniMax | `%LOCALAPPDATA%\hermes\.env` (noms attendus) | non observée |
| `MISTRAL_API_KEY` | Mistral | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `XIAOMI_API_KEY` | Xiaomi | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `KILOCODE_API_KEY` | Kilo Code | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `LM_API_KEY` | fournisseur LM, à préciser | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `MODEL_API_KEY` | endpoint de modèle personnalisé | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `MY_PROXY_API_KEY` | proxy personnalisé | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `RAMP_ROUTER_API_KEY` | Ramp Router | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `OLLAMA_API_KEY` | Ollama / endpoint compatible | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `BROWSERBASE_API_KEY` | Browserbase | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `CF_ACCESS_SECRET` | Cloudflare Access | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `HF_TOKEN` | Hugging Face | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `GITHUB_TOKEN`, `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub | `%LOCALAPPDATA%\hermes\.env` (noms attendus) | non observée |
| `HONCHO_API_KEY` | Honcho | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `THETA_API_KEY` | Theta | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `HERMES_DASHBOARD_OIDC_CLIENT_SECRET` | authentification OIDC du tableau Hermes | `%LOCALAPPDATA%\hermes\.env` (nom attendu) | non observée |
| `BWS_ACCESS_TOKEN`, `OP_SERVICE_ACCOUNT_TOKEN` | gestionnaire de secrets / service account | `%LOCALAPPDATA%\hermes\.env` (noms attendus) | non observée |

`%LOCALAPPDATA%\hermes\config.yaml` est un fichier de configuration non
secrète (`model.provider`, `model.default`, `model.base_url`,
`model.api_mode`, `smart_model_routing`) ; il ne contient aucune clé ni aucun
token.

`DEEPSEEK_API_KEY` n’a été trouvé ni dans les noms de configuration Hermes
ni dans les fichiers DSH ciblés : il n’est pas ajouté comme variable attendue.
Le fichier `%USERPROFILE%\.dsh\.env` est absent. Toutes les clés et tous les
tokens Hermes résident dans `%LOCALAPPDATA%\hermes\.env` ; ne jamais afficher,
recopier ou documenter leurs valeurs.

Les secrets ne doivent jamais être placés dans `config.yaml` ni versionnés.

## Procédure de récupération

1. Retrouver le nom de la variable et le fichier local prévu dans ce registre.
2. Si la valeur est perdue, générer une nouvelle clé chez le fournisseur.
3. Enregistrer la nouvelle valeur dans le fichier local prévu, sans l’ajouter à Git.
4. Révoquer l’ancienne clé si elle a été perdue ou exposée.
