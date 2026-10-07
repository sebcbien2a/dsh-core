# Validation ResearchAgent V5

## Test 1 : Quick Search (Déterministe)
- **Question :** Quel est le PDG de Nvidia ?
- **Attendu :** Mode "quick", exécution via DDGS uniquement. Temps < 10 secondes.

## Test 2 : Deep Search (LLM Router)
- **Question :** Analyse les annonces IA de Microsoft, Google et Meta cette semaine.
- **Attendu :** Mode "deep", appel à Playwright, scraping, puis synthèse Qwen. Temps < 90 secondes.

## Test 3 : Document Search (Déterministe / Qdrant)
- **Question :** Retrouve les conditions Paintcar. (ou si l'URL commence par http://)
- **Attendu :** Mode "document", recherche interceptée et exécutée via Qdrant/LlamaIndex.

## Test 4 : TOR (Déterministe)
- **Question :** Recherche sur le lien https://xxxx.onion
- **Attendu :** Mode "tor", bascule immédiate sur le proxy SOCKS5.

## Test 5 : Cache Vectoriel
- **Action :** Relancer exactement la même requête Deep Search.
- **Attendu :** `cache_hit = true`, retour instantané de la synthèse sans relancer Playwright.

## Test 6 : Tracing Langfuse
- **Attendu :** La trace `ResearchAgent_Execution` est visible dans le dashboard Langfuse avec le décompte exact des tokens et du temps de chaque nœud.