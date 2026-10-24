# test_experimental_multitva.py
import os
import json
import ollama
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from typing import Optional

from services.document_profiler import DocumentProfiler
from services.routing_engine import RoutingEngine
from model_router import ExecutionMode, ModelRole, resolve_model

load_dotenv()

VISION_MODEL = resolve_model(ModelRole.VISION, ExecutionMode.LOCAL)
NUEXTRACT_MODEL = os.getenv("NUEXTRACT_MODEL", "numind/nuextract3:q4_k_m") 

# ==========================================
# SCHÉMA EXPÉRIMENTAL V8.7 (Isolé du reste)
# ==========================================
class LigneTVA(BaseModel):
    base_ht: float
    taux_pourcentage: float
    montant_tva: float

class InvoiceDataExperimental(BaseModel):
    fournisseur: str
    date_facture: str
    numero_facture: Optional[str] = None
    
    montant_ht: float
    montant_tva: float
    montant_ttc: float
    
    # Le tableau multi-TVA au centre du crash-test
    lignes_tva: list[LigneTVA] = Field(default_factory=list)
    
    devise: str = "EUR"

    @model_validator(mode="after")
    def process_and_verify(self):
        # 1. Vérification Globale
        attendu_ttc = round(self.montant_ht + self.montant_tva, 2)
        if abs(attendu_ttc - self.montant_ttc) > 0.02:
            raise ValueError(f"Maths globales invalides : HT ({self.montant_ht}) + TVA ({self.montant_tva}) != TTC ({self.montant_ttc})")
            
        # 2. Vérification de la ventilation
        if self.lignes_tva:
            somme_ht = sum(ligne.base_ht for ligne in self.lignes_tva)
            somme_tva = sum(ligne.montant_tva for ligne in self.lignes_tva)
            
            if abs(somme_ht - self.montant_ht) > 0.02:
                raise ValueError(f"Ventilation HT invalide : Sommes bases ({somme_ht}) != HT Global ({self.montant_ht})")
            if abs(somme_tva - self.montant_tva) > 0.02:
                raise ValueError(f"Ventilation TVA invalide : Sommes TVA ({somme_tva}) != TVA Globale ({self.montant_tva})")
                
        return self

# ==========================================
# SCRIPT DE TEST
# ==========================================
profiler = DocumentProfiler()
router = RoutingEngine(vision_model=VISION_MODEL)

fichier = Path(r"A:\dsh-core\workspace\invoices\input\ticket_resto.pdf")

print("=== V8.7 EXPERIMENTAL : TEST MULTI-TVA ===")

if fichier.exists():
    print("\n--- 1. Pipeline Physique ---")
    profil = profiler.profile(fichier)
    document_result = router.extract(fichier, profil)
    texte_ocr = document_result.raw_text.strip()
    
    if not texte_ocr:
        print("❌ L'OCR a échoué. Texte vide.")
        exit()

    print("\n--- TEXTE OCR ---")
    print(texte_ocr[:3000])
    print("\n-----------------")

    print(f"\n--- 2. Pipeline NuExtract ({NUEXTRACT_MODEL}) ---")
    
    template = {
        "fournisseur": "string",
        "date_facture": "YYYY-MM-DD",
        "numero_facture": "string",
        "montant_ht": "number",
        "montant_tva": "number",
        "montant_ttc": "number",
        "lignes_tva": [
            {
                "base_ht": "number",
                "taux_pourcentage": "number",
                "montant_tva": "number"
            }
        ],
        "devise": "EUR"
    }

    prompt = f"""<|input|>
### Template:
{json.dumps(template)}
### Text:
{texte_ocr}
<|output|>"""

    try:
        response = ollama.chat(
            model=NUEXTRACT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0}
        )
        
        raw_json = response["message"]["content"].strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json.replace("```json", "").replace("```", "").strip()
        elif raw_json.startswith("```"):
            raw_json = raw_json.replace("```", "").strip()
            
        print("\n[NuExtract] RÉSULTAT BRUT :")
        parsed_data = json.loads(raw_json)
        print(json.dumps(parsed_data, indent=2, ensure_ascii=False))
        
        print("\n--- 3. Validation Pydantic V8.7 ---")
        valid_invoice = InvoiceDataExperimental(**parsed_data)
        
        print("✅ SUCCÈS : NuExtract a compris la ventilation multi-taux !")
        print(json.dumps(valid_invoice.model_dump(), indent=2, ensure_ascii=False))

    except json.JSONDecodeError:
        print("❌ ERREUR JSON")
    except Exception as e:
        print(f"❌ ERREUR PYDANTIC : {e}")

else:
    print("Fichier introuvable.")