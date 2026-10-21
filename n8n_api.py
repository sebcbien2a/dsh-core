# n8n_api.py - Point d'entrée principal (Webhook) pour les requêtes n8n
import os
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from dotenv import load_dotenv

# Force le chargement du fichier .env AVANT le reste
load_dotenv()

import shutil
from pathlib import Path
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
import uvicorn

# IMPORT CORRIGÉ : On pointe vers le bon dossier et le bon nom de classe
from agents.invoice_agent import InvoiceAgent
from services.ingestion_manager import (
    IngestionManager,
    InvoiceIngestionValidationError,
)

app = FastAPI(
    title="DSH-Core API", 
    description="Points d'appui locaux pour l'orchestrateur n8n",
    version="1.0"
)

agent: InvoiceAgent | None = None
ingestion_manager = IngestionManager()
INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")

# URL CORRIGÉE : Correspond exactement à ce que tu as mis dans le nœud HTTP Request de n8n
@app.post("/api/v1/ingest")
async def process_invoice(
    file: UploadFile = File(...),
    # Keep response_mode in multipart form data alongside the existing upload.
    response_mode: Literal["legacy", "canonical"] = Form("legacy"),
):
    global agent
    try:
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        file_path = INPUT_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        print(f"\n[API] Nouvelle requête n8n reçue : {file.filename}")

        if response_mode == "canonical":
            document_id = uuid4()
            now = datetime.now(timezone.utc)
            result = ingestion_manager.ingest_canonical_invoice(
                file_path,
                document_id=document_id,
                created_at=now,
                updated_at=now,
            )
            return {
                "status": "success",
                "response_mode": "canonical",
                "document_id": str(document_id),
                "invoice": result.invoice.model_dump(mode="json"),
                "legacy_data": result.legacy_data,
                "warnings": result.warnings,
                "metadata": result.metadata,
            }

        if agent is None:
            print("Initialisation de l'Agent Facture (chargement des modèles en mémoire)...")
            agent = InvoiceAgent()
        facture_validee = agent.process_invoice(str(file_path))
        
        print(f"[API] Traitement terminé avec succès pour {file.filename}")
        
        # Renvoi du JSON structuré à n8n
        return facture_validee

    except InvoiceIngestionValidationError as e:
        if response_mode == "canonical":
            raise HTTPException(
                status_code=422,
                detail="Invoice ingestion validation failed",
            ) from e
        print(f"[API] Erreur lors du traitement : {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        if response_mode == "canonical":
            raise HTTPException(
                status_code=500,
                detail="Invoice ingestion failed",
            ) from e
        print(f"[API] Erreur lors du traitement : {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

if __name__ == "__main__":
    print("===================================================")
    print("  DSH-CORE API EN ÉCOUTE POUR N8N (PORT 8000)")
    print("===================================================")
    uvicorn.run(app, host="0.0.0.0", port=8000)