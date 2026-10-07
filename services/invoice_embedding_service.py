import requests
from typing import Dict, Any, List

class InvoiceEmbeddingService:
    """
    Génère des embeddings à partir des données métier
    d'une facture via nomic-embed-text.
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434/api/embeddings",
        model_name: str = "nomic-embed-text"
    ):
        self.ollama_url = ollama_url
        self.model_name = model_name

    def build_invoice_text(self, invoice_data: Dict[str, Any]) -> str:
        """
        Transforme une facture en texte exploitable
        pour l'embedding sémantique.
        """
        return f"""
Fournisseur: {invoice_data.get('fournisseur', '')}
Numero facture: {invoice_data.get('numero_facture', '')}
Date facture: {invoice_data.get('date_facture', '')}
Date echeance: {invoice_data.get('date_echeance', '')}
Categorie: {invoice_data.get('categorie', '')}
Mode paiement: {invoice_data.get('mode_paiement', '')}
Montant HT: {invoice_data.get('montant_ht', '')}
Montant TVA: {invoice_data.get('montant_tva', '')}
Montant TTC: {invoice_data.get('montant_ttc', '')}
Devise: {invoice_data.get('devise', 'EUR')}
""".strip()

    def generate_embedding(self, invoice_data: Dict[str, Any]) -> List[float]:
        """
        Retourne un vecteur d'embedding.
        """
        invoice_text = self.build_invoice_text(invoice_data)
        
        response = requests.post(
            self.ollama_url,
            json={
                "model": self.model_name,
                "prompt": invoice_text
            },
            timeout=30
        )
        
        response.raise_for_status()
        embedding = response.json().get("embedding")
        
        if not embedding:
            raise ValueError("Aucun embedding retourne par Ollama.")
            
        return embedding