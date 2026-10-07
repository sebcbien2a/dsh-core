import os
import requests
from typing import Optional

# Langfuse V4
from langfuse import observe

from repositories.skills_repository import SkillsRepository


class SkillRouter:
    """
    Routeur d'apprentissage de DSH-Core (Phase V8).
    Intercepte les tâches, recherche des compétences (skills) pertinentes
    dans le catalogue vectoriel, et formate le contexte pour l'injecter aux agents.
    """

    def __init__(
        self,
        skills_repo: SkillsRepository,
        similarity_threshold: float = 0.70,  # <-- SEUIL BAISSÉ POUR CAPTER LE SKILL ENRICHI
        top_k: int = 1
    ):
        self.skills_repo = skills_repo
        self.similarity_threshold = similarity_threshold
        self.top_k = top_k
        
        # Principe V8 : Model-Agnostic
        self.embed_model = os.getenv("EMBED_MODEL", "nomic-embed-text")
        
        # URL brute d'Ollama (sans balise HTML parasite)
        self.ollama_embed_url = "http://localhost:11434/api/embeddings"

    def _generate_embedding(self, text: str) -> list[float]:
        """Génère l'embedding de la tâche via Ollama."""
        try:
            resp = requests.post(
                self.ollama_embed_url,
                json={"model": self.embed_model, "prompt": text},
                timeout=30
            )
            resp.raise_for_status()
            return resp.json().get("embedding", [])
        except Exception as e:
            print(f"[SkillRouter] Erreur de vectorisation : {e}")
            return []

    @observe(name="SkillRouter_Retrieve")
    def retrieve_skill_for_task(self, task: str) -> Optional[str]:
        """
        Recherche sémantiquement si un skill existant permet de résoudre la tâche.
        Renvoie le contexte formaté (str) prêt à être injecté, ou None.
        """
        print(f"[SkillRouter] Analyse de la tâche pour injection de compétences : '{task}'")
        
        task_embedding = self._generate_embedding(task)
        if not task_embedding:
            return None

        # Recherche dans le catalogue Qdrant via le repository
        relevant_skills = self.skills_repo.find_relevant_skills(
            task_embedding=task_embedding,
            limit=self.top_k,
            score_threshold=self.similarity_threshold
        )

        if not relevant_skills:
            print("[SkillRouter] Aucun skill pertinent trouvé pour cette tâche.")
            return None

        # Extraction du meilleur résultat
        best_skill = relevant_skills[0]
        skill_id = best_skill.get("_skill_id")
        skill_content = best_skill.get("skill_content")
        similarity = best_skill.get("_similarity_score", 0.0)

        if skill_content and skill_id:
            print(f"[SkillRouter] 🎯 Skill activé (ID: {skill_id}) - Score: {similarity:.2f}")
            
            # Incrémentation de l'indicateur d'usage (KPI de réutilisation)
            self.skills_repo.increment_usage(skill_id)
            
            # Formatage du contexte
            formatted_context = f"--- Skill Recommandé (Pertinence: {similarity:.2f}) ---\n{skill_content}"
            return formatted_context

        return None