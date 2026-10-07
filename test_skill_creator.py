import json
from pathlib import Path
from services.document_profiler import DocumentProfiler
from services.routing_engine import RoutingEngine
from services.invoice_skill_creator import InvoiceSkillCreator

profiler = DocumentProfiler()
router = RoutingEngine()
creator = InvoiceSkillCreator()

fichier_smtrt = Path(r"A:\dsh-core\workspace\invoices\input\facture_transport.PDF")
profil = profiler.profile(fichier_smtrt)
document_result = router.extract(fichier_smtrt, profil)
raw_text = document_result.raw_text

# 1. Extraction initiale de NuExtract
extracted_data = {
    "fournisseur": "RCSAIXENPROVENCE",
    "numero_facture": "3ZALESPIELETES",
    "type_piece": "FACTURE",
    "type_tva": "NORMALE",
    "montant_ht": 161.3,
    "montant_tva": 32.26,
    "montant_ttc": 193.56
}

# 2. Correction validée par l'opérateur
corrected_data = {
    "fournisseur": "SMTRT SAS",
    "numero_facture": "60900194",
    "type_tva": "NORMALE"
}

print("=== APPRENTISSAGE MÉTIER : GÉNÉRATION DU SKILL SMTRT ===")
skill = creator.create_skill(
    raw_text=raw_text,
    extracted_data=extracted_data,
    corrected_data=corrected_data
)

print("\n--- Contenu du Skill généré ---")
print(json.dumps(skill, indent=2, ensure_ascii=False))