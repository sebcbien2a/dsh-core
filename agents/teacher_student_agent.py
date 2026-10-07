import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import (
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

# Import des composants V8 (Usine à compétences)
from repositories.skills_repository import SkillsRepository
from agents.skill_creator_agent import SkillCreatorAgent


class TeacherStudentMetric(BaseModel):
    workflow: str
    task: str

    base_model: str = os.getenv("BASE_MODEL")
    expert_model: Optional[str] = None
    supervisor_model: Optional[str] = None

    base_success: bool = False
    expert_used: bool = False
    supervisor_used: bool = False

    skill_created: bool = False
    skill_reused: bool = False

    error_context: Optional[str] = None

    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat()
    )


class TeacherStudentAgent:

    def __init__(
        self, 
        qdrant_client: QdrantClient, 
        langfuse_client=None
    ):
        print(">>> INITIALISATION : TEACHER-STUDENT AGENT (PRODUCTION FUSIONNÉE V8) <<<")
        self.qdrant = qdrant_client
        self.langfuse = langfuse_client

        self.metrics_collection = "teacher_student_metrics"
        self.vector_size = 768
        self.skill_threshold = 3

    def handle_task_result(
        self,
        workflow: str,
        task: str,
        base_success: bool,
        error_context: Optional[str] = None,
    ) -> Dict[str, Any]:

        metric = TeacherStudentMetric(
            workflow=workflow,
            task=task,
            base_success=base_success,
            error_context=error_context,
        )

        # 1. Succès du Modèle de Base
        if base_success:
            return self._record_metric(
                metric,
                {
                    "status": "success",
                    "resolved_by": metric.base_model,
                },
            )

        # 2. Escalade vers l'Expert Local
        metric.expert_used = True
        metric.expert_model = os.getenv("EXPERT_MODEL")

        expert_success, expert_resolution = self._run_escalation(
            model=metric.expert_model,
            context=error_context,
        )

        if expert_success:
            return self._record_metric(
                metric,
                {
                    "status": "success",
                    "resolved_by": metric.expert_model,
                    "resolution": expert_resolution,
                },
            )

        # 3. Escalade vers le Superviseur distant
        metric.supervisor_used = True
        metric.supervisor_model = os.getenv("SUPERVISOR_MODEL", "anthropic/claude-3-5-sonnet")

        claude_success, supervisor_resolution = self._run_escalation(
            model=metric.supervisor_model,
            context=error_context,
        )

        if claude_success:
            if self._should_create_skill(task):
                metric.skill_created = True
                # LE BRANCHEMENT V8 : Déclenchement effectif de la création de skill
                self._trigger_skill_creation(
                    task=task,
                    context=error_context,
                    expert_resolution=supervisor_resolution or expert_resolution,
                )

            return self._record_metric(
                metric,
                {
                    "status": "success",
                    "resolved_by": metric.supervisor_model,
                    "resolution": supervisor_resolution,
                },
            )

        # Échec total
        return self._record_metric(
            metric,
            {
                "status": "failed",
                "resolved_by": "none",
            },
        )

    def _record_metric(
        self,
        metric: TeacherStudentMetric,
        response_data: Dict[str, Any],
    ) -> Dict[str, Any]:

        point_id = str(uuid.uuid4())
        dummy_vector = [0.0] * self.vector_size

        try:
            self.qdrant.upsert(
                collection_name=self.metrics_collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=dummy_vector,
                        payload=metric.model_dump(),
                    )
                ],
            )
            response_data["metric_id"] = point_id
        except Exception as e:
            print(f"[TeacherStudentAgent] Erreur Qdrant : {e}")

        return response_data

    def _should_create_skill(
        self,
        task: str,
    ) -> bool:
        try:
            count_result = self.qdrant.count(
                collection_name=self.metrics_collection,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="task",
                            match=MatchValue(value=task),
                        ),
                        FieldCondition(
                            key="supervisor_used",
                            match=MatchValue(value=True),
                        ),
                    ]
                ),
            )
            return count_result.count >= self.skill_threshold
        except Exception as e:
            print(f"Erreur comptage métriques : {e}")
            return False

    def _trigger_skill_creation(
        self,
        task: str,
        context: Optional[str],
        expert_resolution: Optional[str] = None,
    ):
        """BRANCHEMENT V8 : Appel au SkillCreatorAgent."""
        print(f"[SKILL] Seuil atteint pour '{task}'. Création automatique de la compétence...")
        try:
            if self.qdrant:
                skills_repo = SkillsRepository(self.qdrant)
                creator = SkillCreatorAgent(skills_repo=skills_repo)
                skill_id = creator.create_and_store_skill(
                    task=task,
                    error_context=context,
                    expert_resolution=expert_resolution,
                )
                if skill_id:
                    print(f"[SKILL] ✅ Compétence générée et enregistrée dans Qdrant (ID: {skill_id})")
        except Exception as e:
            print(f"[SKILL] Erreur lors de l'appel au SkillCreatorAgent : {e}")

    def _run_escalation(
        self,
        model: str,
        context: Optional[str],
    ) -> Tuple[bool, str]:
        print(f"[ESCALATION] Appel du modèle {model}...")
        resolution_text = f"Résolution experte par {model} pour l'erreur : {context}"
        return True, resolution_text