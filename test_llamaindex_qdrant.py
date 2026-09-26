import qdrant_client
from llama_index.core import Document, Settings, VectorStoreIndex, StorageContext
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama

# 1. Configuration des modèles locaux V2
Settings.llm = Ollama(model="qwen3.5:9b", request_timeout=120.0)
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text:latest")

# 2. Connexion au client Qdrant local
client = qdrant_client.QdrantClient(url="http://localhost:6333")

# 3. Initialisation du connecteur vectoriel
vector_store = QdrantVectorStore(client=client, collection_name="document_bus")
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# 4. Document de test pour le bus documentaire
doc_test = Document(
    text="Le module mcp_scrape utilise le port 9150 pour router les requetes a travers le reseau Tor en toute securite.",
    metadata={"source": "reseau_tor", "categorie": "infrastructure"}
)

print("Indexation du document dans le bus documentaire Qdrant...")
index = VectorStoreIndex.from_documents([doc_test], storage_context=storage_context)
print("Indexation terminee.")

# 5. Test d'interrogation RAG
print("\nRecherche documentaire en cours...")
query_engine = index.as_query_engine()
reponse = query_engine.query("Quel port est utilise pour le routage Tor ?")

print(f"\nReponse de l'agent LlamaIndex : {reponse}")