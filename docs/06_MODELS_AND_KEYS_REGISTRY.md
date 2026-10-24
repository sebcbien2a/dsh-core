# Registre des modèles et clés

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
| `numind/nuextract3:q4_k_m` | 3.4 GB | extraction | non | RÉFÉRENCÉ MAIS RÔLE INCERTAIN (tests/scripts seulement) | benchmarker |
| `qwen3.5:4b` | 3.4 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `qwen3.5:2b` | 2.7 GB | extraction, général léger | non | RÉFÉRENCÉ ET ACTIF (code d’extraction/routage) | conserver |
| `qwen2.5vl:7b` | 6.0 GB | vision, OCR | non | RÉFÉRENCÉ ET ACTIF (routeur vision) | conserver |
| `nomic-embed-text:latest` | 274 MB | embeddings | non | RÉFÉRENCÉ ET ACTIF (indexation et mémoire) | conserver |
| `qwen3.6:27b` | 17 GB | coding, raisonnement | non | RÉFÉRENCÉ ET ACTIF (agents/routeur) | conserver |
| `qwen3.5:9b` | 6.6 GB | général, coding | non | RÉFÉRENCÉ ET ACTIF (agents, mémoire et routeur) | conserver |
| `qwen2.5-coder:7b` | 4.7 GB | coding | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `llama3.2:latest` | 2.0 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `llama3.2-vision:latest` | 7.8 GB | vision | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `deepseek-r1:32b` | 19 GB | raisonnement | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `qwen2.5-coder:32b` | 19 GB | coding | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `deepseek-coder-v2:16b` | 8.9 GB | coding | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `mistral-small:latest` | 14 GB | général | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `hermes3:latest` | 4.7 GB | général, agent | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |
| `deepseek-r1:14b` | 9.0 GB | raisonnement | non | INSTALLÉ MAIS NON RÉFÉRENCÉ | benchmarker |

Aucun des identifiants ci-dessus n’est « référencé mais non installé » selon
la comparaison exacte effectuée.

## Modèles cloud configurés ou prévus

| Fournisseur | Identifiant exact | Rôle | Variable associée | Statut |
|---|---|---|---|---|
| OpenRouter (sélection Hermes) | `anthropic/claude-opus-4.6` | général, raisonnement | `OPENROUTER_API_KEY` | modèle sélectionné ; fournisseur non configuré selon `hermes doctor` |
| NVIDIA via OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | préparation, orchestration RAG, review | `CLOUD_PREPARATION_MODEL`, `CLOUD_REVIEW_MODEL` | cible sélectionnée, intégration non validée |
| Xiaomi via OpenRouter | `xiaomi/mimo-v2.6-pro` | expert principal, vision, coding, missions agentiques longues ; entrées texte, image, audio et vidéo ; sortie texte | `CLOUD_EXPERT_MODEL`, `CLOUD_VISION_MODEL`, `CLOUD_CODING_MODEL` | **CIBLE SÉLECTIONNÉE, INTÉGRATION NON VALIDÉE** |
| DeepSeek via OpenRouter | `deepseek/deepseek-v4.1-flash` | premier fallback agentique | `CLOUD_AGENTIC_FALLBACK_MODEL` | fallback cible, intégration non validée |
| Google via OpenRouter | `google/gemini-3.8-flash` | second fallback multimodal, PDF et catalogues complexes, images multiples | `CLOUD_MULTIMODAL_FALLBACK_MODEL` | fallback cible, intégration non validée |

Cette sélection reste réversible et ne signifie pas que MiMo est toujours
supérieur à DeepSeek. Réaliser un benchmark comparatif sur 5 à 10 missions
réelles avant validation de l’intégration. Ne pas figer les tarifs : les
conditions tarifaires doivent être revérifiées avant utilisation.
Les variables locales, rôles cloud et fallbacks sont définis dans `.env.example`.
Le mode prévu est `AI_MODE=hybrid`. Les identifiants de modèles ne doivent
pas être codés en dur : le code devra les lire depuis les variables de rôle.
Les calculs tarifaires et les écritures comptables restent hors LLM.
Migration future de Nomic vers BGE-M3 : À BENCHMARKER avant toute adoption.

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
- `qwen2.5vl:7b` : `router.py`, `generate_journal.py` et journaux ciblés.
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
- `%LOCALAPPDATA%\hermes\.env` : Hermes.
- `%USERPROFILE%\.dsh\.env` : DeepSeek Harness.

Ne pas partager ni copier automatiquement des clés entre ces fichiers. Les
noms Hermes ci-dessous sont ceux observés dans sa configuration ; cela ne
vérifie pas un éventuel stockage distinct (par exemple trousseau ou
gestionnaire d’identifiants).

| Variable | Fournisseur / rôle | Fichier attendu ou observé | Présence |
|---|---|---|---|
| `LANGFUSE_SECRET_KEY` | Langfuse, traçage DSH | `A:\dsh-core\.env` | oui |
| `OPENROUTER_API_KEY` | OpenRouter, modèles cloud DSH | `A:\dsh-core\.env` | non dans les dotenv inspectés ; exemple vide |
| `OPENROUTER_API_KEY` | OpenRouter, fournisseur distant Hermes | `%LOCALAPPDATA%\hermes\.env` ou authentification Hermes | non dans dotenv ; OpenRouter non configuré |
| `ANTHROPIC_API_KEY` | Anthropic | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `OPENAI_API_KEY` | OpenAI | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `GEMINI_API_KEY`, `GOOGLE_API_KEY` | Google Gemini | `%LOCALAPPDATA%\hermes\config.yaml` (noms attendus) | non dans dotenv |
| `NVIDIA_API_KEY` | NVIDIA | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `NOUS_API_KEY` | Nous | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `ARCEEAI_API_KEY` | Arcee AI | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `DEEPINFRA_API_KEY` | DeepInfra | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `AI_GATEWAY_API_KEY` | AI Gateway | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `GLM_API_KEY` | GLM | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `GROQ_API_KEY` | Groq | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `KIMI_API_KEY` | Kimi | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `MINIMAX_API_KEY`, `MINIMAX_CN_API_KEY` | MiniMax | `%LOCALAPPDATA%\hermes\config.yaml` (noms attendus) | non dans dotenv |
| `MISTRAL_API_KEY` | Mistral | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `XIAOMI_API_KEY` | Xiaomi | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `KILOCODE_API_KEY` | Kilo Code | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `LM_API_KEY` | fournisseur LM, à préciser | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `MODEL_API_KEY` | endpoint de modèle personnalisé | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `MY_PROXY_API_KEY` | proxy personnalisé | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `RAMP_ROUTER_API_KEY` | Ramp Router | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `OLLAMA_API_KEY` | Ollama / endpoint compatible | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `BROWSERBASE_API_KEY` | Browserbase | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `CF_ACCESS_SECRET` | Cloudflare Access | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `HF_TOKEN` | Hugging Face | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `GITHUB_TOKEN`, `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub | `%LOCALAPPDATA%\hermes\config.yaml` (noms attendus) | non dans dotenv |
| `HONCHO_API_KEY` | Honcho | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `THETA_API_KEY` | Theta | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `HERMES_DASHBOARD_OIDC_CLIENT_SECRET` | authentification OIDC du tableau Hermes | `%LOCALAPPDATA%\hermes\config.yaml` (nom attendu) | non dans dotenv |
| `BWS_ACCESS_TOKEN`, `OP_SERVICE_ACCOUNT_TOKEN` | gestionnaire de secrets / service account | `%LOCALAPPDATA%\hermes\config.yaml` (noms attendus) | non dans dotenv |

`DEEPSEEK_API_KEY` n’a été trouvé ni dans les noms de configuration Hermes
ni dans les fichiers DSH ciblés : il n’est pas ajouté comme variable attendue.
Le fichier `%USERPROFILE%\.dsh\.env` est absent. La clé Hermes reste séparée
dans `%LOCALAPPDATA%\hermes\.env` ou dans son mécanisme d’authentification.

## Procédure de récupération

1. Retrouver le nom de la variable et le fichier local prévu dans ce registre.
2. Si la valeur est perdue, générer une nouvelle clé chez le fournisseur.
3. Enregistrer la nouvelle valeur dans le fichier local prévu, sans l’ajouter à Git.
4. Révoquer l’ancienne clé si elle a été perdue ou exposée.
