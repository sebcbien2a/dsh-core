import re
from typing import Dict, Any, List, Tuple

class InvoiceReconciliationEngine:
    def __init__(self):
        self.mots_avoir = ["RECTIFICATIVE", "RECTIFICADA", "AVOIR", "CREDIT NOTE"]

    def reconcile(self, text_ocr: str, parsed_data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Valide et redresse le typage documentaire et la cohérence HT/TVA/TTC."""
        texte_upper = text_ocr.upper()
        commentaires = []

        # 1. Typage de la pièce
        if any(mot in texte_upper for mot in self.mots_avoir):
            parsed_data["type_piece"] = "AVOIR"
            commentaires.append("Certitude documentaire : Document identifié comme AVOIR via règle OCR.")
        else:
            parsed_data["type_piece"] = "FACTURE"

        # 2. Recherche du triplet certifié (TOTAL HT / MT TVA / NET A PAYER)
        match_ht = re.search(r"TOTAL\s*H\.?\s*T\.?[\s\S]{0,80}?(\d+[.,]\d{2})", text_ocr, re.IGNORECASE)
        match_tva = re.search(r"MT\s*TVA[\s\S]{0,80}?(\d+[.,]\d{2})", text_ocr, re.IGNORECASE)
        match_net = re.search(r"NET\s*[AÀ]?\s*PAYER[\s\S]{0,100}?(\d+[.,]\d{2})", text_ocr, re.IGNORECASE)

        triplet_trouve = False
        if match_ht and match_tva and match_net:
            ht_f = float(match_ht.group(1).replace(",", "."))
            tva_f = float(match_tva.group(1).replace(",", "."))
            ttc_f = float(match_net.group(1).replace(",", "."))

            if abs((ht_f + tva_f) - ttc_f) <= 0.05 and ht_f > 0 and ttc_f > 0:
                parsed_data["montant_ht"] = ht_f
                parsed_data["montant_tva"] = tva_f
                parsed_data["montant_ttc"] = ttc_f
                parsed_data["montant_paye"] = ttc_f
                parsed_data["montant_remise"] = 0.0
                commentaires.append("Montants fiscaux certifiés extraits directement des libellés (TOTAL HT / MT TVA / NET A PAYER).")
                triplet_trouve = True

        # 3. Filet mathématique si NuExtract a extrait un prix unitaire ou une taxe intermédiaire
        if not triplet_trouve and parsed_data.get("type_piece") == "FACTURE":
            tva_llm = parsed_data.get("montant_tva")
            taux_llm = parsed_data.get("taux_tva_pourcentage")
            ttc_llm = parsed_data.get("montant_ttc")

            if tva_llm and taux_llm == 20.0 and ttc_llm:
                ht_theorique = round(tva_llm / 0.20, 2)
                ttc_theorique = round(ht_theorique + tva_llm, 2)

                if abs(ttc_llm - ttc_theorique) > 30.0:
                    parsed_data["montant_ht"] = ht_theorique
                    parsed_data["montant_ttc"] = ttc_theorique
                    parsed_data["montant_paye"] = ttc_theorique
                    parsed_data["montant_remise"] = 0.0
                    commentaires.append(f"TTC incohérent détecté ({ttc_llm}). Réconciliation appliquée depuis la TVA ({tva_llm} € à 20%).")

        return parsed_data, commentaires