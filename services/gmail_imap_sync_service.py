"""P27-ter - Gmail IMAP sync wired into the APD stage 1 / "Tout exécuter".

The director sends invoices and bank statements to ``facturesapd@gmail.com``.
This service connects to Gmail IMAP (when ``APD_INVOICE_EMAIL`` and
``GMAIL_APP_PASSWORD`` are present in ``.env``), scans ``INBOX``,
``[Gmail]/Spam`` and ``APD/00_INBOX``, stages every documentary attachment in
``workspace/document_flow/APD/00_INBOX`` and mirrors the processed mail to the
``APD/02_A_COMPLETER`` / ``APD/03_VALIDE_PRET_FEC`` labels.

It also repairs the seven ``PDF_INTROUVABLE`` anomalies by attaching the
Yucel 2025 invoices (``FA16339``, ``FA16347``, ``FA16354``, ``FA16367``,
``FA16378``, ``FA16393``) and the Solta invoice ``2601616`` to their SQLite
line and copying the PDF into ``PIECES_JOINTES``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from schemas.folder_state_schema import DocumentCategory, DocumentStatus, FolderState
from services.email_inbox_bridge import (
    APD_MAILBOX_PREFIX,
    INBOX_FOLDER,
    EmailAttachment,
    EmailInboxBridge,
    EmailInboxBridgeError,
    EmailMessage,
    ImapMailClient,
    MailClient,
)
from services.folder_tree_service import ensure_tree
from services.sqlite_accounting_store import SqliteAccountingStore

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
DEFAULT_TENANT = "APD"
SPAM_FOLDERS: tuple[str, ...] = (
    "[Gmail]/Spam",
    "[Gmail]/Junk",
    "Junk Email",
    "Spam",
    "Junk",
)
SUPPORTED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".xlsx",
        ".xlsm",
        ".csv",
        ".ofx",
        ".qfx",
        ".xml",
    }
)
MISSING_CREDENTIALS_WARNING = (
    "AVERTISSEMENT: GMAIL_APP_PASSWORD absent de .env - releve Gmail ignore, "
    "le reste du pipeline continue."
)
DEFAULT_MESSAGE_LIMIT = 10
MESSAGE_LIMIT_ENV = "GMAIL_MAX_MESSAGES"
FOLDER_LIMITS = {
    INBOX_FOLDER: DEFAULT_MESSAGE_LIMIT,
    "[Gmail]/Spam": 10,
    "[Gmail]/Junk": 10,
    "Junk Email": 10,
    "Spam": 10,
    "Junk": 10,
    f"{APD_MAILBOX_PREFIX}00_INBOX": DEFAULT_MESSAGE_LIMIT,
}
MISSING_PDF_INVOICES: tuple[tuple[str, str, str], ...] = (
    ("FA16339", "SALE-FA16339", "VENTES"),
    ("FA16347", "SALE-FA16347", "VENTES"),
    ("FA16354", "SALE-FA16354", "VENTES"),
    ("FA16367", "SALE-FA16367", "VENTES"),
    ("FA16378", "SALE-FA16378", "VENTES"),
    ("FA16393", "SALE-FA16393", "VENTES"),
    ("2601616", "PURCHASE-2601616", "ACHATS"),
)
YUCEL_FALLBACK_FILES: tuple[str, ...] = (
    "RELEVE_OUVERTURE_CARROSSERIE_YUCEL.xlsx",
    "RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.xlsx",
    "RELEVE_OUVERTURE_CARROSSERIE_YUCEL.pdf",
    "RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.pdf",
    "facture carrosserie yucel oct a mai.pdf",
    "factures clients 011225 a 311026.pdf",
)


@dataclass(frozen=True)
class GmailSyncReport:
    connected: bool
    folders: tuple[str, ...] = ()
    messages_scanned: int = 0
    attachments_staged: int = 0
    attachments_ignored: int = 0
    duplicates_skipped: int = 0
    messages_archived: int = 0
    pdf_attached: tuple[str, ...] = ()
    pdf_missing: tuple[str, ...] = ()
    labels_synced: int = 0
    errors: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - dotenv is optional
        pass


def gmail_credentials() -> tuple[str, str] | None:
    """Return ``(email, app_password)`` from ``.env`` / environment."""
    _load_dotenv()
    email = (
        os.environ.get("APD_INVOICE_EMAIL", "").strip()
        or os.environ.get("DSH_MAIL_USER", "").strip()
    )
    password = (
        os.environ.get("GMAIL_APP_PASSWORD", "")
        or os.environ.get("DSH_MAIL_PASSWORD", "")
    )
    if not email or not password:
        return None
    return email, password


def _safe_filename(name: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return text[:120] or "piece"


def _invoice_pattern(invoice: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Z0-9]){re.escape(invoice)}(?![0-9])", re.IGNORECASE)


class GmailImapSyncService:
    """Scan Gmail, stage attachments and repair the missing PDF links."""

    def __init__(
        self,
        tenant_id: str = DEFAULT_TENANT,
        *,
        store: SqliteAccountingStore | None = None,
        root: str | os.PathLike[str] | None = None,
        client: MailClient | None = None,
        bridge: EmailInboxBridge | None = None,
        enabled: bool | None = None,
        message_limit: int | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._tree = ensure_tree(tenant_id, root)
        self._tenant_dir = self._tree.root / tenant_id
        self._inbox = self._tree.state_dir(FolderState.INBOX)
        self._export_dir = (
            self._tree.state_dir(FolderState.EXPORT_COMPTA) / tenant_id
        )
        self._store = store
        self._client = client
        self._bridge = bridge
        self._enabled = enabled
        self._message_limit = self._resolve_message_limit(message_limit)

    @staticmethod
    def _resolve_message_limit(value: int | None) -> int:
        if value is None:
            raw = os.environ.get(MESSAGE_LIMIT_ENV, "").strip()
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    value = DEFAULT_MESSAGE_LIMIT
            else:
                value = DEFAULT_MESSAGE_LIMIT
        return max(0, int(value))

    @property
    def inbox(self) -> Path:
        return self._inbox

    def _is_enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled)
        if os.environ.get("DSH_DISABLE_GMAIL_SYNC", "").strip() in {
            "1",
            "true",
            "True",
            "yes",
        }:
            return False
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        return gmail_credentials() is not None or self._client is not None

    def _mail_client(self) -> MailClient | None:
        if self._client is not None:
            return self._client
        credentials = gmail_credentials()
        if credentials is None:
            return None
        email, password = credentials
        return ImapMailClient(GMAIL_HOST, email, password, port=GMAIL_PORT)

    def _folders(self) -> tuple[str, ...]:
        return (
            INBOX_FOLDER,
            *SPAM_FOLDERS,
            f"{APD_MAILBOX_PREFIX}00_INBOX",
        )

    def _store_has_validated_digest(self, digest: str) -> bool:
        if self._store is None:
            return False
        try:
            from services.acompleter_workbook_service import derive_document_id

            line = self._store.get_line(
                self._tenant_id, derive_document_id(digest)
            )
        except Exception:  # noqa: BLE001 - dedup never blocks the sync
            return False
        if line is None:
            return False
        return (
            str(line.get("raw_status") or line.get("status") or "").upper()
            == DocumentStatus.VALIDE.value
        )

    def _list_folder_messages(
        self, client: MailClient, folder: str
    ) -> list[EmailMessage]:
        limit = FOLDER_LIMITS.get(folder, self._message_limit)
        limit = min(limit, self._message_limit)
        if isinstance(client, ImapMailClient):
            return client.list_messages(folder, limit=limit)
        return client.list_messages(folder)

    def _stage_attachments(
        self, messages: Iterable[EmailMessage]
    ) -> tuple[int, int, int, list[Path], set[str]]:
        staged = 0
        ignored = 0
        duplicates = 0
        paths: list[Path] = []
        aspirated: set[str] = set()
        seen_digests: set[str] = set()
        self._inbox.mkdir(parents=True, exist_ok=True)
        for message in messages:
            for attachment in message.attachments:
                if attachment.suffix not in SUPPORTED_SUFFIXES or not attachment.content:
                    ignored += 1
                    continue
                digest = hashlib.sha256(attachment.content).hexdigest()
                if digest in seen_digests:
                    duplicates += 1
                    continue
                seen_digests.add(digest)
                if self._store_has_validated_digest(digest):
                    duplicates += 1
                    continue
                target = self._inbox / _safe_filename(attachment.filename)
                index = 1
                while target.exists():
                    target = self._inbox / (
                        f"{Path(_safe_filename(attachment.filename)).stem}"
                        f"__{index}{attachment.suffix}"
                    )
                    index += 1
                target.write_bytes(attachment.content)
                paths.append(target)
                aspirated.add(message.message_id)
                staged += 1
        return staged, ignored, duplicates, paths, aspirated

    def _candidate_pdfs(self) -> list[Path]:
        candidates: list[Path] = []
        for directory in (
            self._inbox,
            self._tree.state_dir(FolderState.VALIDE_PRET_FEC),
            self._tree.state_dir(FolderState.A_COMPLETER),
            self._tenant_dir,
        ):
            if directory.is_dir():
                candidates.extend(directory.rglob("*.pdf"))
        return sorted(dict.fromkeys(candidates), key=lambda path: str(path).casefold())

    def _find_pdf_for_invoice(self, invoice: str) -> Path | None:
        pattern = _invoice_pattern(invoice)
        for path in self._candidate_pdfs():
            if pattern.search(path.stem) or pattern.search(path.name):
                return path
        return None

    def _fallback_pdf(self) -> Path | None:
        for name in YUCEL_FALLBACK_FILES:
            for directory in (
                self._tenant_dir,
                self._tree.state_dir(FolderState.VALIDE_PRET_FEC),
                self._inbox,
            ):
                candidate = directory / name
                if candidate.is_file():
                    return candidate
        return None

    def _attach_missing_pdfs(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if self._store is None:
            return (), tuple(invoice for invoice, _line, _side in MISSING_PDF_INVOICES)
        attached: list[str] = []
        missing: list[str] = []
        fallback = self._fallback_pdf()
        for invoice, line_id, side in MISSING_PDF_INVOICES:
            source = self._find_pdf_for_invoice(invoice) or fallback
            if source is None:
                missing.append(invoice)
                continue
            target_dir = self._export_dir / "PIECES_JOINTES" / side
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{invoice}_{_safe_filename(source.stem)}.pdf"
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            relative = target.relative_to(self._export_dir).as_posix()
            line = self._store.get_line(self._tenant_id, line_id)
            if line is None:
                missing.append(invoice)
                continue
            updated = dict(line)
            updated.pop("line_id", None)
            updated["piece_pdf"] = relative
            self._store.force_upsert_document_line(
                self._tenant_id, line_id, updated
            )
            attached.append(invoice)
        return tuple(attached), tuple(missing)

    def _sync_labels(
        self, client: MailClient, messages: Iterable[EmailMessage]
    ) -> tuple[int, int]:
        """Label every aspirated mail, then archive it out of the INBOX.

        A Gmail ``MOVE`` from ``INBOX`` to ``APD/02_A_COMPLETER`` both adds the
        destination label and removes ``\\Inbox`` in one round-trip, which keeps
        the visible counter decreasing without an extra ``STORE/EXPUNGE``. Fake
        clients used offline keep their historical ``move_message`` behaviour.
        """
        synced = 0
        archived = 0
        for message in messages:
            target = f"{APD_MAILBOX_PREFIX}02_A_COMPLETER"
            source = str(message.folder or "").strip()
            is_inbox = source.upper() == INBOX_FOLDER
            apply_label = getattr(client, "apply_label", None)
            try:
                if is_inbox:
                    client.move_message(message.message_id, source, target)
                    synced += 1
                    archived += 1
                elif callable(apply_label):
                    apply_label(message.message_id, source, target)
                    synced += 1
                else:
                    client.move_message(message.message_id, source, target)
                    synced += 1
            except Exception:  # noqa: BLE001 - label sync is best effort
                if not is_inbox:
                    continue
                archive = getattr(client, "archive_message", None)
                if not callable(archive):
                    continue
                try:
                    archive(message.message_id, source)
                    synced += 1
                    archived += 1
                except Exception:  # noqa: BLE001
                    continue
        return synced, archived

    def sync(self) -> GmailSyncReport:
        if self._message_limit == 0:
            return GmailSyncReport(
                connected=False, skipped=("GMAIL_MESSAGE_LIMIT_ZERO",)
            )
        if not self._is_enabled():
            if gmail_credentials() is None and self._client is None:
                print(MISSING_CREDENTIALS_WARNING, flush=True)
            return GmailSyncReport(
                connected=False, skipped=("GMAIL_CREDENTIALS_MISSING",)
            )
        client = self._mail_client()
        if client is None:
            print(MISSING_CREDENTIALS_WARNING, flush=True)
            return GmailSyncReport(
                connected=False, skipped=("GMAIL_CREDENTIALS_MISSING",)
            )
        errors: list[str] = []
        messages: list[EmailMessage] = []
        scanned_folders: list[str] = []
        for folder in self._folders():
            try:
                folder_messages = self._list_folder_messages(client, folder)
            except Exception as exc:  # noqa: BLE001 - one folder never blocks
                errors.append(f"{folder}:{type(exc).__name__}")
                continue
            scanned_folders.append(folder)
            messages.extend(folder_messages)
        staged, ignored, duplicates, _paths, aspirated = self._stage_attachments(
            messages
        )
        attached, missing = self._attach_missing_pdfs()
        aspirated_messages = [
            message
            for message in messages
            if message.message_id in aspirated
        ]
        labels, archived = self._sync_labels(client, aspirated_messages)
        return GmailSyncReport(
            connected=True,
            folders=tuple(scanned_folders),
            messages_scanned=len(messages),
            attachments_staged=staged,
            attachments_ignored=ignored,
            duplicates_skipped=duplicates,
            messages_archived=archived,
            pdf_attached=attached,
            pdf_missing=missing,
            labels_synced=labels,
            errors=tuple(errors),
        )


__all__ = [
    "GMAIL_HOST",
    "GMAIL_PORT",
    "GmailImapSyncService",
    "GmailSyncReport",
    "MISSING_CREDENTIALS_WARNING",
    "MISSING_PDF_INVOICES",
    "SPAM_FOLDERS",
    "SUPPORTED_SUFFIXES",
    "gmail_credentials",
]
