import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance, Filter, FieldCondition, MatchValue


class MemoryRepository:
    """
    Dépôt gérant la mémoire épisodique (historique des conversations et du contexte).
    Sert de couche de stockage vectoriel Qdrant exclusive pour le MemoryManager.
    """

    def __init__(
        self, 
        qdrant_client: QdrantClient, 
        collection_name: str = "episodic_memory",
        vector_size: int = 768
    ):
        self.qdrant = qdrant_client
        self.collection_name = collection_name
        self.vector_size = vector_size
        
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        """Vérifie et crée la collection vectorielle si elle n'existe pas encore."""
        try:
            if not self.qdrant.collection_exists(self.collection_name):
                self.qdrant.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE)
                )
        except Exception as e:
            print(f"[MemoryRepository] Erreur d'initialisation Qdrant : {e}")

    def save_memory(
        self, 
        session_id: str, 
        role: str, 
        content: str, 
        embedding: List[float], 
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Enregistre un fragment de mémoire (un tour de conversation ou une action système).
        """
        memory_id = str(uuid.uuid4())
        payload = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        if metadata:
            payload.update(metadata)

        try:
            self.qdrant.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=memory_id,
                        vector=embedding,
                        payload=payload
                    )
                ]
            )
            return memory_id
        except Exception as e:
            print(f"[MemoryRepository] Erreur lors de la sauvegarde : {e}")
            return ""

    def search_semantic_memory(
        self, 
        query_embedding: List[float], 
        limit: int = 5, 
        score_threshold: float = 0.75
    ) -> List[Dict[str, Any]]:
        """
        Recherche sémantique pour retrouver des concepts similaires discutés
        dans le passé (indépendamment de la session).
        """
        try:
            search_result = self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=limit,
                score_threshold=score_threshold
            )

            memories = []
            for point in search_result:
                data = point.payload or {}
                data["_similarity"] = point.score
                data["_qdrant_id"] = str(point.id)
                memories.append(data)
                
            return memories
        except Exception as e:
            print(f"[MemoryRepository] Erreur de recherche sémantique : {e}")
            return []

    def get_session_history(
        self, 
        session_id: str, 
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Récupère l'historique chronologique exact d'une session (filtre strict Qdrant).
        """
        try:
            search_result = self.qdrant.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="session_id",
                            match=MatchValue(value=session_id)
                        )
                    ]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=False
            )
            
            points = search_result[0]
            
            # Trie par timestamp pour garantir l'ordre naturel chronologique
            memories = [p.payload for p in points if p.payload]
            memories.sort(key=lambda x: x.get("timestamp", ""))
            
            return memories
            
        except Exception as e:
            print(f"[MemoryRepository] Erreur de récupération de session : {e}")
            return []