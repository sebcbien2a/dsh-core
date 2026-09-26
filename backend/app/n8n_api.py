"""
Phase 4 / 8 : API FastAPI Webhook (Le Pont n8n -> LangGraph)
Reçoit les requêtes HTTP de n8n et déclenche le graphe déterministe.
"""

from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
import uvicorn
import sys
import logging

# Ajout du chemin pour importer le graphe sanctuarisé
sys.path.append(r"A:\dsh-core\backend")
from app.graphs.invoice_graph import build_invoice_graph

# Configuration Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DSH-Core.API")

# Initialisation de l'application FastAPI
app = FastAPI(title="DSH-Core API Webhook pour n8n")
router = APIRouter(prefix="/api/v1")

# Instanciation de la machine d'états LangGraph (Une seule fois au démarrage)
logger.info("Démarrage de l'usine LangGraph...")
invoice_pipeline = build_invoice_graph()

# --- MODÈLES DE DONNÉES (ENTRÉE N8N) ---
class InvoicePayload(BaseModel):
    filename: str
    file_path: str
    source: str = "n8n"

# --- ROUTES ---
@router.post("/process-invoice")
async def process_invoice_webhook(payload: InvoicePayload):
    """
    Point d'entrée unique. n8n envoie un JSON avec le chemin du fichier.
    FastAPI injecte ce fichier dans le StateGraph.
    """
    logger.info(f"Webhook reçu de n8n pour le fichier : {payload.filename}")
    
    try:
        # 1. Construction de l'état initial strict attendu par InvoiceState
        initial_state = {
            "filename": payload.filename,
            "file_path": payload.file_path,
            "file_type": "unknown",
            "raw_text": "",
            "extracted_data": {},
            "validation_passed": False,
            "confidence_score": 0.0,
            "is_complex": False,
            "skill_file_path": "",
            "qdrant_payload": None,
            "error_message": "",
            "messages": [("user", f"Démarre le traitement pour {payload.filename}")]
        }
        
        # 2. Configuration de la session (Thread ID unique basé sur le nom du fichier)
        config = {"configurable": {"thread_id": f"thread_{payload.filename}"}}
        
        # 3. Déclenchement de la cascade LangGraph
        logger.info("Injection dans le pipeline LangGraph...")
        final_state = invoice_pipeline.invoke(initial_state, config)
        
        # 4. Réponse synchrone retournée à n8n
        return {
            "status": "success",
            "file_type": final_state.get("file_type"),
            "is_complex": final_state.get("is_complex"),
            "skill_created": final_state.get("skill_file_path") != "",
            "data": final_state.get("extracted_data")
        }
        
    except Exception as e:
        logger.error(f"Erreur critique dans le pipeline : {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Enregistrement des routes
app.include_router(router)

if __name__ == "__main__":
    logger.info("Lancement du serveur Uvicorn sur le port 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)