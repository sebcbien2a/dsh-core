import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams


class SkillsRepository:
    """
    Dépôt (Repository) pour stocker et récupérer les compétences acquises (Skills).
    Base de données vectorielle : Qdrant.
    """
    
    def __init__(self, qdrant_client: QdrantClient, collection_name: str = "agent_skills"):
        self.client = qdrant_client
        self.collection_name = collection_name
        self.vector_size = 768  # Taille standard pour le modèle "nomic-embed-text"

        # Création automatique de la collection si elle n'existe pas encore
        try:
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size, 
                        distance=Distance.COSINE
                    )
                )
                print(f"[SkillsRepository] Collection '{self.collection_name}' créée avec succès.")
        except Exception as e:
            print(f"[SkillsRepository] Avertissement Qdrant : {e}")


    def save_skill(
        self, 
        task_name: str, 
        skill_content: str, 
        embedding: List[float], 
        metadata: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Sauvegarde la nouvelle compétence dans Qdrant avec son vecteur.
        Signature stricte appelée par SkillCreatorAgent.
        """
        skill_id = str(uuid.uuid4())
        payload = {
            "task_name": task_name,
            "skill_content": skill_content,
            "created_at": datetime.now().isoformat(),
            "usage_count": 0,  # Initialisation du KPI de réutilisation
            "metadata": metadata or {}
        }
        
        try:
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=skill_id,
                        vector=embedding,
                        payload=payload
                    )
                ]
            )
            return skill_id
        except Exception as e:
            print(f"[SkillsRepository] Erreur lors de l'enregistrement du Skill : {e}")
            return None


    def find_relevant_skills(
        self, 
        task_embedding: List[float], 
        limit: int = 1, 
        score_threshold: float = 0.85
    ) -> List[Dict[str, Any]]:
        """
        Recherche par similarité vectorielle les compétences pertinentes.
        Mise à jour pour l'API Qdrant 1.19.1+ (utilisation de query_points).
        """
        try:
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=task_embedding,
                limit=limit,
                score_threshold=score_threshold
            ).points
            
            formatted_skills = []
            for hit in results:
                formatted_skills.append({
                    "_skill_id": str(hit.id),
                    "skill_content": hit.payload.get("skill_content", ""),
                    "task_name": hit.payload.get("task_name", ""),
                    "_similarity_score": hit.score
                })
            return formatted_skills
        except Exception as e:
            print(f"[SkillsRepository] Erreur lors de la recherche vectorielle : {e}")
            return []


    def increment_usage(self, skill_id: str):
        """
        Incrémente le compteur d'utilisation d'une compétence pour mesurer son ROI.
        Appelé par le SkillRouter lorsqu'un skill est effectivement injecté.
        """
        try:
            # 1. Récupération des données existantes
            points = self.client.retrieve(
                collection_name=self.collection_name,
                ids=[skill_id],
                with_payload=True
            )
            
            if points:
                current_payload = points[0].payload or {}
                new_count = current_payload.get("usage_count", 0) + 1
                
                # 2. Mise à jour exclusive du champ usage_count
                self.client.set_payload(
                    collection_name=self.collection_name,
                    payload={"usage_count": new_count},
                    points=[skill_id]
                )
        except Exception as e:
            print(f"[SkillsRepository] Erreur lors de l'incrémentation du KPI : {e}")