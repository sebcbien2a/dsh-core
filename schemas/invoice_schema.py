from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

class TypePiece(str, Enum):
    FACTURE = "FACTURE"
    AVOIR = "AVOIR"

class TypeTVA(str, Enum):
    NORMALE = "NORMALE"                      # Régime standard (TVA déductible classique)
    AUTOLIQUIDATION = "AUTOLIQUIDATION"      # Intracommunautaire (TVA payée nulle, CA3 requise)
    NON_ASSUJETTI = "NON_ASSUJETTI"          # Franchise en base art. 293 B CGI (Aucune TVA)

class InvoiceData(BaseModel):
    # --- 0. Métadonnées & Typage métier ---
    type_piece: TypePiece = TypePiece.FACTURE
    type_tva: TypeTVA = TypeTVA.NORMALE
    origine_document: str = "INCONNU"
    
    fournisseur: str
    date_facture: str
    numero_facture: Optional[str] = None
    
    # --- 1. Valeurs fiscales d'origine ---
    montant_ht: float
    taux_tva_pourcentage: Optional[float] = None
    taux_tva_theorique: Optional[float] = None  # 20.0 % pour calcul CA3 en autoliquidation
    montant_tva: Optional[float] = None
    montant_ttc: float
    
    # --- 2. Valeurs commerciales ---
    montant_remise: Optional[float] = 0.0
    montant_paye: Optional[float] = None
    
    # --- 3. Valeurs recalculées par DSH-Core ---
    montant_ht_net: Optional[float] = None
    montant_tva_net: Optional[float] = None
    montant_ttc_net: Optional[float] = None
    
    # --- 4. Statut et Audit (Soft Fail) ---
    statut_traitement: str = "VALIDE"
    commentaires_controle: list[str] = Field(default_factory=list)
    
    devise: str = "EUR"

    @model_validator(mode="after")
    def process_and_verify(self):
        # ==========================================
        # CONTRÔLES DES RÉGIMES DE TVA SPÉCIAUX
        # ==========================================
        if self.type_tva in [TypeTVA.AUTOLIQUIDATION, TypeTVA.NON_ASSUJETTI]:
            self.montant_tva = 0.0
            self.taux_tva_pourcentage = None
            
            if self.type_tva == TypeTVA.AUTOLIQUIDATION and self.taux_tva_theorique is None:
                self.taux_tva_theorique = 20.0

        # ==========================================
        # CONTRÔLES TAUX TVA (Factures NORMALE)
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE and self.taux_tva_pourcentage is not None:
            if self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage < 0:
                self.commentaires_controle.append(f"Taux TVA négatif détecté ({self.taux_tva_pourcentage}%).")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"
            elif self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage > 30:
                self.commentaires_controle.append(f"Taux TVA improbable détecté ({self.taux_tva_pourcentage}%). Valeur invalidée.")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # CONTRÔLES MONTANTS (Restreints aux Factures)
        # ==========================================
        if self.montant_tva is not None and self.type_piece == TypePiece.FACTURE:
            if self.montant_tva < 0:
                self.commentaires_controle.append(f"Montant TVA négatif détecté ({self.montant_tva}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc > 0 and self.montant_tva > self.montant_ttc:
                self.commentaires_controle.append("TVA supérieure au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > 0 and self.montant_tva > self.montant_ht:
                self.commentaires_controle.append("TVA supérieure au HT : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        if self.type_piece == TypePiece.FACTURE:
            if self.montant_ht < 0:
                self.commentaires_controle.append(f"Montant HT négatif détecté ({self.montant_ht}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc < 0:
                self.commentaires_controle.append(f"Montant TTC négatif détecté ({self.montant_ttc}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > self.montant_ttc:
                self.commentaires_controle.append("HT supérieur au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # DÉTECTION D'HALLUCINATION (HT = TVA = TTC)
        # ==========================================
        if (
            self.montant_tva is not None
            and abs(self.montant_ht) > 0
            and abs(self.montant_ttc) > 0
            and abs(self.montant_ht - self.montant_tva) < 0.01
            and abs(self.montant_tva - self.montant_ttc) < 0.01
        ):
            self.commentaires_controle.append("Configuration HT = TVA = TTC détectée. Hallucination probable du LLM.")
            self.statut_traitement = "A_VERIFIER"
            self.taux_tva_pourcentage = None
            self.montant_tva = None

        # ==========================================
        # VÉRIFICATION FISCALE SELON RÉGIME
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE:
            if self.montant_tva is not None:
                attendu_ttc = round(self.montant_ht + self.montant_tva, 2)
                if abs(attendu_ttc - self.montant_ttc) > 0.02:
                    self.statut_traitement = "A_VERIFIER"
                    self.commentaires_controle.append(
                        f"Anomalie fiscale : HT ({self.montant_ht}) + TVA ({self.montant_tva}) != TTC ({self.montant_ttc})"
                    )
            else:
                if self.type_piece == TypePiece.FACTURE:
                    self.commentaires_controle.append("TVA absente ou invalidée : contrôle fiscal automatique impossible.")
                    self.statut_traitement = "A_VERIFIER"
        else:
            if abs(self.montant_ht - self.montant_ttc) > 0.02:
                self.statut_traitement = "A_VERIFIER"
                self.commentaires_controle.append(
                    f"Anomalie régime {self.type_tva.value} : HT ({self.montant_ht}) != TTC ({self.montant_ttc})"
                )
            
        # ==========================================
        # COHÉRENCE BANCAIRE
        # ==========================================
        remise = self.montant_remise or 0.0
        if self.montant_paye is None:
            self.montant_paye = round(self.montant_ttc - remise, 2)
            
        attendu_paye = round(self.montant_ttc - remise, 2)
        if abs(attendu_paye - self.montant_paye) > 0.02:
            self.statut_traitement = "A_VERIFIER"
            self.commentaires_controle.append(
                f"Anomalie bancaire : TTC ({self.montant_ttc}) - Remise ({remise}) != Payé ({self.montant_paye})"
            )
            
        # ==========================================
        # CALCUL TRÉSORERIE NETTE
        # ==========================================
        if self.montant_ttc != 0:
            ratio = self.montant_paye / self.montant_ttc
            self.montant_ht_net = round(self.montant_ht * ratio, 2)
            
            tva_valide = self.montant_tva if self.montant_tva is not None else 0.0
            self.montant_tva_net = round(tva_valide * ratio, 2)
            self.montant_ttc_net = self.montant_paye
            
            attendu_ttc_net = round(self.montant_ht_net + self.montant_tva_net, 2)
            if abs(attendu_ttc_net - self.montant_ttc_net) > 0.02:
                self.montant_ht_net = round(self.montant_ttc_net - self.montant_tva_net, 2)
        else:
            self.montant_ht_net = 0.0
            self.montant_tva_net = 0.0
            self.montant_ttc_net = 0.0

        return self


class InvoiceDocumentType(str, Enum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    RECEIPT = "RECEIPT"
    FUEL_RECEIPT = "FUEL_RECEIPT"
    MEAL_RECEIPT = "MEAL_RECEIPT"
    UNKNOWN = "UNKNOWN"


class InvoiceDirection(str, Enum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    UNKNOWN = "UNKNOWN"


class ValidationStatus(str, Enum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class InvoiceParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    normalized_name: str | None = None
    siret: str | None = None
    vat_number: str | None = None
    address: str | None = None

    @model_validator(mode="before")
    @classmethod
    def adapt_legacy_party(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        for legacy_name, canonical_name in (
            ("nom", "name"),
            ("numero_tva", "vat_number"),
            ("adresse", "address"),
        ):
            if legacy_name in data:
                data.setdefault(canonical_name, data.pop(legacy_name))
        return data

    @field_validator("name", "normalized_name", "siret", "vat_number", "address")
    @classmethod
    def validate_non_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("string values must not be empty or whitespace")
        return value

    @field_validator("siret")
    @classmethod
    def validate_siret(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 14 or not value.isascii() or not value.isdigit()):
            raise ValueError("siret must contain exactly 14 digits")
        return value


class InvoiceAmount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "EUR"
    net_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    gross_amount: Decimal
    rounding_tolerance: Decimal = Decimal("0.02")

    @model_validator(mode="before")
    @classmethod
    def normalize_decimals(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        for key in ("net_amount", "tax_amount", "gross_amount", "rounding_tolerance"):
            if data.get(key) is not None and not isinstance(data[key], Decimal):
                data[key] = Decimal(str(data[key]))
        return data

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if len(value) != 3 or not value.isascii() or not value.isalpha() or value.upper() != value:
            raise ValueError("currency must contain exactly three uppercase letters")
        return value

    @field_validator("net_amount", "tax_amount", "gross_amount", "rounding_tolerance")
    @classmethod
    def validate_non_negative_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("amounts must not be negative")
        return value

    @field_validator("rounding_tolerance")
    @classmethod
    def validate_positive_tolerance(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("rounding_tolerance must be strictly positive")
        return value

    @model_validator(mode="after")
    def validate_amount_consistency(self) -> "InvoiceAmount":
        if self.net_amount is not None and self.tax_amount is not None:
            difference = abs(
                self.net_amount + self.tax_amount - self.gross_amount
            )
            if difference > self.rounding_tolerance:
                raise ValueError(
                    "net_amount plus tax_amount must match gross_amount "
                    "within rounding_tolerance"
                )
        return self

    @field_serializer(
        "net_amount",
        "tax_amount",
        "gross_amount",
        "rounding_tolerance",
        when_used="always",
    )
    def serialize_decimal(self, value: Decimal | None) -> str | None:
        return str(value) if value is not None else None


class InvoiceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantite: Decimal
    prix_unitaire_ht: Decimal
    total_ht: Decimal
    taux_tva: Decimal

    @model_validator(mode="before")
    @classmethod
    def normalize_decimals(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        for key in ("quantite", "prix_unitaire_ht", "total_ht", "taux_tva"):
            if data.get(key) is not None and not isinstance(data[key], Decimal):
                data[key] = Decimal(str(data[key]))
        return data

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be empty or whitespace")
        return value

    @field_serializer(
        "quantite",
        "prix_unitaire_ht",
        "total_ht",
        "taux_tva",
        when_used="always",
    )
    def serialize_decimal(self, value: Decimal) -> str:
        return str(value)


class InvoiceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    purchase_order: str | None = None
    external_reference: str | None = None

    @field_validator(
        "invoice_number",
        "purchase_order",
        "external_reference",
    )
    @classmethod
    def validate_non_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("string values must not be empty or whitespace")
        return value

    @model_validator(mode="after")
    def validate_due_date(self) -> "InvoiceReference":
        if (
            self.issue_date is not None
            and self.due_date is not None
            and self.due_date < self.issue_date
        ):
            raise ValueError("due_date must not be before issue_date")
        return self

    @field_serializer("issue_date", "due_date", when_used="always")
    def serialize_date(self, value: date | None) -> str | None:
        return value.isoformat() if value is not None else None


class InvoiceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID | None = None
    document_type: InvoiceDocumentType = InvoiceDocumentType.UNKNOWN
    direction: InvoiceDirection = InvoiceDirection.UNKNOWN
    company_id: str | None = None
    supplier: InvoiceParty | None = None
    customer: InvoiceParty | None = None
    reference: InvoiceReference = Field(default_factory=InvoiceReference)
    lines: list[InvoiceLine] = Field(default_factory=list)
    amount: InvoiceAmount
    category: str | None = None
    source_file: str | None = None
    source_email: str | None = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    extraction_warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def adapt_actual_invoice_payloads(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)

        document_type = data.get("document_type", data.get("type_piece"))
        if document_type is not None:
            document_type_value = getattr(document_type, "value", document_type)
            document_type_map = {
                "FACTURE": InvoiceDocumentType.INVOICE,
                "AVOIR": InvoiceDocumentType.CREDIT_NOTE,
                "INVOICE": InvoiceDocumentType.INVOICE,
                "CREDIT_NOTE": InvoiceDocumentType.CREDIT_NOTE,
                "RECEIPT": InvoiceDocumentType.RECEIPT,
                "FUEL_RECEIPT": InvoiceDocumentType.FUEL_RECEIPT,
                "MEAL_RECEIPT": InvoiceDocumentType.MEAL_RECEIPT,
            }
            data["document_type"] = document_type_map.get(
                document_type_value,
                InvoiceDocumentType.UNKNOWN,
            )
        elif "origine_document" in data:
            origin = getattr(data["origine_document"], "value", data["origine_document"])
            data["document_type"] = {
                "FACTURE": InvoiceDocumentType.INVOICE,
                "INVOICE": InvoiceDocumentType.INVOICE,
                "AVOIR": InvoiceDocumentType.CREDIT_NOTE,
                "CREDIT_NOTE": InvoiceDocumentType.CREDIT_NOTE,
                "RECEIPT": InvoiceDocumentType.RECEIPT,
                "FUEL_RECEIPT": InvoiceDocumentType.FUEL_RECEIPT,
                "MEAL_RECEIPT": InvoiceDocumentType.MEAL_RECEIPT,
            }.get(origin, InvoiceDocumentType.UNKNOWN)

        aliases = {
            "fournisseur": "supplier",
            "client": "customer",
            "lignes": "lines",
            "categorie": "category",
            "commentaires_controle": "extraction_warnings",
        }
        for legacy_name, canonical_name in aliases.items():
            if legacy_name in data:
                data.setdefault(canonical_name, data.pop(legacy_name))
        for party_field in ("supplier", "customer"):
            if isinstance(data.get(party_field), str):
                data[party_field] = {"name": data[party_field]}

        reference_data = dict(data.get("reference") or {})
        for legacy_name, canonical_name in (
            ("numero_facture", "invoice_number"),
            ("date_facture", "issue_date"),
            ("date_emission", "issue_date"),
            ("date_echeance", "due_date"),
            ("date_due", "due_date"),
        ):
            if legacy_name in data:
                reference_data.setdefault(canonical_name, data.pop(legacy_name))
        if reference_data:
            data["reference"] = reference_data

        amount_data = dict(data.get("amount") or {})
        for legacy_name, canonical_name in (
            ("total_ht", "net_amount"),
            ("montant_ht", "net_amount"),
            ("montant_tva", "tax_amount"),
            ("total_ttc", "gross_amount"),
            ("montant_ttc", "gross_amount"),
            ("devise", "currency"),
        ):
            if legacy_name in data:
                amount_data.setdefault(canonical_name, data.pop(legacy_name))
        if amount_data:
            data["amount"] = amount_data

        legacy_status = data.pop("statut_traitement", None)
        if "validation_status" not in data and legacy_status is not None:
            data["validation_status"] = {
                "VALIDE": ValidationStatus.VALIDATED,
                "VALIDATED": ValidationStatus.VALIDATED,
                "REJETE": ValidationStatus.REJECTED,
                "REJECTED": ValidationStatus.REJECTED,
                "A_VERIFIER": ValidationStatus.NEEDS_REVIEW,
                "NEEDS_REVIEW": ValidationStatus.NEEDS_REVIEW,
            }.get(legacy_status, ValidationStatus.PENDING)

        data.pop("origine_document", None)
        return data

    @field_validator(
        "company_id",
        "category",
        "source_file",
        "source_email",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("string values must not be empty or whitespace")
        return value

    @field_validator("extraction_warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        if any(not warning.strip() for warning in value):
            raise ValueError("extraction warnings must not be empty or whitespace")
        return value

    @model_validator(mode="after")
    def validate_invoice(self) -> "InvoiceSchema":
        if self.created_at is not None and self.updated_at is not None:
            try:
                if self.updated_at < self.created_at:
                    raise ValueError("updated_at must be greater than or equal to created_at")
            except TypeError as exc:
                raise ValueError(
                    "created_at and updated_at must use compatible time zones"
                ) from exc

        if self.direction is InvoiceDirection.PURCHASE and self.supplier is None:
            raise ValueError("supplier is required for PURCHASE invoices")
        if self.direction is InvoiceDirection.SALE and self.customer is None:
            raise ValueError("customer is required for SALE invoices")
        return self

    @field_serializer("document_id", when_used="always")
    def serialize_document_id(self, value: UUID | None) -> str | None:
        return str(value) if value is not None else None

    @field_serializer("created_at", "updated_at", when_used="always")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @classmethod
    def from_invoice_data(
        cls,
        invoice_data: BaseModel,
        *,
        document_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> "InvoiceSchema":
        return cls.from_legacy_dict(
            invoice_data.model_dump(mode="python"),
            document_id=document_id,
            created_at=created_at,
            updated_at=updated_at,
        )

    @classmethod
    def from_legacy_dict(
        cls,
        data: Mapping[str, Any],
        *,
        document_id: UUID,
        created_at: datetime,
        updated_at: datetime,
    ) -> "InvoiceSchema":
        legacy_document_type = data.get("document_type", data.get("type_piece"))
        document_type = {
            "FACTURE": InvoiceDocumentType.INVOICE,
            "INVOICE": InvoiceDocumentType.INVOICE,
            "AVOIR": InvoiceDocumentType.CREDIT_NOTE,
            "CREDIT_NOTE": InvoiceDocumentType.CREDIT_NOTE,
            "RECEIPT": InvoiceDocumentType.RECEIPT,
            "FUEL_RECEIPT": InvoiceDocumentType.FUEL_RECEIPT,
            "MEAL_RECEIPT": InvoiceDocumentType.MEAL_RECEIPT,
        }.get(getattr(legacy_document_type, "value", legacy_document_type), InvoiceDocumentType.UNKNOWN)

        legacy_status = data.get("validation_status", data.get("statut_traitement"))
        validation_status = {
            "VALIDATED": ValidationStatus.VALIDATED,
            "VALIDE": ValidationStatus.VALIDATED,
            "REJECTED": ValidationStatus.REJECTED,
            "NEEDS_REVIEW": ValidationStatus.NEEDS_REVIEW,
            "A_VERIFIER": ValidationStatus.NEEDS_REVIEW,
        }.get(getattr(legacy_status, "value", legacy_status), ValidationStatus.PENDING)

        supplier_data = data.get("supplier", data.get("fournisseur"))
        supplier = cls._legacy_party(supplier_data)
        customer = cls._legacy_party(data.get("customer", data.get("client")))

        amount_data = {
            "currency": data.get("currency", data.get("devise", "EUR")),
            "net_amount": cls._legacy_decimal(
                data.get("net_amount", data.get("montant_ht", data.get("total_ht")))
            ),
            "tax_amount": cls._legacy_decimal(
                data.get("tax_amount", data.get("montant_tva"))
            ),
            "gross_amount": cls._legacy_decimal(
                data.get("gross_amount", data.get("montant_ttc", data.get("total_ttc")))
            ),
        }
        reference = {
            "invoice_number": data.get(
                "invoice_number", data.get("numero_facture")
            ),
            "issue_date": data.get(
                "issue_date", data.get("date_facture", data.get("date_emission"))
            ),
            "due_date": data.get("due_date", data.get("date_echeance")),
            "purchase_order": data.get("purchase_order"),
            "external_reference": data.get("external_reference"),
        }
        direction = data.get("direction", InvoiceDirection.UNKNOWN)
        warnings = data.get(
            "extraction_warnings", data.get("commentaires_controle", [])
        )
        return cls.model_validate(
            {
                "document_id": document_id,
                "document_type": document_type,
                "direction": direction,
                "company_id": data.get("company_id"),
                "supplier": supplier,
                "customer": customer,
                "reference": reference,
                "lines": data.get("lines", data.get("lignes", [])),
                "amount": amount_data,
                "category": data.get("category", data.get("categorie")),
                "source_file": data.get("source_file"),
                "source_email": data.get("source_email"),
                "validation_status": validation_status,
                "confidence_score": data.get("confidence_score"),
                "extraction_warnings": warnings,
                "metadata": data.get("metadata", {}),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )

    @staticmethod
    def _legacy_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))

    @staticmethod
    def _legacy_party(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return {"name": value}
        if isinstance(value, Mapping):
            return {
                "name": value.get("name", value.get("nom")),
                "normalized_name": value.get("normalized_name"),
                "siret": value.get("siret"),
                "vat_number": value.get("vat_number", value.get("numero_tva")),
                "address": value.get("address", value.get("adresse")),
            }
        raise ValueError("legacy party must be a string or mapping")