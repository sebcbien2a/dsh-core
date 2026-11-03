"""P14.4 - Automatic ingestion from ``00_INBOX`` to the document state tree.

The service scans one tenant's ``00_INBOX``, fingerprints every file with its
SHA-256 content hash, rejects duplicates already known by
:class:`services.sqlite_accounting_store.SqliteAccountingStore` or parked under
``05_ARCHIVE_ERREUR``, then moves each survivor atomically to ``01_EN_COURS``
before running the real extraction pipeline. Extraction is performed by
:class:`services.invoice_producer_protocol.PdfInvoiceProducer`, which bridges
the frozen perception facade :mod:`services.document_pipeline` (native text
layer / routing metadata) and delegates scanned documents to a lazily built
:class:`services.local_invoice_extractor.LocalInvoiceExtractor`.

Routing is deterministic:

* complete fields with a confidence at or above the threshold ->
  ``03_VALIDE_PRET_FEC/<CATEGORIE>/`` plus a ``VALIDE`` store line;
* missing fields, an unclassified category or low confidence ->
  ``02_A_COMPLETER/`` plus an ``EN_ATTENTE`` store line and a non-destructive
  ``_A_COMPLETER.xlsx`` row;
* unreadable file, corrupt document or extraction failure ->
  ``05_ARCHIVE_ERREUR/``.

Nothing is ever deleted and every move is traced in the returned report.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    FolderState,
    MissingFieldEnum,
    PaymentSource,
    counterparty_account_for_payment_source,
    normalize_payment_source,
)
from schemas.invoice_schema import InvoiceSchema
from services.acompleter_workbook_service import (
    OUTCOME_CREATED,
    OUTCOME_UPDATED,
    AcompleterWorkbook,
    AcompleterWorkbookError,
    WorkbookLockedError,
    derive_document_id,
)
from services.folder_tree_service import ensure_tree, validate_tenant_id
from services.hotel_expense_parser import parse_hotel_evening_stage
from services.invoice_producer_protocol import PdfInvoiceProducer
from services.vault_accounting_bridge import (
    STATUS_PARTIAL,
    STATUS_WRITTEN,
    VaultValidationRecord,
)

_CHUNK = 1 << 16
_CENT = Decimal("0.01")
_DEFAULT_CONFIDENCE_THRESHOLD = 0.75
_DETERMINISTIC_TOOLS = frozenset({"pymupdf_text_regex"})
_UNKNOWN_SUPPLIER = "FOURNISSEUR_INCONNU"

OUTCOME_VALIDE = "VALIDE"
OUTCOME_A_COMPLETER = "A_COMPLETER"
OUTCOME_DUPLICATE = "DUPLICATE_SHA256"
OUTCOME_ERREUR = "ERREUR"

_CATEGORY_KEYWORDS: tuple[tuple[DocumentCategory, tuple[str, ...]], ...] = (
    (
        DocumentCategory.TRANSPORTS_LOGISTIQUE,
        (
            "transport",
            "fret",
            "logistique",
            "colis",
            "chronopost",
            "dhl",
            "douane",
        ),
    ),
    (
        DocumentCategory.BANQUE_FINANCE,
        ("banque", "agios", "frais financier", "interet", "releve"),
    ),
    (
        DocumentCategory.FISCAL_SOCIAL,
        (
            "urssaf",
            "impot",
            "taxe fonciere",
            "taxe habitation",
            "declaration tva",
            "liquidation tva",
            "salaire",
            "paie",
            "cotisation",
        ),
    ),
    (
        DocumentCategory.IMMOBILISATIONS,
        ("materiel", "machine", "vehicule", "immobilisation", "outillage"),
    ),
    (
        DocumentCategory.FRAIS_GENERAUX,
        (
            "loyer",
            "electricite",
            "energie",
            "edf",
            "engie",
            "telecom",
            "orange",
            "sfr",
            "assurance",
            "honoraire",
            "abonnement",
        ),
    ),
    (
        DocumentCategory.ACHATS_MARCHANDISES,
        ("peinture", "vernis", "fourniture", "marchandise", "matiere"),
    ),
    (
        DocumentCategory.VENTES_CLIENTS,
        ("facture client", "vente client"),
    ),
)

_DATE_PATTERNS = (
    re.compile(r"^(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})$"),
    re.compile(r"^(?P<day>\d{1,2})[-/.](?P<month>\d{1,2})[-/.](?P<year>\d{2,4})$"),
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_dest(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, destination)
    except OSError:
        shutil.move(str(source), str(destination))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        amount = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        raw = (
            raw.replace("\u00a0", "")
            .replace("\u202f", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        try:
            amount = Decimal(raw)
        except (InvalidOperation, ValueError):
            return None
    if not amount.is_finite():
        return None
    return amount.quantize(_CENT)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        try:
            year = int(match.group("year"))
            if year < 100:
                year += 2000
            return date(year, int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _category_value(value: Any) -> DocumentCategory | None:
    try:
        return DocumentCategory(value)
    except (TypeError, ValueError):
        return None


def _infer_category(*parts: Any) -> DocumentCategory:
    haystack = " ".join(_fold(part) for part in parts if part)
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return category
    return DocumentCategory.DIVERS_A_CLASSER


def _category_from_payload(
    payload: Mapping[str, Any],
    *,
    source_name: str = "",
    supplier_name: str | None = None,
    category_hint: DocumentCategory | str | None = None,
) -> DocumentCategory:
    hinted = _category_value(category_hint)
    if hinted is not None:
        return hinted
    for container in (
        payload,
        payload.get("metadata"),
        payload.get("invoice"),
        payload.get("data"),
    ):
        if not isinstance(container, Mapping):
            continue
        for key in ("category", "categorie", "categorie_suggeree"):
            found = _category_value(container.get(key))
            if found is not None:
                return found
    raw_text = payload.get("raw_text")
    raw_text = raw_text if isinstance(raw_text, str) else ""
    return _infer_category(source_name, supplier_name, raw_text[:4000])


def _payload_value(payload: Mapping[str, Any], key: str) -> Any:
    for container in (
        payload,
        payload.get("metadata"),
        payload.get("invoice"),
        payload.get("data"),
    ):
        if isinstance(container, Mapping) and container.get(key) is not None:
            return container.get(key)
    return None


def _confidence_from_invoice(
    invoice: InvoiceSchema, payload: Mapping[str, Any]
) -> float:
    if invoice.confidence_score is not None:
        return _clamp_confidence(invoice.confidence_score)
    for container in (invoice.metadata, payload.get("metadata"), payload):
        if not isinstance(container, Mapping):
            continue
        for key in ("confidence_score", "confidence", "extraction_confidence"):
            if container.get(key) is not None:
                return _clamp_confidence(container.get(key))
        tool = container.get("extraction_tool")
        if isinstance(tool, str) and tool in _DETERMINISTIC_TOOLS:
            return 0.9
    return 0.85


@dataclass(frozen=True)
class ExtractedInvoice:
    """Normalised extraction result consumed by the routing service."""

    issue_date: date | None = None
    invoice_number: str | None = None
    supplier_name: str | None = None
    amount_ht: Decimal | None = None
    amount_tva: Decimal | None = None
    amount_ttc: Decimal | None = None
    category: DocumentCategory | str = DocumentCategory.DIVERS_A_CLASSER
    confidence: float = 0.0
    currency: str = "EUR"
    document_type: str = "INVOICE"
    extraction_tool: str | None = None
    payment_source: PaymentSource | str | None = None
    counterparty_account: str | None = None
    expense_subcategory: str | None = None
    hotel_place: str | None = None
    ht_accommodation: Decimal | None = None
    ht_breakfast_meals: Decimal | None = None
    tva_deductible: Decimal | None = None
    tva_non_deductible: Decimal | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_date", _to_date(self.issue_date))
        object.__setattr__(self, "invoice_number", _clean_text(self.invoice_number))
        supplier = _clean_text(self.supplier_name)
        if supplier is not None and _fold(supplier) == _fold(_UNKNOWN_SUPPLIER):
            supplier = None
        object.__setattr__(self, "supplier_name", supplier)
        object.__setattr__(self, "amount_ht", _to_decimal(self.amount_ht))
        object.__setattr__(self, "amount_tva", _to_decimal(self.amount_tva))
        object.__setattr__(self, "amount_ttc", _to_decimal(self.amount_ttc))
        object.__setattr__(
            self,
            "category",
            _category_value(self.category) or DocumentCategory.DIVERS_A_CLASSER,
        )
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence))
        currency = str(self.currency or "EUR").strip().upper()
        object.__setattr__(
            self, "currency", currency if re.fullmatch(r"[A-Z]{3}", currency) else "EUR"
        )
        document_type = str(self.document_type or "INVOICE").strip().upper()
        object.__setattr__(self, "document_type", document_type or "INVOICE")
        source = normalize_payment_source(self.payment_source)
        object.__setattr__(self, "payment_source", source)
        account = (
            counterparty_account_for_payment_source(source)
            or _clean_text(self.counterparty_account)
        )
        object.__setattr__(self, "counterparty_account", account)
        object.__setattr__(
            self, "expense_subcategory", _clean_text(self.expense_subcategory)
        )
        object.__setattr__(self, "hotel_place", _clean_text(self.hotel_place))
        object.__setattr__(self, "ht_accommodation", _to_decimal(self.ht_accommodation))
        object.__setattr__(
            self, "ht_breakfast_meals", _to_decimal(self.ht_breakfast_meals)
        )
        object.__setattr__(self, "tva_deductible", _to_decimal(self.tva_deductible))
        object.__setattr__(
            self, "tva_non_deductible", _to_decimal(self.tva_non_deductible)
        )
        object.__setattr__(
            self, "raw", self.raw if isinstance(self.raw, Mapping) else {}
        )

    @property
    def missing_fields(self) -> tuple[MissingFieldEnum, ...]:
        missing: list[MissingFieldEnum] = []
        if self.issue_date is None:
            missing.append(MissingFieldEnum.DATE_PIECE)
        if self.invoice_number is None:
            missing.append(MissingFieldEnum.NUMERO_PIECE)
        if self.supplier_name is None:
            missing.append(MissingFieldEnum.NOM_FOURNISSEUR)
        if self.amount_ht is None:
            missing.append(MissingFieldEnum.MONTANT_HT)
        if self.amount_tva is None:
            missing.append(MissingFieldEnum.MONTANT_TVA)
        if self.amount_ttc is None:
            missing.append(MissingFieldEnum.MONTANT_TTC)
        if self.category == DocumentCategory.DIVERS_A_CLASSER:
            missing.append(MissingFieldEnum.CATEGORIE)
        return tuple(missing)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields


def extracted_from_invoice(
    invoice: InvoiceSchema,
    *,
    raw_payload: Mapping[str, Any] | None = None,
    source_name: str = "",
    category_hint: DocumentCategory | str | None = None,
    confidence: float | None = None,
) -> ExtractedInvoice:
    """Project a canonical ``InvoiceSchema`` onto the ingestion contract."""

    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    supplier_name = invoice.supplier.name if invoice.supplier is not None else None
    metadata = invoice.metadata if isinstance(invoice.metadata, Mapping) else {}
    tool = metadata.get("extraction_tool")
    tool = tool if isinstance(tool, str) else None
    category = _category_value(category_hint)
    if category is None:
        category = _category_value(invoice.category)
    if category is None:
        category = _category_from_payload(
            payload,
            source_name=source_name,
            supplier_name=supplier_name,
        )
    payment_source = _payload_value(payload, "payment_source")
    if payment_source is None:
        payment_source = metadata.get("payment_source")
    expense_subcategory = _payload_value(payload, "expense_subcategory")
    if expense_subcategory is None:
        expense_subcategory = metadata.get("expense_subcategory")
    hotel_fields = {
        key: _payload_value(payload, key)
        for key in (
            "hotel_place",
            "ht_accommodation",
            "ht_breakfast_meals",
            "tva_deductible",
            "tva_non_deductible",
        )
    }
    hotel_fields = {
        key: value
        for key, value in hotel_fields.items()
        if value is not None
    }
    if not hotel_fields and (
        expense_subcategory == "HOTELLERIE"
        or parse_hotel_evening_stage(payload) is not None
    ):
        breakdown = parse_hotel_evening_stage(payload)
        if breakdown is not None:
            hotel_fields = breakdown.as_metadata()
    return ExtractedInvoice(
        issue_date=invoice.reference.issue_date,
        invoice_number=invoice.reference.invoice_number,
        supplier_name=supplier_name,
        amount_ht=invoice.amount.net_amount,
        amount_tva=invoice.amount.tax_amount,
        amount_ttc=invoice.amount.gross_amount,
        category=category,
        confidence=(
            _clamp_confidence(confidence)
            if confidence is not None
            else _confidence_from_invoice(invoice, payload)
        ),
        currency=invoice.amount.currency,
        document_type=invoice.document_type.value,
        extraction_tool=tool,
        payment_source=payment_source,
        expense_subcategory=expense_subcategory,
        hotel_place=hotel_fields.get("hotel_place"),
        ht_accommodation=hotel_fields.get("ht_accommodation"),
        ht_breakfast_meals=hotel_fields.get("ht_breakfast_meals"),
        tva_deductible=hotel_fields.get("tva_deductible"),
        tva_non_deductible=hotel_fields.get("tva_non_deductible"),
        raw=payload,
    )


class PipelineDocumentExtractor:
    """Real extractor: perception bridge + deterministic text / local vision."""

    def __init__(
        self,
        *,
        vision_extractor: Callable[[Path], Mapping[str, Any]] | None = None,
        category_hint: DocumentCategory | str | None = None,
    ) -> None:
        self._vision = vision_extractor
        self._category_hint = category_hint

    def _vision_callable(self, source: Path) -> Mapping[str, Any]:
        if self._vision is None:
            from services.local_invoice_extractor import LocalInvoiceExtractor

            self._vision = LocalInvoiceExtractor()
        return self._vision(source)

    def __call__(self, source: str | Path) -> ExtractedInvoice:
        path = Path(source)
        producer = PdfInvoiceProducer(path, vision_extractor=self._vision_callable)
        invoice = producer.produce()
        return extracted_from_invoice(
            invoice,
            raw_payload=producer.last_payload,
            source_name=path.name,
            category_hint=self._category_hint,
        )


@dataclass(frozen=True)
class IngestedDocument:
    """Observable outcome for one file scanned in ``00_INBOX``."""

    nom_fichier: str
    sha256: str
    outcome: str
    destination: str | None
    category: DocumentCategory | None = None
    missing_fields: tuple[MissingFieldEnum, ...] = ()
    confidence: float | None = None
    detail: str | None = None


@dataclass(frozen=True)
class InboxIngestionReport:
    """Aggregate result of one ingestion pass."""

    scanned: int = 0
    duplicates: int = 0
    moved_to_03: int = 0
    moved_to_02: int = 0
    archived: int = 0
    rows_written: int = 0
    rows_updated: int = 0
    deferred_lock: int = 0
    vault_events: int = 0
    documents: tuple[IngestedDocument, ...] = ()
    traces: tuple[str, ...] = ()


class _Accumulator:
    def __init__(self) -> None:
        self.scanned = 0
        self.duplicates = 0
        self.moved_to_03 = 0
        self.moved_to_02 = 0
        self.archived = 0
        self.rows_written = 0
        self.rows_updated = 0
        self.deferred_lock = 0
        self.vault_events = 0
        self.documents: list[IngestedDocument] = []
        self.traces: list[str] = []

    def report(self) -> InboxIngestionReport:
        return InboxIngestionReport(
            scanned=self.scanned,
            duplicates=self.duplicates,
            moved_to_03=self.moved_to_03,
            moved_to_02=self.moved_to_02,
            archived=self.archived,
            rows_written=self.rows_written,
            rows_updated=self.rows_updated,
            deferred_lock=self.deferred_lock,
            vault_events=self.vault_events,
            documents=tuple(self.documents),
            traces=tuple(self.traces),
        )


class InboxIngestionService:
    """Drive files from ``00_INBOX`` through extraction to their final folder."""

    def __init__(
        self,
        tenant_id: str,
        store: Any,
        root: str | os.PathLike[str] | None = None,
        *,
        extractor: Callable[[Path], Any] | None = None,
        confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
        vault_bridge: Any | None = None,
    ) -> None:
        self._tenant_id = validate_tenant_id(tenant_id)
        self._store = store
        self._tree = ensure_tree(self._tenant_id, root)
        self._workbook = AcompleterWorkbook(self._tree.root / self._tenant_id)
        self._extractor = extractor if extractor is not None else PipelineDocumentExtractor()
        self._vault_bridge = vault_bridge
        threshold = _clamp_confidence(confidence_threshold)
        if threshold != confidence_threshold:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self._confidence_threshold = threshold

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def tree(self) -> Any:
        return self._tree

    @property
    def workbook(self) -> AcompleterWorkbook:
        return self._workbook

    @property
    def vault_bridge(self) -> Any | None:
        return self._vault_bridge

    def ingest_inbox(self) -> InboxIngestionReport:
        """Scan ``00_INBOX`` and route every file; never raises on one file."""
        return self.process_inbox()

    def process_inbox(
        self, paths: Iterable[str | Path] | None = None
    ) -> InboxIngestionReport:
        """Route ``00_INBOX`` files, optionally restricted to stabilised paths.

        ``paths=None`` keeps the historical full-scan behaviour used by
        :meth:`ingest_inbox`. When an explicit iterable is supplied (the
        background watcher only passes files whose size and ``mtime`` have
        settled), every entry must resolve inside ``00_INBOX``; anything else
        is ignored so a bad caller can never pull a file from another folder.
        """
        inbox = self._tree.state_dir(FolderState.INBOX)
        acc = _Accumulator()
        archived_hashes = self._archived_hashes()
        claimed: set[str] = set()
        if paths is None:
            candidates = sorted(
                (path for path in inbox.iterdir() if path.is_file()),
                key=lambda item: item.name,
            )
        else:
            candidates = self._select_inbox_paths(inbox, paths)
        for path in candidates:
            acc.scanned += 1
            try:
                digest = _file_sha256(path)
            except OSError as exc:
                self._archive(path, acc, "UNREADABLE", detail=str(exc))
                continue
            if (
                digest in archived_hashes
                or digest in claimed
                or self._store_has_document(digest)
            ):
                self._archive(path, acc, "DUPLICATE_SHA256", sha256=digest)
                continue
            claimed.add(digest)
            self._ingest_one(path, digest, acc)
        return acc.report()

    @staticmethod
    def _select_inbox_paths(
        inbox: Path, paths: Iterable[str | Path]
    ) -> list[Path]:
        inbox_resolved = inbox.resolve()
        selected: list[Path] = []
        seen: set[Path] = set()
        for raw in paths:
            try:
                candidate = Path(raw).resolve()
            except (OSError, TypeError, ValueError):
                continue
            if candidate in seen or not candidate.is_file():
                continue
            if candidate.parent != inbox_resolved:
                continue
            seen.add(candidate)
            selected.append(candidate)
        return sorted(selected, key=lambda item: item.name)

    # -- pipeline --------------------------------------------------------

    def _ingest_one(self, path: Path, digest: str, acc: _Accumulator) -> None:
        env_cours = self._tree.state_dir(FolderState.EN_COURS)
        working = _unique_dest(env_cours / path.name)
        try:
            _move(path, working)
        except OSError as exc:
            acc.traces.append(f"{digest}:MOVE_TO_01_FAILED:{path.name}:{exc}")
            return
        acc.traces.append(f"{digest}:00_INBOX->01_EN_COURS/{working.name}")

        try:
            extracted = self._extract(working)
        except Exception as exc:
            self._archive(
                working,
                acc,
                "EXTRACTION_FAILED",
                sha256=digest,
                detail=_error_detail(exc),
            )
            return

        missing = extracted.missing_fields
        high_confidence = extracted.confidence >= self._confidence_threshold
        if extracted.is_complete and high_confidence:
            self._route_valid(working, digest, extracted, acc)
        else:
            self._route_pending(working, digest, extracted, missing, acc)

    def _extract(self, source: Path) -> ExtractedInvoice:
        result = self._extractor(source)
        if isinstance(result, ExtractedInvoice):
            return result
        if isinstance(result, InvoiceSchema):
            return extracted_from_invoice(
                result, source_name=source.name
            )
        if isinstance(result, Mapping):
            payload = dict(result)
            invoice = InvoiceSchema.model_validate(payload)
            return extracted_from_invoice(
                invoice,
                raw_payload=payload,
                source_name=source.name,
            )
        raise TypeError(
            f"extractor must return ExtractedInvoice, InvoiceSchema or Mapping, got "
            f"{type(result).__name__}"
        )

    def _route_valid(
        self,
        source: Path,
        digest: str,
        extracted: ExtractedInvoice,
        acc: _Accumulator,
    ) -> None:
        category = _category_value(extracted.category) or DocumentCategory.DIVERS_A_CLASSER
        destination = _unique_dest(
            self._tree.category_dir(FolderState.VALIDE_PRET_FEC, category) / source.name
        )
        nom_fichier = destination.name
        rel_path = destination.relative_to(self._tree.root / self._tenant_id).as_posix()
        self._persist(digest, nom_fichier, DocumentStatus.VALIDE, rel_path, extracted)
        _move(source, destination)
        acc.moved_to_03 += 1
        self._notify_vault(digest, nom_fichier, rel_path, category, extracted, acc)
        acc.documents.append(
            IngestedDocument(
                nom_fichier=nom_fichier,
                sha256=digest,
                outcome=OUTCOME_VALIDE,
                destination=rel_path,
                category=category,
                confidence=extracted.confidence,
            )
        )
        acc.traces.append(f"{digest}:01_EN_COURS->{rel_path}")

    def _route_pending(
        self,
        source: Path,
        digest: str,
        extracted: ExtractedInvoice,
        missing: tuple[MissingFieldEnum, ...],
        acc: _Accumulator,
    ) -> None:
        category = _category_value(extracted.category) or DocumentCategory.DIVERS_A_CLASSER
        destination = _unique_dest(
            self._tree.state_dir(FolderState.A_COMPLETER) / source.name
        )
        nom_fichier = destination.name
        try:
            outcome = self._workbook.upsert(
                digest, nom_fichier, missing, category
            )
        except WorkbookLockedError as exc:
            acc.deferred_lock += 1
            acc.traces.append(f"{digest}:WORKBOOK_LOCKED:{exc.lock_path.name}")
            outcome = None
        except AcompleterWorkbookError as exc:
            self._archive(
                source,
                acc,
                "WORKBOOK_ERROR",
                sha256=digest,
                detail=_error_detail(exc),
            )
            return
        else:
            if outcome == OUTCOME_CREATED:
                acc.rows_written += 1
            elif outcome == OUTCOME_UPDATED:
                acc.rows_updated += 1

        rel_path = destination.relative_to(self._tree.root / self._tenant_id).as_posix()
        try:
            self._persist(
                digest,
                nom_fichier,
                DocumentStatus.EN_ATTENTE,
                rel_path,
                extracted,
            )
        except Exception as exc:
            self._archive(
                source,
                acc,
                "STORE_ERROR",
                sha256=digest,
                detail=_error_detail(exc),
            )
            return
        _move(source, destination)
        acc.moved_to_02 += 1
        acc.documents.append(
            IngestedDocument(
                nom_fichier=nom_fichier,
                sha256=digest,
                outcome=OUTCOME_A_COMPLETER,
                destination=rel_path,
                category=category,
                missing_fields=missing,
                confidence=extracted.confidence,
                detail="WORKBOOK_LOCKED" if outcome is None else None,
            )
        )
        acc.traces.append(f"{digest}:01_EN_COURS->{rel_path}")

    # -- persistence -----------------------------------------------------

    def _notify_vault(
        self,
        digest: str,
        nom_fichier: str,
        rel_path: str,
        category: DocumentCategory,
        extracted: ExtractedInvoice,
        acc: _Accumulator,
    ) -> None:
        """Mirror the validation into the vault without ever failing the route."""
        if self._vault_bridge is None:
            return
        try:
            record = VaultValidationRecord(
                tenant_id=self._tenant_id,
                sha256=digest,
                nom_fichier=nom_fichier,
                supplier_name=extracted.supplier_name,
                invoice_number=extracted.invoice_number,
                issue_date=extracted.issue_date,
                amount_ht=extracted.amount_ht,
                amount_ttc=extracted.amount_ttc,
                category=category.value,
                document_id=derive_document_id(digest),
                relative_path=rel_path,
                currency=extracted.currency,
            )
            result = self._vault_bridge.notify_validated(record)
        except Exception as exc:
            acc.traces.append(f"{digest}:VAULT_ERROR:{_error_detail(exc)}")
            return
        status = getattr(result, "status", "failed")
        acc.traces.append(f"{digest}:VAULT:{status}")
        if status in (STATUS_WRITTEN, STATUS_PARTIAL):
            acc.vault_events += 1

    def _persist(
        self,
        sha256: str,
        nom_fichier: str,
        status: DocumentStatus,
        rel_path: str,
        extracted: ExtractedInvoice,
    ) -> str:
        document_id = derive_document_id(sha256)
        line = {
            "operation_date": (
                extracted.issue_date.isoformat()
                if extracted.issue_date is not None
                else None
            ),
            "label": extracted.invoice_number or nom_fichier,
            "debit_amount": None,
            "credit_amount": None,
            "currency": extracted.currency,
            "counterparty": extracted.supplier_name,
            "invoice_id": document_id,
            "invoice_url": rel_path,
            "category": (
                _category_value(extracted.category) or DocumentCategory.DIVERS_A_CLASSER
            ).value,
            "amount_ht": (
                str(extracted.amount_ht) if extracted.amount_ht is not None else None
            ),
            "amount_tva": (
                str(extracted.amount_tva) if extracted.amount_tva is not None else None
            ),
            "amount_ttc": (
                str(extracted.amount_ttc) if extracted.amount_ttc is not None else None
            ),
            "document_type": extracted.document_type,
            "expense_subcategory": extracted.expense_subcategory,
            "hotel_place": extracted.hotel_place,
            "ht_accommodation": (
                str(extracted.ht_accommodation)
                if extracted.ht_accommodation is not None
                else None
            ),
            "ht_breakfast_meals": (
                str(extracted.ht_breakfast_meals)
                if extracted.ht_breakfast_meals is not None
                else None
            ),
            "tva_deductible": (
                str(extracted.tva_deductible)
                if extracted.tva_deductible is not None
                else None
            ),
            "tva_non_deductible": (
                str(extracted.tva_non_deductible)
                if extracted.tva_non_deductible is not None
                else None
            ),
            "payment_source": (
                extracted.payment_source.value
                if extracted.payment_source is not None
                else None
            ),
            "counterparty_account": (
                extracted.counterparty_account
                or counterparty_account_for_payment_source(extracted.payment_source)
            ),
            "status": status.value,
        }
        return self._store.upsert_document_line(self._tenant_id, document_id, line)

    def _store_has_document(self, digest: str) -> bool:
        return (
            self._store.get_line(self._tenant_id, derive_document_id(digest))
            is not None
        )

    # -- archive / traces ------------------------------------------------

    def _archive(
        self,
        path: Path,
        acc: _Accumulator,
        reason: str,
        *,
        sha256: str | None = None,
        detail: str | None = None,
    ) -> None:
        destination = _unique_dest(
            self._tree.state_dir(FolderState.ARCHIVE_ERREUR) / path.name
        )
        rel_path = destination.relative_to(self._tree.root / self._tenant_id).as_posix()
        try:
            _move(path, destination)
        except OSError as exc:
            acc.traces.append(f"{reason}:{path.name}:ARCHIVE_FAILED:{exc}")
            return
        acc.archived += 1
        if reason == "DUPLICATE_SHA256":
            acc.duplicates += 1
        suffix = f":{detail}" if detail else ""
        prefix = f"{sha256}:" if sha256 else ""
        acc.documents.append(
            IngestedDocument(
                nom_fichier=path.name,
                sha256=sha256 or "",
                outcome=(
                    OUTCOME_DUPLICATE if reason == "DUPLICATE_SHA256" else OUTCOME_ERREUR
                ),
                destination=rel_path,
                detail=f"{reason}{suffix}",
            )
        )
        acc.traces.append(f"{prefix}{reason}:{path.name}->{rel_path}{suffix}")

    def _archived_hashes(self) -> set[str]:
        hashes: set[str] = set()
        archive = self._tree.state_dir(FolderState.ARCHIVE_ERREUR)
        for path in archive.rglob("*"):
            if not path.is_file() or path.name.startswith("~$"):
                continue
            try:
                hashes.add(_file_sha256(path))
            except OSError:
                continue
        return hashes


def _error_detail(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    label = code if isinstance(code, str) and code else type(exc).__name__.upper()
    message = str(exc).strip()
    return f"{label}: {message}" if message and message != label else label


__all__ = [
    "ExtractedInvoice",
    "InboxIngestionReport",
    "InboxIngestionService",
    "IngestedDocument",
    "OUTCOME_A_COMPLETER",
    "OUTCOME_DUPLICATE",
    "OUTCOME_ERREUR",
    "OUTCOME_VALIDE",
    "PipelineDocumentExtractor",
    "extracted_from_invoice",
]
