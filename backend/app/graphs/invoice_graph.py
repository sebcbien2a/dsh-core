# backend/app/graphs/invoice_graph.py
"""
DSH-CORE: LangGraph Orchestration Layer
Module: invoice_graph.py

Description:
    Graph d'état déterministe pour le traitement des factures.
    Gère le triage (Vision/OCR), la validation arithmétique et l'auto-apprentissage
    via le nœud Expert/Superviseur.
"""

import os
import json
import logging
from typing import TypedDict, Annotated, Literal, Dict, Any

# Imports LangGraph
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages

# Imports locaux (Placeholders pour la structure du projet)
# import ollama
# from backend.services.ocr_service import SuryaOCRProcessor
# from backend.services.qdrant_service import QdrantIndexer
# from backend.services.memory_service import Mem0Manager

# Configuration Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DSH-Core.InvoiceGraph")

# --- 1. DÉFINITION DE L'ÉTAT (STATE) ---

class InvoiceState(TypedDict):
    """
    Schéma de données circulant dans le graphe.
    """
    # Métadonnées du fichier
    filename: str
    file_path: str
    file_type: Literal["image", "pdf", "unknown"]
    
    # Données extraites (brutes puis structurées)
    raw_text: str
    extracted_data: Dict[str, Any]
    
    # Flags de décision
    validation_passed: bool
    confidence_score: float
    is_complex: bool
    
    # Résultats finaux
    skill_file_path: str
    qdrant_payload: Any
    error_message: str
    
    # Historique des messages (pour l'observabilité Langfuse/LangGraph Studio)
    messages: Annotated[list, add_messages]

# --- 2. NŒUDS DU GRAPH (NODES) ---

def triage_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud de Triage Initial.
    Détermine le format du fichier pour router vers la bonne extraction.
    """
    logger.info(f"[TRIAGE] Analyse du fichier: {state['filename']}")
    
    filename = state['filename']
    extension = filename.split('.')[-1].lower()
    
    # Logique de détermination du type
    if extension in ['jpg', 'jpeg', 'png', 'tiff', 'webp']:
        file_type = "image"
    elif extension in ['pdf']:
        file_type = "pdf"
    else:
        file_type = "unknown"
        
    return {
        "file_type": file_type,
        "raw_text": "" # Reset
    }

def vision_extraction_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud Vision : Utilise Qwen2.5-VL pour l'extraction géométrique sur images.
    """
    logger.info("[VISION] Extraction en cours via Qwen VL...")
    
    # TODO: Intégration réelle avec Ollama Client
    # response = ollama.chat(model='qwen2.5-vl:7b', messages=[...])
    
    # Simulation de la réponse du modèle
    mock_data = {
        "vendor": "Fournisseur Test Vision",
        "total": 150.00,
        "tax": 37.50,
        "date": "2023-10-27",
        "items": [{"desc": "Service Cloud", "amount": 112.50}]
    }
    
    return {
        "raw_text": f"[VISUEL] Détecté: {json.dumps(mock_data)}",
        "extracted_data": mock_data
    }

def ocr_extraction_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud OCR : Utilise Surya pour l'extraction massive de texte sur PDF.
    """
    logger.info("[OCR] Extraction en cours via Surya...")
    
    # TODO: Intégration réelle avec Surya OCR Pipeline
    # ocr_result = surya_pipeline(state['file_path'])
    
    # Simulation
    mock_text = """
    FACTURE N°12345
    Fournisseur: Big Tech Solutions
    Sous-total HT: 500.00
    TVA 20%: 100.00
    Total TTC: 600.00
    Date: 27/10/2023
    """
    
    return {
        "raw_text": mock_text,
        "extracted_data": {} # Sera structuré plus tard
    }

def validation_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud de Structuration et Validation.
    Utilise Qwen 9B pour vérifier l'arithmétique (HT + TVA = TTC) et structurer via Pydantic.
    """
    logger.info("[VALIDATION] Structuration et vérification arithmétique...")
    
    # TODO: Appel à Qwen 3.5 / 9B avec prompt de validation Pydantic
    
    extracted = state.get("extracted_data", {})
    
    # Simulation de la logique de validation
    is_valid_math = True
    confidence = 0.0
    
    if not extracted:
        # Cas où l'OCR a du texte brut mais pas de structure JSON
        # L'LLM tente de parser le raw_text
        import re
        # Exemple simple de regex pour la simulation
        if "Total" in state["raw_text"] or "TTC" in state["raw_text"]:
            confidence = 0.85
            extracted = {
                "vendor": "Parse From Text",
                "total": 600.00,
                "status": "parsed"
            }
        else:
            confidence = 0.3 # Trop risqué
    else:
        confidence = 0.95

    return {
        "extracted_data": extracted,
        "validation_passed": confidence > 0.5 and is_valid_math,
        "confidence_score": confidence,
        "is_complex": confidence < 0.7 # Seuil d'escalade
    }

def ingestion_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud d'Ingestion Standard.
    Envoie les données validées vers Qdrant (document_bus) et déclenche n8n.
    """
    logger.info("[INGESTION] Envoi vers Qdrant et trigger n8n...")
    
    # TODO: Appel au client Qdrant
    # qdrant_client.upsert(collection_name="document_bus", points=[...])
    
    return {
        "qdrant_payload": state["extracted_data"],
        "messages": [("system", f"Facture {state['filename']} ingérée avec succès.")]
    }

def expert_node(state: InvoiceState) -> InvoiceState:
    """
    Nœud Expert / Superviseur (Cascade Cognitive).
    Déclenché si le document est complexe ou inconnu.
    1. Qwen 27B essaie de résoudre.
    2. Si échec, Claude 3.5 rédige un fichier SKILL_[FURNISSEUR].md.
    """
    logger.warning("[EXPERT] Escalade vers le niveau Expert/Superviseur...")
    
    # Simulation de la création de compétence
    vendor_name = "Unknown_Vendor"
    if state["extracted_data"]:
        vendor_name = state["extracted_data"].get("vendor", "Unknown_Vendor")
        
    skill_filename = f"SKILL_{vendor_name.replace(' ', '_')}.md"
    skill_path = f"A:\\dsh-core\\skills\\{skill_filename}"
    
    logger.info(f"[EXPERT] Création du skill: {skill_path}")
    
    return {
        "skill_file_path": skill_path,
        "is_complex": False, # Marquer comme résolu par l'expert
        "validation_passed": True,
        "messages": [("expert", f"Skill créé pour {vendor_name}. Redirection en ingestion.")]
    }

# --- 3. FONCTIONS DE ROUTAGE (CONDITIONAL EDGES) ---

def route_after_triage(state: InvoiceState) -> Literal["vision_extraction", "ocr_extraction", "__end__"]:
    """
    Décide du chemin d'extraction après le triage.
    """
    file_type = state.get("file_type")
    if file_type == "image":
        return "vision_extraction"
    elif file_type == "pdf":
        return "ocr_extraction"
    else:
        return "__end__" # Ou vers un nœud d'erreur

def route_after_validation(state: InvoiceState) -> Literal["ingestion", "expert", "__end__"]:
    """
    Fonction de décision pure.
    Si la confiance est faible ou si la structure est complexe -> Expert.
    Sinon -> Ingestion directe.
    """
    if state.get("is_complex") or not state.get("validation_passed"):
        return "expert"
    return "ingestion"

# --- 4. CONSTRUCTION DU GRAPH ---

def build_invoice_graph():
    """
    Assemble le graphe LangGraph.
    """
    workflow = StateGraph(InvoiceState)
    
    # 1. Ajout des Nœuds
    workflow.add_node("triage", triage_node)
    workflow.add_node("vision_extraction", vision_extraction_node)
    workflow.add_node("ocr_extraction", ocr_extraction_node)
    workflow.add_node("validation", validation_node)
    workflow.add_node("ingestion", ingestion_node)
    workflow.add_node("expert", expert_node)
    
    # 2. Configuration des connexions (Edges)
    
    # Entrée du graphe -> Triage
    workflow.set_entry_point("triage")
    
    # Triage -> Extraction (Conditionnel)
    workflow.add_conditional_edges(
        "triage",
        route_after_triage,
        {
            "vision_extraction": "vision_extraction",
            "ocr_extraction": "ocr_extraction",
            "__end__": END
        }
    )
    
    # Extraction -> Validation (Toujours)
    workflow.add_edge("vision_extraction", "validation")
    workflow.add_edge("ocr_extraction", "validation")
    
    # Validation -> Routage (Conditionnel)
    workflow.add_conditional_edges(
        "validation",
        route_after_validation,
        {
            "ingestion": "ingestion",
            "expert": "expert",
            "__end__": END
        }
    )
    
    # Expert -> Ingestion (Après apprentissage, on ré-ingère)
    workflow.add_edge("expert", "ingestion")
    
    # Ingestion -> Fin
    workflow.add_edge("ingestion", END)
    
    # 3. Compilation avec MemorySaver (Checkpointing)
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    return app

# --- 5. UTILISATION (EXEMPLE) ---

if __name__ == "__main__":
    # Initialisation
    invoice_app = build_invoice_graph()
    
    # État initial simulé
    initial_state = InvoiceState(
        filename="facture_test_complex.pdf",
        file_path="A:\\dsh-core\\docs\\facture_test_complex.pdf",
        file_type="pdf",
        raw_text="",
        extracted_data={},
        validation_passed=False,
        confidence_score=0.0,
        is_complex=False,
        skill_file_path="",
        qdrant_payload=None,
        error_message="",
        messages=[]
    )
    
    # Exécution du graphe
    # Note: config est requis pour le checkpointer (thread_id pour la session)
    config = {"configurable": {"thread_id": "1"}}
    
    try:
        result = invoice_app.invoke(initial_state, config)
        print("\n--- RÉSULTAT FINAL ---")
        print(f"Type Fichier: {result['file_type']}")
        print(f"Validation: {result['validation_passed']}")
        print(f"Complexe: {result['is_complex']}")
        print(f"Données: {json.dumps(result['extracted_data'], indent=2, ensure_ascii=False)}")
        if result.get('skill_file_path'):
            print(f"Skill Créé: {result['skill_file_path']}")
    except Exception as e:
        logger.error(f"Erreur lors de l'exécution du graphe: {e}")