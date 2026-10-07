import os
from pathlib import Path
import pymupdf  # PyMuPDF
from schemas.profile_schema import DocumentProfile

class DocumentProfiler:
    def __init__(self, text_threshold: int = 50):
        # Seuil de mots (et non de caractères) pour valider une couche texte
        self.word_threshold = text_threshold // 5

    def profile(self, file_path: str | Path) -> DocumentProfile:
        path = Path(file_path)
        ext = path.suffix.lower()
        
        prof = DocumentProfile(
            extension=ext,
            has_text_layer=False,
            word_count=0,
            has_tables=False,
            has_images=False,
            is_scanned=False,
        )

        if ext in ['.csv', '.xlsx', '.xls']:
            prof.has_text_layer = True
            prof.profile_type = "spreadsheet"
            if ext != '.csv':
                prof.has_tables = True
            return prof

        if ext in ['.jpg', '.png', '.webp', '.jpeg', '.heic']:
            prof.is_scanned = True
            prof.has_images = True
            prof.profile_type = "image"
            return prof

        if ext == '.pdf':
            self._analyze_pdf(path, prof)
            
        return prof

    def _analyze_pdf(self, path: Path, prof: DocumentProfile):
        try:
            doc = pymupdf.open(path)
            if len(doc) == 0:
                prof.is_scanned = True
                prof.profile_type = "scanned_pdf"
                return

            page = doc[0]
            
            # 1. Extraction et comptage strict du texte
            text = page.get_text()
            prof.word_count = len(text.split())
            
            if prof.word_count > self.word_threshold:
                prof.has_text_layer = True

            # 2. Détection d'images embarquées
            if len(page.get_images()) > 0:
                prof.has_images = True

            # 3. Détection structurelle de tableaux
            tables = page.find_tables()
            if tables and len(tables.tables) > 0:
                prof.has_tables = True

            doc.close()

            # 4. Classification purement physique
            if not prof.has_text_layer:
                prof.is_scanned = True
                prof.profile_type = "scanned_pdf"
            elif prof.has_tables:
                prof.profile_type = "complex_pdf"
            else:
                prof.profile_type = "native_pdf"

        except Exception as e:
            print(f"[Profiler] Erreur structurelle sur le PDF {path.name}: {e}")
            # Sécurité : un PDF corrompu/illisible est traité comme un scan visuel
            prof.is_scanned = True
            prof.profile_type = "scanned_pdf"