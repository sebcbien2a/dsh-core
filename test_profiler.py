# test_profiler.py
import os
import json
from dotenv import load_dotenv
from pathlib import Path
from services.document_profiler import DocumentProfiler
from services.routing_engine import RoutingEngine
from services.business_extraction_manager import BusinessExtractionManager

load_dotenv()
# On utilise le modèle rapide pour tout le monde
VISION_MODEL = os.getenv("VISION_FAST", "qwen3.5:2b")
BASE_MODEL = os.getenv("BASE_MODEL", "qwen3.5:2b")

profiler = DocumentProfiler()
router = RoutingEngine(vision_model=VISION_MODEL)
extractor = BusinessExtractionManager(base_model=BASE_MODEL)

# On teste sur notre fameux ticket capricieux
fichiers_test = [
    Path(r"A:\dsh-core\workspace\invoices\input\test_image.jpg")
]

print("=== Validation Étape C : De l'image au JSON Métier ===")

for fichier in fichiers_test:
    if fichier.exists():
        # 1. Pipeline Physique (Profilage + Extraction + Enveloppe)
        profil = profiler.profile(fichier)
        document_result = router.extract(fichier, profil)
        
        # --- DEBUG OCR : On vérifie ce que la force brute a capturé ---
        print("\n=== DEBUG OCR ===")
        print(f"Taille du texte brut : {len(document_result.raw_text)} caractères")
        print("--- DEBUT TEXTE BRUT ---")
        print(document_result.raw_text)
        print("--- FIN TEXTE BRUT ---")
        print("===================\n")
        
        # 2. Pipeline Métier (LLM JSON + Validation Pydantic)
        resultat_final = extractor.extract_to_json(document_result)
        
        print(f"\n--- VERDICT FINAL POUR {fichier.name} ---")
        print(json.dumps(resultat_final, indent=2, ensure_ascii=False))
        print("-" * 60)
    else:
        print(f"Fichier introuvable : {fichier}")