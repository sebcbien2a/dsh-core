# invoice_indexer.py - Indexation vectorielle et recherche sémantique des factures dans Qdrant
import json
from pathlib import Path
import uuid
import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "invoices"
EMBED_MODEL = "nomic-embed-text:latest"
EMBED_DIM = 768
OUTPUT_JSON_DIR = Path(r"A:\dsh-core\workspace\invoices\output_json")


class InvoiceIndexer:
    def __init__(self):
        self.client = QdrantClient(url=QDRANT_URL)
        self._initialiser_collection()

    def _initialiser_collection(self):
        """Crée la collection 'invoices' si elle n'existe pas encore."""
        collections = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME not in collections:
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )
            print(f"Collection Qdrant '{COLLECTION_NAME}' initialisée ({EMBED_DIM} dimensions).")

    def generer_embedding(self, texte: str) -> list[float]:
        """Génère le vecteur d'embedding 768d via nomic-embed-text."""
        reponse = ollama.embeddings(model=EMBED_MODEL, prompt=texte)
        return reponse["embedding"]

    def indexer_facture_json(self, json_path: Path):
        """Lit un JSON de facture validé et l'indexe dans Qdrant avec son payload complet."""
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Construction du texte descriptif destiné à la recherche sémantique
        lignes_desc = ", ".join([f"{item['description']} (x{item['quantite']})" for item in data.get("lignes", [])])
        texte_recherche = (
            f"Facture {data['numero_facture']} émise le {data['date_emission']}. "
            f"Fournisseur : {data['fournisseur']['nom']}. "
            f"Client : {data['client']['nom']}. "
            f"Articles : {lignes_desc}. "
            f"Total HT : {data['total_ht']} EUR, Total TTC : {data['total_ttc']} EUR."
        )

        vecteur = self.generer_embedding(texte_recherche)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, data["numero_facture"]))

        point = PointStruct(
            id=point_id,
            vector=vecteur,
            payload={
                "numero_facture": data["numero_facture"],
                "date_emission": data["date_emission"],
                "fournisseur_nom": data["fournisseur"]["nom"],
                "client_nom": data["client"]["nom"],
                "total_ht": data["total_ht"],
                "montant_tva": data["montant_tva"],
                "total_ttc": data["total_ttc"],
                "texte_complet": texte_recherche,
                "donnees_json": data
            }
        )

        self.client.upsert(collection_name=COLLECTION_NAME, points=[point])
        print(f" Facture {data['numero_facture']} indexée dans Qdrant avec succès.")

    def rechercher(self, requete: str, limit: int = 3):
        """Effectue une recherche sémantique en langage naturel."""
        vecteur_requete = self.generer_embedding(requete)
        resultats = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vecteur_requete,
            limit=limit
        ).points

        print(f"\n--- Recherche : « {requete} » ---")
        for res in resultats:
            score = round(res.score, 4)
            p = res.payload
            print(f"-> [Score: {score}] Facture : {p['numero_facture']} | Fournisseur : {p['fournisseur_nom']} | TTC : {p['total_ttc']} €")
            print(f"   Détails : {p['texte_complet']}")


if __name__ == "__main__":
    print("=== INDEXATION ET RECHERCHE FACTURES DANS QDRANT ===")
    indexer = InvoiceIndexer()

    # 1. Indexation de tous les fichiers JSON disponibles
    fichiers_json = list(OUTPUT_JSON_DIR.glob("*.json"))
    for f in fichiers_json:
        indexer.indexer_facture_json(f)

    # 2. Test de recherche sémantique en langage naturel
    indexer.rechercher("achats de vernis et matériel de carrosserie")