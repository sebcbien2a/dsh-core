import os
import json
from datetime import datetime

class MultiLevelMemory:
    def __init__(self, cold_storage_path="memory/cold_storage"):
        self.hot_memory = []
        self.cold_storage_path = cold_storage_path
        
        # S'assure que le dossier d'archive froide existe
        if not os.path.exists(self.cold_storage_path):
            os.makedirs(self.cold_storage_path)

    def add_to_hot(self, data: str):
        """Ajoute l'élément en mémoire chaude"""
        self.hot_memory.append(data)

    def persist_to_cold(self, filename_prefix="invoice_archive"):
        """Transfère et sauvegarde durablement la mémoire chaude en mémoire froide (JSON)"""
        if not self.hot_memory:
            print("[MemoryManager] Rien à persister en mémoire froide.")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}.json"
        filepath = os.path.join(self.cold_storage_path, filename)

        archive_data = {
            "timestamp": timestamp,
            "records": self.hot_memory
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(archive_data, f, indent=4, ensure_ascii=False)

        print(f"[MemoryManager] Données archivées avec succès en mémoire froide : {filepath}")
        return filepath