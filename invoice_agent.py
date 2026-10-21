# invoice_agent.py - Agent local de structuration comptable et validation Pydantic
import os
import json
import shutil
from pathlib import Path
from typing import Any
import ollama
from dotenv import load_dotenv
from pydantic import ValidationError

from schemas.invoice_schema import InvoiceSchema

# 1. FORCER LE CHARGEMENT DE LA STACK DÈS LE DÉPART
load_dotenv()
VISION_MODEL = os.getenv("VISION_MODEL")

INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")
PROCESSED_DIR = Path(r"A:\dsh-core\workspace\invoices\processed")
OUTPUT_JSON_DIR = Path(r"A:\dsh-core\workspace\invoices\output_json")

SYSTEM_PROMPT = """Tu es un expert-comptable numérique spécialisé dans la structuration de données de facturation.
À partir du document visuel fourni, ton rôle est d'extraire et de structurer l'information au format JSON strict conforme à ce schéma :

{
  "numero_facture": "string",
  "date_emission": "YYYY-MM-DD",
  "date_echeance": "YYYY-MM-DD ou null",
  "fournisseur": {
    "nom": "string",
    "siret": "string ou null",
    "numero_tva": "string ou null",
    "adresse": "string ou null"
  },
  "client": {
    "nom": "string",
    "siret": "string ou null",
    "adresse": "string ou null"
  },
  "lignes": [
    {
      "description": "string",
      "quantite": float,
      "prix_unitaire_ht": float,
      "total_ht": float,
      "taux_tva": float
    }
  ],
  "total_ht": float,
  "montant_tva": float,
  "total_ttc": float,
  "devise": "EUR"
}

RÈGLES CRITIQUES :
- Réponds UNIQUEMENT avec un bloc JSON valide, sans texte d'introduction ni balises markdown additionnelles.
- Assure-toi que les nombres sont des flottants décimaux (ex. : 100.0, 20.0).
- Règle d'or : Total HT + Montant TVA doit être rigoureusement égal au Total TTC.
"""


class InvoiceAgentValidationError(Exception):
    """Raised when extracted invoice data violates the canonical contract."""


class InvoiceAgent:
    def __init__(self):
        # Sécurité : Si le .env n'est pas lu, le programme plantera ici avec un message clair.
        if not VISION_MODEL:
            raise ValueError("ERREUR CRITIQUE : VISION_MODEL n'est pas défini dans le .env !")
        self.vision_model = VISION_MODEL
        print(f"[InvoiceAgent] Initialisé avec le modèle visuel : {self.vision_model}")

    def validate_invoice(self, payload: dict[str, Any]) -> InvoiceSchema:
        try:
            return InvoiceSchema.model_validate(payload)
        except ValidationError as exc:
            raise InvoiceAgentValidationError(
                "Extracted invoice data does not satisfy the canonical contract"
            ) from exc

    def structurer_document_en_json(self, file_path: Path) -> dict:
        """Envoie le document binaire directement à Qwen Vision pour extraction JSON."""
        
        # Lecture du fichier en bytes pour Ollama
        with open(file_path, "rb") as f:
            document_bytes = f.read()

        prompt_utilisateur = "Voici la pièce comptable. Extrais toutes les données avec précision et structure-les en JSON."

        reponse = ollama.chat(
            model=self.vision_model,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": prompt_utilisateur,
                    "images": [document_bytes]
                }
            ],
            options={"temperature": 0.0}  # Déterminisme maximal
        )

        texte_reponse = reponse["message"]["content"].strip()
        
        # Affichage de la réponse brute
        print("\n=== REPONSE BRUTE QWEN VL ===")
        print(texte_reponse)
        print("=============================\n")
        
        # Nettoyage si le modèle englobe le résultat dans des balises markdown ```json
        if texte_reponse.startswith("```"):
            lignes = texte_reponse.split("\n")
            texte_reponse = "\n".join([l for l in lignes if not l.startswith("```")]).strip()

        return json.loads(texte_reponse)

    def traiter_facture(self, file_path: Path) -> InvoiceSchema:
        """Traite une facture : Extraction Vision -> Validation Pydantic -> Archivage."""
        print(f"\n--- Traitement de : {file_path.name} ---")

        # 1. Extraction directe via le modèle de vision (remplace Surya OCR)
        print(f"-> Extraction et structuration visuelle en cours via {self.vision_model}...")
        donnees_brutes = self.structurer_document_en_json(file_path)

        # 2. Barrière Pydantic (validation arithmétique HT + TVA == TTC)
        print("-> Application de la barrière Pydantic...")
        facture_validee = self.validate_invoice(donnees_brutes)
        print("-> Validation arithmétique Pydantic : SUCCÈS")

        # 3. Sauvegarde du résultat JSON
        nom_base = file_path.stem
        OUTPUT_JSON_DIR.mkdir(parents=True, exist_ok=True)
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        fichier_sortie_json = OUTPUT_JSON_DIR / f"{nom_base}.json"
        with open(fichier_sortie_json, "w", encoding="utf-8") as f:
            json.dump(
                facture_validee.model_dump(mode="json"),
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"-> Fichier JSON généré : {fichier_sortie_json}")

        # 4. Déplacement de la facture traitée
        destination_document = PROCESSED_DIR / file_path.name
        shutil.move(str(file_path), str(destination_document))
        print(f"-> Document déplacé vers : {destination_document}")

        return facture_validee


if __name__ == "__main__":
    agent = InvoiceAgent()
    
    # Prise en charge des PDF en plus des images pour le test local
    fichiers_entree = list(INPUT_DIR.glob("*.pdf")) + list(INPUT_DIR.glob("*.png")) + list(INPUT_DIR.glob("*.jpg"))
    
    if not fichiers_entree:
        print(f"Aucun fichier à traiter dans {INPUT_DIR}.")
    else:
        for fichier in fichiers_entree:
            try:
                facture = agent.traiter_facture(fichier)
                print(
                    "✅ Facture "
                    f"{facture.reference.invoice_number} traitée et validée avec succès !"
                )
            except Exception as e:
                print(f"❌ Échec du traitement pour {fichier.name} : {e}")