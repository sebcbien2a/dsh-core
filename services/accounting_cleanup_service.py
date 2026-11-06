"""P25 - Non-destructive accounting cleanup for dates, phantoms and filenames."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import DocumentCategory, FolderState

_SUFFIX = re.compile(r"(?:__\d+){2,}(?=\.pdf$)", re.IGNORECASE)
_DATE_IN_NAME = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_FA_NUMBER = re.compile(r"FA(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class CleanupReport:
    dates_fixed: int = 0
    phantom_lines_removed: int = 0
    pdfs_renamed: int = 0
    default_workbooks_removed: int = 0
    duplicates_quarantined: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


def _default_open_date(invoice: str) -> str:
    match = _FA_NUMBER.search(invoice)
    if match and int(match.group(1)) < 16406:
        return "2025-10-31"
    return ""


def normalize_missing_dates(store: Any, tenant_id: str) -> int:
    """Fill missing invoice dates and FA16 opening dates without touching paid ones."""
    fixed = 0
    for row in store.list_master_lines(tenant_id):
        category = str(row.get("category") or "")
        if category != DocumentCategory.VENTES_CLIENTS.value:
            continue
        if str(row.get("operation_date") or "").strip():
            continue
        invoice = str(row.get("invoice_id") or "")
        date_value = ""
        for candidate in (
            row.get("origin_document_date"),
            row.get("reconciliation_date"),
        ):
            text = str(candidate or "").strip()
            if text:
                date_value = text
                break
        if not date_value:
            match = _DATE_IN_NAME.search(str(row.get("invoice_url") or ""))
            if match:
                date_value = match.group(1)
        if not date_value:
            date_value = _default_open_date(invoice)
        if not date_value:
            continue
        line_id = str(row.get("line_id") or "")
        line = store.get_line(tenant_id, line_id)
        if line is None:
            continue
        updated = dict(line)
        updated.pop("line_id", None)
        updated["operation_date"] = date_value
        store.upsert_document_line(tenant_id, line_id, updated)
        fixed += 1
    return fixed


def dedupe_phantom_lines(store: Any, tenant_id: str) -> int:
    """Remove amount-less/date-less rows when a richer row has the same piece."""
    rows = store.list_master_lines(tenant_id)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get("invoice_id") or "").strip().casefold()
        if not key:
            label = str(row.get("label") or "")
            match = re.search(r"\b(2[0-9]{9,})\b", label)
            key = match.group(1) if match else ""
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    removed = 0
    for key, items in groups.items():
        if len(items) < 2:
            continue
        rich = [
            row
            for row in items
            if any(
                str(row.get(field) or "").strip() not in {"", "0", "0.0", "0.00"}
                for field in ("amount_ht", "amount_tva", "amount_ttc", "operation_date")
            )
        ]
        if not rich:
            continue
        for row in items:
            if row in rich:
                continue
            line_id = str(row.get("line_id") or "")
            if not line_id:
                continue
            if store.delete_line(tenant_id, line_id):
                removed += 1
    return removed


def clean_pdf_suffixes(
    tenant_dir: Path, store: Any, tenant_id: str
) -> tuple[int, int, tuple[str, ...]]:
    """Rename ``__1__1.pdf`` artefacts and keep paths/SQLite in sync."""
    archive = tree_archive = (
        tenant_dir / "05_ARCHIVE_ERREUR" / "DOUBLONS_PDF"
    )
    renamed = 0
    quarantined = 0
    errors: list[str] = []
    for path in tenant_dir.rglob("*.pdf"):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        clean_name = _SUFFIX.sub("", path.name)
        if clean_name == path.name:
            continue
        target = path.with_name(clean_name)
        if target.exists():
            try:
                same = _file_sha256(path) == _file_sha256(target)
            except OSError:
                same = False
            if same:
                archive.mkdir(parents=True, exist_ok=True)
                destination = archive / path.name
                index = 1
                while destination.exists():
                    destination = archive / f"{path.stem}__{index}{path.suffix}"
                    index += 1
                try:
                    shutil.move(str(path), str(destination))
                    quarantined += 1
                except OSError as exc:
                    errors.append(f"{path.name}:{exc}")
                continue
            target = target.with_name(
                f"{target.stem}__clean{target.suffix}"
            )
        try:
            path.rename(target)
        except OSError as exc:
            errors.append(f"{path.name}:{exc}")
            continue
        renamed += 1
        old_name = path.name
        for row in store.list_master_lines(tenant_id):
            line_id = str(row.get("line_id") or "")
            if not line_id:
                continue
            updated = None
            for field in ("piece_pdf", "invoice_url"):
                value = str(row.get(field) or "")
                if not value or Path(value).name != old_name:
                    continue
                if updated is None:
                    line = store.get_line(tenant_id, line_id)
                    if line is None:
                        break
                    updated = dict(line)
                    updated.pop("line_id", None)
                parent = Path(value).parent
                updated[field] = (
                    str(Path(parent) / target.name).replace("\\", "/")
                )
            if updated is not None:
                store.upsert_document_line(tenant_id, line_id, updated)
    return renamed, quarantined, tuple(errors)


def remove_default_workbooks(export_dir: Path) -> int:
    """Delete legacy ``*_default_*.xlsx`` workbooks from the active export dir."""
    removed = 0
    if not export_dir.is_dir():
        return 0
    for path in export_dir.glob("*_default_*.xlsx"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def run_cleanup(
    store: Any,
    tenant_id: str,
    tenant_dir: Path,
    export_dir: Path,
) -> CleanupReport:
    dates = normalize_missing_dates(store, tenant_id)
    phantoms = dedupe_phantom_lines(store, tenant_id)
    renamed, quarantined, errors = clean_pdf_suffixes(
        tenant_dir, store, tenant_id
    )
    defaults = remove_default_workbooks(export_dir)
    return CleanupReport(
        dates_fixed=dates,
        phantom_lines_removed=phantoms,
        pdfs_renamed=renamed,
        default_workbooks_removed=defaults,
        duplicates_quarantined=quarantined,
        errors=errors,
    )


__all__ = [
    "CleanupReport",
    "clean_pdf_suffixes",
    "dedupe_phantom_lines",
    "normalize_missing_dates",
    "remove_default_workbooks",
    "run_cleanup",
]
