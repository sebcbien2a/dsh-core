from mem0 import Memory

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "host": "localhost",
            "port": 6333,
            "collection_name": "mem0_memories",
            "embedding_model_dims": 768,
        },
    },
    "llm": {
        "provider": "ollama",
        "config": {
            "model": "qwen3.5:9b",
        },
    },
    "embedder": {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text:latest",
            "embedding_dims": 768,
        },
    },
}

print("Connexion à Qdrant et initialisation de Mem0...")
m = Memory.from_config(config)

# 1. Écriture d'un fait test
print("Écriture du fait dans Qdrant...")
result = m.add("L'architecture dsh-core utilise une cascade Qwen 3.5 9B vers Claude.", user_id="user_admin")
print("Résultat écriture :", result)

# 2. Recherche sémantique
print("\nRecherche sémantique...")
memories = m.search(query="Quel est le fonctionnement de dsh-core ?", filters={"user_id": "user_admin"})
print("Souvenirs trouvés :")
for mem in memories.get("results", []):
    print(f"- {mem.get('memory')}")