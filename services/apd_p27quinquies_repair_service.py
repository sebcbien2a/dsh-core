"""P27-quinquies - Deterministic APD accounting repairs.

This service applies the director's seven P27-quinquies rules directly to the
SQLite ledger before any workbook is exported:

1. principal-role tier separation (a supplier is never a client ledger sheet);
2. case-insensitive tier canonicalisation (``Boxtal`` -> ``BOXTAL``);
3. Boxtal invoice + OFX debit lettering so a category summary shows one line;
4. Bosch/Sia and OpenRouter PDF date/amount extraction and 2025 fiscal year;
5. local ``PIECES_JOINTES`` copies (never a ``../..`` OneDrive redirect);
6. Caisse d'Épargne OFX fees marked ``PAYE_PRELEVE`` with their bank date;
7. technical OFX / batch PDF exclusion from the mother-table invoice rows.
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from services.bank_reconciliation_service import MAX_SUBSET_SUM_PIECES
from services.bosch_sia_invoice_parser import (
    BOSCH_SIA_CATEGORY,
    BOSCH_SIA_SUPPLIER_NAME,
    parse_bosch_sia_invoice,
)
from services.pdf_splitter_and_linker import (
    PURCHASES_SUBDIR,
    SALES_SUBDIR,
    copy_unit_piece,
    find_piece_for_document,
    normalized_purchase_filename,
    normalized_sales_filename,
)
from services.tier_reference_service import TierReferenceService

_CENT = Decimal("0.01")
_FISCAL_2025_START = date(2024, 1, 1)
_FISCAL_2026_START = date(2026, 1, 1)
_BOXTAL = "BOXTAL"
_OPENROUTER = "OPENROUTER"
_BOSCH = "BOSCH_SIA"
_CAISSE_EPARGNE = "CAISSE D'EPARGNE"
_SOURCE_OFX = "SOURCE_OFX"
_TECHNICAL_TYPES = frozenset(
    {
        "SOURCE_OFX",
        "BORDEREAU_LCR",
        "LCR_REMITTANCE",
        "LCR_PAYMENT",
        "LCR_SUB_PAYMENT",
    }
)
_BATCH_PREFIXES = (
    "factures clients",
    "facture client yucel",
    "facture carrosserie yucel",
    "bordereau_remise",
    "bordereau remise",
    "detailremise",
    "detail_remise",
)
_BOSCH_FILENAME = re.compile(
    r"Y16V_SD_INVOICE_?(20\d{6})_?(\d{5,})", re.IGNORECASE
)
_SOLTA_BUGGED = re.compile(r"^(?:2601616[ _-]?)+", re.IGNORECASE)
_OFX_RANGE = re.compile(r"operations_(\d{8})_(\d{8})", re.IGNORECASE)
_FEE_LABEL = re.compile(
    r"commission|abonnement|abo\s+ce|forfait|cotisation|frais",
    re.IGNORECASE,
)
_OPENROUTER_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class P27QuinquiesReport:
    renamed_tiers: int = 0
    repaired_bosch: int = 0
    repaired_openrouter: int = 0
    repaired_documents: int = 0
    repaired_counterparties: int = 0
    repaired_dates: int = 0
    linked_boxtal: int = 0
    copied_pieces: int = 0
    linked_statements: int = 0
    repaired_fee_rows: int = 0
    fiscal_2025_rows: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".")).quantize(_CENT)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _is_fiscal_2025(value: date | None) -> bool:
    return value is not None and _FISCAL_2025_START <= value < _FISCAL_2026_START


def _date_from_filename(value: Any) -> date | None:
    text = str(value or "")
    match = re.search(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)", text)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _pdf_text(path: Path) -> str:
    try:
        import fitz

        if hasattr(fitz, "TOOLS"):
            fitz.TOOLS.mupdf_display_errors(False)
        document = fitz.open(str(path))
        try:
            return "\n".join(page.get_text("text") for page in document)
        finally:
            document.close()
    except Exception:  # noqa: BLE001 - a missing PDF never blocks the repair
        return ""


def _document_number(row: dict[str, Any]) -> str:
    for key in ("invoice_id", "document_invoice_id", "piece_number"):
        value = str(row.get(key) or "").strip()
        if value and not re.fullmatch(r"[0-9a-fA-F-]{32,}", value):
            return value
    match = re.search(r"_(\d{5,})\.pdf$", str(row.get("invoice_url") or ""))
    if match is not None:
        return match.group(1)
    return str(row.get("line_id") or "")


def _is_bank_row(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").upper()
    line_id = str(row.get("line_id") or "").upper()
    if document_type in {"OFX", "QFX"} or line_id.startswith("OFX-"):
        return True
    return bool(
        _decimal(row.get("debit_amount")) or _decimal(row.get("credit_amount"))
    )


def _is_technical_row(row: dict[str, Any]) -> bool:
    document_type = str(row.get("document_type") or "").upper()
    if document_type in _TECHNICAL_TYPES:
        return True
    for key in ("invoice_url", "piece_pdf", "label"):
        raw = str(row.get(key) or "").strip()
        if not raw:
            continue
        name = raw.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if name.endswith((".ofx", ".qfx")):
            return True
        folded = _fold(name).replace("_", " ").replace("-", " ")
        folded = re.sub(r"\s+", " ", folded).strip()
        if any(folded.startswith(prefix) for prefix in _BATCH_PREFIXES):
            return True
        if "data " in folded and "archived" in folded:
            return True
    return False


def _update_row(store: Any, tenant_id: str, row: dict[str, Any], **changes: Any) -> None:
    line_id = str(row.get("line_id") or "")
    if not line_id:
        return
    updated = dict(row)
    updated.pop("line_id", None)
    updated.update(changes)
    store.force_upsert_document_line(tenant_id, line_id, updated)


def _canonicalise_tiers(
    store: Any, tenant_id: str, rows: list[dict[str, Any]], tiers: TierReferenceService
) -> int:
    renamed = 0
    for raw_name in sorted(
        {
            str(row.get("counterparty") or "").strip()
            for row in rows
            if str(row.get("counterparty") or "").strip()
        }
    ):
        record = tiers.resolve(raw_name)
        if record is None or record.canonical == raw_name:
            continue
        renamed += store.rename_counterparty(tenant_id, raw_name, record.canonical)
    return renamed


def _bosch_source(tenant_dir: Path, export_dir: Path, row: dict[str, Any]) -> Path | None:
    for candidate in (
        tenant_dir / str(row.get("invoice_url") or ""),
        export_dir / str(row.get("piece_pdf") or ""),
    ):
        if candidate.is_file():
            return candidate
    return None


def _repair_bosch(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
    rows: list[dict[str, Any]],
) -> int:
    twins: dict[str, dict[str, Any]] = {}
    for row in rows:
        number = _document_number(row)
        if (
            number
            and _decimal(row.get("amount_ttc")) not in (None, Decimal("0.00"))
            and str(row.get("counterparty") or "").upper() in {_BOSCH, "ROBERT BOSCH - SIA ABRASIVES"}
        ):
            twins.setdefault(number, row)
    repaired = 0
    for row in rows:
        reference = str(row.get("invoice_url") or "")
        if "Y16V_SD_INVOICE" not in reference.upper():
            continue
        if _decimal(row.get("amount_ttc")) not in (None, Decimal("0.00")) and row.get(
            "operation_date"
        ):
            continue
        number = _document_number(row)
        filename_match = _BOSCH_FILENAME.search(reference)
        if filename_match is not None:
            number = filename_match.group(2) or number
        source = _bosch_source(tenant_dir, export_dir, row)
        document = None
        if source is not None:
            try:
                document = parse_bosch_sia_invoice(
                    source, filename=source.name
                )
            except Exception:  # noqa: BLE001 - fall back to the clean twin row
                document = None
        if document is not None:
            changes = {
                "operation_date": (
                    document.document_date.isoformat()
                    if document.document_date
                    else row.get("operation_date")
                ),
                "invoice_id": document.document_number,
                "amount_ht": str(document.signed_amount_ht),
                "amount_tva": str(document.signed_amount_tva),
                "amount_ttc": str(document.signed_amount_ttc),
                "document_type": document.document_type,
                "counterparty": _BOSCH,
                "category": BOSCH_SIA_CATEGORY,
                "status": "VALIDE",
                "origin_document_number": document.origin_document_number or None,
            }
        else:
            twin = twins.get(number)
            if twin is None:
                inferred = _date_from_filename(reference)
                if inferred is None:
                    continue
                changes = {"operation_date": inferred.isoformat()}
            else:
                changes = {
                    "operation_date": twin.get("operation_date"),
                    "invoice_id": twin.get("invoice_id") or number,
                    "amount_ht": twin.get("amount_ht"),
                    "amount_tva": twin.get("amount_tva"),
                    "amount_ttc": twin.get("amount_ttc"),
                    "document_type": twin.get("document_type"),
                    "counterparty": _BOSCH,
                    "category": BOSCH_SIA_CATEGORY,
                    "status": "VALIDE",
                }
        if changes.get("operation_date"):
            operation_date = _parse_date(changes["operation_date"])
            if operation_date is not None and _is_fiscal_2025(operation_date):
                changes["fiscal_year_target"] = "2025"
        _update_row(store, tenant_id, row, **changes)
        repaired += 1
    return repaired


def _openrouter_date(text: str) -> date | None:
    match = re.search(
        r"Date\s+(?:of\s+issue|paid|due)\s*\n?\s*"
        r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    month = _OPENROUTER_MONTHS.get(match.group(1).casefold())
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def _openrouter_amount(text: str) -> Decimal | None:
    amounts = [
        _decimal(value)
        for value in re.findall(r"\$([0-9][0-9,]*\.\d{2})", text)
    ]
    valid = [value for value in amounts if value is not None and value > 0]
    if not valid:
        return None
    return max(valid)


def _repair_openrouter(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
    rows: list[dict[str, Any]],
) -> int:
    repaired = 0
    for row in rows:
        if str(row.get("counterparty") or "").upper() != _OPENROUTER:
            continue
        if _decimal(row.get("amount_ttc")) not in (None, Decimal("0.00")):
            continue
        source = _bosch_source(tenant_dir, export_dir, row)
        if source is None:
            continue
        text = _pdf_text(source)
        if not text:
            continue
        amount = _openrouter_amount(text)
        operation_date = _openrouter_date(text) or _date_from_filename(
            str(row.get("invoice_url") or "")
        )
        if amount is None and operation_date is None:
            continue
        changes: dict[str, Any] = {
            "counterparty": _OPENROUTER,
            "currency": "USD",
            "document_type": "FACTURE",
            "status": "VALIDE",
        }
        if operation_date is not None:
            changes["operation_date"] = operation_date.isoformat()
            if _is_fiscal_2025(operation_date):
                changes["fiscal_year_target"] = "2025"
        if amount is not None:
            changes.update(
                {
                    "amount_ht": str(amount.quantize(_CENT)),
                    "amount_tva": "0.00",
                    "amount_ttc": str(amount.quantize(_CENT)),
                }
            )
        _update_row(store, tenant_id, row, **changes)
        repaired += 1
    return repaired


def _french_invoice_date(text: str) -> date | None:
    for match in re.finditer(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2,4})(?!\d)", text):
        year = int(match.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, int(match.group(2)), int(match.group(1)))
        except ValueError:
            continue
    return None


def _net_to_pay_totals(text: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """Best-effort HT/TVA/TTC from the ``NET À PAYER`` block of a French bill."""
    match = None
    for match in re.finditer(r"NET\s*[A-Z]?\s*PAYER", text, re.IGNORECASE):
        pass
    if match is None:
        return None
    section = text[match.end() : match.end() + 800]
    values = [
        _decimal(value.replace("\u00a0", "").replace(" ", ""))
        for value in re.findall(
            r"(?<!\d)(\d{1,3}(?:[ \u00a0.]\d{3})*,\d{2})(?!\d)", section
        )
    ]
    valid = [value for value in values if value is not None and value > 0]
    if not valid:
        return None
    ttc = max(valid)
    rate_match = re.search(r"\b(20|10|5[.,]5|2[.,]1)\s*%", text)
    if rate_match is None:
        return max(Decimal("0.00"), ttc), Decimal("0.00"), ttc
    rate = _decimal(rate_match.group(1).replace(",", ".")) or Decimal("20")
    ht = (ttc / (Decimal("1") + rate / Decimal("100"))).quantize(_CENT)
    return ht, (ttc - ht).quantize(_CENT), ttc


def _repair_generic_documents(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
    rows: list[dict[str, Any]],
) -> int:
    repaired = 0
    for row in rows:
        if _is_bank_row(row) or _is_technical_row(row):
            continue
        missing_date = _parse_date(row.get("operation_date")) is None
        missing_amount = _decimal(row.get("amount_ttc")) in (
            None,
            Decimal("0.00"),
        )
        if not missing_date and not missing_amount:
            continue
        source = _bosch_source(tenant_dir, export_dir, row)
        if source is None:
            continue
        text = _pdf_text(source)
        if not text:
            continue
        changes: dict[str, Any] = {}
        if missing_date:
            inferred = _french_invoice_date(text) or _date_from_filename(
                row.get("invoice_url") or row.get("piece_pdf")
            )
            if inferred is not None:
                changes["operation_date"] = inferred.isoformat()
                if _is_fiscal_2025(inferred):
                    changes["fiscal_year_target"] = "2025"
        if missing_amount:
            totals = _net_to_pay_totals(text)
            if totals is not None:
                ht, tva, ttc = totals
                changes.update(
                    {
                        "amount_ht": str(ht.quantize(_CENT)),
                        "amount_tva": str(tva.quantize(_CENT)),
                        "amount_ttc": str(ttc.quantize(_CENT)),
                    }
                )
        if changes:
            _update_row(store, tenant_id, row, **changes)
            repaired += 1
    return repaired


def _repair_counterparties_from_pdf(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
    rows: list[dict[str, Any]],
    tiers: TierReferenceService,
) -> int:
    """Recover a noisy counterparty (``V/COMMERCIAL :``) from the PDF text."""
    candidates: list[tuple[str, str]] = []
    for record in tiers.records():
        if record.canonical == "APD_INTERNE":
            continue
        names = {record.canonical.replace("_", " "), *record.aliases}
        for name in names:
            clean = re.sub(r"\s+", " ", name).strip()
            if len(clean) < 6:
                continue
            candidates.append((clean, record.canonical))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    repaired = 0
    for row in rows:
        if _is_bank_row(row) or _is_technical_row(row):
            continue
        raw = str(row.get("counterparty") or "").strip()
        if tiers.resolve(raw) is not None:
            continue
        source = _bosch_source(tenant_dir, export_dir, row)
        if source is None:
            continue
        text = _pdf_text(source)
        if not text:
            continue
        for name, canonical in candidates:
            if re.search(
                rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])",
                text,
                re.IGNORECASE,
            ):
                _update_row(store, tenant_id, row, counterparty=canonical)
                repaired += 1
                break
    return repaired


def _fill_missing_dates(
    store: Any, tenant_id: str, rows: list[dict[str, Any]]
) -> int:
    repaired = 0
    for row in rows:
        if _is_bank_row(row) or _is_technical_row(row):
            continue
        operation_date = _parse_date(row.get("operation_date"))
        if operation_date is None:
            operation_date = _date_from_filename(
                row.get("invoice_url") or row.get("piece_pdf")
            )
            if operation_date is None:
                continue
            changes: dict[str, Any] = {"operation_date": operation_date.isoformat()}
            if _is_fiscal_2025(operation_date):
                changes["fiscal_year_target"] = "2025"
            _update_row(store, tenant_id, row, **changes)
            repaired += 1
        if (
            operation_date is not None
            and _is_fiscal_2025(operation_date)
            and not str(row.get("fiscal_year_target") or "").strip()
        ):
            _update_row(
                store, tenant_id, row, fiscal_year_target="2025"
            )
    return repaired


def _tier_rows(rows: Iterable[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("counterparty") or "").upper() == name
    ]


def _link_boxtal(
    store: Any, tenant_id: str, rows: list[dict[str, Any]]
) -> int:
    invoices = [
        row
        for row in _tier_rows(rows, _BOXTAL)
        if not _is_bank_row(row)
        and str(row.get("document_type") or "").upper() in {"FACTURE", "AVOIR"}
        and _decimal(row.get("amount_ttc")) not in (None, Decimal("0.00"))
    ]
    linked = 0
    for bank in _tier_rows(rows, _BOXTAL):
        if not _is_bank_row(bank):
            continue
        if str(bank.get("reconciliation_status") or "").upper() in {
            "PAYEE_RAPPROCHEE",
            "REMBOURSE_RAPPROCHE",
        }:
            continue
        debit = _decimal(bank.get("debit_amount")) or Decimal("0.00")
        credit = _decimal(bank.get("credit_amount")) or Decimal("0.00")
        amount = abs(debit or credit)
        if amount == 0:
            continue
        bank_date = _parse_date(bank.get("operation_date"))
        best: tuple[int, dict[str, Any]] | None = None
        for invoice in invoices:
            invoice_amount = abs(_decimal(invoice.get("amount_ttc")) or Decimal("0.00"))
            if invoice_amount != amount:
                continue
            invoice_date = _parse_date(invoice.get("operation_date"))
            delta = (
                abs((bank_date - invoice_date).days)
                if bank_date and invoice_date
                else 0
            )
            if bank_date and invoice_date and delta > 15:
                continue
            if best is None or delta < best[0]:
                best = (delta, invoice)
        if best is None:
            continue
        invoice = best[1]
        number = _document_number(invoice)
        reference = f"BOXTAL {number}"
        status = (
            "REMBOURSE_RAPPROCHE"
            if str(invoice.get("document_type") or "").upper() == "AVOIR"
            else "PAYEE_RAPPROCHEE"
        )
        _update_row(
            store,
            tenant_id,
            invoice,
            reconciliation_status=status,
            reconciliation_date=str(bank.get("operation_date") or ""),
            reconciliation_reference=reference,
            reconciliation_amount=str(amount),
            balance_due="0.00",
        )
        _update_row(
            store,
            tenant_id,
            bank,
            counterparty=_BOXTAL,
            invoice_id=number,
            status="VALIDE",
            reconciliation_status=status,
            reconciliation_date=str(bank.get("operation_date") or ""),
            reconciliation_reference=reference,
            reconciliation_amount=str(amount),
        )
        linked += 1
    return linked


def _piece_relative(export_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(export_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _ensure_local_piece(
    store: Any,
    tenant_id: str,
    row: dict[str, Any],
    tenant_dir: Path,
    export_dir: Path,
) -> int:
    if _is_bank_row(row) or _is_technical_row(row):
        return 0
    number = _document_number(row)
    side = (
        SALES_SUBDIR
        if str(row.get("category") or "").upper() == "VENTES_CLIENTS"
        else PURCHASES_SUBDIR
    )
    pieces_root = export_dir / "PIECES_JOINTES" / side
    pieces_root.mkdir(parents=True, exist_ok=True)
    current = str(row.get("piece_pdf") or "").replace("\\", "/")
    current_path = export_dir / current if current else None
    bugged = current_path is not None and _SOLTA_BUGGED.match(current_path.name) is not None
    if current_path is not None and current_path.is_file() and not bugged:
        return 0
    source: Path | None = None
    for candidate in (
        tenant_dir / str(row.get("invoice_url") or ""),
        current_path if current_path is not None else None,
    ):
        if candidate is not None and candidate.is_file():
            source = candidate
            break
    desired: str | None = None
    operation_date = _parse_date(row.get("operation_date"))
    if number == "2601616":
        desired = "SOLTA_LR_COLOR_FACTURE_2601616_2026-11-05.pdf"
    elif number:
        if side == SALES_SUBDIR:
            desired = normalized_sales_filename(
                number, operation_date, str(row.get("counterparty") or "")
            )
        else:
            desired = normalized_purchase_filename(
                str(row.get("counterparty") or ""),
                str(row.get("document_type") or "FACTURE"),
                number,
                operation_date,
            )
    if source is None:
        found = find_piece_for_document(export_dir, number, side=side)
        if found is None:
            return 0
        _update_row(
            store,
            tenant_id,
            row,
            piece_pdf=_piece_relative(export_dir, found),
        )
        return 1
    if desired:
        existing = pieces_root / desired
        if existing.is_file():
            _update_row(
                store,
                tenant_id,
                row,
                piece_pdf=_piece_relative(export_dir, existing),
            )
            return 1
    try:
        target = copy_unit_piece(
            source,
            export_dir,
            side=side,
            document_type=str(row.get("document_type") or "FACTURE"),
            document_number=number,
            document_date=operation_date,
            party=str(row.get("counterparty") or ""),
            filename=desired,
        )
    except Exception:  # noqa: BLE001 - one piece never blocks the repair
        return 0
    changes: dict[str, Any] = {
        "piece_pdf": _piece_relative(export_dir, target),
    }
    if bugged:
        ged_target = (
            tenant_dir
            / "03_VALIDE_PRET_FEC"
            / (
                "AVOIRS_FOURNISSEURS"
                if str(row.get("document_type") or "").upper() == "AVOIR"
                else "FACTURES_FOURNISSEURS"
            )
            / target.name
        )
        ged_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.resolve() != ged_target.resolve():
                shutil.copy2(source, ged_target)
            changes["invoice_url"] = ged_target.relative_to(tenant_dir).as_posix()
        except OSError:
            pass
    _update_row(store, tenant_id, row, **changes)
    return 1


def _ofx_source_pieces(
    rows: Iterable[dict[str, Any]]
) -> list[tuple[date, date, dict[str, Any]]]:
    sources: list[tuple[date, date, dict[str, Any]]] = []
    for row in rows:
        if str(row.get("document_type") or "").upper() != _SOURCE_OFX:
            continue
        match = _OFX_RANGE.search(str(row.get("invoice_url") or ""))
        if match is None:
            continue
        try:
            start = datetime.strptime(match.group(1), "%d%m%Y").date()
            end = datetime.strptime(match.group(2), "%d%m%Y").date()
        except ValueError:
            continue
        sources.append((start, end, row))
    return sources


def _repair_bank_fees(
    store: Any,
    tenant_id: str,
    rows: list[dict[str, Any]],
) -> int:
    sources = _ofx_source_pieces(rows)
    repaired = 0
    for row in rows:
        if str(row.get("counterparty") or "").upper() != _CAISSE_EPARGNE:
            continue
        if not _is_bank_row(row):
            continue
        if str(row.get("document_type") or "").upper() in _TECHNICAL_TYPES:
            continue
        debit = _decimal(row.get("debit_amount")) or Decimal("0.00")
        if debit <= 0:
            continue
        label = str(row.get("label") or "")
        if "REMISE ENCAISSEMENT" in _fold(label).upper():
            continue
        if not _FEE_LABEL.search(label):
            continue
        operation_date = _parse_date(row.get("operation_date"))
        source_row = None
        for start, end, candidate in sources:
            if operation_date is None or start <= operation_date <= end:
                source_row = candidate
                break
        if source_row is None and sources:
            source_row = sources[0][2]
        reference = (
            Path(str(source_row.get("invoice_url") or "")).name
            if source_row is not None
            else "RELEVE OFX"
        )
        changes: dict[str, Any] = {
            "status": "PAYE_PRELEVE",
            "reconciliation_status": "PAYE_PRELEVE",
            "reconciliation_date": str(row.get("operation_date") or ""),
            "reconciliation_reference": f"RELEVE OFX {reference}".strip(),
            "payment_reference": reference,
            "payment_method": "PRELEVEMENT",
            "balance_due": "0.00",
        }
        piece = str(source_row.get("piece_pdf") or "") if source_row else ""
        if piece:
            changes["piece_pdf"] = piece
        _update_row(store, tenant_id, row, **changes)
        repaired += 1
    return repaired


def _attach_source_statements(
    store: Any,
    tenant_id: str,
    export_dir: Path,
    rows: list[dict[str, Any]],
) -> int:
    """Give every bank row a local link to its OFX statement PDF."""
    sources = _ofx_source_pieces(rows)
    if not sources:
        return 0
    attached = 0
    for row in rows:
        if not _is_bank_row(row):
            continue
        if str(row.get("document_type") or "").upper() in _TECHNICAL_TYPES:
            continue
        current = str(row.get("piece_pdf") or "").replace("\\", "/")
        if current and (export_dir / current).is_file():
            continue
        operation_date = _parse_date(row.get("operation_date"))
        selected = None
        for start, end, candidate in sources:
            if operation_date is None or start <= operation_date <= end:
                selected = candidate
                break
        if selected is None:
            selected = sources[0][2]
        piece = str(selected.get("piece_pdf") or "").strip()
        if not piece:
            continue
        _update_row(store, tenant_id, row, piece_pdf=piece)
        attached += 1
    return attached


def repair_p27quinquies(
    store: Any,
    tenant_id: str,
    tenant_dir: str | os.PathLike[str],
    export_dir: str | os.PathLike[str],
) -> P27QuinquiesReport:
    """Apply every P27-quinquies SQLite and PDF repair (idempotent)."""
    tenant_path = Path(tenant_dir)
    export_path = Path(export_dir)
    tiers = TierReferenceService()
    tiers.bootstrap(write_missing=False)
    rows = store.list_master_lines(tenant_id)
    renamed = _canonicalise_tiers(store, tenant_id, rows, tiers)
    rows = store.list_master_lines(tenant_id)
    bosch = _repair_bosch(store, tenant_id, tenant_path, export_path, rows)
    rows = store.list_master_lines(tenant_id)
    openrouter = _repair_openrouter(
        store, tenant_id, tenant_path, export_path, rows
    )
    rows = store.list_master_lines(tenant_id)
    documents = _repair_generic_documents(
        store, tenant_id, tenant_path, export_path, rows
    )
    rows = store.list_master_lines(tenant_id)
    counterparts = _repair_counterparties_from_pdf(
        store, tenant_id, tenant_path, export_path, rows, tiers
    )
    rows = store.list_master_lines(tenant_id)
    dates = _fill_missing_dates(store, tenant_id, rows)
    rows = store.list_master_lines(tenant_id)
    boxtal = _link_boxtal(store, tenant_id, rows)
    rows = store.list_master_lines(tenant_id)
    fees = _repair_bank_fees(store, tenant_id, rows)
    rows = store.list_master_lines(tenant_id)
    statements = _attach_source_statements(
        store, tenant_id, export_path, rows
    )
    rows = store.list_master_lines(tenant_id)
    copied = 0
    for row in rows:
        if _is_bank_row(row) or _is_technical_row(row):
            continue
        copied += _ensure_local_piece(
            store, tenant_id, row, tenant_path, export_path
        )
    rows = store.list_master_lines(tenant_id)
    fiscal_2025 = 0
    for row in rows:
        operation_date = _parse_date(row.get("operation_date"))
        if operation_date is None or not _is_fiscal_2025(operation_date):
            continue
        if str(row.get("fiscal_year_target") or "").strip() == "2025":
            fiscal_2025 += 1
            continue
        if _is_bank_row(row):
            continue
        _update_row(store, tenant_id, row, fiscal_year_target="2025")
        fiscal_2025 += 1
    return P27QuinquiesReport(
        renamed_tiers=renamed,
        repaired_bosch=bosch,
        repaired_openrouter=openrouter,
        repaired_documents=documents,
        repaired_counterparties=counterparts,
        repaired_dates=dates,
        linked_boxtal=boxtal,
        copied_pieces=copied,
        linked_statements=statements,
        repaired_fee_rows=fees,
        fiscal_2025_rows=fiscal_2025,
    )


__all__ = [
    "MAX_SUBSET_SUM_PIECES",
    "P27QuinquiesReport",
    "repair_p27quinquies",
]
