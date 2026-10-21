import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup

from api_routes.canonical_skills import router as canonical_skills_router
from agents.research_agent import ResearchAgent
from agents.coder_agent import CoderAgent
import memory_manager
from orchestrator import dsh_graph


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


app = FastAPI(title="DSH Core API", version="1.0")

# Activation explicite uniquement jusqu'à l'ajout de l'authentification.
if _env_flag_enabled("DSH_ENABLE_CANONICAL_SKILLS_API"):
    app.include_router(canonical_skills_router)

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

class GraphRequest(BaseModel):
    task: str

# -------------------------------------------------------------
# Endpoints de base & Agents
# -------------------------------------------------------------

@app.get("/")
def read_root():
    return {"status": "online"}

@app.post("/api/research")
def run_research(request: QueryRequest):
    resultat_brut = research_agent.search_and_structure(request.query)
    return {
        "status": "success",
        "agent": "research_scraper",
        "result": resultat_brut
    }

@app.post("/api/code")
def run_coder(request: CodeRequest):
    resultat_brut = coder_agent.write_code(request.task, request.filename)
    return {
        "status": "success",
        "agent": "coder_agent",
        "result": resultat_brut
    }

@app.post("/api/execute")
def run_execute(request: ExecuteRequest):
    resultat_brut = coder_agent.execute_code(request.filename)
    return {
        "status": "success",
        "agent": "executor_agent",
        "result": resultat_brut
    }

@app.post("/api/autocode")
def run_autocode(request: AutoCodeRequest):
    try:
        resultat_brut = coder_agent.auto_code_and_fix(request.task, request.filename, request.max_retries)
        return {
            "status": "success",
            "agent": "autocoder_agent",
            "result": resultat_brut
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------
# Endpoints Router & Scraping (Tor / Web)
# -------------------------------------------------------------

@app.post("/api/router")
def run_router(request: RouterRequest):
    try:
        from router import route_prompt
        result = route_prompt(
            prompt=request.prompt,
            image_provided=bool(request.image_path)
        )
        return {
            "status": "success", 
            "agent": "router",
            "result": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/scrape")
def run_scrape(request: ScrapeRequest):
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
                "agent": "scraper_tool",
                "result": {
                    "url": request.url,
                    "content_length": len(text),
                    "text_snippet": text[:3000]
                }
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
    try:
        result = memory_manager.register_skill(
            name=request.name,
            description=request.description,
            triggers=request.triggers,
            code=request.code
        )
        return {"status": "success", "agent": "memory_manager", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skills/find")
def api_find_skill(query: str):
    try:
        match = memory_manager.find_skill(query)
        result = {"found": True, "skill": match} if match else {"found": False, "skill": None}
        return {"status": "success", "agent": "memory_manager", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/skills")
def api_list_skills():
    try:
        result = memory_manager.list_all_skills()
        return {"status": "success", "agent": "memory_manager", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/orchestrator")
def run_langgraph(request: GraphRequest):
    try:
        initial_state = {"task": request.task, "complexity": "", "result": "", "status": "démarré"}
        final_state = dsh_graph.invoke(initial_state)
        
        return {
            "status": "success",
            "agent": "orchestrator",
            "result": {
                "complexity_detected": final_state["complexity"],
                "final_result": final_state["result"]
            }
        }
    except Exception as e:
        return {"status": "error", "agent": "orchestrator", "result": str(e)}