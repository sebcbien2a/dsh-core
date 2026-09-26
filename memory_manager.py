# memory_manager.py - Gestionnaire unifie V2 (mem0, LlamaIndex & Competences)
import json
from pathlib import Path
import qdrant_client
from mem0 import Memory

from llama_index.core import Document, Settings, VectorStoreIndex, StorageContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama

# ==========================================
# 1. INITIALISATION LLAMAINDEX (BUS DOCUMENTAIRE)
# ==========================================
Settings.llm = Ollama(model="qwen3.5:9b", request_timeout=120.0)
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text:latest", request_timeout=120.0)

client_qdrant = qdrant_client.QdrantClient(url="http://localhost:6333")
vector_store = QdrantVectorStore(client=client_qdrant, collection_name="document_bus")
storage_context = StorageContext.from_defaults(vector_store=vector_store)

def indexer_document(texte: str, metadata: dict = None) -> bool:
    """Indexe un fragment ou un document technique dans la collection document_bus."""
    try:
        doc = Document(text=texte, metadata=metadata or {})
        VectorStoreIndex.from_documents([doc], storage_context=storage_context)
        print(" Document indexe avec succes dans document_bus.")
        return True
    except Exception as e:
        print(f" Erreur d'indexation LlamaIndex : {e}")
        return False

def interroger_documents(question: str) -> str:
    """Interroge le bus documentaire via RAG avec Qwen 3.5 9B."""
    try:
        index = VectorStoreIndex.from_vector_store(vector_store, embed_model=Settings.embed_model)
        query_engine = index.as_query_engine(llm=Settings.llm)
        reponse = query_engine.query(question)
        return str(reponse)
    except Exception as e:
        return f"Erreur lors de la recherche documentaire : {e}"

# ==========================================
# 2. CONFIGURATION MEMOIRE EPISODIQUE (MEM0)
# ==========================================
config_mem0 = {
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen3.5:9b"
        }
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text:latest"
        }
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "localhost",
            "port": 6333,
            "embedding_model_dims": 768
        }
    }
}

memoire_m0 = None
try:
    memoire_m0 = Memory.from_config(config_dict=config_mem0)
except Exception as e:
    print(f" Avertissement initialisation mem0 : {e}")

def ajouter_souvenir(utilisateur: str, texte: str):
    """Grave une nouvelle information contextuelle dans la memoire episodique."""
    if not memoire_m0:
        print("Moteur mem0 non disponible.")
        return
    print(f" Memorisation pour {utilisateur} : '{texte}'")
    memoire_m0.add(texte, user_id=utilisateur)

def recuperer_contexte(utilisateur: str, question: str) -> str:
    """Fouille les souvenirs episodiques lies a la demande."""
    if not memoire_m0:
        return "Moteur de memoire vectorielle non disponible."
        
    resultats = memoire_m0.search(question, filters={"user_id": utilisateur})
    
    if isinstance(resultats, dict) and "results" in resultats:
        items = resultats["results"]
    elif isinstance(resultats, list):
        items = resultats
    else:
        items = [resultats]
        
    if not items:
        return "Aucun souvenir pertinent trouve dans la memoire."
    
    contexte = "Souvenirs recuperes :\n"
    for res in items:
        if isinstance(res, dict):
            texte_memoire = res.get('memory', res.get('text', str(res)))
        else:
            texte_memoire = str(res)
        contexte += f"- {texte_memoire}\n"
    return contexte

# ==========================================
# 3. BIBLIOTHEQUE DE COMPETENCES (SKILLS)
# ==========================================
SKILLS_DIR = Path("A:/dsh-core/skills")
REGISTRY_FILE = SKILLS_DIR / "registry.json"

def init_skills():
    """Cree le dossier skills et le fichier de registre s'ils n'existent pas."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2, ensure_ascii=False)

def register_skill(name: str, description: str, triggers: list[str], code: str) -> dict:
    """Enregistre un script autonome et l'indexe dans le catalogue."""
    init_skills()
    clean_name = name.lower().replace(" ", "_").replace("-", "_")
    script_path = SKILLS_DIR / f"{clean_name}.py"
    
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)
    
    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        registry = json.load(f)
    
    registry[clean_name] = {
        "name": clean_name,
        "description": description,
        "triggers": triggers,
        "script_path": str(script_path)
    }
    
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    if memoire_m0:
        ajouter_souvenir(
            "system",
            f"Competence Python '{clean_name}' disponible : {description}. Mots-cles: {', '.join(triggers)}"
        )
        
    return {"status": "success", "skill": clean_name, "path": str(script_path)}

def find_skill(query: str) -> dict | None:
    """Recherche si une competence existante correspond aux declencheurs."""
    init_skills()
    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        registry = json.load(f)
    
    query_clean = query.lower()
    for name, skill in registry.items():
        for trigger in skill.get("triggers", []):
            if trigger.lower() in query_clean:
                return skill
    return None

def list_all_skills() -> dict:
    """Renvoie la liste des competences enregistrees."""
    init_skills()
    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# ==========================================
# 4. VALIDATION GLOBALE
# ==========================================
if __name__ == "__main__":
    print("=== TEST GLOBAL MEMORY MANAGER V2 ===")
    
    # 1. Test Mem0 sur Qdrant
    print("\n--- Test Mem0 ---")
    ajouter_souvenir("admin", "Le port Tor valide pour le scraping est 9150.")
    print(recuperer_contexte("admin", "Quel est le port Tor ?"))

    # 2. Test LlamaIndex sur Qdrant
    print("\n--- Test LlamaIndex ---")
    reponse_rag = interroger_documents("Quel port est utilise pour le routage Tor ?")
    print(f"Reponse RAG Qwen : {reponse_rag}")

    # 3. Test Skills
    print("\n--- Test Skills ---")
    res_skill = register_skill(
        name="ping_qdrant",
        description="Verifie la disponibilite de la base vectorielle",
        triggers=["ping", "qdrant_check"],
        code="print('Qdrant en ligne')"
    )
    print(f"Statut skill : {res_skill['status']}")