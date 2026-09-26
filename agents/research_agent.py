import json
from ddgs import DDGS

class ResearchAgent:
    def __init__(self, model_name="llama3.2"):
        print(">>> NOUVEL AGENT CHARGÉ EN MÉMOIRE <<<")
        self.model_name = model_name

    def _should_search(self, query: str) -> bool:
        keywords = ["2025", "2026", "nouveauté", "nouveautés", "actu", "actualité", "dernière", "derniers", "ollama", "vision"]
        return any(kw in query.lower() for kw in keywords)

    def search_and_structure(self, query: str) -> dict:
        if self._should_search(query):
            print(f"[ResearchAgent ({self.model_name})] Lancement de la recherche Web pour : '{query}'...")
            results = []
            try:
                with DDGS() as ddgs:
                    # Recherche ciblée France puis repli mondial si besoin
                    raw_results = list(ddgs.text(query, region="fr-fr", max_results=3))
                    if not raw_results:
                        raw_results = list(ddgs.text(query, region="wt-wt", max_results=3))

                    for r in raw_results:
                        results.append({
                            "title": r.get("title", ""),
                            "snippet": r.get("body", ""),
                            "url": r.get("href", "")
                        })
            except Exception as e:
                results = [{"error": f"Erreur de recherche : {str(e)}"}]

            return {
                "query": query,
                "type": "web_search",
                "results": results
            }
        else:
            print(f"[ResearchAgent ({self.model_name})] Mode Conversationnel (pas de recherche web requise).")
            return {
                "query": query,
                "type": "conversation",
                "response": "Échange conversationnel sans recherche Web."
            }
