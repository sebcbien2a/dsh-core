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
    SUPPORTED_ATTACHMENT_SUFFIXES,
    EmailAttachment,
    EmailInboxBridge,
    EmailInboxBridgeError,
    EmailMessage,
    ImapMailClient,
    MailClient,
)

GMAIL_HOST = "imap.gmail.com"
GMAIL_PORT = 993
DEFAULT_TENANT = "default"
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


@dataclass(frozen=True)
class MessagePreview:
    message_id: str
    subject: str
    sender: str
    attachments: tuple[AttachmentPreview, ...] = ()


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

    @property
    def ignored(self) -> int:
        return self.ignored_size + self.ignored_type


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
    )


def build_dry_run_report(
    messages: Sequence[EmailMessage],
    *,
    folder: str,
    min_bytes: int = MIN_ATTACHMENT_BYTES,
) -> DryRunReport:
    previews: list[MessagePreview] = []
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
        previews.append(
            MessagePreview(
                message_id=message.message_id,
                subject=message.subject,
                sender=message.sender,
                attachments=tuple(attachment_previews),
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
    )


def run_dry_run(
    client: MailClient,
    *,
    config: ApdMailConfig,
    limit: int | None = None,
    min_bytes: int = MIN_ATTACHMENT_BYTES,
) -> DryRunReport:
    messages = client.list_messages(config.inbox_folder)
    if limit is not None:
        messages = messages[:limit]
    return build_dry_run_report(
        messages, folder=config.inbox_folder, min_bytes=min_bytes
    )


def print_dry_run(report: DryRunReport) -> None:
    print("=" * 78)
    print("RELEVE GMAIL APD - DRY-RUN (aucune piece deplacee)")
    print("=" * 78)
    print(
        f"Boite : {report.folder} | Messages : {report.messages_scanned}"
        f" | Pieces : {report.attachments_seen}"
    )
    for message in report.messages:
        print(f"- Message {message.message_id}: {message.subject or '(sans objet)'}")
        for attachment in message.attachments:
            state = "ELIGIBLE" if attachment.eligible else f"IGNOREE ({attachment.reason})"
            print(
                f"    {attachment.filename} ({attachment.size} octets) -> {state}"
            )
    print("-" * 78)
    print(
        f"Bilan : {report.eligible} eligible(s), {report.ignored} ignoree(s)"
        f" [taille={report.ignored_size}, type={report.ignored_type}]"
        f" | seuil={report.min_attachment_bytes} octets"
    )
    print("=" * 78)


def print_fetch_report(report: Any) -> None:
    print("=" * 78)
    print("RELEVE GMAIL APD")
    print("=" * 78)
    print(
        f"Messages : {report.scanned_messages} | Pieces : {report.attachments_seen}"
        f" | Deposees : {report.staged} | Doublons : {report.duplicates}"
        f" | Ignorees : {report.ignored} | Deplacements : {report.mail_moves}"
    )
    print(f"Labels miroirs : {', '.join(report.mirrored_folders)}")
    if report.traces:
        print(f"Traces : {len(report.traces)}")
    if report.errors:
        for error in report.errors:
            print(f"ERREUR : {error}")
    print("=" * 78)


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
            print("INFO : --trigger-watcher est ignore en mode --dry-run.")
        mail_client = client or build_imap_client(config)
        report = run_dry_run(
            mail_client,
            config=config,
            limit=args.limit,
            min_bytes=MIN_ATTACHMENT_BYTES,
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
    bridge_report = bridge.fetch_and_stage_emails(limit=args.limit)
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
            f"errors={len(getattr(watcher_report, 'errors', ()))}"
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
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
