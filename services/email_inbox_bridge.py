"""P17 - Manual e-mail ingestion bridge for the APD accounting flow.

The bridge is intentionally *manual*: nothing polls a mailbox. A human (or a
cron job explicitly started by a human) calls :meth:`fetch_and_stage_emails`.
The mail client is injected through the small :class:`MailClient` protocol so
every test runs fully offline; :class:`ImapMailClient` is a lazy, stdlib-only
default implementation.

Behaviour:

* the six document states are mirrored as mailbox folders / Gmail labels
  (``00_INBOX`` ... ``05_ARCHIVE_ERREUR``);
* PDF and image attachments are hashed (SHA-256), written atomically to the
  tenant's ``00_INBOX`` and immediately handed to
  :class:`services.inbox_ingestion_service.InboxIngestionService`, which runs
  the normal Vision / document pipeline;
* :meth:`sync_email_folder_state` moves the source mail so the mailbox always
  follows the state of the attached PDF (``02_A_COMPLETER``, ``03_...`` ...).
"""

from __future__ import annotations

import hashlib
import imaplib
import os
import re
from dataclasses import dataclass, replace
from email import message_from_bytes
from email.message import Message
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from schemas.folder_state_schema import (
    APD_PROFILE,
    CompanyProfile,
    FolderState,
)
from services.folder_tree_service import ensure_tree, validate_tenant_id
from services.inbox_ingestion_service import (
    OUTCOME_A_COMPLETER,
    OUTCOME_DUPLICATE,
    OUTCOME_ERREUR,
    OUTCOME_VALIDE,
    InboxIngestionReport,
    InboxIngestionService,
)

MAILBOX_STATES: tuple[str, ...] = tuple(state.value for state in FolderState)
SUPPORTED_ATTACHMENT_SUFFIXES = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
)
DEFAULT_MAIL_FOLDER = FolderState.INBOX.value
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UNSAFE_FILENAME = re.compile(r"[\\/\x00]")


class EmailInboxBridgeError(ValueError):
    """Invalid e-mail bridge operation; ``code`` is stable for tests/logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class EmailAttachment:
    """One attachment as returned by a :class:`MailClient`."""

    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.casefold()

    @property
    def supported(self) -> bool:
        return self.suffix in SUPPORTED_ATTACHMENT_SUFFIXES and bool(self.content)


@dataclass(frozen=True)
class EmailMessage:
    """Minimal mail envelope consumed by the bridge."""

    message_id: str
    folder: str = DEFAULT_MAIL_FOLDER
    subject: str = ""
    sender: str = ""
    attachments: tuple[EmailAttachment, ...] = ()


@dataclass(frozen=True)
class EmailAttachmentRecord:
    """Durable view of one staged attachment and its mirrored mail state."""

    sha256: str
    message_id: str
    filename: str
    mailbox_folder: str
    staged_path: Path
    state: FolderState = FolderState.INBOX

    @property
    def relative_path(self) -> str:
        return self.staged_path.name


@dataclass(frozen=True)
class EmailIngestionReport:
    """Observable outcome of one manual :meth:`fetch_and_stage_emails` call."""

    scanned_messages: int = 0
    attachments_seen: int = 0
    staged: int = 0
    duplicates: int = 0
    ignored: int = 0
    mail_moves: int = 0
    mirrored_folders: tuple[str, ...] = ()
    records: tuple[EmailAttachmentRecord, ...] = ()
    ingestion: tuple[InboxIngestionReport, ...] = ()
    errors: tuple[str, ...] = ()
    traces: tuple[str, ...] = ()


@runtime_checkable
class MailClient(Protocol):
    """Injected IMAP/Gmail client (fake clients only need these methods)."""

    def ensure_folder(self, folder: str) -> None: ...

    def list_messages(self, folder: str) -> list[EmailMessage]: ...

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None: ...


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_text(value: object) -> str:
    if not isinstance(value, str):
        raise EmailInboxBridgeError("INVALID_SHA256", repr(value))
    text = value.strip().lower()
    if _SHA256.fullmatch(text) is None:
        raise EmailInboxBridgeError("INVALID_SHA256", repr(value))
    return text


def _safe_filename(filename: object, digest: str, suffix: str) -> str:
    text = Path(str(filename or "")).name.strip()
    if not text or _UNSAFE_FILENAME.search(text):
        text = f"piece-{digest[:8]}{suffix}"
    if Path(text).suffix.casefold() not in SUPPORTED_ATTACHMENT_SUFFIXES:
        text = f"{Path(text).stem or 'piece'}{suffix}"
    return text


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-email")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def state_for_ingestion_outcome(outcome: str) -> FolderState:
    """Map an ingestion outcome to the mailbox mirror state."""
    if outcome == OUTCOME_VALIDE:
        return FolderState.VALIDE_PRET_FEC
    if outcome == OUTCOME_A_COMPLETER:
        return FolderState.A_COMPLETER
    if outcome in (OUTCOME_DUPLICATE, OUTCOME_ERREUR):
        return FolderState.ARCHIVE_ERREUR
    return FolderState.EN_COURS


class ImapMailClient:
    """Small stdlib IMAP client (lazy network, configurable via environment).

    Tests never instantiate it: they inject a deterministic fake implementing
    :class:`MailClient`. Production credentials are read from
    ``DSH_MAIL_HOST``, ``DSH_MAIL_USER``, ``DSH_MAIL_PASSWORD``,
    ``DSH_MAIL_PORT`` (default 993) and ``DSH_MAIL_SSL`` (default ``1``).
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        use_ssl: bool = True,
    ) -> None:
        if not host or not username or not password:
            raise EmailInboxBridgeError("INVALID_MAIL_CONFIG")
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port)
        self.use_ssl = bool(use_ssl)
        self._connection: imaplib.IMAP4 | None = None

    @classmethod
    def from_env(cls) -> "ImapMailClient":
        host = os.environ.get("DSH_MAIL_HOST", "").strip()
        username = os.environ.get("DSH_MAIL_USER", "").strip()
        password = os.environ.get("DSH_MAIL_PASSWORD", "")
        if not host or not username or not password:
            raise EmailInboxBridgeError("MAIL_CREDENTIALS_MISSING")
        port_text = os.environ.get("DSH_MAIL_PORT", "993").strip() or "993"
        try:
            port = int(port_text)
        except ValueError as exc:
            raise EmailInboxBridgeError("INVALID_MAIL_CONFIG", port_text) from exc
        use_ssl = os.environ.get("DSH_MAIL_SSL", "1").strip() not in {
            "0",
            "false",
            "False",
        }
        return cls(host, username, password, port=port, use_ssl=use_ssl)

    @property
    def connection(self) -> imaplib.IMAP4:
        if self._connection is None:
            if self.use_ssl:
                self._connection = imaplib.IMAP4_SSL(self.host, self.port)
            else:
                self._connection = imaplib.IMAP4(self.host, self.port)
            self._connection.login(self.username, self.password)
        return self._connection

    def ensure_folder(self, folder: str) -> None:
        try:
            self.connection.create(folder)
        except imaplib.IMAP4.error:
            return

    def list_messages(self, folder: str) -> list[EmailMessage]:
        status, _ = self.connection.select(folder)
        if status != "OK":
            return []
        status, data = self.connection.search(None, "ALL")
        if status != "OK":
            return []
        messages: list[EmailMessage] = []
        for uid in data[0].split():
            status, payload = self.connection.fetch(uid, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw = payload[0]
            if not isinstance(raw, tuple) or len(raw) < 2:
                continue
            parsed = message_from_bytes(raw[1])
            messages.append(
                EmailMessage(
                    message_id=uid.decode("ascii", "ignore"),
                    folder=folder,
                    subject=str(parsed.get("Subject") or ""),
                    sender=str(parsed.get("From") or ""),
                    attachments=tuple(_attachments(parsed)),
                )
            )
        return messages

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        self.ensure_folder(target_folder)
        status, _ = self.connection.select(source_folder)
        if status != "OK":
            raise EmailInboxBridgeError(
                "MAIL_FOLDER_UNAVAILABLE", source_folder
            )
        status, _ = self.connection.uid(
            "MOVE", message_id.encode("ascii"), target_folder
        )
        if status != "OK":
            status, _ = self.connection.uid(
                "COPY", message_id.encode("ascii"), target_folder
            )
            if status != "OK":
                raise EmailInboxBridgeError(
                    "MAIL_MOVE_FAILED", f"{message_id}:{source_folder}"
                )
            self.connection.uid("STORE", message_id.encode("ascii"), "+FLAGS", "(\\Deleted)")
            self.connection.expunge()


def _attachments(parsed: Message) -> list[EmailAttachment]:
    found: list[EmailAttachment] = []
    for part in parsed.walk():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        found.append(
            EmailAttachment(
                filename=filename,
                content=payload,
                mime_type=part.get_content_type(),
            )
        )
    return found


class EmailInboxBridge:
    """Stage manual mailbox attachments into the P14 document flow."""

    def __init__(
        self,
        tenant_id: str | None = None,
        store: Any | None = None,
        root: str | os.PathLike[str] | None = None,
        *,
        client: MailClient | None = None,
        imap_client: MailClient | None = None,
        profile: CompanyProfile = APD_PROFILE,
        ingestion: InboxIngestionService | None = None,
        extractor: Any | None = None,
        process_attachments: bool = True,
        auto_sync_mail: bool = True,
    ) -> None:
        if not isinstance(profile, CompanyProfile):
            raise EmailInboxBridgeError("INVALID_PROFILE", repr(profile))
        if client is not None and imap_client is not None:
            raise EmailInboxBridgeError(
                "INVALID_MAIL_CONFIG", "client and imap_client are exclusive"
            )
        self._profile = profile
        self._tenant_id = validate_tenant_id(tenant_id or profile.tenant_id)
        self._tree = ensure_tree(self._tenant_id, root)
        self._client = client if client is not None else imap_client
        self._store = store
        self._process_attachments = bool(process_attachments)
        self._auto_sync_mail = bool(auto_sync_mail)
        if ingestion is not None:
            self._ingestion = ingestion
        elif self._process_attachments and store is not None:
            self._ingestion = InboxIngestionService(
                self._tenant_id,
                store,
                root,
                extractor=extractor,
            )
        else:
            self._ingestion = None
        self._records: dict[str, EmailAttachmentRecord] = {}
        self._message_folders: dict[str, str] = {}

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def profile(self) -> CompanyProfile:
        return self._profile

    @property
    def tree(self) -> Any:
        return self._tree

    @property
    def records(self) -> tuple[EmailAttachmentRecord, ...]:
        return tuple(self._records.values())

    def _mail_client(self) -> MailClient:
        if self._client is None:
            self._client = ImapMailClient.from_env()
        return self._client

    def ensure_mail_folders(self) -> tuple[str, ...]:
        """Create the six mirror folders/labels in the mailbox."""
        client = self._mail_client()
        for folder in MAILBOX_STATES:
            client.ensure_folder(folder)
        return MAILBOX_STATES

    def fetch_and_stage_emails(
        self, limit: int | None = None
    ) -> EmailIngestionReport:
        """Manual trigger: stage attachments then run the extraction pipeline."""
        if limit is not None and limit < 0:
            raise EmailInboxBridgeError("INVALID_LIMIT", repr(limit))
        client = self._mail_client()
        mirrored = self.ensure_mail_folders()
        messages = client.list_messages(DEFAULT_MAIL_FOLDER)
        if limit is not None:
            messages = messages[:limit]

        staged = duplicates = ignored = moves = 0
        records: list[EmailAttachmentRecord] = []
        ingestion_reports: list[InboxIngestionReport] = []
        errors: list[str] = []
        traces: list[str] = []
        attachments_seen = 0

        for message in messages:
            for attachment in message.attachments:
                attachments_seen += 1
                if not attachment.supported:
                    ignored += 1
                    traces.append(
                        f"{message.message_id}:IGNORED:{attachment.filename}"
                    )
                    continue
                digest = _sha256_bytes(attachment.content)
                if digest in self._records:
                    duplicates += 1
                    traces.append(f"{digest}:DUPLICATE_MAIL_ATTACHMENT")
                    continue
                filename = _safe_filename(
                    attachment.filename, digest, attachment.suffix
                )
                destination = _unique_destination(
                    self._tree.state_dir(FolderState.INBOX) / filename
                )
                _atomic_write(destination, attachment.content)
                record = EmailAttachmentRecord(
                    sha256=digest,
                    message_id=message.message_id,
                    filename=filename,
                    mailbox_folder=DEFAULT_MAIL_FOLDER,
                    staged_path=destination,
                )
                self._records[digest] = record
                self._message_folders.setdefault(
                    message.message_id, DEFAULT_MAIL_FOLDER
                )
                records.append(record)
                staged += 1
                traces.append(
                    f"{digest}:MAIL->00_INBOX/{destination.name}"
                )

                if not self._process_attachments or self._ingestion is None:
                    continue
                try:
                    report = self._ingestion.process_inbox([destination])
                except Exception as exc:  # noqa: BLE001 - one attachment never blocks
                    detail = _error_detail(exc)
                    errors.append(f"{digest}:INGESTION:{detail}")
                    traces.append(f"{digest}:INGESTION_ERROR:{detail}")
                    continue
                ingestion_reports.append(report)
                target = _report_state(report, digest)
                if target is None:
                    continue
                if self._auto_sync_mail:
                    try:
                        if self.sync_email_folder_state(digest, target):
                            moves += 1
                            traces.append(
                                f"{digest}:MAIL_STATE->{target.value}"
                            )
                    except Exception as exc:  # noqa: BLE001 - mail is non-blocking
                        detail = _error_detail(exc)
                        errors.append(f"{digest}:MAIL_SYNC:{detail}")
                        traces.append(f"{digest}:MAIL_SYNC_ERROR:{detail}")

        return EmailIngestionReport(
            scanned_messages=len(messages),
            attachments_seen=attachments_seen,
            staged=staged,
            duplicates=duplicates,
            ignored=ignored,
            mail_moves=moves,
            mirrored_folders=mirrored,
            records=tuple(records),
            ingestion=tuple(ingestion_reports),
            errors=tuple(errors),
            traces=tuple(traces),
        )

    def sync_email_folder_state(
        self,
        sha256: str,
        target_state: FolderState | str,
    ) -> bool:
        """Move the source mail into the mirror folder of the PDF state."""
        digest = _sha256_text(sha256)
        try:
            target = FolderState(target_state)
        except (TypeError, ValueError):
            raise EmailInboxBridgeError(
                "INVALID_EMAIL_STATE", repr(target_state)
            ) from None
        record = self._records.get(digest)
        if record is None:
            raise EmailInboxBridgeError("EMAIL_TRACKING_NOT_FOUND", digest)
        source = self._message_folders.get(
            record.message_id, record.mailbox_folder
        )
        if source == target.value:
            if record.mailbox_folder != target.value:
                self._records[digest] = replace(
                    record,
                    mailbox_folder=target.value,
                    state=target,
                )
            return False
        self._mail_client().move_message(
            record.message_id, source, target.value
        )
        self._message_folders[record.message_id] = target.value
        self._records[digest] = replace(
            record,
            mailbox_folder=target.value,
            state=target,
        )
        return True


def _report_state(
    report: InboxIngestionReport, digest: str
) -> FolderState | None:
    for document in report.documents:
        if document.sha256 == digest:
            return state_for_ingestion_outcome(document.outcome)
    return None


def _error_detail(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    label = code if isinstance(code, str) and code else type(exc).__name__.upper()
    message = str(exc).strip()
    return f"{label}: {message}" if message and message != label else label


__all__ = [
    "DEFAULT_MAIL_FOLDER",
    "EmailAttachment",
    "EmailAttachmentRecord",
    "EmailInboxBridge",
    "EmailInboxBridgeError",
    "EmailIngestionReport",
    "EmailMessage",
    "ImapMailClient",
    "MAILBOX_STATES",
    "MailClient",
    "SUPPORTED_ATTACHMENT_SUFFIXES",
    "state_for_ingestion_outcome",
]
