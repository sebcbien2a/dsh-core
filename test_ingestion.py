import os
from services.ingestion_manager import IngestionManager, AssetSource

def run_test():
    print("==================================================")
    print("🚀 TEST DE L'INGESTION MANAGER (V7.8A)")
    print("==================================================")

    manager = IngestionManager()
    
    # Création du dossier temp et des faux fichiers
    test_dir = "C:/tmp/dsh_test_ingestion"
    os.makedirs(test_dir, exist_ok=True)
    
    # On teste nos nouvelles sources (API, N8N, ONEDRIVE, MANUAL)
    files_to_test = [
        (f"{test_dir}/facture_rexel_septembre.pdf", AssetSource.EMAIL),
        (f"{test_dir}/releve_banque_sg_2026.csv", AssetSource.N8N),
        (f"{test_dir}/ticket_peage_a10.jpg", AssetSource.ONEDRIVE),
        (f"{test_dir}/photo_vacances.png", AssetSource.API)
    ]
    
    # Génération physique des fichiers factices
    for file_path, _ in files_to_test:
        with open(file_path, 'w') as file:
            file.write("Fichier factice pour test d'ingestion.")
            
    # Test d'ingestion pour chaque fichier
    for file_path, source in files_to_test:
        print("\n" + "-"*50)
        result = manager.ingest(file_path, source=source)
        print(f"RÉSULTAT JSON : {result}")

    # Nettoyage des fichiers factices
    for file_path, _ in files_to_test:
        try:
            os.remove(file_path)
        except:
            pass

if __name__ == "__main__":
    run_test()