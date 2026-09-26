# n8n_api.py - Point d'entrée principal (Webhook) pour les requêtes n8n
import shutil
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
import uvicorn

# Import de notre agent existant
from invoice_agent import InvoiceProcessingAgent

app = FastAPI(
    title="DSH-Core API", 
    description="Points d'appui locaux pour l'orchestrateur n8n",
    version="1.0"
)

print("Initialisation de l'Agent Facture (chargement des modèles en mémoire)...")
agent = InvoiceProcessingAgent()

INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")
INPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/v1/process-invoice")
async def process_invoice(file: UploadFile = File(...)):
    """
    Point d'entrée pour n8n :
    1. Reçoit le fichier (PDF/Image) de n8n.
    2. Le sauvegarde dans le dossier input.
    3. Lance le pipeline OCR + Qwen + Pydantic.
    4. Renvoie le JSON structuré à n8n.
    """
    try:
        # 1. Sauvegarde temporaire du fichier reçu
        file_path = INPUT_DIR / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        print(f"\n[API] Nouvelle requête n8n reçue : {file.filename}")
        
        # 2. Traitement par l'agent (Surya + Qwen + Pydantic)
        # (L'agent va aussi déplacer l'image dans 'processed' et sauver le JSON localement)
        facture_validee = agent.traiter_facture(file_path)
        
        print(f"[API] Traitement terminé avec succès pour {file.filename}")
        
        # 3. Renvoi du JSON propre à n8n
        return facture_validee.model_dump()
        
    except Exception as e:
        print(f"[API] Erreur lors du traitement : {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("===================================================")
    print("  DSH-CORE API EN ÉCOUTE POUR N8N (PORT 8000)")
    print("===================================================")
    uvicorn.run(app, host="0.0.0.0", port=8000)