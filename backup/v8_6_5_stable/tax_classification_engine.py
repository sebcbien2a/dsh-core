from typing import Dict, Any, Tuple, List

class TaxClassificationEngine:
    def __init__(self):
        self.mots_transfrontaliers = [
            "INTRACOMUNITARIA",
            "LIVRAISON INTRACOMMUNAUTAIRE",
            "REVERSE CHARGE"
        ]

    def classify(self, text_ocr: str, parsed_data: Dict[str, Any]) -> Tuple[str, Any, List[str]]:
        """
        Détermine le régime fiscal : NORMALE, AUTOLIQUIDATION ou NON_ASSUJETTI.
        Retourne (type_tva, taux_tva_theorique, commentaires).
        """
        texte_upper = text_ocr.upper()
        commentaires = []
        
        is_transfrontalier = any(mot in texte_upper for mot in self.mots_transfrontaliers)

        # 1. Opération transfrontalière intra-UE certifiée (ex: Crisol Color)
        if is_transfrontalier:
            commentaires.append("Régime fiscal qualifié : AUTOLIQUIDATION (Opération intra-UE certifiée).")
            return "AUTOLIQUIDATION", 20.0, commentaires

        # 2. TVA positive réelle présente sur la facture
        montant_tva = parsed_data.get("montant_tva")
        if montant_tva and montant_tva > 0:
            return "NORMALE", None, commentaires

        # 3. TVA nulle ou absente sur facture domestique (ex: Yasmin Consulting)
        commentaires.append("Régime fiscal qualifié : NON_ASSUJETTI (TVA facturée nulle sur opération domestique).")
        return "NON_ASSUJETTI", None, commentaires