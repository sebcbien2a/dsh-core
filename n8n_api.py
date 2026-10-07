# n8n_api.py - Point d'entrée principal (Webhook) pour les requêtes n8n
import os
from dotenv import load_dotenv

# Force le chargement du fichier .env AVANT le reste
load_dotenv()

import shutil
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
import uvicorn

# IMPORT CORRIGÉ : On pointe vers le bon dossier et le bon nom de classe
from agents.invoice_agent import InvoiceAgent

app = FastAPI(
    title="DSH-Core API", 
    description="Points d'appui locaux pour l'orchestrateur n8n",
    version="1.0"
)

print("Initialisation de l'Agent Facture (chargement des modèles en mémoire)...")
# INSTANCIATION CORRIGÉE
agent = InvoiceAgent()

INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")
INPUT_DIR.mkdir(parents=True, exist_ok=True)

# URL CORRIGÉE : Correspond exactement à ce que tu as mis dans le nœud HTTP Request de n8n
@app.post("/api/v1/ingest")
async def process_invoice(file: UploadFile = File(...)):
    try:
        file_path = INPUT_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        print(f"\n[API] Nouvelle requête n8n reçue : {file.filename}")
        
        # Lancement du vrai agent
        facture_validee = agent.process_invoice(str(file_path))
        
        print(f"[API] Traitement terminé avec succès pour {file.filename}")
        
        # Renvoi du JSON structuré à n8n
        return facture_validee
        
    except Exception as e:
        print(f"[API] Erreur lors du traitement : {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("===================================================")
    print("  DSH-CORE API EN ÉCOUTE POUR N8N (PORT 8000)")
    print("===================================================")
    uvicorn.run(app, host="0.0.0.0", port=8000)