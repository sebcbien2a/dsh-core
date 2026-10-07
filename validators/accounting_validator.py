from typing import Dict, Any, List
from datetime import datetime

class AccountingValidator:
    """
    Service de validation métier (logique comptable avancée).
    Vérifie la cohérence des montants, des dates et l'historique du fournisseur.
    """

    def __init__(self, tolerance_margin: float = 0.05):
        self.tolerance_margin = tolerance_margin

    def deduce_vat_rate(self, montant_ht: float, montant_tva: float) -> float:
        if montant_ht <= 0:
            return 0.0
        
        taux_calcule = (montant_tva / montant_ht) * 100
        taux_standards = [0.0, 2.1, 5.5, 10.0, 20.0]
        return min(taux_standards, key=lambda x: abs(x - taux_calcule))

    def _parse_date(self, date_str: str) -> datetime:
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", ""))
        except ValueError:
            return None

    def validate_invoice_logic(self, invoice_data: Dict[str, Any]) -> Dict[str, Any]:
        validated = invoice_data.copy()
        errors = []
        
        ht = float(validated.get("montant_ht") or 0.0)
        tva = float(validated.get("montant_tva") or 0.0)
        ttc = float(validated.get("montant_ttc") or 0.0)
        fournisseur = (validated.get("fournisseur") or "").strip()
        categorie = (validated.get("categorie") or "").lower()
        
        # 1. Fournisseur vide
        if not fournisseur:
            errors.append("Le nom du fournisseur est vide.")
            
        # 2. Montant négatif sans être un avoir
        is_avoir = "avoir" in categorie or "remboursement" in categorie
        if ttc < 0 and not is_avoir:
            errors.append("Montant négatif mais la facture n'est pas catégorisée comme un avoir.")
            
        # 3. Cohérence et taux de TVA
        taux_deduit = self.deduce_vat_rate(ht, tva)
        validated["taux_tva_calcule"] = taux_deduit
        if ht > 0 and (tva / ht) > 0.25:
            errors.append(f"Taux de TVA incohérent ou anormalement élevé ({(tva/ht)*100:.2f}%).")

        # 4. Cohérence des dates
        date_facture_str = validated.get("date_facture")
        date_echeance_str = validated.get("date_echeance")
        
        if date_facture_str:
            date_fact = self._parse_date(date_facture_str)
            if date_fact:
                if date_fact > datetime.now():
                    errors.append(f"La date de la facture ({date_facture_str}) est dans le futur.")
                
                if date_echeance_str:
                    date_ech = self._parse_date(date_echeance_str)
                    if date_ech and date_ech < date_fact:
                        errors.append("La date d'échéance est antérieure à la date de la facture.")

        validated["business_errors"] = errors
        validated["is_valid_business"] = len(errors) == 0
        return validated

    def check_historical_anomalies(
        self, 
        current_invoice: Dict[str, Any], 
        historical_invoices: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        anomaly_report = {
            "is_anomalous": False,
            "reasons": []
        }
        
        if not historical_invoices:
            return anomaly_report
            
        current_ttc = float(current_invoice.get("montant_ttc", 0.0))
        current_num = current_invoice.get("numero_facture")
        current_fournisseur = current_invoice.get("fournisseur")
        
        total_historique = 0
        
        for inv in historical_invoices:
            # 5. Détection de facture dupliquée via l'historique de similarité
            if current_num and inv.get("numero_facture") == current_num and inv.get("fournisseur") == current_fournisseur:
                anomaly_report["is_anomalous"] = True
                anomaly_report["reasons"].append(f"Doublon détecté : Le numéro {current_num} existe déjà pour {current_fournisseur}.")
            
            total_historique += float(inv.get("montant_ttc", 0.0))
        
        # 6. Vérification de l'écart par rapport à la moyenne
        if len(historical_invoices) > 0:
            moyenne = total_historique / len(historical_invoices)
            if moyenne > 0 and current_ttc > (moyenne * 3):
                anomaly_report["is_anomalous"] = True
                anomaly_report["reasons"].append(f"Montant inhabituellement élevé (TTC: {current_ttc}, Moyenne: {moyenne:.2f}).")
                
        return anomaly_report