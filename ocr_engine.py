# ocr_engine.py - Moteur d'extraction déterministe Surya OCR v0.22+
import html
import os
from pathlib import Path
import re
from PIL import Image, ImageDraw

# Sanctuarisation du cache Hugging Face sur A:\
os.environ["HF_HOME"] = r"A:\dsh-core\models\huggingface"

from surya.recognition import RecognitionPredictor

INPUT_DIR = Path(r"A:\dsh-core\workspace\invoices\input")
PROCESSED_DIR = Path(r"A:\dsh-core\workspace\invoices\processed")


class OCREngine:
    def __init__(self):
        print("Chargement du modèle Surya OCR...")
        self.rec_predictor = RecognitionPredictor()
        print("Modèle Surya opérationnel sur GPU.")

    def extraire_texte(self, image_path: Path | str) -> list[str]:
        """Extrait l'ensemble des blocs de texte et nettoie le balisage."""
        img = Image.open(image_path).convert("RGB")
        predictions = self.rec_predictor([img], full_page=True)

        lignes = []
        if predictions:
            page = predictions[0]
            # Itération directe sur les blocs détectés par Surya
            elements = getattr(page, "text_lines", None) or getattr(page, "blocks", None) or page
            for item in elements:
                contenu = getattr(item, "html", None) or getattr(item, "text", "")
                if contenu:
                    # Nettoyage des balises HTML (<p>...</p>) et entités
                    texte_propre = re.sub(r"<[^>]+>", "", contenu)
                    texte_propre = html.unescape(texte_propre).strip()
                    if texte_propre:
                        lignes.append(texte_propre)
        return lignes


def generer_facture_test(chemin: Path):
    """Génère l'image de test si absente."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (700, 450), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    lignes_texte = [
        "FACTURE N° : FA-2026-0042",
        "Date : 2026-09-25",
        "Fournisseur : Paintcar Distribution France",
        "SIRET : 123 456 789 00012",
        "Client : Atelier Carrosserie Val de Loire",
        "-------------------------------------------",
        "Article : Vernis Carrosserie UHS 5L",
        "Quantite : 2   |   Prix unitaire HT : 50.00 EUR",
        "-------------------------------------------",
        "Total HT : 100.00 EUR",
        "TVA (20%) : 20.00 EUR",
        "Total TTC : 120.00 EUR"
    ]

    y = 30
    for ligne in lignes_texte:
        draw.text((40, y), ligne, fill=(0, 0, 0))
        y += 30

    img.save(chemin)


if __name__ == "__main__":
    print("=== TEST EXTRACTION SURYA OCR V0.22 ===")

    test_image_path = INPUT_DIR / "test_facture.png"
    if not test_image_path.exists():
        generer_facture_test(test_image_path)

    engine = OCREngine()
    resultats = engine.extraire_texte(test_image_path)

    print("\n--- Données extraites prêtes pour validation Pydantic ---")
    for idx, ligne in enumerate(resultats, 1):
        print(f"[{idx:02d}] {ligne}")