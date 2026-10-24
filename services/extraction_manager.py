# services/extraction_manager.py
import json
from typing import Any

import ollama
from pydantic import ValidationError
from model_router import ExecutionMode, ModelRole, resolve_model, validate_model_identifier
from schemas.invoice_schema import InvoiceSchema
from schemas.document_result import DocumentResult


class InvoiceExtractionValidationError(Exception):
    """Raised when extracted invoice data violates the canonical contract."""


class ExtractionManager:
    def __init__(self, base_model: str = None):
        self.base_model = (
            validate_model_identifier(base_model)
            if base_model is not None
            else resolve_model(ModelRole.BASE, ExecutionMode.LOCAL)
        )

    def extract_to_json(self, document: DocumentResult) -> dict:
        print(f"\n[ExtractionManager] Structuration du texte brut via {self.base_model}...")

        # Le prompt est mis à jour pour coller EXACTEMENT à ton InvoiceSchema
        prompt = f"""Tu es un expert comptable implacable. Transforme ce texte brut extrait d'un document en un JSON strict qui respecte EXACTEMENT cette structure.
        
Origine du texte : {document.profile_type} extrait via {document.extraction_tool}
Texte brut :
{document.raw_text}

Format JSON attendu (réponds UNIQUEMENT le JSON, aucun texte avant ou après) :
{{
  "numero_facture": "Numéro ou FA-INCONNU",
  "date_emission": "YYYY-MM-DD",
  "fournisseur": {{
    "nom": "Nom du vendeur"
  }},
  "client": {{
    "nom": "Nom de l'acheteur"
  }},
  "lignes": [
    {{
      "description": "Nom de l'article",
      "quantite": 1.0,
      "prix_unitaire_ht": 0.0,
      "total_ht": 0.0,
      "taux_tva": 20.0
    }}
  ],
  "total_ht": 0.0,
  "montant_tva": 0.0,
  "total_ttc": 0.0,
  "devise": "EUR"
}}"""

        try:
            response = ollama.chat(
                model=self.base_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.0}
            )
            
            raw_json = response["message"]["content"].strip()
            
            # Nettoyage si le modèle ajoute des balises Markdown
            if raw_json.startswith("```json"):
                raw_json = raw_json.replace("```json", "").replace("```", "").strip()
            elif raw_json.startswith("```"):
                raw_json = raw_json.replace("```", "").strip()

            parsed_data = json.loads(raw_json)
            return self._validate_data(parsed_data)

        except json.JSONDecodeError:
            print("[ExtractionManager] ❌ ÉCHEC : Le modèle n'a pas renvoyé un JSON lisible.")
            return {"status": "error", "message": "JSON invalide", "raw_output": raw_json}
        except Exception as e:
            print(f"[ExtractionManager] ❌ ÉCHEC API Ollama : {e}")
            return {"status": "critical_error", "message": str(e)}

    def validate_invoice(self, payload: dict[str, Any]) -> InvoiceSchema:
        try:
            return InvoiceSchema.model_validate(payload)
        except ValidationError as exc:
            raise InvoiceExtractionValidationError(
                "Extracted invoice data does not satisfy the canonical contract"
            ) from exc

    def _validate_data(self, data: dict) -> dict:
        print("[ExtractionManager] Validation Pydantic complète en cours...")
        try:
            valid_invoice = self.validate_invoice(data)
            print("[ExtractionManager] ✅ SUCCÈS : Données comptables validées ! L'équation mathématique est juste.")
            return {
                "status": "success",
                "data": valid_invoice.model_dump(mode="json"),
            }
        except InvoiceExtractionValidationError as exc:
            validation_error = exc.__cause__
            if not isinstance(validation_error, ValidationError):
                raise
            print("[ExtractionManager] ⚠ REJET PYDANTIC : Échec de la validation métier.")
            error_msgs = [err["msg"] for err in validation_error.errors()]
            return {"status": "validation_failed", "extracted_data": data, "errors": error_msgs}