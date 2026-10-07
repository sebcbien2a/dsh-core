from qdrant_client import QdrantClient
from typing import Dict, Any

class TeacherStudentRepository:
    def __init__(self, qdrant_client: QdrantClient):
        self.qdrant = qdrant_client
        self.collection = "teacher_student_metrics"

    def get_global_metrics(self) -> Dict[str, Any]:
        """
        Scrolle toute la collection Qdrant pour calculer l'évolution du système.
        """
        try:
            # Récupération de tous les points (avec gestion de la pagination si gros volume)
            records, next_page_offset = self.qdrant.scroll(
                collection_name=self.collection,
                limit=10000, 
                with_payload=True,
                with_vectors=False # Pas besoin des vecteurs pour des stats
            )
            
            total_tasks = len(records)
            if total_tasks == 0:
                return {"error": "Aucune métrique enregistrée pour le moment."}

            # Initialisation des compteurs
            stats = {
                "total_workflows_processed": total_tasks,
                "autonomy_9b_success": 0,
                "escalations_to_27b": 0,
                "escalations_to_claude": 0,
                "skills_created": 0,
                "skills_reused": 0
            }

            # Agrégation des payloads
            for record in records:
                payload = record.payload
                
                if payload.get("base_success") is True:
                    stats["autonomy_9b_success"] += 1
                    
                if payload.get("expert_used") is True:
                    stats["escalations_to_27b"] += 1
                    
                if payload.get("supervisor_used") is True or payload.get("claude_used") is True:
                    stats["escalations_to_claude"] += 1
                    
                if payload.get("skill_created") is True:
                    stats["skills_created"] += 1
                    
                if payload.get("skill_reused") is True:
                    stats["skills_reused"] += 1

            # Calcul des pourcentages vitaux
            autonomy_rate = round((stats["autonomy_9b_success"] / total_tasks) * 100, 2)
            claude_dependency_rate = round((stats["escalations_to_claude"] / total_tasks) * 100, 2)

            return {
                "raw_metrics": stats,
                "kpi": {
                    "9b_autonomy_rate_percent": autonomy_rate,
                    "claude_dependency_rate_percent": claude_dependency_rate,
                    "system_learning_ROI": f"{stats['skills_created']} compétences acquises"
                }
            }

        except Exception as e:
            return {"error": f"Erreur lors du calcul des métriques : {str(e)}"}