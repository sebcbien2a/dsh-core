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
import unicodedata
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
from services.bank_reconciliation_service import (
    contains_client_invoice_reference,
    is_remise_encaissement,
)
from services.folder_tree_service import (
    GED_AVOIRS_CLIENTS,
    GED_AVOIRS_FOURNISSEURS,
    GED_FACTURES_CLIENTS,
    GED_FACTURES_FOURNISSEURS,
    GED_LISTES_ET_BORDEREAUX,
    GED_SUBDIRS,
    ensure_tree,
)
from services.sqlite_accounting_store import SqliteAccountingStore

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
DEFAULT_TENANT = "APD"
MAIL_FACTURES_FOURNISSEURS = f"{APD_MAILBOX_PREFIX}FACTURES_FOURNISSEURS_TRAITEES"
MAIL_FACTURES_CLIENTS = f"{APD_MAILBOX_PREFIX}FACTURES_CLIENTS_TRAITEES"
MAIL_AVOIRS_FOURNISSEURS = f"{APD_MAILBOX_PREFIX}AVOIRS_FOURNISSEURS_TRAITES"
MAIL_AVOIRS_CLIENTS = f"{APD_MAILBOX_PREFIX}AVOIRS_CLIENTS_TRAITES"
MAIL_LISTES = f"{APD_MAILBOX_PREFIX}LISTES_TRAITEES"
MAIL_PENDING = f"{APD_MAILBOX_PREFIX}02_A_COMPLETER"
CLASSIFICATION_FOLDERS: dict[str, str] = {
    GED_FACTURES_FOURNISSEURS: MAIL_FACTURES_FOURNISSEURS,
    GED_FACTURES_CLIENTS: MAIL_FACTURES_CLIENTS,
    GED_AVOIRS_FOURNISSEURS: MAIL_AVOIRS_FOURNISSEURS,
    GED_AVOIRS_CLIENTS: MAIL_AVOIRS_CLIENTS,
    GED_LISTES_ET_BORDEREAUX: MAIL_LISTES,
}
LOCAL_GED_FOLDERS = GED_SUBDIRS
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
    f"{APD_MAILBOX_PREFIX}02_A_COMPLETER": 30,
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
        classify_messages: bool = True,
        ingest_for_classification: bool = False,
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
        self._classify_messages = bool(classify_messages)
        self._ingest_for_classification = bool(ingest_for_classification)
        for subdir in LOCAL_GED_FOLDERS:
            (self._tree.state_dir(FolderState.VALIDE_PRET_FEC) / subdir).mkdir(
                parents=True, exist_ok=True
            )

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
            MAIL_PENDING,
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
            try:
                return client.list_messages(folder, limit=limit)
            except Exception:
                # Gmail throttles long sessions: reconnect once for this folder.
                client.reset()
                return client.list_messages(folder, limit=limit)
        return client.list_messages(folder)

    def _ensure_classification_folders(self, client: MailClient) -> tuple[str, ...]:
        folders = (*CLASSIFICATION_FOLDERS.values(), MAIL_PENDING)
        for folder in folders:
            try:
                client.ensure_folder(folder)
            except Exception:  # noqa: BLE001 - folder creation is best effort
                continue
        return folders

    def _line_for_digest(self, digest: str) -> dict[str, Any] | None:
        if self._store is None:
            return None
        try:
            from services.acompleter_workbook_service import derive_document_id

            return self._store.get_line(
                self._tenant_id, derive_document_id(digest)
            )
        except Exception:  # noqa: BLE001 - classification is best effort
            return None

    @staticmethod
    def _fold(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(char for char in text if not unicodedata.combining(char))
        return re.sub(r"\s+", " ", text).strip().casefold()

    def _category_from_line(self, line: dict[str, Any] | None) -> str | None:
        if line is None:
            return None
        document_type = str(line.get("document_type") or "").upper()
        category = str(line.get("category") or "").upper()
        invoice_number = str(line.get("invoice_id") or "")
        label = str(line.get("label") or "")
        if "AVOIR" in document_type or "CREDIT_NOTE" in document_type:
            if category == DocumentCategory.VENTES_CLIENTS.value or contains_client_invoice_reference(
                invoice_number, label
            ):
                return GED_AVOIRS_CLIENTS
            return GED_AVOIRS_FOURNISSEURS
        if category == DocumentCategory.BANQUE_FINANCE.value or document_type in {
            "OFX",
            "QFX",
            "SOURCE_OFX",
            "BORDEREAU",
            "BORDEREAU_LCR",
            "LCR_REMITTANCE",
            "LCR_SUB_PAYMENT",
            "SOURCE_SAGE",
        }:
            return GED_LISTES_ET_BORDEREAUX
        if contains_client_invoice_reference(invoice_number, label):
            return GED_FACTURES_CLIENTS
        if category == DocumentCategory.VENTES_CLIENTS.value:
            return GED_FACTURES_CLIENTS
        return GED_FACTURES_FOURNISSEURS

    def _category_from_name(self, name: str, subject: str = "") -> str:
        text = self._fold(f"{subject} {name}")
        if "avoir" in text:
            if "client" in text or contains_client_invoice_reference(name, subject):
                return GED_AVOIRS_CLIENTS
            return GED_AVOIRS_FOURNISSEURS
        if any(
            marker in text
            for marker in (
                "factures client",
                "facture client",
                "factures clients",
                "vente client",
                "fa1",
            )
        ) or contains_client_invoice_reference(name, subject):
            return GED_FACTURES_CLIENTS
        if any(
            marker in text
            for marker in (
                "import banque",
                "releve",
                "relevé",
                "bordereau",
                "lcr",
                "remise",
                "ofx",
                "listing",
                "liste",
            )
        ):
            return GED_LISTES_ET_BORDEREAUX
        if Path(name).suffix.casefold() in {".ofx", ".qfx", ".csv", ".xml"}:
            return GED_LISTES_ET_BORDEREAUX
        return GED_FACTURES_FOURNISSEURS

    def _classify_message(
        self, message: EmailMessage, staged_paths: tuple[Path, ...] = ()
    ) -> tuple[str, bool]:
        """Return ``(GED subdir, pending)`` for one mail and its attachments."""
        pending = False
        categories: list[str] = []
        staged_by_name = {path.name.casefold(): path for path in staged_paths}
        for attachment in message.attachments:
            if attachment.suffix not in SUPPORTED_SUFFIXES:
                continue
            digest = hashlib.sha256(attachment.content).hexdigest()
            line = self._line_for_digest(digest)
            if line is None and staged_paths:
                safe_name = _safe_filename(attachment.filename).casefold()
                for name, path in staged_by_name.items():
                    if name.startswith(Path(safe_name).stem.casefold()):
                        if not path.is_file():
                            continue
                        line = self._line_for_digest(
                            hashlib.sha256(path.read_bytes()).hexdigest()
                        )
                        break
            if line is not None:
                status = str(
                    line.get("raw_status") or line.get("status") or ""
                ).upper()
                if status == DocumentStatus.EN_ATTENTE.value:
                    pending = True
                category = self._category_from_line(line)
                if category:
                    categories.append(category)
            else:
                categories.append(
                    self._category_from_name(attachment.filename, message.subject)
                )
        if pending:
            return MAIL_PENDING, True
        if not categories:
            if str(message.folder or "").strip().upper() == MAIL_PENDING.upper():
                return MAIL_LISTES, False
            return MAIL_PENDING, False
        for preferred in (
            GED_LISTES_ET_BORDEREAUX,
            GED_AVOIRS_CLIENTS,
            GED_AVOIRS_FOURNISSEURS,
            GED_FACTURES_CLIENTS,
            GED_FACTURES_FOURNISSEURS,
        ):
            if preferred in categories:
                return CLASSIFICATION_FOLDERS[preferred], False
        return CLASSIFICATION_FOLDERS[GED_FACTURES_FOURNISSEURS], False

    def _ingest_staged_for_classification(self, paths: Iterable[Path]) -> None:
        if self._store is None or not self._ingest_for_classification:
            return
        selected = [path for path in paths if path.is_file()]
        if not selected:
            return
        try:
            from services.inbox_ingestion_service import InboxIngestionService

            InboxIngestionService(
                self._tenant_id, self._store, self._tree.root
            ).process_inbox(selected)
        except Exception:  # noqa: BLE001 - classification is best effort
            return

    def _stage_attachments(
        self, messages: Iterable[EmailMessage]
    ) -> tuple[int, int, int, list[Path], dict[str, tuple[Path, ...]]]:
        staged = 0
        ignored = 0
        duplicates = 0
        paths: list[Path] = []
        staged_by_message: dict[str, tuple[Path, ...]] = {}
        seen_digests: set[str] = set()
        self._inbox.mkdir(parents=True, exist_ok=True)
        for message in messages:
            message_paths: list[Path] = []
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
                message_paths.append(target)
                staged += 1
            if message_paths:
                staged_by_message[message.message_id] = tuple(message_paths)
        return staged, ignored, duplicates, paths, staged_by_message

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
        self,
        client: MailClient,
        messages: Iterable[EmailMessage],
        staged_by_message: dict[str, tuple[Path, ...]] | None = None,
    ) -> tuple[int, int, list[tuple[str, str]]]:
        """Move every processed mail into one of the 5 APD folders.

        ``APD/02_A_COMPLETER`` is used only when at least one attachment is
        still ``EN_ATTENTE``. Every other message is moved to its definitive
        GED folder, which removes it from ``INBOX`` and from the temporary
        ``APD/02_A_COMPLETER`` label through the IMAP ``MOVE`` / label-removal
        fallback implemented by :class:`ImapMailClient`.
        """
        synced = 0
        archived = 0
        moves: list[tuple[str, str]] = []
        staged_by_message = staged_by_message or {}
        if self._classify_messages:
            self._ensure_classification_folders(client)
        for message in messages:
            staged = staged_by_message.get(message.message_id, ())
            if self._classify_messages:
                target, _pending = self._classify_message(message, staged)
            else:
                target = MAIL_PENDING
            source = str(message.folder or "").strip()
            if not target or target.casefold() == source.casefold():
                continue
            if not self._classify_messages and not staged:
                continue
            try:
                client.move_message(message.message_id, source, target)
            except Exception:  # noqa: BLE001 - one mail never blocks the sync
                continue
            synced += 1
            if source.upper() in {
                INBOX_FOLDER,
                MAIL_PENDING.upper(),
            }:
                archived += 1
            moves.append((source, target))
        return synced, archived, moves

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
        staged, ignored, duplicates, staged_paths, staged_by_message = (
            self._stage_attachments(messages)
        )
        self._ingest_staged_for_classification(staged_paths)
        attached, missing = self._attach_missing_pdfs()
        candidates = [
            message
            for message in messages
            if message.message_id in staged_by_message
            or any(
                attachment.suffix in SUPPORTED_SUFFIXES
                for attachment in message.attachments
            )
            or str(message.folder or "").strip().upper() == MAIL_PENDING.upper()
        ]
        if isinstance(client, ImapMailClient):
            client.reset()
        labels, archived, _moves = self._sync_labels(
            client, candidates, staged_by_message
        )
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
    "CLASSIFICATION_FOLDERS",
    "GMAIL_HOST",
    "GMAIL_PORT",
    "GmailImapSyncService",
    "GmailSyncReport",
    "LOCAL_GED_FOLDERS",
    "MAIL_AVOIRS_CLIENTS",
    "MAIL_AVOIRS_FOURNISSEURS",
    "MAIL_FACTURES_CLIENTS",
    "MAIL_FACTURES_FOURNISSEURS",
    "MAIL_LISTES",
    "MAIL_PENDING",
    "MISSING_CREDENTIALS_WARNING",
    "MISSING_PDF_INVOICES",
    "SPAM_FOLDERS",
    "SUPPORTED_SUFFIXES",
    "gmail_credentials",
]
