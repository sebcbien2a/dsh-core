import time
from qdrant_client import QdrantClient

from repositories.skills_repository import SkillsRepository
from agents.skill_creator_agent import SkillCreatorAgent
from services.skill_router import SkillRouter

def run_learning_loop_test():
    print("==================================================")
    print("🚀 TEST DE LA BOUCLE D'APPRENTISSAGE DSH-CORE (V8)")
    print("==================================================")

    print("\n[1/5] Initialisation des composants...")
    try:
        qdrant = QdrantClient(url="http://localhost:6333")
        skills_repo = SkillsRepository(qdrant_client=qdrant)
        skill_creator = SkillCreatorAgent(skills_repo=skills_repo)
        skill_router = SkillRouter(skills_repo=skills_repo)
        print("✅ Composants connectés à Qdrant et Ollama.")
    except Exception as e:
        print(f"❌ Erreur d'initialisation : {e}")
        return

    print("\n[2/5] Simulation d'un échec métier...")
    failed_task = "Extraire la TVA d'une facture complexe où HT et TTC sont inversés visuellement."
    error_context = "Le modèle de base a confondu le montant HT et TTC car la colonne de droite contenait le HT, provoquant une erreur mathématique."
    expert_resolution = "La colonne 'Total' à droite est en réalité le HT. Le TTC est toujours la ligne finale en bas à gauche."
    
    print(f"Tâche : {failed_task}")
    print(f"Erreur : {error_context}")
    print(f"Solution Experte : {expert_resolution}")

    print("\n[3/5] Déclenchement du SkillCreatorAgent...")
    new_skill_id = skill_creator.create_and_store_skill(
        task=failed_task, 
        error_context=error_context,
        expert_resolution=expert_resolution
    )
    
    if not new_skill_id:
        print("❌ Échec de la création du skill.")
        return
    print(f"✅ Skill généré et stocké dans Qdrant (ID: {new_skill_id})")

    print("Attente de 2 secondes pour l'indexation vectorielle...")
    time.sleep(2)

    print("\n[4/5] Interception d'une nouvelle tâche par le SkillRouter...")
    new_task = "Traiter une nouvelle facture du même fournisseur avec les colonnes HT et TTC inversées."
    print(f"Nouvelle tâche soumise : {new_task}")
    
    # APPEL CORRIGÉ (Ancienne méthode renvoyant un str ou None)
    skill_found = skill_router.retrieve_skill_for_task(task=new_task)

    print("\n[5/5] Résultat de l'injection :")
    if skill_found:
        print("✅ SUCCÈS : Le SkillRouter a retrouvé la compétence correspondante !")
        print(f"KPI d'usage mis à jour en base.")
        print("\n--- CONTEXTE PRÊT POUR INJECTION (Agent de Base) ---")
        print(skill_found)
        print("----------------------------------------------------")
        print("\n🏆 BOUCLE D'APPRENTISSAGE V8 VALIDÉE OFFICIELLEMENT.")
    else:
        print("❌ ÉCHEC : Le SkillRouter n'a pas retrouvé la compétence. (Vérifier le seuil de similarité ou Ollama)")

if __name__ == "__main__":
    run_learning_loop_test()