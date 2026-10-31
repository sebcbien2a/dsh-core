import os
import json
from pathlib import Path
from dotenv import load_dotenv
import ollama

from schemas.invoice_schema import InvoiceData
from services.document_profiler import DocumentProfiler
from services.routing_engine import RoutingEngine
from services.invoice_reconciliation_engine import InvoiceReconciliationEngine
from services.tax_classification_engine import TaxClassificationEngine
from services.invoice_skill_applier import InvoiceSkillApplier

load_dotenv()

VISION_MODEL = os.getenv("VISION_FAST", "qwen3.5:2b")
NUEXTRACT_MODEL = os.getenv("NUEXTRACT_MODEL", "numind/nuextract3:q4_k_m") 

profiler = DocumentProfiler()
router = RoutingEngine(vision_model=VISION_MODEL)
reconciliation_engine = InvoiceReconciliationEngine()
tax_engine = TaxClassificationEngine()
skill_applier = InvoiceSkillApplier()

dossier_input = Path(r"A:\dsh-core\workspace\invoices\input")

print("=== V8.6.4 : TEST BATCH AVEC SKILL EN AUTORITÉ FINALE ===")

fichiers = [f for f in dossier_input.iterdir() if f.is_file() and f.suffix.lower() in ['.pdf', '.jpg', '.jpeg', '.png']]

if not fichiers:
    print(f"❌ Aucun fichier trouvé dans {dossier_input}")
    exit()

for fichier in fichiers:
    print(f"\n{'='*70}")
    print(f"📄 TRAITEMENT EN COURS : {fichier.name}")
    print(f"{'='*70}")

    profil = profiler.profile(fichier)
    document_result = router.extract(fichier, profil)
    texte_ocr = document_result.raw_text.strip()
    
    if not texte_ocr:
        print(f"❌ L'OCR a échoué sur {fichier.name}. Texte vide. On passe au suivant.")
        continue

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

    prompt = f"<|input|>\n### Template:\n{json.dumps(template)}\n### Text:\n{texte_ocr}\n<|output|>"

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
        commentaires_hybrides = []

        # =====================================================================
        # ÉTAPE 1 : Réconciliation montants & typage documentaire standard
        # =====================================================================
        parsed_data, com_rec = reconciliation_engine.reconcile(texte_ocr, parsed_data)
        commentaires_hybrides.extend(com_rec)

        # =====================================================================
        # ÉTAPE 2 : Qualification du régime fiscal standard
        # =====================================================================
        type_tva, taux_theorique, com_tax = tax_engine.classify(texte_ocr, parsed_data)
        parsed_data["type_tva"] = type_tva
        parsed_data["taux_tva_theorique"] = taux_theorique
        if type_tva in ["AUTOLIQUIDATION", "NON_ASSUJETTI"]:
            parsed_data["montant_tva"] = 0.0
            parsed_data["taux_tva_pourcentage"] = None
        commentaires_hybrides.extend(com_tax)

        # =====================================================================
        # ÉTAPE 3 : APPLICATION DU SKILL MÉTIER (Autorité finale)
        # =====================================================================
        active_skill = skill_applier.find_matching_skill(texte_ocr)
        if active_skill:
            com_skill = skill_applier.apply_skill(active_skill, texte_ocr, parsed_data)
            commentaires_hybrides.extend(com_skill)

        # =====================================================================
        # ÉTAPE 4 : Métadonnées documentaires
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
        
        print(f"{icone} STATUT FINAL : {statut} (Type: {valid_invoice.type_piece.value} | Régime: {valid_invoice.type_tva.value})")
        print(json.dumps(valid_invoice.model_dump(), indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"❌ ERREUR sur {fichier.name} : {e}")

print(f"\n🎉 TRAITEMENT BATCH TERMINÉ. {len(fichiers)} fichiers analysés.")