from typing import Dict, Any, List, Optional
from repositories.graph_repository import GraphRepository


class Neo4jService:
    """
    Service métier pour l'interrogation du graphe de connaissances (Knowledge Graph).
    Encapsule les requêtes Cypher pour exposer des méthodes simples aux autres composants.
    """

    def __init__(self, graph_repo: GraphRepository):
        self.graph_repo = graph_repo

    def get_supplier_history(self, supplier_name: str) -> List[Dict[str, Any]]:
        """
        Récupère l'historique complet des factures associées à un fournisseur.
        Utile pour croiser avec l'AccountingValidator sur le long terme.
        """
        query = """
        MATCH (i:Invoice)-[:BELONGS_TO]->(s:Supplier {name: $supplier_name})
        RETURN i.id AS invoice_id, i.montant_ttc AS montant_ttc, 
               i.date_facture AS date_facture, i.qdrant_vector_id AS qdrant_id
        ORDER BY i.date_facture DESC
        """
        return self.graph_repo.execute_query(query, {"supplier_name": supplier_name})

    def get_invoice_lineage(self, invoice_num: str, supplier_name: str) -> List[Dict[str, Any]]:
        """
        Retrace la généalogie d'une facture : Document source -> Facture -> Fournisseur.
        Indispensable pour l'audit et la traçabilité.
        """
        invoice_id = f"{supplier_name}_{invoice_num}".replace(" ", "_").upper()
        
        query = """
        MATCH (d:Document)-[:CONTAINS]->(i:Invoice {id: $invoice_id})-[:BELONGS_TO]->(s:Supplier)
        RETURN d.hash AS document_hash, i.id AS invoice_id, s.name AS supplier_name
        """
        return self.graph_repo.execute_query(query, {"invoice_id": invoice_id})

    def get_skills_for_task(self, task_name: str) -> List[Dict[str, Any]]:
        """
        Trouve toutes les compétences (skills) qui résolvent une famille de tâche donnée.
        Permet de cartographier ce que le système a appris.
        """
        query = """
        MATCH (s:Skill)-[:SOLVES]->(t:Task {name: $task_name})
        RETURN s.id AS skill_id, s.snippet AS skill_snippet
        """
        return self.graph_repo.execute_query(query, {"task_name": task_name})
        
    def verify_document_ingestion(self, document_hash: str) -> bool:
        """
        Expose la vérification d'existence d'un document (bouclier anti-doublon).
        """
        return self.graph_repo.document_exists(document_hash)