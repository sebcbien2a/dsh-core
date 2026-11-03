"""P15.1 - Event bridge between accounting validation and the Obsidian vault.

The accounting pipeline (P14/P16) is the source of truth: a validated document
is persisted in ``SqliteAccountingStore`` and moved to
``03_VALIDE_PRET_FEC/<CATEGORIE>/`` before the vault is notified. This bridge
therefore *never* raises and never rolls back the accounting transaction: a
vault write failure (file open in Obsidian, disconnected OneDrive, read-only
vault) is logged as a non-blocking error and the pipeline continues.

For every ``VALIDE`` document the bridge:

- enriches ``04-graph/entities/{supplier}.md`` with an ``achat`` relation to the
  validated piece (``[[piece-...]]`` wikilink) and a summary carrying the last
  validation date, amount and category;
- appends a dated event in ``05-memoire/events/validation-{document_id}.md``.

Both notes go through :class:`~services.obsidian_vault_service.VaultWriter`, so
the human review layer applies: ``source: human``, ``locked: true``,
``status: preserved`` and any ``content_hash`` divergence leave the note
byte-identical (outcome ``preserved``). Supplier names are slugified
deterministically because vault paths only accept safe segments
(``ACME SAS`` -> ``acme-sas``).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from schemas.obsidian_vault_schema import NoteSource
from services.acompleter_workbook_service import derive_document_id
from services.obsidian_vault_service import VaultWriter, WriteResult

LOGGER = logging.getLogger(__name__)

STATUS_WRITTEN = "written"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

DEFAULT_EVENT_ENTITY = "comptabilite"
DEFAULT_CURRENCY = "EUR"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def entity_slug(value: object, *, fallback: str = "fournisseur-inconnu") -> str:
    """Deterministic vault-safe entity slug (``Établissements Müller`` -> ...).

    Accents are folded, every non-alphanumeric run becomes ``-`` and the result
    is lower-cased so the same supplier always targets the same note, including
    on case-insensitive Windows / OneDrive file systems.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    slug = _SLUG_RE.sub("-", text.casefold()).strip("-")
    if not slug:
        return fallback
    slug = slug[:64].strip("-")
    return slug or fallback


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_date(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _clean_amount(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return f"{value:.2f}"
    text = str(value).strip()
    if not text:
        return None
    try:
        return f"{Decimal(text.replace(',', '.')):.2f}"
    except (InvalidOperation, ValueError):
        return text


def _clean_currency(value: object) -> str:
    text = str(value or DEFAULT_CURRENCY).strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else DEFAULT_CURRENCY


@dataclass(frozen=True)
class VaultValidationRecord:
    """One validated accounting document to mirror into the vault."""

    tenant_id: str
    sha256: str
    nom_fichier: str
    supplier_name: str | None = None
    invoice_number: str | None = None
    issue_date: date | datetime | str | None = None
    amount_ht: Decimal | str | None = None
    amount_ttc: Decimal | str | None = None
    category: str | None = None
    document_id: str | None = None
    relative_path: str | None = None
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id",
            "sha256",
            "nom_fichier",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "supplier_name", _clean_text(self.supplier_name))
        object.__setattr__(self, "invoice_number", _clean_text(self.invoice_number))
        object.__setattr__(self, "issue_date", _clean_date(self.issue_date))
        object.__setattr__(self, "amount_ht", _clean_amount(self.amount_ht))
        object.__setattr__(self, "amount_ttc", _clean_amount(self.amount_ttc))
        object.__setattr__(self, "category", _clean_text(self.category))
        object.__setattr__(self, "relative_path", _clean_text(self.relative_path))
        object.__setattr__(self, "currency", _clean_currency(self.currency))

    @property
    def effective_document_id(self) -> str:
        """Stable document id, derived from the SHA-256 when not supplied."""
        return _clean_text(self.document_id) or derive_document_id(self.sha256)

    @property
    def event_id(self) -> str:
        return f"validation-{self.effective_document_id}"

    @property
    def piece_slug(self) -> str:
        reference = self.invoice_number or self.effective_document_id
        return f"piece-{entity_slug(reference, fallback='piece')}"

    @property
    def supplier_slug(self) -> str | None:
        if self.supplier_name is None:
            return None
        return entity_slug(self.supplier_name)

    def summary(self) -> str:
        parts = ["Pièce"]
        if self.invoice_number:
            parts.append(self.invoice_number)
        parts.append("validée")
        if self.issue_date:
            parts.append(f"le {self.issue_date}")
        if self.amount_ttc:
            parts.append(f"pour {self.amount_ttc} {self.currency} TTC")
        if self.category:
            parts.append(f"({self.category})")
        return " ".join(parts)

    def supplier_summary(self) -> str:
        details = ["Fournisseur suivi par le pont comptable."]
        details.append(f"Dernière pièce : {self.summary()}.")
        if self.relative_path:
            details.append(f"Dossier : {self.relative_path}.")
        return " ".join(details)


@dataclass(frozen=True)
class VaultBridgeReport:
    """Observable outcome of one vault notification (never raises)."""

    status: str = STATUS_SKIPPED
    supplier: str | None = None
    entity: WriteResult | None = None
    event: WriteResult | None = None
    errors: tuple[str, ...] = ()

    @property
    def written(self) -> bool:
        return self.event is not None and not self.errors

    @property
    def event_written(self) -> bool:
        return self.event is not None


class VaultAccountingBridge:
    """Mirror accounting validations into the Obsidian vault (non-blocking)."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        writer: VaultWriter | None = None,
        entity_name: str = DEFAULT_EVENT_ENTITY,
    ) -> None:
        self._writer = writer or VaultWriter(root)
        self._entity_name = entity_slug(entity_name, fallback=DEFAULT_EVENT_ENTITY)

    @property
    def writer(self) -> VaultWriter:
        return self._writer

    def notify_validated(self, record: VaultValidationRecord) -> VaultBridgeReport:
        """Export one validated document; every failure is captured, not raised."""
        supplier_slug: str | None = None
        entity_result: WriteResult | None = None
        event_result: WriteResult | None = None
        errors: list[str] = []

        try:
            supplier_slug = record.supplier_slug
        except Exception as exc:  # noqa: BLE001 - bridge must never break pipes
            errors.append(_error_detail("SUPPLIER", exc))
            LOGGER.warning("Vault supplier resolution failed: %s", exc)
            return VaultBridgeReport(status=STATUS_FAILED, errors=tuple(errors))

        if supplier_slug is not None:
            try:
                entity_result = self._writer.update_entity(
                    supplier_slug,
                    relations=[("achat", record.piece_slug)],
                    summary=record.supplier_summary(),
                    source=NoteSource.AGENT,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(_error_detail("ENTITY", exc))
                LOGGER.warning("Vault entity write failed: %s", exc)

        try:
            event_result = self._writer.write_event(
                record.event_id,
                entity=supplier_slug or self._entity_name,
                summary=record.summary(),
                occurred_at=record.issue_date,
                links=[record.piece_slug],
                source=NoteSource.AGENT,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(_error_detail("EVENT", exc))
            LOGGER.warning("Vault event write failed: %s", exc)

        if errors:
            status = STATUS_PARTIAL if (entity_result or event_result) else STATUS_FAILED
        elif event_result is not None:
            status = STATUS_WRITTEN
        else:
            status = STATUS_SKIPPED

        return VaultBridgeReport(
            status=status,
            supplier=supplier_slug,
            entity=entity_result,
            event=event_result,
            errors=tuple(errors),
        )


def _error_detail(scope: str, exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    label = code if isinstance(code, str) and code else type(exc).__name__.upper()
    message = str(exc).strip()
    return f"{scope}:{label}: {message}" if message and message != label else f"{scope}:{label}"


__all__ = [
    "DEFAULT_CURRENCY",
    "DEFAULT_EVENT_ENTITY",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_SKIPPED",
    "STATUS_WRITTEN",
    "VaultAccountingBridge",
    "VaultBridgeReport",
    "VaultValidationRecord",
    "entity_slug",
]
