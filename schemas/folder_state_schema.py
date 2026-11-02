"""Pydantic contracts for the OneDrive / Gmail document state machine (P14.1).

Six folder states (``00_INBOX`` to ``05_ARCHIVE_ERREUR``), eight business
categories, the closed enum of missing fields and the per-document state record
shared by the tree service (P14.1), the ``_A_COMPLETER.xlsx`` manager (P14.2)
and the bidirectional synchroniser (P14.3). Deterministic and offline: this
module performs no I/O.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FolderState(str, Enum):
    """Stable values are the canonical folder names of the state tree."""

    INBOX = "00_INBOX"
    EN_COURS = "01_EN_COURS"
    A_COMPLETER = "02_A_COMPLETER"
    VALIDE_PRET_FEC = "03_VALIDE_PRET_FEC"
    EXPORT_COMPTA = "04_EXPORT_COMPTA"
    ARCHIVE_ERREUR = "05_ARCHIVE_ERREUR"


class DocumentCategory(str, Enum):
    """The eight business categories, normalised under ``03_`` and ``04_``."""

    ACHATS_MARCHANDISES = "ACHATS_MARCHANDISES"
    FRAIS_GENERAUX = "FRAIS_GENERAUX"
    TRANSPORTS_LOGISTIQUE = "TRANSPORTS_LOGISTIQUE"
    IMMOBILISATIONS = "IMMOBILISATIONS"
    BANQUE_FINANCE = "BANQUE_FINANCE"
    FISCAL_SOCIAL = "FISCAL_SOCIAL"
    VENTES_CLIENTS = "VENTES_CLIENTS"
    DIVERS_A_CLASSER = "DIVERS_A_CLASSER"


class MissingFieldEnum(str, Enum):
    """Closed list of reportable missing fields (spec P14, decision 2)."""

    DATE_PIECE = "date_piece"
    NUMERO_PIECE = "numero_piece"
    NOM_FOURNISSEUR = "nom_fournisseur"
    MONTANT_HT = "montant_ht"
    MONTANT_TVA = "montant_tva"
    MONTANT_TTC = "montant_ttc"
    CATEGORIE = "categorie"
    TAUX_TVA = "taux_tva"


class DocumentStatus(str, Enum):
    """Values of the ``Statut`` column in ``_A_COMPLETER.xlsx``."""

    EN_ATTENTE = "EN_ATTENTE"
    VALIDE = "VALIDE"
    REJETE = "REJETE"


class DocumentSource(str, Enum):
    """Where the document entered ``00_INBOX``."""

    GMAIL_ATTACHMENT = "GMAIL_ATTACHMENT"
    ONEDRIVE_DROP = "ONEDRIVE_DROP"
    MANUAL = "MANUAL"


ALLOWED_TRANSITIONS: Mapping[FolderState, frozenset[FolderState]] = {
    FolderState.INBOX: frozenset({FolderState.EN_COURS, FolderState.ARCHIVE_ERREUR}),
    FolderState.EN_COURS: frozenset(
        {FolderState.VALIDE_PRET_FEC, FolderState.A_COMPLETER, FolderState.ARCHIVE_ERREUR}
    ),
    FolderState.A_COMPLETER: frozenset(
        {FolderState.VALIDE_PRET_FEC, FolderState.ARCHIVE_ERREUR}
    ),
    FolderState.VALIDE_PRET_FEC: frozenset(
        {FolderState.EXPORT_COMPTA, FolderState.ARCHIVE_ERREUR}
    ),
    FolderState.EXPORT_COMPTA: frozenset(),
    FolderState.ARCHIVE_ERREUR: frozenset(),
}


def is_allowed_transition(source: FolderState, target: FolderState) -> bool:
    """Return True when ``source -> target`` is a normative transition."""
    return target in ALLOWED_TRANSITIONS[FolderState(source)]


class DocumentStateRecord(BaseModel):
    """Immutable per-document state tracked by the ``00_``-``05_`` machine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str
    nom_fichier: str
    state: FolderState
    category: DocumentCategory
    champs_manquants: list[MissingFieldEnum] = Field(default_factory=list)
    categorie_suggeree: DocumentCategory = DocumentCategory.DIVERS_A_CLASSER
    statut: DocumentStatus = DocumentStatus.EN_ATTENTE
    source: DocumentSource
    horodatage: datetime

    @field_validator("sha256")
    @classmethod
    def valid_sha256(cls, value: str) -> str:
        text = value.strip().lower()
        if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
            raise ValueError("sha256 must be 64 hex characters")
        return text

    @field_validator("nom_fichier")
    @classmethod
    def valid_nom_fichier(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("nom_fichier must not be empty or whitespace")
        if "/" in text or "\\" in text or "\x00" in text:
            raise ValueError("nom_fichier must not contain path separators")
        return text

    @field_validator("champs_manquants")
    @classmethod
    def dedupe_champs_manquants(
        cls, values: list[MissingFieldEnum]
    ) -> list[MissingFieldEnum]:
        seen: set[MissingFieldEnum] = set()
        cleaned: list[MissingFieldEnum] = []
        for value in values:
            item = MissingFieldEnum(value)
            if item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned
