"""Pydantic contracts for the OneDrive / Gmail document state machine (P14.1).

Six folder states (``00_INBOX`` to ``05_ARCHIVE_ERREUR``), eight business
categories, the closed enum of missing fields and the per-document state record
shared by the tree service (P14.1), the ``_A_COMPLETER.xlsx`` manager (P14.2)
and the bidirectional synchroniser (P14.3). Deterministic and offline: this
module performs no I/O.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum, EnumMeta
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class _MissingFieldMeta(EnumMeta):
    """Keep the historical 8-value iteration while exposing P17 additions.

    The P14.1 contract freezes the *iterated* decision-2 list to eight fields.
    P17 adds two attributable restaurant fields without breaking that
    contract: they are real enum members (``MissingFieldEnum("invite_restaurant")``
    works) but are excluded from iteration so legacy consumers and tests keep
    seeing exactly the frozen eight values.
    """

    _P17_ADDITIONS = frozenset({"invite_restaurant", "lieu_restaurant"})

    def __iter__(cls):  # type: ignore[override]
        for member in super().__iter__():
            if member.value not in cls._P17_ADDITIONS:
                yield member


class MissingFieldEnum(str, Enum, metaclass=_MissingFieldMeta):
    """Closed list of reportable missing fields (spec P14, decision 2)."""

    DATE_PIECE = "date_piece"
    NUMERO_PIECE = "numero_piece"
    NOM_FOURNISSEUR = "nom_fournisseur"
    MONTANT_HT = "montant_ht"
    MONTANT_TVA = "montant_tva"
    MONTANT_TTC = "montant_ttc"
    CATEGORIE = "categorie"
    TAUX_TVA = "taux_tva"
    INVITE_RESTAURANT = "invite_restaurant"
    LIEU_RESTAURANT = "lieu_restaurant"


class ExpenseSubCategory(str, Enum):
    """The ``FRAIS_GENERAUX`` analytic sub-categories (P17 / P22).

    ``HOTELLERIE`` is deliberately separated from ``RESTAURATION`` because the
    VAT of accommodation is not deductible (or only partially deductible),
    whereas meal VAT is. ``INFORMATIQUE_WEB`` and ``HONORAIRES_JURIDIQUES``
    were added with the P22 director arbitrations for web hosting and legal
    fees. Each value is also the physical sub-folder name under
    ``03_VALIDE_PRET_FEC/FRAIS_GENERAUX/`` and
    ``04_EXPORT_COMPTA/FRAIS_GENERAUX/``.
    """

    RESTAURATION = "RESTAURATION"
    CARBURANT = "CARBURANT"
    TRANSPORT_PARKING = "TRANSPORT_PARKING"
    HOTELLERIE = "HOTELLERIE"
    INFORMATIQUE_WEB = "INFORMATIQUE_WEB"
    HONORAIRES_JURIDIQUES = "HONORAIRES_JURIDIQUES"


class FiscalYearMode(str, Enum):
    """Fiscal-year switching modes used to optimise the tax bracket (P17)."""

    PRECEDENT = "PRECEDENT"
    ACTUEL = "ACTUEL"
    SUIVANT = "SUIVANT"


PaymentSourceLiteral = Literal["BANQUE_PRO", "COMPTE_PERSO_DIRIGEANT"]


class PaymentSource(str, Enum):
    """How an expense was settled (P17 APD business rule).

    ``COMPTE_PERSO_DIRIGEANT`` pieces are paid by the manager's personal
    account. They are posted as regular charges / deductible VAT through the
    ``455000_DIRIGEANT`` counterpart account and must never wait for a
    professional bank statement line.
    """

    BANQUE_PRO = "BANQUE_PRO"
    COMPTE_PERSO_DIRIGEANT = "COMPTE_PERSO_DIRIGEANT"


DIRIGEANT_COUNTERPART_ACCOUNT = "455000_DIRIGEANT"
COMPTE_COURANT_DIRIGEANT_ACCOUNT = DIRIGEANT_COUNTERPART_ACCOUNT
DIRIGEANT_PERSONAL_ACCOUNT = "455000"
BANQUE_PRO = PaymentSource.BANQUE_PRO.value
COMPTE_PERSO_DIRIGEANT = PaymentSource.COMPTE_PERSO_DIRIGEANT.value
PAYMENT_SOURCES = tuple(source.value for source in PaymentSource)


def normalize_payment_source(
    value: PaymentSource | PaymentSourceLiteral | str | None,
) -> PaymentSource | None:
    """Return the canonical payment source or ``None`` when unspecified."""
    if value is None:
        return None
    if isinstance(value, PaymentSource):
        return value
    text = str(value).strip().upper()
    if not text:
        return None
    try:
        return PaymentSource(text)
    except ValueError:
        return None


def requires_bank_statement_match(
    value: PaymentSource | PaymentSourceLiteral | str | None,
) -> bool:
    """Return ``False`` for manager-funded expenses (no pro bank match)."""
    return normalize_payment_source(value) is not PaymentSource.COMPTE_PERSO_DIRIGEANT


def counterparty_account_for_payment_source(
    value: PaymentSource | PaymentSourceLiteral | str | None,
) -> str | None:
    """Return the mandatory counterpart account of the payment source."""
    if normalize_payment_source(value) is PaymentSource.COMPTE_PERSO_DIRIGEANT:
        return DIRIGEANT_COUNTERPART_ACCOUNT
    return None


class TvaDedupStatus(str, Enum):
    """Anti-duplicate status of one VAT line in the master tables (P17)."""

    UNIQUE = "TVA_UNIQUE"
    DUPLICATE_EXCLUDED = "DOUBLON_TVA_EXCLU"
    NON_VERIFIABLE = "TVA_NON_VERIFIABLE"


CLASSIFIED_MARKER_PREFIX = "IDENTIFIEE_CLASSEE:"
NON_CLASSIFIED_MARKER = "NON_CLASSEE"
DEFERRED_MONTHS: frozenset[str] = frozenset({"01", "02"})


def classify_marker(subcategory: ExpenseSubCategory | str | None) -> str:
    """Return ``IDENTIFIEE_CLASSEE:<SOUS_CAT>`` or ``NON_CLASSEE``."""
    if subcategory is None:
        return NON_CLASSIFIED_MARKER
    try:
        value = ExpenseSubCategory(subcategory).value
    except (TypeError, ValueError):
        return NON_CLASSIFIED_MARKER
    return f"{CLASSIFIED_MARKER_PREFIX}{value}"


class CompanyProfile(BaseModel):
    """Frozen company identity used by the manual e-mail connector (P17)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = "APD"
    name: str = "Assistance Peintures Diffusion"
    siren: str
    vat_number: str
    invoice_email: str

    @field_validator("tenant_id")
    @classmethod
    def valid_tenant_id(cls, value: str) -> str:
        text = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", text):
            raise ValueError("tenant_id must match the shared tenant grammar")
        return text

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("name must not be empty")
        return text

    @field_validator("siren")
    @classmethod
    def valid_siren(cls, value: str) -> str:
        text = re.sub(r"\s+", "", value)
        if not text.isdigit() or len(text) != 9:
            raise ValueError("siren must contain exactly 9 digits")
        return text

    @field_validator("vat_number")
    @classmethod
    def valid_vat_number(cls, value: str) -> str:
        text = re.sub(r"\s+", "", value).upper()
        if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{2,13}", text):
            raise ValueError("vat_number must be a valid intra-EU VAT number")
        return text

    @field_validator("invoice_email")
    @classmethod
    def valid_invoice_email(cls, value: str) -> str:
        text = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
            raise ValueError("invoice_email must be a valid e-mail address")
        return text


APD_PROFILE = CompanyProfile(
    tenant_id="APD",
    name="Assistance Peintures Diffusion",
    siren="810908392",
    vat_number="FR11810908392",
    invoice_email="facturesapd@gmail.com",
)


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
    payment_source: PaymentSource | None = None
    counterparty_account: str | None = None

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

    @model_validator(mode="after")
    def bind_personal_counterparty_account(self) -> "DocumentStateRecord":
        if self.payment_source is PaymentSource.COMPTE_PERSO_DIRIGEANT:
            object.__setattr__(
                self,
                "counterparty_account",
                DIRIGEANT_COUNTERPART_ACCOUNT,
            )
        return self
