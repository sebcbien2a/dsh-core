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
import unicodedata
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
DEFAULT_MAILBOX_PREFIX = ""
APD_MAILBOX_PREFIX = "APD/"
INBOX_FOLDER = "INBOX"

# P20 - strict lexical eligibility for a raw Gmail INBOX scan. A message is
# aspirated only when it carries at least one PDF >= 5 KiB *and* one accounting
# keyword appears in its subject, body or attachment filename. Everything else
# must stay untouched in INBOX (never moved, labelled or deleted).
APD_ELIGIBILITY_KEYWORDS: tuple[str, ...] = (
    "facture",
    "factura",
    "invoice",
    "avoir",
    "credit note",
    "refacturation",
    "bordereau",
    "lcr",
    "releve",
    "quittance",
    "recu",
    "facturation",
    "smtrt",
    "boxtal",
    "free",
    "bosch",
    "sia",
    "total",
    "crisol",
)
MIN_APD_PDF_BYTES = 5 * 1024
# Hard socket ceiling so a stalled Gmail handshake can never hang a CLI run.
IMAP_SOCKET_TIMEOUT = 15.0
ELIGIBLE_REASON = "ELIGIBLE"
REASON_NO_PDF = "PDF_ABSENT"
REASON_PDF_TOO_SMALL = "PDF_INFERIEUR_A_5_KO"
REASON_NO_KEYWORD = "MOT_CLE_COMPTABLE_ABSENT"

_SHA256 = re.compile(r"[0-9a-f]{64}")
_UNSAFE_FILENAME = re.compile(r'[\\/\x00-\x1f<>:"|?*]')
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")
_TRAILING_DOTS_SPACES = re.compile(r"[. ]+$")


def _fold_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", text).strip().casefold()


@dataclass(frozen=True)
class EmailEligibility:
    """P20 lexical decision for one message read from a raw INBOX."""

    eligible: bool
    reason: str = ELIGIBLE_REASON
    matched_keyword: str | None = None
    pdf_attachments: tuple[str, ...] = ()


def evaluate_apd_email_eligibility(
    message: EmailMessage,
    *,
    min_pdf_bytes: int = MIN_APD_PDF_BYTES,
) -> EmailEligibility:
    """Apply the strict P20 rule to one message without any mailbox mutation.

    A valid message needs both a documented PDF (suffix ``.pdf`` and at least
    ``min_pdf_bytes`` bytes) and an accounting keyword in the subject, body or
    any attachment filename. The function is pure and side-effect free.
    """
    if not isinstance(min_pdf_bytes, int) or isinstance(min_pdf_bytes, bool):
        raise EmailInboxBridgeError("INVALID_MIN_ATTACHMENT_BYTES", repr(min_pdf_bytes))
    if min_pdf_bytes < 0:
        raise EmailInboxBridgeError("INVALID_MIN_ATTACHMENT_BYTES", repr(min_pdf_bytes))
    messages = _iter_messages(message)
    attachments = [
        attachment
        for item in messages
        for attachment in item.attachments
    ]
    pdfs = [
        attachment for attachment in attachments if attachment.suffix == ".pdf"
    ]
    if not pdfs:
        return EmailEligibility(False, REASON_NO_PDF)
    large_pdfs = tuple(
        attachment.filename
        for attachment in pdfs
        if len(attachment.content) >= min_pdf_bytes
    )
    if not large_pdfs:
        return EmailEligibility(False, REASON_PDF_TOO_SMALL)
    haystack = _fold_text(
        " ".join(
            (
                *(item.subject or "" for item in messages),
                *(item.body or "" for item in messages),
                *(attachment.filename for attachment in attachments),
            )
        )
    )
    for keyword in APD_ELIGIBILITY_KEYWORDS:
        if _fold_text(keyword) in haystack:
            return EmailEligibility(
                True,
                ELIGIBLE_REASON,
                keyword,
                large_pdfs,
            )
    return EmailEligibility(False, REASON_NO_KEYWORD, pdf_attachments=large_pdfs)


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
    body: str = ""
    nested: tuple["EmailMessage", ...] = ()


def _iter_messages(message: EmailMessage) -> list[EmailMessage]:
    found = [message]
    for child in message.nested:
        found.extend(_iter_messages(child))
    return found


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
    source_folder: str = ""
    fallback_used: bool = False
    messages_ignored: int = 0
    eligibility: tuple[EmailEligibility, ...] = ()


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
    raw = Path(str(filename or "")).name.strip()
    text = "".join(" " if _UNSAFE_FILENAME.match(char) else char for char in raw)
    text = _TRAILING_DOTS_SPACES.sub("", _WHITESPACE.sub(" ", text).strip())
    if not text:
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


def _normalize_mailbox_prefix(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EmailInboxBridgeError("INVALID_MAILBOX_PREFIX", repr(value))
    prefix = value.strip().replace("\\", "/").strip("/")
    if not prefix:
        return ""
    if any(ord(char) < 32 for char in prefix) or "//" in prefix:
        raise EmailInboxBridgeError("INVALID_MAILBOX_PREFIX", repr(value))
    return f"{prefix}/"


def _normalize_mail_folder(folder: object) -> str:
    if not isinstance(folder, str):
        raise EmailInboxBridgeError("INVALID_MAIL_FOLDER", repr(folder))
    value = folder.strip()
    if (
        not value
        or len(value) > 200
        or any(ord(char) < 32 for char in value)
        or "\x00" in value
    ):
        raise EmailInboxBridgeError("INVALID_MAIL_FOLDER", repr(folder))
    return value


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
                self._connection = imaplib.IMAP4_SSL(
                    self.host, self.port, timeout=IMAP_SOCKET_TIMEOUT
                )
            else:
                self._connection = imaplib.IMAP4(
                    self.host, self.port, timeout=IMAP_SOCKET_TIMEOUT
                )
            self._connection.login(self.username, self.password)
        return self._connection

    def ensure_folder(self, folder: str) -> None:
        try:
            self.connection.create(folder)
        except imaplib.IMAP4.error:
            return

    def list_messages(self, folder: str) -> list[EmailMessage]:
        # BODY.PEEK keeps the \Seen flag untouched: a dry-run must never mark
        # a Gmail message as read. The client also never issues DELETE/EXPUNGE.
        status, _ = self.connection.select(folder, readonly=True)
        if status != "OK":
            return []
        status, data = self.connection.search(None, "ALL")
        if status != "OK":
            return []
        messages: list[EmailMessage] = []
        for uid in data[0].split():
            status, payload = self.connection.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not payload:
                continue
            raw = payload[0]
            if not isinstance(raw, tuple) or len(raw) < 2:
                continue
            parsed = message_from_bytes(raw[1])
            messages.append(
                parse_email_message(
                    raw[1],
                    message_id=uid.decode("ascii", "ignore"),
                    folder=folder,
                )
            )
        return messages

    def move_message(
        self, message_id: str, source_folder: str, target_folder: str
    ) -> None:
        """Mirror one message to another label without ever deleting it.

        Gmail supports the IMAP ``MOVE`` extension. When the server refuses it
        we deliberately raise instead of falling back to ``COPY`` + ``STORE
        \\Deleted`` + ``EXPUNGE``: the absolute P20 rule forbids deleting mail.
        """
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
            raise EmailInboxBridgeError(
                "MAIL_MOVE_FAILED", f"{message_id}:{source_folder}"
            )


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _message_body(parsed: Message) -> str:
    """Extract bounded visible text, including attached Outlook sub-messages."""
    texts = _message_texts(parsed)
    text = "\n".join(texts)
    text = _HTML_TAG.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()[:20_000]


def _message_texts(parsed: Message, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    if not parsed.is_multipart():
        return [_part_text(parsed)]
    plain: list[str] = []
    html: list[str] = []
    nested: list[str] = []
    for part in parsed.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type == "message/rfc822":
            continue
        filename = part.get_filename() or ""
        if Path(filename).suffix.casefold() == ".eml":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                nested.extend(_message_texts(message_from_bytes(payload), depth + 1))
            continue
        if content_type == "text/plain":
            plain.append(_part_text(part))
        elif content_type == "text/html":
            html.append(_part_text(part))
    if not plain and html:
        plain = html
    for part in parsed.walk():
        if part.get_content_type() != "message/rfc822":
            continue
        payload = part.get_payload()
        if isinstance(payload, list):
            for sub_message in payload:
                if isinstance(sub_message, Message):
                    subject = str(sub_message.get("Subject") or "").strip()
                    if subject:
                        nested.append(subject)
    return [*plain, *nested]


def _attachments(parsed: Message, depth: int = 0) -> list[EmailAttachment]:
    """Flatten every attachment, recursively unpacking Outlook sub-messages."""
    found: list[EmailAttachment] = []
    if depth > 8:
        return found
    for part in parsed.walk():
        content_type = part.get_content_type()
        if content_type == "message/rfc822":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        if Path(filename).suffix.casefold() == ".eml":
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and payload:
                found.extend(
                    _attachments(message_from_bytes(payload), depth + 1)
                )
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        found.append(
            EmailAttachment(
                filename=filename,
                content=payload,
                mime_type=content_type,
            )
        )
    return found


def parse_email_message(raw: bytes, *, message_id: str = "", folder: str = "") -> EmailMessage:
    """Parse one raw RFC-822 message, flattening nested ``message/rfc822``."""
    parsed = message_from_bytes(raw)
    return EmailMessage(
        message_id=message_id,
        folder=folder or DEFAULT_MAIL_FOLDER,
        subject=str(parsed.get("Subject") or ""),
        sender=str(parsed.get("From") or ""),
        attachments=tuple(_attachments(parsed)),
        body=_message_body(parsed),
    )


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
        mailbox_prefix: str = DEFAULT_MAILBOX_PREFIX,
        min_attachment_bytes: int = 0,
    ) -> None:
        if not isinstance(profile, CompanyProfile):
            raise EmailInboxBridgeError("INVALID_PROFILE", repr(profile))
        if client is not None and imap_client is not None:
            raise EmailInboxBridgeError(
                "INVALID_MAIL_CONFIG", "client and imap_client are exclusive"
            )
        if (
            not isinstance(min_attachment_bytes, int)
            or isinstance(min_attachment_bytes, bool)
            or min_attachment_bytes < 0
        ):
            raise EmailInboxBridgeError(
                "INVALID_MIN_ATTACHMENT_BYTES", repr(min_attachment_bytes)
            )
        self._profile = profile
        self._tenant_id = validate_tenant_id(tenant_id or profile.tenant_id)
        self._tree = ensure_tree(self._tenant_id, root)
        self._client = client if client is not None else imap_client
        self._store = store
        self._process_attachments = bool(process_attachments)
        self._auto_sync_mail = bool(auto_sync_mail)
        self._mailbox_prefix = _normalize_mailbox_prefix(mailbox_prefix)
        self._min_attachment_bytes = min_attachment_bytes
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

    @property
    def mailbox_prefix(self) -> str:
        return self._mailbox_prefix

    @property
    def mailbox_folders(self) -> tuple[str, ...]:
        return tuple(
            f"{self._mailbox_prefix}{state.value}" for state in FolderState
        )

    def _folder(self, state: FolderState | str) -> str:
        value = state.value if isinstance(state, FolderState) else str(state)
        return f"{self._mailbox_prefix}{value}"

    def _mail_client(self) -> MailClient:
        if self._client is None:
            self._client = ImapMailClient.from_env()
        return self._client

    def ensure_mail_folders(self) -> tuple[str, ...]:
        """Create the six mirror folders/labels in the mailbox."""
        client = self._mail_client()
        folders = self.mailbox_folders
        for folder in folders:
            client.ensure_folder(folder)
        return folders

    def fetch_and_stage_emails(
        self,
        limit: int | None = None,
        *,
        folder: str | None = None,
    ) -> EmailIngestionReport:
        """Manual trigger: stage attachments then run the extraction pipeline.

        When ``folder`` is omitted the APD inbox label is scanned first. If it
        is empty the runner falls back to the standard Gmail ``INBOX`` and then
        applies the strict P20 lexical filter: only eligible messages are
        aspirated, every other message is left strictly untouched.
        """
        if limit is not None and limit < 0:
            raise EmailInboxBridgeError("INVALID_LIMIT", repr(limit))
        client = self._mail_client()
        mirrored = self.ensure_mail_folders()
        requested = self._folder(FolderState.INBOX)
        if folder is not None:
            requested = _normalize_mail_folder(folder)
        messages = client.list_messages(requested)
        fallback_used = False
        if not messages and folder is None and requested != INBOX_FOLDER:
            inbox_messages = client.list_messages(INBOX_FOLDER)
            if inbox_messages:
                messages = inbox_messages
                requested = INBOX_FOLDER
                fallback_used = True
        filter_applied = requested.strip().upper() == INBOX_FOLDER
        scanned_messages = len(messages)
        if limit is not None:
            messages = messages[:limit]

        staged = duplicates = ignored = moves = 0
        messages_ignored = 0
        records: list[EmailAttachmentRecord] = []
        ingestion_reports: list[InboxIngestionReport] = []
        eligibility_results: list[EmailEligibility] = []
        errors: list[str] = []
        traces: list[str] = []
        attachments_seen = 0

        for message in messages:
            if filter_applied:
                eligibility = evaluate_apd_email_eligibility(
                    message,
                    min_pdf_bytes=max(
                        self._min_attachment_bytes, MIN_APD_PDF_BYTES
                    ),
                )
                eligibility_results.append(eligibility)
                if not eligibility.eligible:
                    messages_ignored += 1
                    traces.append(
                        f"{message.message_id}:MESSAGE_IGNORED:{eligibility.reason}"
                    )
                    continue
            for attachment in message.attachments:
                attachments_seen += 1
                if not attachment.supported:
                    ignored += 1
                    traces.append(
                        f"{message.message_id}:IGNORED:{attachment.filename}"
                    )
                    continue
                if len(attachment.content) < self._min_attachment_bytes:
                    ignored += 1
                    traces.append(
                        f"{message.message_id}:IGNORED_SMALL:{attachment.filename}"
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
                    mailbox_folder=requested,
                    staged_path=destination,
                )
                self._records[digest] = record
                self._message_folders.setdefault(
                    message.message_id, requested
                )
                records.append(record)
                staged += 1
                traces.append(
                    f"{digest}:MAIL->{requested}/{destination.name}"
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
            scanned_messages=scanned_messages,
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
            source_folder=requested,
            fallback_used=fallback_used,
            messages_ignored=messages_ignored,
            eligibility=tuple(eligibility_results),
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
        target_folder = self._folder(target)
        if source == target_folder:
            if record.mailbox_folder != target_folder:
                self._records[digest] = replace(
                    record,
                    mailbox_folder=target_folder,
                    state=target,
                )
            return False
        self._mail_client().move_message(
            record.message_id, source, target_folder
        )
        self._message_folders[record.message_id] = target_folder
        self._records[digest] = replace(
            record,
            mailbox_folder=target_folder,
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
    "APD_ELIGIBILITY_KEYWORDS",
    "APD_MAILBOX_PREFIX",
    "DEFAULT_MAILBOX_PREFIX",
    "DEFAULT_MAIL_FOLDER",
    "ELIGIBLE_REASON",
    "EmailAttachment",
    "EmailAttachmentRecord",
    "EmailEligibility",
    "EmailInboxBridge",
    "EmailInboxBridgeError",
    "EmailIngestionReport",
    "EmailMessage",
    "ImapMailClient",
    "INBOX_FOLDER",
    "MAILBOX_STATES",
    "MailClient",
    "MIN_APD_PDF_BYTES",
    "parse_email_message",
    "REASON_NO_KEYWORD",
    "REASON_NO_PDF",
    "REASON_PDF_TOO_SMALL",
    "SUPPORTED_ATTACHMENT_SUFFIXES",
    "evaluate_apd_email_eligibility",
    "state_for_ingestion_outcome",
]
