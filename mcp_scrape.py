import sys
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("DSH-Tor-Scraper")

@mcp.tool()
def scrape_page(url: str) -> str:
    """Scrape le texte d'un site Web (Standard ou Onion) via le réseau TOR."""
    
    # Configuration du proxy Tor (9150 pour Tor Browser, 9050 pour Tor Expert Bundle)
    proxies = {
        "http://": "socks5h://127.0.0.1:9150",
        "https://": "socks5h://127.0.0.1:9150"
    }
    
    try:
        # Client httpx configuré pour passer par Tor
        with httpx.Client(proxies=proxies, timeout=45.0, follow_redirects=True) as client:
            # Fake User-Agent pour éviter de se faire bloquer par les pare-feux basiques
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
            
            response = client.get(url, headers=headers)
            response.raise_for_status()
            
            # On récupère le texte HTML. 
            # (Optionnel : on pourrait ajouter BeautifulSoup ici pour nettoyer les balises)
            texte_brut = response.text
            
            # On limite la taille du texte retourné pour ne pas saturer la VRAM de ton Qwen 9B/27B
            limite_caracteres = 15000
            if len(texte_brut) > limite_caracteres:
                return texte_brut[:limite_caracteres] + "\n\n[... TEXTE TRONQUÉ POUR PRÉSERVER LA MÉMOIRE ...]"
            
            return texte_brut

    except Exception as e:
        return f"Erreur de scraping TOR : Impossible d'accéder à {url}. Détail : {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")