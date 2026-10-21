import os
import json
from pathlib import Path
from datetime import datetime
from typing import Any
from uuid import UUID
import ollama
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing import Optional

from schemas.invoice_schema import InvoiceSchema, TypePiece

# Force le chargement du .env
load_dotenv()
VISION_MODEL = os.getenv("VISION_MODEL")

# =====================================================================
# 1. CONTRAT STRICT DES FACTURES (PYDANTIC)
# =====================================================================
class InvoiceData(BaseModel):
    type_piece: TypePiece | None = None
    statut_traitement: str | None = None
    commentaires_controle: list[str] = Field(default_factory=list)
    fournisseur: str
    date_facture: str
    montant_ht: float
    montant_tva: float
    montant_ttc: float
    devise: str = "EUR"
    numero_facture: Optional[str] = None
    categorie: Optional[str] = None

    @model_validator(mode="after")
    def verify_totals(self):
        attendu = round(self.montant_ht + self.montant_tva, 2)
        if abs(attendu - self.montant_ttc) > 0.02:
            raise ValueError(f"HT ({self.montant_ht}) + TVA ({self.montant_tva}) != TTC ({self.montant_ttc})")
        return self


class InvoiceAgentCanonicalValidationError(Exception):
    """Raised when legacy invoice data cannot be converted to the canonical contract."""


# =====================================================================
# 2. PROMPT VISION POUR QWEN
# =====================================================================
SYSTEM_PROMPT = """Tu es un expert-comptable numérique.
À partir de l'image de la facture fournie, extrais les données et structure-les STRICTEMENT selon ce format JSON :

{
  "fournisseur": "Nom de l'entreprise",
  "date_facture": "YYYY-MM-DD",
  "montant_ht": 0.0,
  "montant_tva": 0.0,
  "montant_ttc": 0.0,
  "devise": "EUR",
  "numero_facture": "NUM-123",
  "categorie": "Matériel, Prestation, etc."
}

RÈGLES CRITIQUES :
- Réponds UNIQUEMENT avec un bloc JSON valide, aucun texte avant ou après.
- Les montants doivent être des nombres décimaux (ex: 1500.50).
- Règle d'or : HT + TVA doit être rigoureusement égal au TTC.
"""

# =====================================================================
# 3. L'AGENT FACTURE (LINÉAIRE & PROPRE)
# =====================================================================
class InvoiceAgent:
    def __init__(self):
        if not VISION_MODEL:
            raise ValueError("ERREUR CRITIQUE : VISION_MODEL n'est pas défini dans le .env !")
        self.vision_model = VISION_MODEL
        print(f"[InvoiceAgent] L'agent est VRAIMENT prêt et utilise : {self.vision_model}")

    def to_canonical_invoice(
        self,
        invoice_data: InvoiceData,
        *,
        document_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> InvoiceSchema:
        try:
            return InvoiceSchema.from_invoice_data(
                invoice_data,
                document_id=document_id,
                created_at=created_at,
                updated_at=updated_at,
            )
        except ValidationError as exc:
            raise InvoiceAgentCanonicalValidationError(
                "Legacy invoice data does not satisfy the canonical contract"
            ) from exc

    def validate_canonical_invoice(
        self,
        payload: dict[str, Any],
        *,
        document_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> InvoiceSchema:
        try:
            invoice_data = InvoiceData.model_validate(payload)
        except ValidationError as exc:
            raise InvoiceAgentCanonicalValidationError(
                "Legacy invoice payload is invalid"
            ) from exc
        return self.to_canonical_invoice(
            invoice_data,
            document_id=document_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    def structurer_document_en_json(self, file_path: Path) -> dict:
        """Envoie l'image binaire directement à Qwen Vision."""
        with open(file_path, "rb") as f:
            document_bytes = f.read()

        print(f"[InvoiceAgent] Envoi de l'image à {self.vision_model}...")
        
        reponse = ollama.chat(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Analyse cette facture.", "images": [document_bytes]}
            ],
            options={"temperature": 0.0}
        )

        texte_reponse = reponse["message"]["content"].strip()
        
        # --- POINT DE CONTRÔLE : LA VÉRITÉ BRUTE ---
        print("\n" + "="*50)
        print("=== REPONSE BRUTE QWEN VL ===")
        print(texte_reponse)
        print("=============================\n")
        
        if texte_reponse.startswith("```"):
            lignes = texte_reponse.split("\n")
            texte_reponse = "\n".join([l for l in lignes if not l.startswith("```")]).strip()

        return json.loads(texte_reponse)

    def traiter_facture(self, file_path: Path) -> InvoiceData:
        print(f"\n--- Traitement de : {file_path.name} ---")
        
        donnees_brutes = self.structurer_document_en_json(file_path)
        
        print("[InvoiceAgent] Validation mathématique Pydantic...")
        facture_validee = InvoiceData(**donnees_brutes)
        print("[InvoiceAgent] SUCCÈS : Données valides !")
        
        return facture_validee

    # Redirection de sécurité au cas où n8n_api appelle l'ancienne méthode
    def process_invoice(self, file_path_str: str) -> dict:
        try:
            validee = self.traiter_facture(Path(file_path_str))
            return {
                "status": "success",
                "data": validee.model_dump(
                    exclude={
                        "type_piece",
                        "statut_traitement",
                        "commentaires_controle",
                    }
                ),
            }
        except Exception as e:
            return {"status": "critical_error", "message": str(e)}