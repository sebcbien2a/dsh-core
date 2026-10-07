from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator

class TypePiece(str, Enum):
    FACTURE = "FACTURE"
    AVOIR = "AVOIR"

class TypeTVA(str, Enum):
    NORMALE = "NORMALE"                      # Régime standard (TVA déductible classique)
    AUTOLIQUIDATION = "AUTOLIQUIDATION"      # Intracommunautaire (TVA payée nulle, CA3 requise)
    NON_ASSUJETTI = "NON_ASSUJETTI"          # Franchise en base art. 293 B CGI (Aucune TVA)

class InvoiceData(BaseModel):
    # --- 0. Métadonnées & Typage métier ---
    type_piece: TypePiece = TypePiece.FACTURE
    type_tva: TypeTVA = TypeTVA.NORMALE
    origine_document: str = "INCONNU"
    
    fournisseur: str
    date_facture: str
    numero_facture: Optional[str] = None
    
    # --- 1. Valeurs fiscales d'origine ---
    montant_ht: float
    taux_tva_pourcentage: Optional[float] = None
    taux_tva_theorique: Optional[float] = None  # 20.0 % pour calcul CA3 en autoliquidation
    montant_tva: Optional[float] = None
    montant_ttc: float
    
    # --- 2. Valeurs commerciales ---
    montant_remise: Optional[float] = 0.0
    montant_paye: Optional[float] = None
    
    # --- 3. Valeurs recalculées par DSH-Core ---
    montant_ht_net: Optional[float] = None
    montant_tva_net: Optional[float] = None
    montant_ttc_net: Optional[float] = None
    
    # --- 4. Statut et Audit (Soft Fail) ---
    statut_traitement: str = "VALIDE"
    commentaires_controle: list[str] = Field(default_factory=list)
    
    devise: str = "EUR"

    @model_validator(mode="after")
    def process_and_verify(self):
        # ==========================================
        # CONTRÔLES DES RÉGIMES DE TVA SPÉCIAUX
        # ==========================================
        if self.type_tva in [TypeTVA.AUTOLIQUIDATION, TypeTVA.NON_ASSUJETTI]:
            self.montant_tva = 0.0
            self.taux_tva_pourcentage = None
            
            if self.type_tva == TypeTVA.AUTOLIQUIDATION and self.taux_tva_theorique is None:
                self.taux_tva_theorique = 20.0

        # ==========================================
        # CONTRÔLES TAUX TVA (Factures NORMALE)
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE and self.taux_tva_pourcentage is not None:
            if self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage < 0:
                self.commentaires_controle.append(f"Taux TVA négatif détecté ({self.taux_tva_pourcentage}%).")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"
            elif self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage > 30:
                self.commentaires_controle.append(f"Taux TVA improbable détecté ({self.taux_tva_pourcentage}%). Valeur invalidée.")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # CONTRÔLES MONTANTS (Restreints aux Factures)
        # ==========================================
        if self.montant_tva is not None and self.type_piece == TypePiece.FACTURE:
            if self.montant_tva < 0:
                self.commentaires_controle.append(f"Montant TVA négatif détecté ({self.montant_tva}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc > 0 and self.montant_tva > self.montant_ttc:
                self.commentaires_controle.append("TVA supérieure au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > 0 and self.montant_tva > self.montant_ht:
                self.commentaires_controle.append("TVA supérieure au HT : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        if self.type_piece == TypePiece.FACTURE:
            if self.montant_ht < 0:
                self.commentaires_controle.append(f"Montant HT négatif détecté ({self.montant_ht}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc < 0:
                self.commentaires_controle.append(f"Montant TTC négatif détecté ({self.montant_ttc}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > self.montant_ttc:
                self.commentaires_controle.append("HT supérieur au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # DÉTECTION D'HALLUCINATION (HT = TVA = TTC)
        # ==========================================
        if (
            self.montant_tva is not None
            and abs(self.montant_ht) > 0
            and abs(self.montant_ttc) > 0
            and abs(self.montant_ht - self.montant_tva) < 0.01
            and abs(self.montant_tva - self.montant_ttc) < 0.01
        ):
            self.commentaires_controle.append("Configuration HT = TVA = TTC détectée. Hallucination probable du LLM.")
            self.statut_traitement = "A_VERIFIER"
            self.taux_tva_pourcentage = None
            self.montant_tva = None

        # ==========================================
        # VÉRIFICATION FISCALE SELON RÉGIME
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE:
            if self.montant_tva is not None:
                attendu_ttc = round(self.montant_ht + self.montant_tva, 2)
                if abs(attendu_ttc - self.montant_ttc) > 0.02:
                    self.statut_traitement = "A_VERIFIER"
                    self.commentaires_controle.append(
                        f"Anomalie fiscale : HT ({self.montant_ht}) + TVA ({self.montant_tva}) != TTC ({self.montant_ttc})"
                    )
            else:
                if self.type_piece == TypePiece.FACTURE:
                    self.commentaires_controle.append("TVA absente ou invalidée : contrôle fiscal automatique impossible.")
                    self.statut_traitement = "A_VERIFIER"
        else:
            if abs(self.montant_ht - self.montant_ttc) > 0.02:
                self.statut_traitement = "A_VERIFIER"
                self.commentaires_controle.append(
                    f"Anomalie régime {self.type_tva.value} : HT ({self.montant_ht}) != TTC ({self.montant_ttc})"
                )
            
        # ==========================================
        # COHÉRENCE BANCAIRE
        # ==========================================
        remise = self.montant_remise or 0.0
        if self.montant_paye is None:
            self.montant_paye = round(self.montant_ttc - remise, 2)
            
        attendu_paye = round(self.montant_ttc - remise, 2)
        if abs(attendu_paye - self.montant_paye) > 0.02:
            self.statut_traitement = "A_VERIFIER"
            self.commentaires_controle.append(
                f"Anomalie bancaire : TTC ({self.montant_ttc}) - Remise ({remise}) != Payé ({self.montant_paye})"
            )
            
        # ==========================================
        # CALCUL TRÉSORERIE NETTE
        # ==========================================
        if self.montant_ttc != 0:
            ratio = self.montant_paye / self.montant_ttc
            self.montant_ht_net = round(self.montant_ht * ratio, 2)
            
            tva_valide = self.montant_tva if self.montant_tva is not None else 0.0
            self.montant_tva_net = round(tva_valide * ratio, 2)
            self.montant_ttc_net = self.montant_paye
            
            attendu_ttc_net = round(self.montant_ht_net + self.montant_tva_net, 2)
            if abs(attendu_ttc_net - self.montant_ttc_net) > 0.02:
                self.montant_ht_net = round(self.montant_ttc_net - self.montant_tva_net, 2)
        else:
            self.montant_ht_net = 0.0
            self.montant_tva_net = 0.0
            self.montant_ttc_net = 0.0

        return self