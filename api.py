import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup

from agents.research_agent import ResearchAgent
from agents.coder_agent import CoderAgent
import memory_manager

app = FastAPI(title="DSH Core API", version="1.0")

# Autorise Cherry Studio, AnythingLLM et n8n à communiquer avec l'API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

research_agent = ResearchAgent()
coder_agent = CoderAgent()

# -------------------------------------------------------------
# Modèles de données (Requêtes)
# -------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str

class CodeRequest(BaseModel):
    task: str
    filename: str

class ExecuteRequest(BaseModel):
    filename: str

class AutoCodeRequest(BaseModel):
    task: str
    filename: str
    max_retries: int = 2

class RouterRequest(BaseModel):
    prompt: str
    image_path: str = None
    complexite_haute: bool = False

class ScrapeRequest(BaseModel):
    url: str

class RegisterSkillRequest(BaseModel):
    name: str
    description: str
    triggers: list[str]
    code: str

# -------------------------------------------------------------
# Endpoints de base & Agents
# -------------------------------------------------------------

@app.get("/")
def read_root():
    return {"status": "online"}

@app.post("/api/research")
def run_research(request: QueryRequest):
    return research_agent.search_and_structure(request.query)

@app.post("/api/code")
def run_coder(request: CodeRequest):
    return coder_agent.write_code(request.task, request.filename)

@app.post("/api/execute")
def run_execute(request: ExecuteRequest):
    return coder_agent.execute_code(request.filename)

@app.post("/api/autocode")
def run_autocode(request: AutoCodeRequest):
    try:
        return coder_agent.auto_code_and_fix(request.task, request.filename, request.max_retries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------
# Endpoints Router & Scraping (Tor / Web)
# -------------------------------------------------------------

@app.post("/api/router")
def run_router(request: RouterRequest):
    """Aiguillage automatique vers le bon modèle local ou cloud."""
    try:
        from router import router_intelligent
        result = router_intelligent(
            prompt=request.prompt,
            image_path=request.image_path,
            complexite_haute=request.complexite_haute
        )
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scrape")
def run_scrape(request: ScrapeRequest):
    """
    Scrape le contenu textuel d'une page :
    - Web standard : connexion directe
    - Lien .onion : teste automatiquement le port 9150 (Tor Browser) puis 9050 (Service Tor)
    """
    try:
        proxies = None
        if ".onion" in request.url:
            proxy_url = "socks5://127.0.0.1:9150"
            try:
                with httpx.Client(proxy=proxy_url, timeout=3.0) as test_client:
                    pass
            except Exception:
                proxy_url = "socks5://127.0.0.1:9050"
        else:
            proxy_url = None

        with httpx.Client(proxy=proxy_url, timeout=45.0, follow_redirects=True) as client:
            response = client.get(
                request.url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)

            return {
                "status": "success",
                "url": request.url,
                "content_length": len(text),
                "text_snippet": text[:3000]
            }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur de scraping (Tor doit être ouvert si lien .onion) : {str(e)}"
        )

# -------------------------------------------------------------
# Endpoints Bibliothèque de Compétences (Skills & Mémoire)
# -------------------------------------------------------------

@app.post("/api/skills/register")
def api_register_skill(request: RegisterSkillRequest):
    """Enregistre un code autonome généré par un modèle pour le rendre accessible à tous."""
    try:
        return memory_manager.register_skill(
            name=request.name,
            description=request.description,
            triggers=request.triggers,
            code=request.code
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skills/find")
def api_find_skill(query: str):
    """Vérifie si une compétence existe déjà pour répondre à une commande."""
    try:
        match = memory_manager.find_skill(query)
        if match:
            return {"found": True, "skill": match}
        return {"found": False, "skill": None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skills")
def api_list_skills():
    """Liste toutes les compétences enregistrées dans le catalogue."""
    try:
        return memory_manager.list_all_skills()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))