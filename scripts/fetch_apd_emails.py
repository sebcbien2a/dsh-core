"""P19 - Manual Gmail fetch runner for the APD accounting flow.

Usage::

    python scripts/fetch_apd_emails.py --dry-run --limit 10
    python scripts/fetch_apd_emails.py --limit 50 --trigger-watcher

Credentials are read from ``.env`` (``APD_INVOICE_EMAIL`` and
``GMAIL_APP_PASSWORD``) then from the process environment. The runner talks to
``imap.gmail.com:993`` through :class:`services.email_inbox_bridge.ImapMailClient`,
mirrors the six states as ``APD/00_INBOX`` ... ``APD/05_ARCHIVE_ERREUR`` Gmail
labels and ignores attachments smaller than 5 KiB or non-documentary files.
Nothing polls the mailbox: a human explicitly starts every run.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.email_inbox_bridge import (  # noqa: E402
    APD_MAILBOX_PREFIX,
    INBOX_FOLDER,
    SUPPORTED_ATTACHMENT_SUFFIXES,
    EmailAttachment,
    EmailEligibility,
    EmailInboxBridge,
    EmailInboxBridgeError,
    EmailMessage,
    ImapMailClient,
    MailClient,
    evaluate_apd_email_eligibility,
)

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
DEFAULT_TENANT = "APD"
MIN_ATTACHMENT_BYTES = 5 * 1024
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ApdMailConfigError(ValueError):
    """Missing or invalid APD mail configuration; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ApdMailConfig:
    email: str
    password: str
    host: str = GMAIL_HOST
    port: int = GMAIL_PORT
    mailbox_prefix: str = APD_MAILBOX_PREFIX

    @property
    def inbox_folder(self) -> str:
        return f"{self.mailbox_prefix}00_INBOX"


@dataclass(frozen=True)
class AttachmentPreview:
    filename: str
    size: int
    documentary: bool
    eligible: bool
    reason: str | None = None
    mime_type: str = "application/octet-stream"


@dataclass(frozen=True)
class MessagePreview:
    message_id: str
    subject: str
    sender: str
    attachments: tuple[AttachmentPreview, ...] = ()
    eligible: bool = True
    reason: str | None = None
    matched_keyword: str | None = None


@dataclass(frozen=True)
class DryRunReport:
    folder: str
    messages_scanned: int
    attachments_seen: int
    eligible: int
    ignored_size: int
    ignored_type: int
    messages: tuple[MessagePreview, ...] = ()
    min_attachment_bytes: int = MIN_ATTACHMENT_BYTES
    filter_applied: bool = False
    fallback_used: bool = False
    eligibility: tuple[EmailEligibility, ...] = ()

    @property
    def ignored(self) -> int:
        return self.ignored_size + self.ignored_type

    @property
    def messages_eligible(self) -> int:
        return sum(1 for message in self.messages if message.eligible)

    @property
    def messages_ignored(self) -> int:
        return sum(1 for message in self.messages if not message.eligible)


@dataclass(frozen=True)
class FetchReport:
    dry_run: bool
    exit_code: int = 0
    dry_run_report: DryRunReport | None = None
    bridge_report: Any | None = None
    watcher_report: Any | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)


def read_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Minimal ``.env`` reader (no dependency, no variable expansion)."""
    env_path = Path(path)
    if not env_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_mail_config(
    *,
    env_file: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ApdMailConfig:
    """Read credentials from ``.env`` then override with the process env."""
    file_values = read_env_file(env_file or DEFAULT_ENV_FILE)
    process = os.environ if environ is None else environ

    def get(name: str, default: str = "") -> str:
        value = process.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
        return str(file_values.get(name, default)).strip()

    email = get("APD_INVOICE_EMAIL") or get("DSH_MAIL_USER")
    password = get("GMAIL_APP_PASSWORD") or get("DSH_MAIL_PASSWORD")
    if not email:
        raise ApdMailConfigError("APD_INVOICE_EMAIL_MISSING")
    if not password:
        raise ApdMailConfigError("GMAIL_APP_PASSWORD_MISSING")
    host = get("DSH_MAIL_HOST", GMAIL_HOST) or GMAIL_HOST
    port_text = get("DSH_MAIL_PORT", str(GMAIL_PORT)) or str(GMAIL_PORT)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ApdMailConfigError("INVALID_MAIL_PORT", port_text) from exc
    prefix = get("APD_MAILBOX_PREFIX", APD_MAILBOX_PREFIX) or APD_MAILBOX_PREFIX
    if not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return ApdMailConfig(
        email=email,
        password=password,
        host=host,
        port=port,
        mailbox_prefix=prefix,
    )


def build_imap_client(config: ApdMailConfig) -> ImapMailClient:
    """Build the real Gmail client; never called during offline tests."""
    return ImapMailClient(
        config.host,
        config.email,
        config.password,
        port=config.port,
        use_ssl=True,
    )


def preview_attachment(
    attachment: EmailAttachment, min_bytes: int = MIN_ATTACHMENT_BYTES
) -> AttachmentPreview:
    documentary = attachment.suffix in SUPPORTED_ATTACHMENT_SUFFIXES
    size = len(attachment.content)
    reason: str | None = None
    if not documentary:
        reason = "TYPE_NON_DOCUMENTAIRE"
    elif size < min_bytes:
        reason = "TAILLE_INFERIEURE_A_5_KO"
    return AttachmentPreview(
        filename=attachment.filename,
        size=size,
        documentary=documentary,
        eligible=reason is None,
        reason=reason,
        mime_type=attachment.mime_type,
    )


def build_dry_run_report(
    messages: Sequence[EmailMessage],
    *,
    folder: str,
    min_bytes: int = MIN_ATTACHMENT_BYTES,
    filter_lexical: bool = False,
    fallback_used: bool = False,
) -> DryRunReport:
    previews: list[MessagePreview] = []
    eligibilities: list[EmailEligibility] = []
    attachments_seen = eligible = ignored_size = ignored_type = 0
    for message in messages:
        attachment_previews: list[AttachmentPreview] = []
        for attachment in message.attachments:
            attachments_seen += 1
            preview = preview_attachment(attachment, min_bytes)
            attachment_previews.append(preview)
            if preview.eligible:
                eligible += 1
            elif preview.documentary:
                ignored_size += 1
            else:
                ignored_type += 1
        message_eligible = True
        reason: str | None = None
        matched_keyword: str | None = None
        if filter_lexical:
            decision = evaluate_apd_email_eligibility(
                message, min_pdf_bytes=min_bytes
            )
            eligibilities.append(decision)
            message_eligible = decision.eligible
            reason = None if decision.eligible else decision.reason
            matched_keyword = decision.matched_keyword
        elif not any(item.eligible for item in attachment_previews):
            message_eligible = False
            reason = "AUCUNE_PIECE_ELIGIBLE"
        previews.append(
            MessagePreview(
                message_id=message.message_id,
                subject=message.subject,
                sender=message.sender,
                attachments=tuple(attachment_previews),
                eligible=message_eligible,
                reason=reason,
                matched_keyword=matched_keyword,
            )
        )
    return DryRunReport(
        folder=folder,
        messages_scanned=len(messages),
        attachments_seen=attachments_seen,
        eligible=eligible,
        ignored_size=ignored_size,
        ignored_type=ignored_type,
        messages=tuple(previews),
        min_attachment_bytes=min_bytes,
        filter_applied=filter_lexical,
        fallback_used=fallback_used,
        eligibility=tuple(eligibilities),
    )


def run_dry_run(
    client: MailClient,
    *,
    config: ApdMailConfig,
    limit: int | None = None,
    min_bytes: int = MIN_ATTACHMENT_BYTES,
    folder: str | None = None,
) -> DryRunReport:
    requested = folder or config.inbox_folder
    messages = client.list_messages(requested)
    fallback_used = False
    if not messages and folder is None and requested.strip().upper() != INBOX_FOLDER:
        inbox_messages = client.list_messages(INBOX_FOLDER)
        if inbox_messages:
            messages = inbox_messages
            requested = INBOX_FOLDER
            fallback_used = True
    if limit is not None:
        messages = messages[:limit]
    filter_lexical = requested.strip().upper() == INBOX_FOLDER
    return build_dry_run_report(
        messages,
        folder=requested,
        min_bytes=min_bytes,
        filter_lexical=filter_lexical,
        fallback_used=fallback_used,
    )


def print_dry_run(report: DryRunReport) -> None:
    print("=" * 78, flush=True)
    print("RELEVE GMAIL APD - DRY-RUN (aucune piece deplacee)", flush=True)
    print("=" * 78, flush=True)
    print(
        f"Boite : {report.folder} | Messages : {report.messages_scanned}"
        f" | Pieces : {report.attachments_seen}", flush=True
    )
    if report.fallback_used:
        print("Fallback automatique : APD/00_INBOX vide -> INBOX", flush=True)
    if report.filter_applied:
        print(
            "Filtre lexical P20 : PDF >= 5 Ko + mot-cle comptable obligatoires", flush=True
        )
    for message in report.messages:
        if report.filter_applied:
            state = "ELIGIBLE" if message.eligible else f"IGNORE ({message.reason})"
            keyword = (
                f" | mot-cle={message.matched_keyword}"
                if message.matched_keyword
                else ""
            )
            print(
                f"- Message {message.message_id} [{state}{keyword}]:"
                f" {message.subject or '(sans objet)'}", flush=True
            )
        else:
            print(f"- Message {message.message_id}: {message.subject or '(sans objet)'}", flush=True)
        for attachment in message.attachments:
            state = "ELIGIBLE" if attachment.eligible else f"IGNOREE ({attachment.reason})"
            print(
                f"    {attachment.filename} ({attachment.size} octets,"
                f" {attachment.mime_type}) -> {state}", flush=True
            )
    print("-" * 78, flush=True)
    print(
        f"Bilan : {report.eligible} eligible(s), {report.ignored} ignoree(s)"
        f" [taille={report.ignored_size}, type={report.ignored_type}]"
        f" | seuil={report.min_attachment_bytes} octets", flush=True
    )
    if report.filter_applied:
        print(
            f"Messages : {report.messages_eligible} eligible(s),"
            f" {report.messages_ignored} ignore(s) et laisses intacts dans INBOX", flush=True
        )
    print("RAPPEL : aucun message deplace, aucun message supprime, aucune piece telechargee.", flush=True)
    print("=" * 78, flush=True)


def print_fetch_report(report: Any) -> None:
    print("=" * 78, flush=True)
    print("RELEVE GMAIL APD", flush=True)
    print("=" * 78, flush=True)
    if getattr(report, "source_folder", ""):
        print(
            f"Boite source : {report.source_folder}"
            + (" (fallback INBOX)" if getattr(report, "fallback_used", False) else ""), flush=True
        )
    print(
        f"Messages : {report.scanned_messages} | Pieces : {report.attachments_seen}"
        f" | Deposees : {report.staged} | Doublons : {report.duplicates}"
        f" | Ignorees : {report.ignored} | Deplacements : {report.mail_moves}", flush=True
    )
    if getattr(report, "messages_ignored", 0):
        print(
            f"Messages non eligibles laisses intacts dans INBOX :"
            f" {report.messages_ignored}", flush=True
        )
    print(f"Labels miroirs : {', '.join(report.mirrored_folders)}", flush=True)
    if report.traces:
        print(f"Traces : {len(report.traces)}", flush=True)
    if report.errors:
        for error in report.errors:
            print(f"ERREUR : {error}", flush=True)
    print("Aucun e-mail n'est supprime : les messages sont uniquement deplaces/labellises.", flush=True)
    print("=" * 78, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual Gmail fetch runner for the APD accounting flow."
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="dotenv file holding APD_INVOICE_EMAIL and GMAIL_APP_PASSWORD",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list eligible attachments without staging or moving anything",
    )
    parser.add_argument("--limit", type=int, default=None, help="max messages")
    parser.add_argument(
        "--folder",
        default=None,
        help=(
            "mailbox to scan (default APD/00_INBOX, automatic fallback to INBOX);"
            " INBOX applies the strict P20 eligibility filter"
        ),
    )
    parser.add_argument(
        "--trigger-watcher",
        action="store_true",
        help="run one DocumentFlowWatcher cycle after a real fetch",
    )
    return parser


def run_fetch(
    args: argparse.Namespace,
    *,
    client: MailClient | None = None,
    store: Any | None = None,
    watcher: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> FetchReport:
    if args.limit is not None and args.limit < 0:
        raise ApdMailConfigError("INVALID_LIMIT", repr(args.limit))
    config = resolve_mail_config(env_file=args.env_file, environ=environ)
    if args.dry_run:
        if args.trigger_watcher:
            print("INFO : --trigger-watcher est ignore en mode --dry-run.", flush=True)
        mail_client = client or build_imap_client(config)
        report = run_dry_run(
            mail_client,
            config=config,
            limit=args.limit,
            min_bytes=MIN_ATTACHMENT_BYTES,
            folder=args.folder,
        )
        print_dry_run(report)
        return FetchReport(dry_run=True, dry_run_report=report)

    if store is None:
        from services.sqlite_accounting_store import SqliteAccountingStore

        store = SqliteAccountingStore()
    mail_client = client or build_imap_client(config)
    bridge = EmailInboxBridge(
        tenant_id=args.tenant,
        store=store,
        client=mail_client,
        mailbox_prefix=config.mailbox_prefix,
        min_attachment_bytes=MIN_ATTACHMENT_BYTES,
    )
    bridge_report = bridge.fetch_and_stage_emails(
        limit=args.limit, folder=args.folder
    )
    print_fetch_report(bridge_report)
    watcher_report = None
    if args.trigger_watcher:
        if watcher is None:
            from services.document_flow_watcher import DocumentFlowWatcher

            watcher = DocumentFlowWatcher(tenant_id=args.tenant, store=store)
        watcher_report = watcher.run_once()
        print(
            "WATCHER : "
            f"cycle={getattr(watcher_report, 'cycle', '?')} "
            f"ingested={getattr(watcher_report, 'ingested', '?')} "
            f"errors={len(getattr(watcher_report, 'errors', ()))}", flush=True
        )
    return FetchReport(
        dry_run=False,
        exit_code=1 if bridge_report.errors else 0,
        bridge_report=bridge_report,
        watcher_report=watcher_report,
        errors=tuple(bridge_report.errors),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    client: MailClient | None = None,
    store: Any | None = None,
    watcher: Any | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = run_fetch(
            args,
            client=client,
            store=store,
            watcher=watcher,
            environ=environ,
        )
    except (ApdMailConfigError, EmailInboxBridgeError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr, flush=True)
        return 2
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
