from typing import Dict, Any, List
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

class InvoiceSimilarityService:
    """
    Service responsable de la recherche sémantique et vectorielle 
    des factures archivées dans Qdrant (Détection de doublons, similarité, historique).
    """

    def __init__(
        self,
        qdrant_client: QdrantClient,
        collection_name: str = "invoice_documents"
    ):
        self.qdrant = qdrant_client
        self.collection_name = collection_name

    def find_similar_invoices(
        self,
        query_vector: List[float],
        limit: int = 3,
        score_threshold: float = 0.85
    ) -> List[Dict[str, Any]]:
        """
        Recherche les factures sémantiquement proches (même type de contenu, 
        fournisseur similaire ou montants proches) grâce au vecteur.
        """
        try:
            search_result = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold
            )

            results = []
            for point in search_result:
                data = point.payload or {}
                data["_similarity_score"] = point.score
                data["_qdrant_id"] = str(point.id)
                results.append(data)

            return results

        except Exception as e:
            print(f"[InvoiceSimilarityService] Erreur recherche vectorielle : {e}")
            return []

    def find_invoices_by_supplier(
        self,
        fournisseur: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Recherche exacte pour extraire l'historique d'un fournisseur.
        Utile pour nourrir l'AccountingValidator et détecter les montants inhabituels.
        """
        if not fournisseur:
            return []
            
        try:
            search_result = self.qdrant.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="fournisseur",
                            match=MatchValue(value=fournisseur)
                        )
                    ]
                ),
                limit=limit
            )
            
            points = search_result[0]
            results = []
            
            for point in points:
                data = point.payload or {}
                data["_qdrant_id"] = str(point.id)
                results.append(data)

            return results

        except Exception as e:
            print(f"[InvoiceSimilarityService] Erreur recherche fournisseur : {e}")
            return []

    def detect_potential_duplicates(
        self,
        query_vector: List[float],
        current_invoice: Dict[str, Any],
        strict_threshold: float = 0.98
    ) -> List[Dict[str, Any]]:
        """
        Croise la recherche vectorielle (très haut score) avec les données métiers 
        pour isoler les vrais doublons (même fournisseur, même numéro, ou même montant/date).
        """
        # 1. On cherche les factures extrêmement similaires (ex: un PDF renommé)
        highly_similar = self.find_similar_invoices(
            query_vector=query_vector, 
            limit=2, 
            score_threshold=strict_threshold
        )
        
        duplicates = []
        current_num = current_invoice.get("numero_facture")
        current_ttc = current_invoice.get("montant_ttc")
        
        for inv in highly_similar:
            # Règle métier : si le vecteur est identique à 98% ET que le montant/numéro correspond
            if inv.get("numero_facture") == current_num or inv.get("montant_ttc") == current_ttc:
                duplicates.append(inv)
                
        return duplicates