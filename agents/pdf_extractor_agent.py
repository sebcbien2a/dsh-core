import os
import pdfplumber

class PDFAgent:
    def __init__(self):
        self.name = "PDFTableExtractor"

    def extract_table(self, file_path: str) -> dict:
        print(f"[{self.name}] Analyse réelle du fichier PDF : {file_path}")
        
        if not os.path.exists(file_path):
            return {
                "status": "error",
                "error": f"Le fichier '{file_path}' est introuvable."
            }

        extracted_tables = []
        raw_text_content = ""

        try:
            with pdfplumber.open(file_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    # 1. Extraction du texte de la page
                    page_text = page.extract_text()
                    if page_text:
                        raw_text_content += f"\n--- Page {page_idx + 1} ---\n" + page_text

                    # 2. Extraction des structures de tableaux
                    tables = page.extract_tables()
                    for table in tables:
                        # Nettoyage des cellules Nones ou vides
                        cleaned_table = [
                            [cell.strip() if cell else "" for cell in row]
                            for row in table if any(row)
                        ]
                        if cleaned_table:
                            extracted_tables.append(cleaned_table)

            return {
                "status": "success",
                "extracted_data": {
                    "file_name": os.path.basename(file_path),
                    "total_tables_found": len(extracted_tables),
                    "tables": extracted_tables,
                    "text_preview": raw_text_content[:300].strip()  # Aperçu du début de facture
                }
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }