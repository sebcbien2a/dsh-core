import os
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import ollama

from schemas.invoice_schema import InvoiceData 
from services.document_profiler import DocumentProfiler
from services.routing_engine import RoutingEngine

load_dotenv()

VISION_MODEL = os.getenv("VISION_FAST", "qwen3.5:2b")
NUEXTRACT_MODEL = os.getenv("NUEXTRACT_MODEL", "numind/nuextract3:q4_k_m") 

profiler = DocumentProfiler()
router = RoutingEngine(vision_model=VISION_MODEL)

dossier_input = Path(r"A:\dsh-core\workspace\invoices\input")

print("=== V8.6.1 : TEST BATCH (PRODUCTION FREEZE) ===")

fichiers = [f for f in dossier_input.iterdir() if f.is_file() and f.suffix.lower() in ['.pdf', '.jpg', '.jpeg', '.png']]

if not fichiers:
    print(f"❌ Aucun fichier trouvé dans {dossier_input}")
    exit()

for fichier in fichiers:
    print(f"\n{'='*70}")
    print(f"📄 TRAITEMENT EN COURS : {fichier.name}")
    print(f"{'='*70}")

    print("\n--- 1. Pipeline Physique ---")
    profil = profiler.profile(fichier)
    document_result = router.extract(fichier, profil)
    texte_ocr = document_result.raw_text.strip()
    
    if not texte_ocr:
        print(f"❌ L'OCR a échoué sur {fichier.name}. Texte vide. On passe au suivant.")
        continue

    print(f"\n--- 2. Pipeline NuExtract ({NUEXTRACT_MODEL}) ---")
    
    template = {
        "fournisseur": "string",
        "date_facture": "YYYY-MM-DD",
        "numero_facture": "string",
        "montant_ht": "number",
        "taux_tva_pourcentage": "number",
        "montant_tva": "number",
        "montant_ttc": "number",
        "montant_remise": "number",
        "montant_paye": "number",
        "devise": "EUR"
    }

    prompt = f"""<|input|>
### Template:
{json.dumps(template)}
### Text:
{texte_ocr}
<|output|>"""

    try:
        response = ollama.chat(
            model=NUEXTRACT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0}
        )
        
        raw_json = response["message"]["content"].strip()
        if raw_json.startswith("```json"):
            raw_json = raw_json.replace("```json", "").replace("```", "").strip()
        elif raw_json.startswith("```"):
            raw_json = raw_json.replace("```", "").strip()
            
        parsed_data = json.loads(raw_json)
        
        print("\n--- 3. Validation Pydantic et Rapport d'Audit Hybride ---")
        
        commentaires_hybrides = []
        texte_upper = texte_ocr.upper()

        # =====================================================================
        # RÈGLE MÉTIER 1 : Réconciliation fiscale déterministe (Triplet certifié)
        # =====================================================================
        match_ht = re.search(r"TOTAL\s*H\.?T\.?[\s\S]{0,25}?(\d+[.,]\d{2})", texte_ocr, re.IGNORECASE)
        match_tva = re.search(r"MT\s*TVA[\s\S]{0,25}?(\d+[.,]\d{2})", texte_ocr, re.IGNORECASE)
        match_net = re.search(r"NET\s*A\s*PAYER[\s\S]{0,35}?(\d+[.,]\d{2})", texte_ocr, re.IGNORECASE)

        if match_ht and match_tva and match_net:
            ht_facture = float(match_ht.group(1).replace(",", "."))
            tva_facture = float(match_tva.group(1).replace(",", "."))
            ttc_facture = float(match_net.group(1).replace(",", "."))

            # Cohérence interne du triplet imprimé
            if abs((ht_facture + tva_facture) - ttc_facture) <= 0.05:
                ht_llm = parsed_data.get("montant_ht")
                tva_llm = parsed_data.get("montant_tva")
                ttc_llm = parsed_data.get("montant_ttc")

                # Vérification de déviation NuExtract
                if (
                    ht_llm is None
                    or tva_llm is None
                    or ttc_llm is None
                    or abs(ht_llm - ht_facture) > 1.0
                    or abs(tva_llm - tva_facture) > 1.0
                    or abs(ttc_llm - ttc_facture) > 1.0
                ):
                    # Garde-fou sur les montants certifiés
                    if ht_facture > 0 and tva_facture >= 0 and ttc_facture > 0:
                        parsed_data["montant_ht"] = ht_facture
                        parsed_data["montant_tva"] = tva_facture
                        parsed_data["montant_ttc"] = ttc_facture
                        parsed_data["montant_paye"] = ttc_facture
                        parsed_data["montant_remise"] = 0.0
                        commentaires_hybrides.append(
                            "Montants fiscaux corrigés depuis les libellés certifiés de la facture (TOTAL HT / MT TVA / NET A PAYER)."
                        )

        # =====================================================================
        # RÈGLE MÉTIER 2 : Typage du document (OCR Déterministe)
        # =====================================================================
        mots_avoir = ["RECTIFICATIVE", "RECTIFICADA", "AVOIR", "CREDIT NOTE"]
        if any(mot in texte_upper for mot in mots_avoir):
            parsed_data["type_piece"] = "AVOIR"
            commentaires_hybrides.append("Certitude documentaire : Document identifié comme AVOIR via règle OCR.")
        else:
            parsed_data["type_piece"] = "FACTURE"

        # =====================================================================
        # RÈGLE MÉTIER 3 : Opérations intracommunautaires / exonérées
        # =====================================================================
        mots_exo = ["INTRACOMUNITARIA EXENTA", "INTRACOMMUNAUTAIRE", "AUTOLIQUIDATION"]
        if any(mot in texte_upper for mot in mots_exo):
            commentaires_hybrides.append("Certitude documentaire : Opération intracommunautaire exonérée détectée.")
            parsed_data["montant_tva"] = 0.0
            parsed_data["taux_tva_pourcentage"] = None
            commentaires_hybrides.append("TVA corrigée par règle métier intracommunautaire.")

        # =====================================================================
        # Métadonnées & Audit
        # =====================================================================
        origine = "INCONNU"
        if profil:
            doc_enum = getattr(profil, 'document_type', getattr(profil, 'type', getattr(profil, 'doc_type', None)))
            if doc_enum:
                origine = getattr(doc_enum, 'name', str(doc_enum))
        parsed_data["origine_document"] = origine
        
        if commentaires_hybrides:
            parsed_data["commentaires_controle"] = commentaires_hybrides
            
        valid_invoice = InvoiceData(**parsed_data)
        
        statut = valid_invoice.statut_traitement
        icone = "✅" if statut == "VALIDE" else "⚠️"
        
        print(f"{icone} STATUT FINAL : {statut} (Type: {valid_invoice.type_piece.value})")
        print(json.dumps(valid_invoice.model_dump(), indent=2, ensure_ascii=False))

    except json.JSONDecodeError:
        print(f"❌ ERREUR : NuExtract n'a pas renvoyé un JSON valide pour {fichier.name}.")
    except Exception as e:
        print(f"❌ ERREUR PYDANTIC ou OLLAMA sur {fichier.name} : {e}")

print(f"\n🎉 TRAITEMENT BATCH TERMINÉ. {len(fichiers)} fichiers analysés.")