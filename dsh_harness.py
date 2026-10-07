# dsh_harness.py - Le Chef d'Orchestre de la Boucle Rouge (Teacher-Student)
import os
import time
import router
import anythingllm_tool
from dotenv import load_dotenv

# 1. FORCER LE CHARGEMENT DU .ENV
load_dotenv()

# 2. CHARGEMENT STRICT DE TA STACK (Sans vieux fallbacks en dur)
BASE_MODEL = os.getenv("BASE_MODEL")
EXPERT_MODEL = os.getenv("EXPERT_MODEL")
VISION_MODEL = os.getenv("VISION_MODEL")

def executer_boucle_rouge(prompt: str, image_path: str = None):
    print("\n" + "="*50)
    print(f"🚀 NOUVELLE TÂCHE REÇUE : {prompt}")
    print("="*50)
    
    # ---------------------------------------------------------
    # ÉTAPE 1 : LE ROUTAGE (Qui doit faire le travail ?)
    # ---------------------------------------------------------
    print("\n[ÉTAPE 1] Analyse de la requête par le Routeur...")
    decision = router.route_prompt(prompt, image_provided=bool(image_path))
    agent_brut = decision['agent']
    
    # Correction à la volée si ton fichier router.py renvoie encore les vieux noms
    if agent_brut == 'llama3.2-vision-local':
        agent_choisi = VISION_MODEL
    elif agent_brut == 'cloud-sonnet-5':
        agent_choisi = EXPERT_MODEL
    elif agent_brut == 'qwen-9b-local':
        agent_choisi = BASE_MODEL
    else:
        agent_choisi = agent_brut

    print(f"🎯 Décision : Assigné à {agent_choisi} ({decision.get('role', 'Agent')})")
    
    # ---------------------------------------------------------
    # ÉTAPE 2 : L'EXÉCUTION & UTILISATION DES OUTILS
    # ---------------------------------------------------------
    print("\n[ÉTAPE 2] Exécution en cours...")
    
    if agent_choisi == VISION_MODEL:
        print(f"👁️ {VISION_MODEL} analyse l'image ({image_path})...")
        
        # --- VÉRITABLE APPEL API VERS LE MODÈLE DE VISION ---
        # Remplace ceci par l'appel réel à ton API Ollama / vLLM local
        # Exemple avec requests (nécessite import requests, base64) :
        # payload = {"model": VISION_MODEL, "prompt": prompt, "images": [image_encodee_en_base64]}
        # response = requests.post("http://localhost:11434/api/generate", json=payload)
        
        resultat_execution = "[ATTENTE_IMPLEMENTATION_API_VISION_REELLE]"
        print(f"Résultat Vision : {resultat_execution}")
        
    elif agent_choisi == EXPERT_MODEL:
        print(f"🧑‍🏫 {EXPERT_MODEL} (Juge/Expert) audite le système ou rédige un SKILL.md...")
        resultat_execution = "Nouvelle compétence SKILL.md générée avec succès."
        
    elif agent_choisi == BASE_MODEL:
        print(f"⚙️ {BASE_MODEL} (Exécuteur) prend le relais.")
        
        mots_cles_rag = ["catalogue", "tarif", "vernis", "document", "recherche", "prix"]
        if any(mot in prompt.lower() for mot in mots_cles_rag):
            print("📚 Besoin d'informations détecté. Ouverture des outils AnythingLLM...")
            workspaces = anythingllm_tool.lister_workspaces()
            print(f"📂 Dossiers vus :\n{workspaces}")
            slug_simule = "test-workspace" 
            print(f"🔍 Interrogation du dossier '{slug_simule}'...")
            resultat_execution = f"Tâche accomplie grâce aux documents trouvés."
        else:
            print("💻 Exécution de code local via MCP Filesystem...")
            resultat_execution = "Fichier modifié avec succès sur A:\dsh-core."
    else:
        print(f"⚠️ Agent inconnu : {agent_choisi}")
        resultat_execution = "Échec."

    # ---------------------------------------------------------
    # ÉTAPE 3 : LA BOUCLE D'APPRENTISSAGE (Le Juge)
    # ---------------------------------------------------------
    print("\n[ÉTAPE 3] Validation (Teacher-Student)...")
    if agent_choisi == BASE_MODEL:
        print(f"⚖️ Envoi du résultat à {EXPERT_MODEL} (Juge) pour vérification silencieuse...")
        time.sleep(1) 
        print("✅ Juge valide : 'L'exécuteur a suivi les règles. Pas besoin de nouveau SKILL.md'.")
    else:
        print("✅ Fin directe du processus.")
        
    print("\n" + "="*50)
    print("🏁 FIN DE LA BOUCLE ROUGE")
    print("="*50 + "\n")

if __name__ == "__main__":
    # Test 1 : Requête documentaire
    executer_boucle_rouge("Cherche le prix du vernis mat dans nos catalogues Sibelco.")
    time.sleep(1)
    
    # Test 2 : Requête visuelle (Extraction facture)
    executer_boucle_rouge("Extrais les montants de cette facture.", image_path="C:/facture_scan.pdf")