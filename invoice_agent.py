# invoice_agent.py - Agent local de structuration comptable et validation Pydantic
import json
from pathlib import Path
import shutil
import ollama

from ocr_engine import OCREngine
from schemas.invoice_schema import InvoiceSchema

INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")
PROCESSED_DIR = Path(r"A:\dsh-core\workspace\invoices\processed")
OUTPUT_JSON_DIR = Path(r"A:\dsh-core\workspace\invoices\output_json")

LLM_STRUCTURER = "qwen3.5:9b"

SYSTEM_PROMPT = """Tu es un expert-comptable numérique spécialisé dans la structuration de données de facturation.
À partir des lignes brutes issues d'un OCR, ton rôle est d'extraire et de structurer l'information au format JSON strict conforme à ce schéma :

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


class InvoiceProcessingAgent:
    def __init__(self):
        self.ocr_engine = OCREngine()

    def structurer_lignes_en_json(self, lignes_ocr: list[str]) -> dict:
        """Envoie les lignes extraites à Qwen 9B pour conversion JSON."""
        prompt_utilisateur = "Voici le texte extrait de la pièce comptable :\n" + "\n".join(lignes_ocr)

        reponse = ollama.chat(
            model=LLM_STRUCTURER,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_utilisateur}
            ],
            options={"temperature": 0.0}  # Température à zéro pour un déterminisme maximal
        )

        texte_reponse = reponse["message"]["content"].strip()
        
        # Nettoyage si le modèle englobe dans des balises markdown ```json
        if texte_reponse.startswith("```"):
            lignes = texte_reponse.split("\n")
            texte_reponse = "\n".join([l for l in lignes if not l.startswith("```")]).strip()

        return json.loads(texte_reponse)

    def traiter_facture(self, image_path: Path) -> InvoiceSchema:
        """Traite une facture de bout en bout : OCR -> Structuration LLM -> Validation Pydantic -> Archivage."""
        print(f"\n--- Traitement de : {image_path.name} ---")

        # 1. Extraction déterministe via Surya
        lignes_ocr = self.ocr_engine.extraire_texte(image_path)
        print(f"-> {len(lignes_ocr)} lignes textuelles détectées par Surya.")

        # 2. Structuration via Qwen 9B
        print(f"-> Structuration JSON en cours via {LLM_STRUCTURER}...")
        donnees_brutes = self.structurer_lignes_en_json(lignes_ocr)

        # 3. Barrière Pydantic (validation arithmétique HT + TVA == TTC)
        facture_validee = InvoiceSchema(**donnees_brutes)
        print("-> Validation arithmétique Pydantic : SUCCÈS")

        # 4. Sauvegarde du résultat JSON
        nom_base = image_path.stem
        fichier_sortie_json = OUTPUT_JSON_DIR / f"{nom_base}.json"
        with open(fichier_sortie_json, "w", encoding="utf-8") as f:
            json.dump(facture_validee.model_dump(), f, indent=2, ensure_ascii=False)
        print(f"-> Fichier JSON généré : {fichier_sortie_json}")

        # 5. Déplacement de la facture traitée
        destination_image = PROCESSED_DIR / image_path.name
        shutil.move(str(image_path), str(destination_image))
        print(f"-> Document déplacé vers : {destination_image}")

        return facture_validee


if __name__ == "__main__":
    agent = InvoiceProcessingAgent()
    
    # Traitement de toutes les images présentes dans workspace/invoices/input/
    fichiers_entree = list(INPUT_DIR.glob("*.png")) + list(INPUT_DIR.glob("*.jpg"))
    if not fichiers_entree:
        print("Aucun fichier à traiter dans workspace/invoices/input/.")
    else:
        for fichier in fichiers_entree:
            try:
                facture = agent.traiter_facture(fichier)
                print(f" Facture {facture.numero_facture} traitée et validée avec succès !")
            except Exception as e:
                print(f" Échec du traitement pour {fichier.name} : {e}")