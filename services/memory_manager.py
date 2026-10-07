from typing import Dict, Any, List, Optional

from repositories.graph_repository import GraphRepository
from repositories.skills_repository import SkillsRepository
from repositories.memory_repository import MemoryRepository

class MemoryManager:
    """
    Orchestrateur central de la mémoire de DSH-Core.
    Synchronise de manière transparente le stockage vectoriel (Qdrant) 
    et le stockage relationnel (Neo4j).
    """

    def __init__(
        self,
        graph_repo: GraphRepository,
        skills_repo: Optional[SkillsRepository] = None,
        memory_repo: Optional[MemoryRepository] = None
    ):
        self.graph_repo = graph_repo
        self.skills_repo = skills_repo
        self.memory_repo = memory_repo

    def is_document_known(self, document_hash: str) -> bool:
        """
        Bouclier anti-doublon absolu (avant OCR).
        """
        return self.graph_repo.document_exists(document_hash)

    def save_invoice_context(
        self, 
        invoice_data: Dict[str, Any], 
        document_hash: Optional[str] = None,
        qdrant_id: Optional[str] = None
    ) -> bool:
        """
        Sauvegarde le contexte de la facture dans le graphe (Neo4j).
        Consolide le lien entre l'ID Qdrant et le nœud Neo4j.
        """
        data_for_graph = invoice_data.copy()
        
        # On attache l'ID vectoriel au nœud du graphe pour faire le pont plus tard
        if qdrant_id:
            data_for_graph["qdrant_vector_id"] = qdrant_id
            
        return self.graph_repo.create_invoice_graph(data_for_graph, document_hash)

    def save_skill(
        self, 
        task_name: str, 
        skill_content: str, 
        embedding: List[float], 
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Double écriture d'une compétence : 
        1. Vecteur Qdrant pour la recherche sémantique.
        2. Nœud Neo4j pour l'historique et la cartographie des capacités de l'agent.
        """
        if not self.skills_repo:
            print("[MemoryManager] SkillsRepository non configuré.")
            return None
            
        # 1. Sauvegarde Vectorielle
        skill_id = self.skills_repo.save_skill(task_name, skill_content, embedding, metadata)
        
        if not skill_id:
            return None
            
        # 2. Sauvegarde Relationnelle
        # On crée le nœud Skill (sans stocker tout le texte lourd, juste un snippet et l'ID)
        self.graph_repo.create_entity(
            label="Skill",
            properties={
                "id": skill_id,
                "task_name": task_name,
                "snippet": skill_content[:100] + "..."
            }
        )
        
        # On lie la compétence à la famille de tâche qu'elle résout
        self.graph_repo.create_entity(
            label="Task", 
            properties={"name": task_name}, 
            unique_key="name"
        )
        
        self.graph_repo.create_relationship(
            label_a="Skill", key_a="id", val_a=skill_id,
            rel_type="SOLVES",
            label_b="Task", key_b="name", val_b=task_name
        )
        
        return skill_id

    def save_episodic_memory(
        self, 
        session_id: str, 
        role: str, 
        content: str, 
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Enregistre un événement conversationnel dans Qdrant ET relie le message 
        à sa session dans le graphe Neo4j.
        """
        if not self.memory_repo:
            print("[MemoryManager] MemoryRepository non configuré.")
            return None
            
        # 1. Sauvegarde Vectorielle
        memory_id = self.memory_repo.save_memory(session_id, role, content, embedding, metadata)
        
        if not memory_id:
            return None
            
        # 2. Construction de l'arbre conversationnel dans Neo4j
        self.graph_repo.create_entity("Session", {"id": session_id})
        
        self.graph_repo.create_entity(
            label="Message", 
            properties={
                "id": memory_id, 
                "role": role, 
                "snippet": content[:50]
            }
        )
        
        self.graph_repo.create_relationship(
            label_a="Session", key_a="id", val_a=session_id,
            rel_type="HAS_MESSAGE",
            label_b="Message", key_b="id", val_b=memory_id
        )
        
        return memory_id