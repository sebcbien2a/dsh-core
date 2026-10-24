# services/routing_engine.py
import pandas as pd
import pymupdf
import ollama
from pathlib import Path
from model_router import ExecutionMode, ModelRole, resolve_model, validate_vision_model
from schemas.profile_schema import DocumentProfile
from schemas.document_result import DocumentResult

class RoutingEngine:
    def __init__(self, vision_model: str | None = None):
        self.vision_model = (
            validate_vision_model(vision_model) if vision_model is not None else None
        )

    def extract(self, file_path: str | Path, profile: DocumentProfile) -> DocumentResult:
        """Route le document vers le bon outil et retourne l'enveloppe DocumentResult."""
        path = Path(file_path)
        ptype = profile.profile_type
        tool_used = "unknown"
        raw_text = ""
        notes = []

        print(f"\n[RoutingEngine] Routage de {path.name} -> {ptype.upper()}")

        if ptype == "native_pdf":
            tool_used = "PyMuPDF"
            notes.append("native_text_extracted")
            raw_text = self._extract_native_pdf(path)
        elif ptype == "spreadsheet":
            tool_used = "Pandas/OpenPyXL"
            notes.append("spreadsheet_to_markdown")
            raw_text = self._extract_spreadsheet(path)
        elif ptype in ["scanned_pdf", "image"]:
            vision_model = self._require_vision_model()
            tool_used = f"Local Vision ({vision_model})"
            notes.append("vision_png_standardized")
            notes.append("autorotate_applied") # Traçabilité de l'action géométrique
            notes.append(f"ocr_{vision_model.replace(':', '_')}")
            raw_text = self._extract_vision(path, vision_model)
        elif ptype == "complex_pdf":
            tool_used = "Docling (Fallback PyMuPDF)"
            notes.append("fallback_pymupdf_used")
            raw_text = self._extract_docling(path)
        elif ptype in ("handwritten", "handwriting", "manuscrit"):
            raise ValueError("HANDWRITING_PATH_MISSING")
        else:
            raise ValueError(f"Profil physique inconnu : {ptype}")

        # Construction et retour de l'enveloppe avec son historique complet
        return DocumentResult(
            source_name=path.name,
            profile_type=ptype,
            extraction_tool=tool_used,
            raw_text=raw_text,
            processing_notes=notes,
            metadata={
                "extension": profile.extension,
                "word_count": profile.word_count,
                "has_tables": profile.has_tables,
                "has_images": profile.has_images,
                "is_scanned": profile.is_scanned
            }
        )
        
    def _extract_native_pdf(self, path: Path) -> str:
        print("-> Outil engagé : PyMuPDF (Texte natif)")
        doc = pymupdf.open(path)
        texte = "\n".join([page.get_text() for page in doc])
        doc.close()
        return texte

    def _extract_spreadsheet(self, path: Path) -> str:
        print("-> Outil engagé : Pandas/OpenPyXL (Tableur)")
        if path.suffix.lower() == '.csv':
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        return df.to_markdown()

    def _require_vision_model(self) -> str:
        if self.vision_model is not None:
            return validate_vision_model(self.vision_model)
        return resolve_model(ModelRole.VISION, ExecutionMode.LOCAL)

    def _extract_vision(self, path: Path, vision_model: str) -> str:
        print(f"-> Outil engagé : Local Vision ({vision_model}) avec AutoRotate")
        doc = pymupdf.open(path)
        
        best_text = ""
        best_len = -1
        best_angle = 0
        
        prompt = (
            "Transcris tout le texte visible. "
            "Si un mot est illisible, écris [?]. "
            "N'invente rien. "
            "Respecte la mise en page."
        )

        # Force Brute : Test des 4 orientations géométriques avec syntaxe robuste
        for angle in [0, 90, 180, 270]:
            matrix = pymupdf.Matrix(1, 1).prerotate(angle)
            
            if path.suffix.lower() == ".pdf":
                pix = doc[0].get_pixmap(matrix=matrix, dpi=200)
            else:
                pix = doc[0].get_pixmap(matrix=matrix)
                
            img_bytes = pix.tobytes("png")
            
            try:
                reponse = ollama.chat(
                    model=vision_model,
                    messages=[{"role": "user", "content": prompt, "images": [img_bytes]}],
                    options={"temperature": 0.0}
                )
                
                texte_courant = reponse["message"]["content"].strip()
                taille_courante = len(texte_courant)
                
                print(f"   [AutoRotate] Test {angle}° -> {taille_courante} caractères")
                
                # On conserve strictement l'orientation produisant le plus de texte
                if taille_courante > best_len:
                    best_len = taille_courante
                    best_text = texte_courant
                    best_angle = angle
            except Exception as e:
                # Si le modèle crash (ex: token repeat limit sur image à l'envers), on ignore et on continue
                print(f"   [AutoRotate] Test {angle}° -> ❌ ÉCHEC du modèle (Ignoré)")

        doc.close()
        print(f"-> Orientation optimale validée : {best_angle}°")
        return best_text

    def _extract_docling(self, path: Path) -> str:
        print("-> Outil engagé : Docling (Tableaux complexes)")
        print("   (Fallback temporaire sur PyMuPDF)")
        return self._extract_native_pdf(path)