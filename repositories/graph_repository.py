import os
from typing import Dict, Any, List, Optional
from neo4j import GraphDatabase

class GraphRepository:
    """
    Dépôt (Repository) pour interagir avec Neo4j.
    Gère le stockage relationnel, la détection de doublons via Hash 
    et la création de sous-graphes métier (Factures, Fournisseurs).
    """

    def __init__(
        self, 
        uri: str = "bolt://" + "localhost:7687", 
        user: str = "neo4j", 
        password: str = ""
    ):
        # Sécurité : le mot de passe doit venir de l'environnement
        self.uri = os.getenv("NEO4J_URI", uri)
        self.user = os.getenv("NEO4J_USER", user)
        self.password = os.getenv("NEO4J_PASSWORD", password)
        
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.driver.verify_connectivity()
            print("[GraphRepository] ✅ Connexion à Neo4j établie avec succès.")
        except Exception as e:
            print(f"[GraphRepository] ❌ Erreur de connexion à Neo4j : {e}")
            self.driver = None

    def close(self):
        """Ferme la connexion au driver proprement."""
        if self.driver:
            self.driver.close()

    def document_exists(self, document_hash: str) -> bool:
        """
        Vérifie si un fichier (PDF, image) a déjà été traité dans le système.
        Sert de bouclier absolu avant l'ingestion OCR.
        """
        if not self.driver:
            return False
            
        query = "MATCH (d:Document {hash: $hash}) RETURN count(d) > 0 AS exists"
        
        try:
            with self.driver.session() as session:
                result = session.run(query, hash=document_hash)
                record = result.single()
                return record["exists"] if record else False
        except Exception as e:
            print(f"[GraphRepository] Erreur lors de la vérification du hash : {e}")
            return False

    def create_entity(self, label: str, properties: Dict[str, Any], unique_key: str = "id") -> Optional[str]:
        """
        Crée ou met à jour un nœud générique dans le graphe.
        """
        if not self.driver:
            return None
            
        unique_value = properties.get(unique_key)
        
        if unique_value:
            query = f"""
            MERGE (n:{label} {{{unique_key}: $unique_value}})
            SET n += $props
            RETURN elementId(n) AS node_id
            """
            parameters = {"unique_value": unique_value, "props": properties}
        else:
            query = f"CREATE (n:{label} $props) RETURN elementId(n) AS node_id"
            parameters = {"props": properties}
        
        try:
            with self.driver.session() as session:
                result = session.run(query, parameters)
                record = result.single()
                return record["node_id"] if record else None
        except Exception as e:
            print(f"[GraphRepository] Erreur création de l'entité {label} : {e}")
            return None

    def create_relationship(
        self, 
        label_a: str, key_a: str, val_a: Any,
        rel_type: str, 
        label_b: str, key_b: str, val_b: Any
    ):
        """
        Crée une relation dirigée entre deux nœuds existants.
        """
        if not self.driver:
            return
            
        query = f"""
        MATCH (a:{label_a} {{{key_a}: $val_a}})
        MATCH (b:{label_b} {{{key_b}: $val_b}})
        MERGE (a)-[r:{rel_type}]->(b)
        RETURN type(r)
        """
        try:
            with self.driver.session() as session:
                session.run(query, val_a=val_a, val_b=val_b)
        except Exception as e:
            print(f"[GraphRepository] Erreur création relation {rel_type} : {e}")

    def create_invoice_graph(self, invoice_data: Dict[str, Any], document_hash: Optional[str] = None) -> bool:
        """
        Orchestre la création complète d'une facture et de ses relations en une seule transaction.
        (Document) -[CONTAINS]-> (Invoice) -[BELONGS_TO]-> (Supplier)
        """
        if not self.driver:
            return False
            
        fournisseur = invoice_data.get("fournisseur", "INCONNU").strip()
        num_facture = invoice_data.get("numero_facture", "SANS_NUMERO").strip()
        
        # Création d'une clé d'idempotence métier unique
        invoice_id = f"{fournisseur}_{num_facture}".replace(" ", "_").upper()
        
        try:
            with self.driver.session() as session:
                # 1. Le Fournisseur
                session.run(
                    "MERGE (s:Supplier {name: $fournisseur})", 
                    fournisseur=fournisseur
                )
                
                # 2. La Facture + Relation au fournisseur
                session.run(
                    """
                    MERGE (i:Invoice {id: $invoice_id})
                    SET i += $props
                    WITH i
                    MATCH (s:Supplier {name: $fournisseur})
                    MERGE (i)-[:BELONGS_TO]->(s)
                    """, 
                    invoice_id=invoice_id, 
                    props=invoice_data,
                    fournisseur=fournisseur
                )
                
                # 3. Le Document (Hash) s'il est fourni
                if document_hash:
                    session.run(
                        """
                        MERGE (d:Document {hash: $hash})
                        WITH d
                        MATCH (i:Invoice {id: $invoice_id})
                        MERGE (d)-[:CONTAINS]->(i)
                        """,
                        hash=document_hash,
                        invoice_id=invoice_id
                    )
            return True
        except Exception as e:
            print(f"[GraphRepository] Erreur création graphe complet de la facture : {e}")
            return False

    def execute_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        if not self.driver:
            return []
        try:
            with self.driver.session() as session:
                result = session.run(query, parameters or {})
                return [dict(record) for record in result]
        except Exception as e:
            print(f"[GraphRepository] Erreur Cypher : {e}")
            return []