import os
import json
import requests
import uuid
from datetime import datetime
from enum import Enum
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from duckduckgo_search import DDGS
from playwright.sync_api import sync_playwright

# Qdrant & LlamaIndex
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import Settings
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama

# Langfuse V4
from langfuse import observe

# Superviseur & Routeur de compétences (V8)
from agents.teacher_student_agent import TeacherStudentAgent
from repositories.skills_repository import SkillsRepository
from services.skill_router import SkillRouter

class SearchMode(str, Enum):
    QUICK = "quick"
    DEEP = "deep"
    TOR = "tor"
    DOCUMENT = "document"
    CONVERSATION = "conversation"

class ResearchResponse(BaseModel):
    status: str
    mode: SearchMode
    source_agent: str = "research_agent"
    result: str
    sources: List[Dict[str, str]] = []
    metadata: Dict[str, Any] = {}

class ResearchAgent:
    def __init__(self):
        print(">>> NOUVEL AGENT DE RECHERCHE V8 (MODEL-AGNOSTIC + SKILLROUTER) CHARGÉ <<<")
        
        # Principes de rôles V8
        self.base_role = "BASE_MODEL"
        self.model_name = os.getenv("BASE_MODEL", "qwen3.5:9b")
        self.embed_model_name = os.getenv("EMBED_MODEL", "nomic-embed-text")
        
        self.ollama_url = "http://localhost:11434/api/generate"
        
        Settings.llm = Ollama(model=self.model_name, request_timeout=60.0)
        Settings.embed_model = OllamaEmbedding(
            model_name=self.embed_model_name, 
            base_url="http://localhost:11434"
        )
        
        try:
            self.qdrant_client = QdrantClient(url="http://localhost:6333")
            if not self.qdrant_client.collection_exists("research_cache"):
                self.qdrant_client.create_collection(
                    collection_name="research_cache",
                    vectors_config=VectorParams(size=768, distance=Distance.COSINE)
                )
        except Exception as e:
            print(f"[ResearchAgent] Avertissement Qdrant : {e}")
            self.qdrant_client = None

        if self.qdrant_client:
            self.ts_agent = TeacherStudentAgent(qdrant_client=self.qdrant_client)
            self.doc_store = QdrantVectorStore(client=self.qdrant_client, collection_name="document_bus")
            self.doc_index = VectorStoreIndex.from_vector_store(vector_store=self.doc_store)
        else:
            self.ts_agent = None
            self.doc_index = None

    def _rule_router(self, query: str) -> Optional[SearchMode]:
        q = query.lower().strip()
        if ".onion" in q: return SearchMode.TOR
        if q.startswith("http://") or q.startswith("https://"): return SearchMode.DOCUMENT
        if len(q.split()) > 15: return SearchMode.DEEP
            
        mots_cles_deep = ["analyse", "comparatif", "synthèse", "rapport", "étude", "veille"]
        if any(word in q for word in mots_cles_deep): return SearchMode.DEEP
            
        return None

    def _analyze_query(self, raw_query: str) -> dict:
        mode = self._rule_router(raw_query)
        if mode:
            print(f"[ResearchAgent] Routage Déterministe activé : {mode}")
            return {"requires_search": True, "depth": mode, "optimized_query": raw_query}

        date_du_jour = datetime.now().strftime("%d %B %Y")
        prompt = f"""Tu es l'architecte de recherche de DSH-Core. Date actuelle : {date_du_jour}.
Analyse cette demande : "{raw_query}"
Détermine la stratégie d'exécution et fournis une réponse UNIQUEMENT en JSON strict.
1. requires_search: true ou false (si salutations simples).
2. depth: "quick" (actu, faits simples, scores).
3. optimized_query: 3 à 6 mots-clés SEO ultra précis.
Format: {{"requires_search": true, "depth": "quick", "optimized_query": "mots clés"}}"""

        try:
            response = requests.post(
                self.ollama_url,
                json={"model": self.model_name, "prompt": prompt, "stream": False, "format": "json"},
                timeout=15
            )
            if response.status_code == 200:
                raw_response = response.json().get("response", "{}")
                clean_response = raw_response.replace("```json", "").replace("```", "").strip()
                data = json.loads(clean_response)
                
                return {
                    "requires_search": data.get("requires_search", True),
                    "depth": SearchMode(data.get("depth", "quick")),
                    "optimized_query": data.get("optimized_query", raw_query)
                }
        except Exception:
            pass
        
        return {"requires_search": True, "depth": SearchMode.QUICK, "optimized_query": raw_query}

    @observe(name="ResearchAgent_Execution")
    def search_and_structure(self, query: str) -> dict:
        # BRANCHEMENT V8 : Injection du Skill contextuel
        skill_context = ""
        if self.qdrant_client:
            try:
                skills_repo = SkillsRepository(self.qdrant_client)
                router = SkillRouter(skills_repo=skills_repo)
                routing_res = router.retrieve_skill_for_task(task=query)
                if routing_res:
                    print(f"[ResearchAgent] 💡 Compétence mémorisée injectée pour le rôle [{self.base_role}] !")
                    skill_context = "\n" + routing_res + "\n"
            except Exception as e:
                print(f"[ResearchAgent] Erreur consultation SkillRouter : {e}")

        analysis = self._analyze_query(query)
        depth = analysis["depth"]
        opt_query = analysis["optimized_query"]

        if not analysis["requires_search"]:
            return ResearchResponse(
                status="success", mode=SearchMode.CONVERSATION, result="Pas de recherche web."
            ).model_dump()

        if depth == SearchMode.TOR: return self._strategy_tor(opt_query)
        if depth == SearchMode.DEEP: return self._strategy_playwright(opt_query, skill_context)
        if depth == SearchMode.DOCUMENT: return self._strategy_document(opt_query, skill_context)

        return self._strategy_ddgs(opt_query, skill_context)

    def _check_cache(self, query: str) -> Optional[str]:
        if not self.qdrant_client: return None
        query_hash = str(uuid.uuid5(uuid.NAMESPACE_DNS, query))
        try:
            points = self.qdrant_client.retrieve(collection_name="research_cache", ids=[query_hash])
            if points:
                print("[ResearchAgent] CACHE HIT Qdrant ! Retour immédiat.")
                return points[0].payload.get("result")
        except Exception:
            pass
        return None

    def _save_cache(self, query: str, mode: str, result: str):
        if not self.qdrant_client: return
        query_hash = str(uuid.uuid5(uuid.NAMESPACE_DNS, query))
        try:
            embed_vector = Settings.embed_model.get_text_embedding(query)
            self.qdrant_client.upsert(
                collection_name="research_cache",
                points=[PointStruct(
                    id=query_hash,
                    vector=embed_vector,
                    payload={"query": query, "mode": mode, "result": result, "created_at": datetime.now().isoformat()}
                )]
            )
        except Exception as e:
            print(f"[ResearchAgent] Échec de sauvegarde cache : {e}")

    @observe(name="Strategy_Quick_DDGS")
    def _strategy_ddgs(self, optimized_query: str, skill_context: str = "") -> dict:
        sources = []
        try:
            ddgs = DDGS()
            for r in list(ddgs.text(optimized_query, region="fr-fr", max_results=4)):
                sources.append({"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")})

            result_text = f"{skill_context}Données web (Quick):\n" + "\n".join([f"- {s['title']}: {s['snippet']}" for s in sources])
            
            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_quick", task=optimized_query, base_success=True)

            return ResearchResponse(
                status="success", mode=SearchMode.QUICK, result=result_text, sources=sources,
                metadata={"cache_hit": False, "llm_used": False}
            ).model_dump()

        except Exception as e:
            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_quick", task=optimized_query, base_success=False, error_context=str(e))
            return ResearchResponse(status="error", mode=SearchMode.QUICK, result=str(e)).model_dump()

    @observe(name="Strategy_Deep_Playwright")
    def _strategy_playwright(self, optimized_query: str, skill_context: str = "") -> dict:
        cached = self._check_cache(optimized_query)
        if cached:
            return ResearchResponse(
                status="success", mode=SearchMode.DEEP, result=skill_context + cached,
                metadata={"cache_hit": True, "pages_scraped": 0, "llm_used": False}
            ).model_dump()

        urls_to_scrape = []
        try:
            ddgs = DDGS()
            urls_to_scrape = [r.get("href") for r in list(ddgs.text(optimized_query, region="fr-fr", max_results=3))]
        except Exception: pass

        scraped_texts = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context()
                for url in urls_to_scrape:
                    try:
                        page = ctx.new_page()
                        page.goto(url, timeout=15000, wait_until="domcontentloaded")
                        scraped_texts.append(f"Source {url}: " + " ".join(page.inner_text("body").split())[:3000])
                        page.close()
                    except Exception: pass
                browser.close()
        except Exception: pass

        context_str = "\n".join(scraped_texts)
        prompt = f"{skill_context}Rédige une synthèse approfondie sur '{optimized_query}' via ces sources:\n{context_str}"
        
        try:
            resp = requests.post(self.ollama_url, json={"model": self.model_name, "prompt": prompt, "stream": False}, timeout=300)
            if resp.status_code != 200:
                raise ValueError("Erreur API LLM (Ollama).")
                
            final_analysis = resp.json().get("response", "Erreur LLM.")
            self._save_cache(optimized_query, "deep", final_analysis)
            
            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_deep", task=optimized_query, base_success=True)

            return ResearchResponse(
                status="success", mode=SearchMode.DEEP, result=final_analysis,
                metadata={"cache_hit": False, "pages_scraped": len(scraped_texts), "llm_used": True, "model": self.model_name}
            ).model_dump()

        except Exception as e:
            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_deep", task=optimized_query, base_success=False, error_context=str(e))
            return ResearchResponse(status="error", mode=SearchMode.DEEP, result=str(e)).model_dump()

    @observe(name="Strategy_Document_Qdrant")
    def _strategy_document(self, query: str, skill_context: str = "") -> dict:
        if not getattr(self, "doc_index", None):
            return ResearchResponse(status="error", mode=SearchMode.DOCUMENT, result="Qdrant hors ligne ou index non initialisé.").model_dump()

        try:
            retriever = self.doc_index.as_retriever(similarity_top_k=5)
            nodes = retriever.retrieve(query)
            context = "\n\n".join([n.text for n in nodes])
            
            prompt = f"{skill_context}Question : {query}\n\nContexte extrait des documents :\n{context}\n\nRéponds uniquement à partir de ce contexte."
            
            resp = requests.post(
                self.ollama_url, 
                json={"model": self.model_name, "prompt": prompt, "stream": False},
                timeout=300
            )
            
            if resp.status_code != 200:
                raise ValueError("Erreur LLM Document.")
                
            answer = resp.json().get("response", "")

            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_document", task=query, base_success=True)

            return ResearchResponse(
                status="success", mode=SearchMode.DOCUMENT, result=answer,
                metadata={"cache_hit": False, "nodes_retrieved": len(nodes), "llm_used": True}
            ).model_dump()

        except Exception as e:
            if getattr(self, "ts_agent", None):
                self.ts_agent.handle_task_result(workflow="research_document", task=query, base_success=False, error_context=str(e))
            return ResearchResponse(status="error", mode=SearchMode.DOCUMENT, result=str(e)).model_dump()

    def _strategy_tor(self, query: str) -> dict:
        return ResearchResponse(status="success", mode=SearchMode.TOR, result="[TOR SOCKS5 non configuré]").model_dump()