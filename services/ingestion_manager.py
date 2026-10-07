import os
import uuid
from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel
from langfuse import observe
from enum import Enum

# ==========================================
# ENUMS : Contrats stricts d'Ingestion
# ==========================================

class AssetType(str, Enum):
    INVOICE = "invoice"
    BANK_STATEMENT = "bank_statement"
    EXPENSE = "expense"        # Tickets restaurant, carburant, péages
    CATALOG = "catalog"        # Catalogues fournisseurs, tarifs
    PROJECT = "project"        # Documents de chantiers / projets
    UNKNOWN = "unknown"

class AssetSource(str, Enum):
    EMAIL = "email"
    API = "api"                # Remplacera AnythingLLM, OpenWebUI, DSH-Desktop...
    ONEDRIVE = "onedrive"
    N8N = "n8n"
    MANUAL = "manual"

class IngestionResult(BaseModel):
    asset_id: str
    asset_type: AssetType
    source: AssetSource
    status: str
    target_ledger: Optional[str] = None
    extracted_data: Optional[Dict[str, Any]] = None
    error_log: Optional[str] = None


# ==========================================
# MANAGER D'INGESTION V7.8A
# ==========================================

class IngestionManager:
    """
    Point d'entrée unique de DSH-Core.
    Reçoit un fichier, détermine sa nature et sa source, puis l'envoie au bon agent via un registre dynamique.
    """
    def __init__(self):
        print(">>> INITIALISATION : INGESTION MANAGER (V7.8A - DYNAMIQUE) <<<")
        
        # Registre dynamique des agents (Lazy Loading pour préserver la RAM)
        self._agents = {}
        
        # Registre logique des ledgers (pour traçabilité avant développement de V7.8B/C)
        self.doc_ledger = "document_bus"
        self.bank_ledger = "banking_bus"

    def _get_agent(self, agent_type: str):
        """Lazy loader pour instancier les agents uniquement quand c'est nécessaire."""
        if agent_type not in self._agents:
            if agent_type == "invoice":
                # L'import se fait ICI pour ne pas charger le modèle si on n'a pas de facture
                from agents.invoice_agent import InvoiceAgent
                self._agents[agent_type] = InvoiceAgent()
            # if agent_type == "banking":
            #     from agents.banking_agent import BankingAgent
            #     self._agents[agent_type] = BankingAgent()
        
        return self._agents.get(agent_type)

    @observe(name="Ingestion_Detect_Type")
    def detect_asset_type(self, file_path: str) -> AssetType:
        """
        Détermine le type du document basé sur les conventions de nommage.
        [VERSION PROVISOIRE] - À terme, sera remplacé/augmenté par une classification VISION_MODEL.
        """
        file_path_lower = file_path.lower()
        
        if "facture" in file_path_lower or "invoice" in file_path_lower:
            return AssetType.INVOICE
            
        if "banque" in file_path_lower or "releve" in file_path_lower or file_path_lower.endswith(".csv"):
            return AssetType.BANK_STATEMENT
            
        if "ticket" in file_path_lower or "peage" in file_path_lower or "carburant" in file_path_lower or "frais" in file_path_lower:
            return AssetType.EXPENSE
            
        if "catalogue" in file_path_lower or "tarif" in file_path_lower:
            return AssetType.CATALOG
            
        if "projet" in file_path_lower or "chantier" in file_path_lower:
            return AssetType.PROJECT

        return AssetType.UNKNOWN

    @observe(name="Ingestion_Route_Asset")
    def ingest(self, file_path: str, source: AssetSource = AssetSource.MANUAL) -> Dict[str, Any]:
        """Le point d'entrée principal pour router un asset vers le bon circuit."""
        print(f"\n[IngestionManager] Nouveau fichier reçu depuis '{source.value}': {file_path}")
        
        asset_id = str(uuid.uuid4())

        if not os.path.exists(file_path):
            return IngestionResult(
                asset_id=asset_id, asset_type=AssetType.UNKNOWN, source=source,
                status="error", error_log=f"Fichier introuvable : {file_path}"
            ).model_dump()

        asset_type = self.detect_asset_type(file_path)
        print(f"[IngestionManager] Type identifié : {asset_type.value}")

        # ROUTAGE DYNAMIQUE
        if asset_type == AssetType.INVOICE:
            agent = self._get_agent("invoice")
            if agent:
                result = agent.process_invoice(file_path)
                if result.get("status") == "success":
                    return IngestionResult(
                        asset_id=result.get("qdrant_id", asset_id),
                        asset_type=asset_type,
                        source=source,
                        status="success",
                        target_ledger=self.doc_ledger,
                        extracted_data=result.get("data")
                    ).model_dump()
                else:
                    return IngestionResult(
                        asset_id=asset_id, asset_type=asset_type, source=source,
                        status="error", error_log=result.get("last_error")
                    ).model_dump()

        elif asset_type == AssetType.BANK_STATEMENT:
            print("[IngestionManager] Routage vers BankingLedger (En construction - V7.8C)")
            return IngestionResult(
                asset_id=asset_id, asset_type=asset_type, source=source,
                status="pending_implementation", target_ledger=self.bank_ledger
            ).model_dump()

        elif asset_type == AssetType.EXPENSE:
            print("[IngestionManager] Routage vers Note de Frais (En construction - V7.8B)")
            return IngestionResult(
                asset_id=asset_id, asset_type=asset_type, source=source,
                status="pending_implementation", target_ledger=self.doc_ledger
            ).model_dump()

        else:
            print("[IngestionManager] Document inconnu, nécessite une classification manuelle/LLM Vision.")
            return IngestionResult(
                asset_id=asset_id, asset_type=asset_type, source=source,
                status="requires_human"
            ).model_dump()