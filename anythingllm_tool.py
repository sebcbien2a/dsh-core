# anythingllm_tool.py - Pont API dynamique entre l'agent local et AnythingLLM
import requests

ANYTHINGLLM_URL = "http://localhost:3001/api/v1"

# Ta clé API est maintenant bien formatée (avec les guillemets pour définir une chaîne de caractères)
API_KEY = "2ET5BC6-61R4S8Y-QG8C7MP-9WZEQWX" 

def lister_workspaces() -> str:
    """
    Outil 1 : Retourne la liste de tous les espaces de travail disponibles dans AnythingLLM.
    L'agent peut utiliser ça pour découvrir quels dossiers existent avant de chercher.
    """
    url = f"{ANYTHINGLLM_URL}/workspaces"
    headers = {"Authorization": f"Bearer {API_KEY}", "accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        workspaces = response.json().get("workspaces", [])
        
        if not workspaces:
            return "Aucun workspace trouvé dans AnythingLLM."
        
        resultats = "Espaces de travail (workspaces) disponibles :\n"
        for w in workspaces:
            resultats += f"- Nom: {w.get('name')} | Slug exact à utiliser: {w.get('slug')}\n"
        return resultats
        
    except Exception as e:
        return f"Erreur lors de la récupération des workspaces : {str(e)}"

def interroger_anythingllm(workspace_slug: str, question: str) -> str:
    """
    Outil 2 : Interroge un espace de travail spécifique d'AnythingLLM.
    L'agent doit fournir le 'workspace_slug' exact (trouvé avec lister_workspaces).
    """
    url = f"{ANYTHINGLLM_URL}/workspace/{workspace_slug}/chat"
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "accept": "application/json"
    }
    
    payload = {
        "message": question,
        "mode": "chat" 
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("textResponse", "Aucune réponse pertinente trouvée.")
        
    except Exception as e:
        return f"Erreur lors de la requête RAG sur '{workspace_slug}' : {str(e)}"

if __name__ == "__main__":
    print("=== TEST DE L'API DYNAMIQUE ANYTHINGLLM ===")
    print("1. Liste des dossiers disponibles :")
    print(lister_workspaces())