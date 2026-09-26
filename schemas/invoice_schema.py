# schemas/invoice_schema.py - Schéma de validation Pydantic strict pour factures
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class SupplierInfo(BaseModel):
    nom: str = Field(..., description="Nom ou raison sociale du fournisseur")
    siret: Optional[str] = Field(None, description="Numéro SIRET / Siren (14 ou 9 chiffres)")
    numero_tva: Optional[str] = Field(None, description="Numéro de TVA intracommunautaire")
    adresse: Optional[str] = Field(None, description="Adresse postale de l'émetteur")


class ClientInfo(BaseModel):
    nom: str = Field(..., description="Nom ou raison sociale du client")
    siret: Optional[str] = Field(None, description="Numéro SIRET du client si professionnel")
    adresse: Optional[str] = Field(None, description="Adresse de facturation du client")


class InvoiceItem(BaseModel):
    description: str = Field(..., description="Désignation du produit ou de la prestation")
    quantite: float = Field(default=1.0, description="Quantité facturée")
    prix_unitaire_ht: float = Field(..., description="Prix unitaire Hors Taxes")
    total_ht: float = Field(..., description="Total HT de la ligne (quantite * prix_unitaire)")
    taux_tva: Optional[float] = Field(default=20.0, description="Taux de TVA applicable en %")


class InvoiceSchema(BaseModel):
    numero_facture: str = Field(..., description="Identifiant unique / numéro de la pièce comptable")
    date_emission: str = Field(..., description="Date d'émission au format ISO YYYY-MM-DD")
    date_echeance: Optional[str] = Field(None, description="Date limite de règlement")
    fournisseur: SupplierInfo
    client: ClientInfo
    lignes: List[InvoiceItem] = Field(default_factory=list, description="Détail des lignes facturées")
    total_ht: float = Field(..., description="Montant total Hors Taxes cumulé")
    montant_tva: float = Field(..., description="Montant total de la TVA")
    total_ttc: float = Field(..., description="Montant net à payer Toutes Taxes Comprises")
    devise: str = Field(default="EUR", description="Devise comptable (EUR, USD, etc.)")
    statut_validation_math: bool = Field(default=False, description="Validation arithmétique interne")

    @model_validator(mode="after")
    def verifier_coherence_mathematique(self):
        """Vérifie l'exactitude de l'équation : Total HT + Montant TVA == Total TTC."""
        somme_theorique = round(self.total_ht + self.montant_tva, 2)
        total_declare = round(self.total_ttc, 2)
        
        # Tolérance de 5 centimes pour absorber les arrondis de centimes par ligne
        if abs(somme_theorique - total_declare) <= 0.05:
            self.statut_validation_math = True
            return self
        
        raise ValueError(
            f"Écart arithmétique détecté : HT ({self.total_ht:.2f}) + "
            f"TVA ({self.montant_tva:.2f}) = {somme_theorique:.2f} €, "
            f"mais le TTC déclaré est de {total_declare:.2f} €"
        )


if __name__ == "__main__":
    print("=== TEST DE VALIDATION DU SCHÉMA FACTURE ===")
    
    # 1. Test avec une facture valide
    donnees_valides = {
        "numero_facture": "FA-2026-0042",
        "date_emission": "2026-09-25",
        "fournisseur": {"nom": "Paintcar Distribution", "siret": "12345678900012"},
        "client": {"nom": "Atelier Carrosserie Centre"},
        "lignes": [
            {"description": "Vernis UHS 5L", "quantite": 2, "prix_unitaire_ht": 50.0, "total_ht": 100.0, "taux_tva": 20.0}
        ],
        "total_ht": 100.0,
        "montant_tva": 20.0,
        "total_ttc": 120.0
    }
    
    facture = InvoiceSchema(**donnees_valides)
    print(f" Facture valide acceptée : N° {facture.numero_facture}")
    print(f"-> Cohérence mathématique validée : {facture.statut_validation_math}")

    # 2. Test avec une facture incohérente (doit lever une alerte)
    print("\n--- Test de détection d'incohérence arithmétique ---")
    donnees_invalides = {
        "numero_facture": "FA-ERREUR",
        "date_emission": "2026-09-25",
        "fournisseur": {"nom": "Fournisseur Test"},
        "client": {"nom": "Client Test"},
        "total_ht": 100.0,
        "montant_tva": 20.0,
        "total_ttc": 150.0  # Erreur volontaire : 100 + 20 != 150
    }
    
    try:
        InvoiceSchema(**donnees_invalides)
    except Exception as e:
        print(f" Incohérence interceptée avec succès :\n   {e}")