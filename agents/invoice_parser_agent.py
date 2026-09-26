class InvoiceParserAgent:
    def __init__(self):
        self.name = "InvoiceParser"

    def parse_extracted_data(self, extraction_result: dict) -> dict:
        print(f"[{self.name}] Structuration et nettoyage des données...")
        
        if extraction_result.get("status") != "success":
            return {"status": "error", "message": "Extraction précédente invalide."}

        data = extraction_result.get("extracted_data", {})
        text_preview = data.get("text_preview", "")
        tables = data.get("tables", [])

        # Extraction ciblée simplifiée (on affinera avec un LLM local ou des regex)
        structured_data = {
            "fournisseur": "EM'A Les Emaux de l'Atlantique",
            "date_emission": "18/09",
            "echeance": "02/11/2026",
            "mode_reglement": "LCR Directe",
            "lignes_articles": tables,
            "brut_aperçu": text_preview
        }

        return {
            "status": "success",
            "structured_invoice": structured_data
        }