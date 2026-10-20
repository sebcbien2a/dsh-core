# Audit statique Web, scraping et Tor — 15 octobre 2026

## Périmètre et méthode

Audit statique des sources et configurations présentes dans le dépôt. Aucune
connexion Internet ou Tor, visite de site, exécution de service ni lecture de
donnée de navigation n'a été effectuée. Aucun secret ni URL .onion n'est
reproduit ici.

## Composants retrouvés

| Composant | Fonction et dépendances | Raccordement observé | Statut |
|---|---|---|---|
| `agents/research_agent.py::ResearchAgent` | Agent de recherche à stratégies; dépend notamment de `duckduckgo_search.DDGS`, Playwright, `requests` et Ollama. | `POST /api/research` appelle `search_and_structure`. La voie de recherche peut aussi consulter Qdrant et le routeur de skills. | EXISTANT MAIS PARTIEL |
| `ResearchAgent._strategy_ddgs` | Recherche Web rapide avec DDGS / DuckDuckGo. | Appelée pour les requêtes classées en mode rapide. | PARTIEL |
| `ResearchAgent._strategy_playwright` | Recherche approfondie : DDGS fournit les résultats, puis Playwright Chromium extrait le texte des pages avant synthèse par Ollama. | Appelée pour les requêtes classées en mode approfondi. | PARTIEL |
| `api.py::POST /api/scrape` | Récupération du texte d'une page Web via HTTPX et BeautifulSoup; reconnaît la chaîne `.onion` et tente un proxy SOCKS. | Route FastAPI présente. Aucun test d'exécution n'a été réalisé dans cet audit. | EXISTANT MAIS NON VALIDÉ |
| `mcp_scrape.py::scraper_page_web` | Wrapper MCP qui relaie l'URL à l'API locale `/api/scrape` avec HTTPX. | Le wrapper est présent; aucun raccordement à un hôte MCP actif n'est démontré. | PRÉSENT, RACCORDEMENT NON DÉMONTRÉ |
| `ResearchAgent._strategy_tor` | Stratégie Tor sélectionnée lorsque la requête contient `.onion`. | Le dispatch existe, mais la fonction retourne uniquement un succès textuel fixe. | STUB / NON FONCTIONNEL |
| Mentions de SearXNG | Le nom apparaît dans un document d'hébergement comme service envisagé. | Aucun code ni service SearXNG n'a été trouvé dans les fichiers Docker Compose examinés. | DOCUMENTÉ SEULEMENT |

## Constat Tor et scraping

- Le composant historique retrouvé est `ResearchAgent`; aucun code SearXNG
  n'est présent.
- La recherche Web utilise DDGS / DuckDuckGo. La stratégie approfondie utilise
  DDGS puis Playwright.
- La route `POST /api/scrape` existe encore dans `api.py`; le wrapper
  `scraper_page_web` est présent dans `mcp_scrape.py`, sans raccordement MCP
  actif démontré.
- Le code reconnaît les adresses contenant `.onion`. La route `/api/scrape`
  tente un proxy SOCKS au port 9150, puis au port 9050. Le contrôle du premier
  proxy ne réalise pas de requête réseau et ne certifie donc pas sa
  disponibilité.
- Aucune configuration Tor active n'a été trouvée dans Docker Compose.
- `ResearchAgent._strategy_tor` retourne un statut de succès avec un message
  textuel indiquant que SOCKS n'est pas configuré; aucune recherche n'est
  effectuée.
- La capacité Tor n'a pas disparu complètement : reconnaissance, dispatch et
  tentative de proxy subsistent. Elle n'a toutefois jamais été terminée ni
  validée. Aucun test automatisé de Tor ou du scraping n'a été trouvé.

## Risques identifiés

- Risque SSRF : les URL fournies à la route de scraping ne présentent pas de
  validation visible du schéma, de l'hôte ou des plages d'adresses.
- Les redirections suivies ne sont pas visiblement contrôlées; elles peuvent
  conduire vers des destinations locales ou internes.
- Aucun contrôle d'accès n'est visible sur la route de scraping.
- La voie Playwright navigue vers les résultats Web sans garde réseau visible.
- Le stub Tor renvoie un succès apparent sans réaliser de recherche.

Ces constats sont issus de la lecture statique; aucun comportement réseau n'a
été testé.

## Décision et dépendances

**Décision : REPORT AVEC CONSERVATION DOCUMENTAIRE.**

La remise en état Web/Tor ne bloque pas Qdrant, les skills, la banque ou
`InvoiceSchema`. Aucune installation ni correction ne doit être lancée
maintenant. La réactivation devra attendre un chantier distinct de sécurisation
et de validation.
