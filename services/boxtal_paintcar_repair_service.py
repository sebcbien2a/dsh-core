"""P27-quater - Boxtal / Paintcar PDF and SQLite repair.

Boxtal invoices are paid by credit card at order time: their ``Net à payer`` is
always ``0,00 €`` while the accounting amount is the printed ``Total TTC``.
This service re-parses every Boxtal PDF, purges the historical ``0,00 €`` /
UUID duplicate rows, renames the pieces and letter the CB debit (invoices) or
CB credit (credit notes). It also repairs the Paintcar ``ILAF6F91`` amount and
keeps the intra-EU VAT regime explicit.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import DocumentCategory
from services.pdf_splitter_and_linker import (
    PURCHASES_SUBDIR,
    copy_unit_piece,
    normalized_purchase_filename,
    relative_link,
)
from services.settled_invoice_parser import (
    BOXTAL,
    SettledInvoiceError,
    parse_settled_invoice,
)

_CENT = Decimal("0.01")
_BOXTAL_NUMBER = re.compile(r"\b(2600\d{6,})\b")
_BOXTAL_HT = re.compile(
    r"Total\s*HT\s*[:\-]?\s*(-?[0-9][0-9\s.,]*)\s*(?:€|EUR)",
    re.IGNORECASE,
)
_BOXTAL_TVA = re.compile(
    r"Total\s*TVA\s*[:\-]?\s*(-?[0-9][0-9\s.,]*)\s*(?:€|EUR)",
    re.IGNORECASE,
)
_BOXTAL_TTC = re.compile(
    r"Total\s*TTC\s*[:\-]?\s*(-?[0-9][0-9\s.,]*)\s*(?:€|EUR)",
    re.IGNORECASE,
)
_PAINTCAR_BASE = re.compile(
    r"BASE\s*I\.?V\.?A\.?[\s\S]{0,400}?(\d{1,3}(?:\.\d{3})*,\d{2})",
    re.IGNORECASE,
)
_PAINTCAR_AMOUNT = re.compile(r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b")
_PAINTCAR_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_CREDIT_NOTE = re.compile(r"\bavoir\b|annulation|rembours", re.IGNORECASE)
_BANK_DATE = re.compile(r"FACT\s+(\d{2})(\d{2})(\d{2})", re.IGNORECASE)


@dataclass(frozen=True)
class RepairReport:
    boxtal_invoices: int = 0
    boxtal_credit_notes: int = 0
    boxtal_duplicates_removed: int = 0
    boxtal_bank_matches: int = 0
    paintcar_fixed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _decimal(value: Any) -> Decimal | None:
    text = str(value or "").replace("\u00a0", " ").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number.quantize(_CENT)


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
    except Exception:  # noqa: BLE001
        return ""


def _date_from_filename(path: Path) -> date | None:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", path.stem)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _boxtal_record_from_text(path: Path, text: str) -> dict[str, Any] | None:
    number_match = _BOXTAL_NUMBER.search(text) or _BOXTAL_NUMBER.search(path.name)
    if number_match is None:
        return None
    number = number_match.group(1)
    date_match = re.search(
        r"Facture\s+n[°o]?\s*" + re.escape(number)
        + r"\s+du\s+(\d{1,2})\s+([^\s]+)\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    invoice_date: date | None = None
    if date_match is not None:
        months = {
            "janvier": 1,
            "fevrier": 2,
            "mars": 3,
            "avril": 4,
            "mai": 5,
            "juin": 6,
            "juillet": 7,
            "aout": 8,
            "septembre": 9,
            "octobre": 10,
            "novembre": 11,
            "decembre": 12,
        }
        month_text = date_match.group(2).casefold()
        month = months.get(month_text)
        if month is None:
            for name, value in months.items():
                if name[:3] in month_text:
                    month = value
                    break
        if month:
            try:
                invoice_date = date(
                    int(date_match.group(3)), month, int(date_match.group(1))
                )
            except ValueError:
                invoice_date = None
    if invoice_date is None:
        invoice_date = _date_from_filename(path)
    ht = _decimal(match.group(1)) if (match := _BOXTAL_HT.search(text)) else None
    tva = _decimal(match.group(1)) if (match := _BOXTAL_TVA.search(text)) else None
    ttc = _decimal(match.group(1)) if (match := _BOXTAL_TTC.search(text)) else None
    is_credit = bool(_CREDIT_NOTE.search(text)) or (
        ttc is not None and ttc < 0
    )
    if ht is None or tva is None or ttc is None:
        return None
    if is_credit:
        ht, tva, ttc = -abs(ht), -abs(tva), -abs(ttc)
    return {
        "number": number,
        "date": invoice_date,
        "ht": ht,
        "tva": tva,
        "ttc": ttc,
        "is_credit": is_credit,
        "source": path,
    }


def _bank_date(label: str) -> date | None:
    match = _BANK_DATE.search(label or "")
    if match is None:
        return None
    day, month, year = (
        int(match.group(1)),
        int(match.group(2)),
        2000 + int(match.group(3)),
    )
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_boxtal_bank(row: dict[str, Any]) -> bool:
    text = f"{row.get('label') or ''} {row.get('counterparty') or ''}".casefold()
    return "boxtal" in text


def _find_bank_match(
    rows: list[dict[str, Any]],
    *,
    amount: Decimal,
    invoice_date: date | None,
    credit: bool,
) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any]] | None = None
    for row in rows:
        if not _is_boxtal_bank(row):
            continue
        if credit:
            value = _decimal(row.get("credit_amount"))
        else:
            value = _decimal(row.get("debit_amount"))
        if value is None or value != abs(amount):
            continue
        raw_date = str(row.get("operation_date") or "")[:10]
        try:
            bank_day = date.fromisoformat(raw_date)
        except ValueError:
            bank_day = _bank_date(str(row.get("label") or ""))
        if bank_day is None:
            continue
        delta = (
            (bank_day - invoice_date).days if invoice_date is not None else 0
        )
        if credit:
            if not (-3 <= delta <= 21):
                continue
            score = abs(delta - 10)
        else:
            fact_day = _bank_date(str(row.get("label") or ""))
            reference = fact_day or invoice_date
            if reference is not None:
                delta = (bank_day - reference).days
                if not (-3 <= delta <= 10):
                    continue
            score = abs(delta)
        if best is None or score < best[0]:
            best = (score, row)
    return best[1] if best else None


def repair_boxtal(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
) -> tuple[int, int, int, int]:
    ged_dir = tenant_dir / "03_VALIDE_PRET_FEC"
    pdfs = [
        path
        for path in ged_dir.rglob("*.pdf")
        if "boxtal" in path.name.casefold()
    ]
    records: dict[str, dict[str, Any]] = {}
    for path in pdfs:
        text = _pdf_text(path)
        record = _boxtal_record_from_text(path, text)
        if record is None:
            continue
        current = records.get(record["number"])
        if current is None or (
            path.name.upper().startswith("BOXTAL_FACTURE_")
            and not current["source"].name.upper().startswith("BOXTAL_FACTURE_")
        ):
            records[record["number"]] = record
    invoice_count = credit_count = matches = removed = 0
    master_rows = store.list_master_lines(tenant_id)
    for number, record in sorted(records.items()):
        existing = store.get_line(tenant_id, f"SETTLED_BOXTAL_{number}")
        target_line_id = f"SETTLED_BOXTAL_{number}"
        document_type = "AVOIR" if record["is_credit"] else "FACTURE"
        source: Path = record["source"]
        side = PURCHASES_SUBDIR
        filename = normalized_purchase_filename(
            BOXTAL,
            document_type,
            number,
            record["date"],
        )
        try:
            piece = copy_unit_piece(
                source,
                export_dir,
                side=side,
                document_type=document_type,
                document_number=number,
                document_date=record["date"],
                party=BOXTAL,
                filename=filename,
            )
            piece_rel = piece.relative_to(export_dir).as_posix()
        except Exception:
            piece_rel = ""
        ged_subdir = (
            "AVOIRS_FOURNISSEURS" if record["is_credit"] else "FACTURES_FOURNISSEURS"
        )
        ged_target = ged_dir / ged_subdir / filename
        ged_target.parent.mkdir(parents=True, exist_ok=True)
        if not ged_target.is_file():
            try:
                shutil.copy2(source, ged_target)
            except OSError:
                pass
        invoice_url = ged_target.relative_to(tenant_dir).as_posix()
        payment_method = "CARTE_BANCAIRE"
        line = dict(existing or {})
        line.pop("line_id", None)
        line.update(
            {
                "operation_date": record["date"].isoformat()
                if record["date"]
                else None,
                "label": (
                    f"{'Avoir' if record['is_credit'] else 'Facture'} "
                    f"BOXTAL {number} - "
                    f"{'Remboursement' if record['is_credit'] else 'Reglee'} "
                    f"({payment_method})"
                ),
                "counterparty": BOXTAL,
                "invoice_id": number,
                "category": DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
                "document_type": document_type,
                "amount_ht": str(record["ht"]),
                "amount_tva": str(record["tva"]),
                "amount_ttc": str(record["ttc"]),
                "status": "VALIDE",
                "payment_method": payment_method,
                "piece_pdf": piece_rel,
                "invoice_url": invoice_url,
                "already_paid_on_invoice": "0" if record["is_credit"] else "1",
                "balance_due": "0.00",
            }
        )
        bank = _find_bank_match(
            master_rows,
            amount=record["ttc"],
            invoice_date=record["date"],
            credit=record["is_credit"],
        )
        if bank is not None:
            line.update(
                {
                    "reconciliation_status": (
                        "REMBOURSE_RAPPROCHE"
                        if record["is_credit"]
                        else "PAYEE_RAPPROCHEE"
                    ),
                    "reconciliation_date": str(bank.get("operation_date") or ""),
                    "reconciliation_reference": str(
                        bank.get("label") or bank.get("line_id") or ""
                    ),
                    "reconciliation_amount": str(abs(record["ttc"])),
                    "fitid": str(bank.get("fitid") or bank.get("line_id") or ""),
                    "payment_reference": str(
                        bank.get("fitid") or bank.get("line_id") or ""
                    ),
                }
            )
            bank_updated = dict(bank)
            bank_line_id = str(bank_updated.pop("line_id", ""))
            if bank_line_id:
                bank_updated.update(
                    {
                        "counterparty": BOXTAL,
                        "invoice_id": number,
                        "reconciliation_status": (
                            "REMBOURSE_RAPPROCHE"
                            if record["is_credit"]
                            else "PAYEE_RAPPROCHEE"
                        ),
                        "reconciliation_date": str(
                            bank.get("operation_date") or ""
                        ),
                        "reconciliation_reference": f"BOXTAL {number}",
                        "reconciliation_amount": str(abs(record["ttc"])),
                        "balance_due": "0.00",
                    }
                )
                try:
                    store.force_upsert_document_line(
                        tenant_id, bank_line_id, bank_updated
                    )
                except Exception:
                    pass
            matches += 1
        store.force_upsert_document_line(tenant_id, target_line_id, line)
        if record["is_credit"]:
            credit_count += 1
        else:
            invoice_count += 1
        for row in master_rows:
            line_id = str(row.get("line_id") or "")
            if not line_id or line_id == target_line_id:
                continue
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("invoice_id", "label", "invoice_url", "piece_pdf")
            ).upper()
            if number in haystack or (
                str(row.get("counterparty") or "").upper() == BOXTAL
                and str(row.get("amount_ttc") or "") in {"", "0", "0.0", "0.00"}
                and not str(row.get("operation_date") or "").strip()
            ):
                if store.delete_line(tenant_id, line_id):
                    removed += 1
    return invoice_count, credit_count, removed, matches


def repair_paintcar(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
) -> int:
    fixed = 0
    rows = store.list_master_lines(tenant_id)
    for row in rows:
        line_id = str(row.get("line_id") or "")
        invoice = str(row.get("invoice_id") or "")
        if not line_id or invoice.upper() != "ILAF6F91":
            continue
        source: Path | None = None
        for candidate in (
            tenant_dir / str(row.get("invoice_url") or ""),
            export_dir / str(row.get("piece_pdf") or ""),
        ):
            if candidate.is_file():
                source = candidate
                break
        text = _pdf_text(source) if source is not None else ""
        base_match = _PAINTCAR_BASE.search(text)
        amount = _decimal(base_match.group(1)) if base_match else None
        if amount is None:
            amounts = [
                _decimal(value)
                for value in _PAINTCAR_AMOUNT.findall(text)
            ]
            valid = [value for value in amounts if value is not None]
            amount = max(valid) if valid else None
        if amount is None or amount <= 0:
            continue
        date_match = _PAINTCAR_DATE.search(text)
        operation_date = None
        if date_match is not None:
            try:
                operation_date = date(
                    int(date_match.group(3)),
                    int(date_match.group(2)),
                    int(date_match.group(1)),
                )
            except ValueError:
                operation_date = None
        line = dict(row)
        line.pop("line_id", None)
        line.update(
            {
                "operation_date": operation_date.isoformat()
                if operation_date
                else row.get("operation_date"),
                "amount_ht": str(amount),
                "amount_tva": "0.00",
                "amount_ttc": str(amount),
                "document_type": "FACTURE",
                "status": "VALIDE",
                "is_classified_marker": (
                    "TIER:PAINTCAR:FOURNISSEUR:AUTOLIQUIDATION_UE_0%"
                ),
            }
        )
        store.force_upsert_document_line(tenant_id, line_id, line)
        fixed += 1
    return fixed


def repair_boxtal_and_paintcar(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
) -> RepairReport:
    invoices, credits, removed, matches = repair_boxtal(
        store, tenant_id, tenant_dir, export_dir
    )
    paintcar = repair_paintcar(store, tenant_id, tenant_dir, export_dir)
    return RepairReport(
        boxtal_invoices=invoices,
        boxtal_credit_notes=credits,
        boxtal_duplicates_removed=removed,
        boxtal_bank_matches=matches,
        paintcar_fixed=paintcar,
    )


__all__ = [
    "RepairReport",
    "repair_boxtal",
    "repair_boxtal_and_paintcar",
    "repair_paintcar",
]
