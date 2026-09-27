import sys
import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("dsh-scrape-server")

@mcp.tool()
def scraper_page_web(url: str) -> str:
    """Scrape le contenu textuel d'une page web standard ou d'un lien .onion (via Tor)."""
    try:
        # Envoie la requête à ton cerveau central FastAPI sur le port 8000
        res = httpx.post("http://127.0.0.1:8000/api/scrape", json={"url": url}, timeout=45.0)
        
        if res.status_code == 200:
            data = res.json()
            return f"Succès du scraping ({data.get('content_length')} caractères) :\n\n{data.get('text_snippet')}"
        else:
            return f"Erreur de l'API : {res.text}"
            
    except Exception as e:
        return f"Erreur de connexion au routeur local : {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")