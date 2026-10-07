import os
import requests
from typing import Optional, List, Dict, Any
from langfuse import observe
from repositories.skills_repository import SkillsRepository

class SkillCreatorAgent:
    """
    Usine à compétences (Model-Agnostic).
    Prend une erreur, son contexte et la solution de l'expert, génère l'embedding 
    sur la connaissance produite (tâche + contenu du skill), et sauvegarde le tout.
    """
    def __init__(self, skills_repo: SkillsRepository):
        self.skills_repo = skills_repo
        
        # Principes de rôles V8
        self.creator_role = "EXPERT_MODEL"
        self.model_name = os.getenv("EXPERT_MODEL")
        self.embed_model = os.getenv("EMBED_MODEL", "nomic-embed-text")
        
        self.ollama_url = "http://localhost:11434/api/generate"
        self.ollama_embed_url = "http://localhost:11434/api/embeddings"

    def _generate_embedding(self, text: str) -> List[float]:
        """Génère l'embedding du texte pour Qdrant."""
        try:
            resp = requests.post(
                self.ollama_embed_url,
                json={"model": self.embed_model, "prompt": text},
                timeout=30
            )
            resp.raise_for_status()
            return resp.json().get("embedding", [])
        except Exception as e:
            print(f"[SkillCreator] Erreur de vectorisation : {e}")
            return []

    @observe(name="SkillCreator_Generate")
    def create_and_store_skill(
        self, 
        task: str, 
        error_context: Optional[str] = None, 
        expert_resolution: Optional[str] = None
    ) -> Optional[str]:
        
        print(f"[SkillCreator] Synthèse d'une nouvelle compétence via le rôle [{self.creator_role}]...")
        
        prompt = f"""Tu es un analyste métier expert.
Un agent a échoué sur cette tâche : {task}
Erreur rencontrée : {error_context}
Solution apportée par l'expert : {expert_resolution}

Rédige une directive métier claire, concise et réutilisable (un "Skill") qui permettra à l'agent de ne plus commettre cette erreur à l'avenir.
Ne renvoie QUE la directive."""
        
        try:
            print(f"[DEBUG] Modèle utilisé : {self.model_name}")
            print("[DEBUG] Avant appel Ollama")
            resp = requests.post(
                self.ollama_url,
                json={"model": self.model_name, "prompt": prompt, "stream": False},
                timeout=300  # Limite passée à 5 minutes pour les gros modèles
            )
            print("[DEBUG] Après appel Ollama")
            
            if resp.status_code == 200:
                skill_content = resp.json().get("response", "").strip()
                if skill_content:
                    # RÈGLE V8 : Vectorisation de la connaissance produite (Tâche + Contenu du Skill)
                    text_to_embed = f"{task}\n{skill_content}"
                    
                    print("[DEBUG] Génération embedding...")
                    embedding = self._generate_embedding(text_to_embed)
                    if not embedding:
                        print("[SkillCreator] Erreur : impossible de générer l'embedding.")
                        return None

                    metadata = {
                        "error_context": error_context,
                        "expert_resolution": expert_resolution,
                        "created_by_role": self.creator_role
                    }

                    print("[DEBUG] Sauvegarde Qdrant...")
                    skill_id = self.skills_repo.save_skill(
                        task_name=task,
                        skill_content=skill_content,
                        embedding=embedding,
                        metadata=metadata
                    )
                    return skill_id
        except Exception as e:
            print(f"[SkillCreator] Erreur critique lors de la génération : {e}")
            
        return None