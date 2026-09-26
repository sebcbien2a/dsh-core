import sys
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("DSH-Skills-Registry")

@mcp.tool()
def enregistrer_competence(nom: str, description: str, declencheurs: list[str], code_python: str) -> str:
    """Enregistre un nouveau script Python dans la bibliothèque de compétences (utilisé par Claude)."""
    try:
        payload = {
            "name": nom,
            "description": description,
            "triggers": declencheurs,
            "code": code_python
        }
        res = httpx.post("http://127.0.0.1:8000/api/skills/register", json=payload, timeout=10.0)
        return f"Succès : {res.json()}"
    except Exception as e:
        return f"Erreur enregistrement : {str(e)}"

@mcp.tool()
def chercher_competence(recherche: str) -> str:
    """Cherche un script Python existant dans le catalogue pour répondre à la demande (utilisé par Qwen/Mistral)."""
    try:
        res = httpx.get(f"http://127.0.0.1:8000/api/skills/find?query={recherche}", timeout=10.0)
        data = res.json()
        if data.get("found"):
            return f"Compétence trouvée : {data['skill']}"
        return "Aucune compétence correspondante trouvée."
    except Exception as e:
        return f"Erreur recherche : {str(e)}"

@mcp.tool()
def executer_script_local(nom_fichier: str) -> str:
    """Exécute un script local via l'API locale sur le port 8000."""
    try:
        res = httpx.post("http://127.0.0.1:8000/api/execute", json={"filename": nom_fichier}, timeout=60.0)
        return f"Résultat de l'exécution :\n{res.text}"
    except Exception as e:
        return f"Erreur d'exécution : {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")