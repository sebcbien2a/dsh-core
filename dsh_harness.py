# dsh_harness.py - Le Chef d'Orchestre de la Boucle Rouge (Teacher-Student)
import router
import anythingllm_tool
import time

def executer_boucle_rouge(prompt: str, image_path: str = None):
    print("\n" + "="*50)
    print(f"🚀 NOUVELLE TÂCHE REÇUE : {prompt}")
    print("="*50)
    
    # ---------------------------------------------------------
    # ÉTAPE 1 : LE ROUTAGE (Qui doit faire le travail ?)
    # ---------------------------------------------------------
    print("\n[ÉTAPE 1] Analyse de la requête par le Routeur...")
    decision = router.route_prompt(prompt, image_provided=bool(image_path))
    agent_choisi = decision['agent']
    print(f"🎯 Décision : Assigné à {agent_choisi} ({decision['role']})")
    print(f"🧠 Raison : {decision['reasoning']}")
    
    # ---------------------------------------------------------
    # ÉTAPE 2 : L'EXÉCUTION & UTILISATION DES OUTILS
    # ---------------------------------------------------------
    print("\n[ÉTAPE 2] Exécution en cours...")
    
    if agent_choisi == 'llama3.2-vision-local':
        print(f"👁️ Llama Vision analyse l'image ({image_path})...")
        # Ici viendra l'appel API vers Ollama Llama Vision
        resultat_execution = "Texte extrait de l'image (Simulation OCR)."
        
    elif agent_choisi == 'cloud-sonnet-5':
        print("🧑‍🏫 Claude audite le système ou rédige un SKILL.md...")
        # Ici viendra l'appel à l'API Anthropic
        resultat_execution = "Nouvelle compétence SKILL.md générée avec succès."
        
    elif agent_choisi == 'qwen-9b-local':
        print("⚙️ Qwen (Exécuteur) prend le relais.")
        
        # Le modèle décide s'il a besoin d'interroger la documentation (RAG)
        mots_cles_rag = ["catalogue", "tarif", "vernis", "document", "recherche", "prix"]
        if any(mot in prompt.lower() for mot in mots_cles_rag):
            print("📚 Besoin d'informations détecté. Qwen ouvre ses outils AnythingLLM...")
            
            # L'agent utilise son premier outil pour voir les dossiers existants
            workspaces = anythingllm_tool.lister_workspaces()
            print(f"📂 Qwen voit les dossiers suivants :\n{workspaces}")
            
            # Simulation : L'agent a trouvé le slug et utilise son deuxième outil
            # (Dans la vraie boucle, l'agent extraira le slug lui-même)
            slug_simule = "test-workspace" # À remplacer dynamiquement par l'agent plus tard
            print(f"🔍 Qwen interroge le dossier '{slug_simule}'...")
            
            # resultat_rag = anythingllm_tool.interroger_anythingllm(slug_simule, prompt)
            resultat_execution = f"Tâche accomplie grâce aux documents trouvés."
        else:
            print("💻 Qwen exécute du code local via MCP Filesystem...")
            resultat_execution = "Fichier modifié avec succès sur A:\dsh-core."

    # ---------------------------------------------------------
    # ÉTAPE 3 : LA BOUCLE D'APPRENTISSAGE (Le Juge)
    # ---------------------------------------------------------
    print("\n[ÉTAPE 3] Validation (Teacher-Student)...")
    if agent_choisi == 'qwen-9b-local':
        print("⚖️ Envoi du résultat à Claude (Juge) pour vérification silencieuse...")
        time.sleep(1) # Simulation du temps de réflexion de Claude
        print("✅ Claude valide : 'L'exécuteur a suivi les règles. Pas besoin de nouveau SKILL.md'.")
    else:
        print("✅ Fin directe du processus.")
        
    print("\n" + "="*50)
    print("🏁 FIN DE LA BOUCLE ROUGE")
    print("="*50 + "\n")

if __name__ == "__main__":
    # Test 1 : Requête documentaire (Déclenche AnythingLLM)
    executer_boucle_rouge("Cherche le prix du vernis mat dans nos catalogues Sibelco.")
    
    time.sleep(2)
    
    # Test 2 : Requête visuelle (Déclenche Llama Vision)
    executer_boucle_rouge("Extrais les montants de cette facture.", image_path="C:/facture_scan.jpg")