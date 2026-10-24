# services/business_extraction_manager.py
import json
import ollama
from pydantic import ValidationError
from model_router import ExecutionMode, ModelRole, resolve_model, validate_model_identifier
from schemas.document_result import DocumentResult
from schemas.invoice_schema import InvoiceData

class BusinessExtractionManager:
    def __init__(self, base_model: str = None):
        self.base_model = (
            validate_model_identifier(base_model)
            if base_model is not None
            else resolve_model(ModelRole.BASE, ExecutionMode.LOCAL)
        )

    def extract_to_json(self, document: DocumentResult) -> dict:
        print(f"\n[BusinessExtractionManager] Analyse métier via {self.base_model}...")
        
        # SÉCURITÉ 1 : On vérifie ce que l'OCR a réellement lu
        texte_extrait = document.raw_text.strip()
        print(f"[BusinessExtractionManager] Texte reçu ({len(texte_extrait)} caractères) : {texte_extrait[:60]}...")
        
        if not texte_extrait:
            return {"status": "error", "message": "Le texte brut est vide, l'OCR a échoué."}

        # PROMPT STRICT : Interdiction totale de calculer
        prompt = f"""Tu es un extracteur de données strict, pas une calculatrice. 
RÈGLE ABSOLUE : Tu dois extraire UNIQUEMENT les nombres exactement tels qu'ils sont imprimés sur le document. 
NE FAIS AUCUN CALCUL. N'additionne rien. Si un chiffre n'est pas écrit noir sur blanc, mets 0.0.
        
Texte brut extrait via {document.extraction_tool} :
{texte_extrait}

Format JSON attendu :
{{
  "fournisseur": "Nom de l'entreprise",
  "date_facture": "YYYY-MM-DD",
  "montant_ht": 0.0,
  "montant_tva": 0.0,
  "montant_ttc": 0.0,
  "devise": "EUR",
  "numero_facture": "NUM-123"
}}"""

        try:
            response = ollama.chat(
                model=self.base_model,
                messages=[
                    {"role": "system", "content": "Tu es un extracteur de données strict. Tu réponds UNIQUEMENT par du JSON valide, sans aucun texte autour."},
                    {"role": "user", "content": prompt}
                ],
                format="json", # Force Ollama à bloquer tout ce qui n'est pas du JSON
                options={"temperature": 0.0}
            )
            
            raw_json = response["message"]["content"].strip()
            
            if not raw_json:
                return {"status": "error", "message": "Le modèle a renvoyé une réponse vide", "raw_output": ""}

            parsed_data = json.loads(raw_json)
            
            # Mise à jour de l'enveloppe
            document.business_type = "invoice"
            document.processing_notes.append(f"llm_extraction_{self.base_model.replace(':', '_')}")
            
            return self._validate_data(parsed_data, document)

        except json.JSONDecodeError:
            return {"status": "error", "message": "JSON invalide généré", "raw_output": raw_json}
        except Exception as e:
            return {"status": "critical_error", "message": str(e)}

    def _validate_data(self, data: dict, document: DocumentResult) -> dict:
        print("[BusinessExtractionManager] Validation Pydantic (HT + TVA = TTC) en cours...")
        try:
            valid_invoice = InvoiceData(**data)
            document.processing_notes.append("pydantic_validation_success")
            print("[BusinessExtractionManager] ✅ SUCCÈS : Données validées !")
            return {
                "status": "success", 
                "document_metadata": document.model_dump(exclude={'raw_text'}),
                "data": valid_invoice.model_dump()
            }
        except ValidationError as e:
            document.processing_notes.append("pydantic_validation_failed")
            print("[BusinessExtractionManager] ⚠ REJET : Échec mathématique ou structurel.")
            return {
                "status": "validation_failed", 
                "document_metadata": document.model_dump(exclude={'raw_text'}),
                "extracted_data": data, 
                "errors": [err["msg"] for err in e.errors()]
            }