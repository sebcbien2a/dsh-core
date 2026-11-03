"""P14/P15/P16 field validation runner.

Injects a manifest of real documents in ``00_INBOX`` then runs the genuine
:class:`services.inbox_ingestion_service.InboxIngestionService`: SHA-256 dedup,
atomic move to ``01_EN_COURS``, extraction, and conditional routing to
``03_VALIDE_PRET_FEC/<CATEGORIE>/`` or ``02_A_COMPLETER/``. The runner then
validates the pending rows through ``_A_COMPLETER.xlsx``, lets
``FolderSyncService`` move them to ``03_``, imports a bank line, arbitrates the
reconciliation, then exports the per-category analytic workbook.

The runner never deletes anything: it refuses to start when a content hash is
already present in the tenant tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    FolderState,
    MissingFieldEnum,
)
from services.acompleter_workbook_service import derive_document_id
from services.accounting_spreadsheet_export_service import CategorySummaryExporter
from services.bank_statement_parser_service import normalize_bank_rows
from services.folder_sync_service import FolderSyncService
from services.folder_tree_service import ensure_tree
from services.inbox_ingestion_service import (
    OUTCOME_A_COMPLETER,
    OUTCOME_DUPLICATE,
    OUTCOME_ERREUR,
    ExtractedInvoice,
    InboxIngestionReport,
    InboxIngestionService,
)
from services.sqlite_accounting_store import SqliteAccountingStore

DEFAULT_TENANT = "default"
DEFAULT_ROOT = REPO_ROOT / "workspace" / "document_flow"
DEFAULT_DB = REPO_ROOT / "data" / "accounting" / "accounting.sqlite3"
DEFAULT_EXPORT_DIR = REPO_ROOT / "workspace" / "exports" / "accounting"
STOP_CHOICES = ("inbox", "ingestion", "a_completer", "valide")
EXTRACTOR_CHOICES = ("pipeline", "manifest")
KINDS = ("INVOICE", "CREDIT_NOTE")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class DocumentSpec:
    source: Path
    name: str
    sha256: str
    kind: str
    category: DocumentCategory
    counterparty: str
    piece_number: str
    operation_date: str
    ht: Decimal
    tva: Decimal
    ttc: Decimal
    missing: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decimal_amount(value: Any, field: str) -> Decimal:
    try:
        amount = Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise SystemExit(f"INVALID_AMOUNT:{field}:{value!r}") from exc
    return amount.quantize(CENT)


def resolve_source(manifest_path: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise SystemExit(f"FILE_NOT_FOUND:{path}")
    return path


def load_manifest(manifest_path: Path) -> tuple[list[DocumentSpec], list[dict[str, Any]]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"INVALID_MANIFEST:{exc}") from exc
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise SystemExit("INVALID_MANIFEST:documents must be a non-empty list")

    specs: list[DocumentSpec] = []
    seen_hashes: set[str] = set()
    for index, item in enumerate(documents, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"INVALID_MANIFEST:documents[{index}] must be an object")
        source = resolve_source(manifest_path, str(item.get("path") or ""))
        digest = sha256_file(source)
        if digest in seen_hashes:
            raise SystemExit(f"DUPLICATE_SHA256_IN_BATCH:{source.name}:{digest}")
        seen_hashes.add(digest)
        kind = str(item.get("kind") or "INVOICE").upper()
        if kind not in KINDS:
            raise SystemExit(f"INVALID_KIND:{kind}")
        try:
            category = DocumentCategory(str(item.get("category") or ""))
        except ValueError as exc:
            raise SystemExit(f"INVALID_CATEGORY:{item.get('category')!r}") from exc
        missing = tuple(str(value) for value in item.get("missing") or ())
        for field in missing:
            if field not in {entry.value for entry in MissingFieldEnum}:
                raise SystemExit(f"INVALID_MISSING_FIELD:{field}")
        if MissingFieldEnum.CATEGORIE.value in missing:
            raise SystemExit("INVALID_MISSING_FIELD:categorie blocks the VALIDE transition")
        ht = decimal_amount(item.get("ht", "0"), "ht")
        tva = decimal_amount(item.get("tva", "0"), "tva")
        ttc_raw = item.get("ttc")
        ttc = decimal_amount(ttc_raw, "ttc") if ttc_raw is not None else (ht + tva).quantize(CENT)
        if kind == "CREDIT_NOTE":
            ht = -abs(ht)
            tva = -abs(tva)
            ttc = -abs(ttc)
        specs.append(
            DocumentSpec(
                source=source,
                name=source.name,
                sha256=digest,
                kind=kind,
                category=category,
                counterparty=str(item.get("counterparty") or ""),
                piece_number=str(item.get("piece_number") or source.stem),
                operation_date=str(item.get("operation_date") or ""),
                ht=ht,
                tva=tva,
                ttc=ttc,
                missing=missing,
            )
        )

    bank = payload.get("bank") or []
    if not isinstance(bank, list):
        raise SystemExit("INVALID_MANIFEST:bank must be a list")
    return specs, bank


def ensure_no_collision(tree: Any, tenant: str, specs: list[DocumentSpec]) -> None:
    tenant_dir = tree.root / tenant
    existing = {
        sha256_file(path)
        for state in FolderState
        for path in tree.state_dir(state).rglob("*")
        if path.is_file() and not path.name.startswith("~$")
    }
    for spec in specs:
        if spec.sha256 in existing:
            raise SystemExit(f"SHA256_ALREADY_IN_TREE:{spec.name}:{spec.sha256}")
        destination = (
            tree.category_dir(FolderState.VALIDE_PRET_FEC, spec.category) / spec.name
        )
        if destination.exists():
            raise SystemExit(f"DESTINATION_EXISTS:{destination}")
        for state in (
            FolderState.INBOX,
            FolderState.EN_COURS,
            FolderState.A_COMPLETER,
            FolderState.ARCHIVE_ERREUR,
        ):
            candidate = tree.state_dir(state) / spec.name
            if candidate.exists():
                raise SystemExit(f"FILENAME_ALREADY_IN_TREE:{candidate}")
    if not tenant_dir.is_dir():
        raise SystemExit(f"TENANT_DIR_NOT_FOUND:{tenant_dir}")


def record_of(spec: DocumentSpec) -> dict[str, Any]:
    return {
        "document": spec.name,
        "sha256": spec.sha256,
        "category": spec.category.value,
        "missing": list(spec.missing),
    }


class ManifestExtractor:
    """Deterministic extractor backed by the field-validation manifest.

    The ingestion orchestration stays genuine (dedup, atomic moves, workbook,
    store, conditional routing); only the LLM/OCR field extraction is replaced
    by the manifest truth so the P16 field validation is reproducible.
    """

    def __init__(self, specs: list[DocumentSpec]) -> None:
        self._by_hash = {spec.sha256: spec for spec in specs}

    def __call__(self, source: Path) -> ExtractedInvoice:
        digest = sha256_file(Path(source))
        spec = self._by_hash.get(digest)
        if spec is None:
            raise ValueError(f"NO_MANIFEST_ENTRY:{Path(source).name}")
        missing = set(spec.missing)
        return ExtractedInvoice(
            issue_date=None if "date_piece" in missing else spec.operation_date,
            invoice_number=None if "numero_piece" in missing else spec.piece_number,
            supplier_name=None if "nom_fournisseur" in missing else spec.counterparty,
            amount_ht=None if "montant_ht" in missing else spec.ht,
            amount_tva=None if "montant_tva" in missing else spec.tva,
            amount_ttc=None if "montant_ttc" in missing else spec.ttc,
            category=spec.category,
            confidence=0.5 if missing else 0.99,
            document_type=spec.kind,
        )


def enrich_store(
    store: SqliteAccountingStore, tenant: str, specs: list[DocumentSpec], tenant_dir: Path
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for spec in specs:
        destination = (
            tenant_dir
            / FolderState.VALIDE_PRET_FEC.value
            / spec.category.value
            / spec.name
        )
        if not destination.is_file():
            raise SystemExit(f"VALIDATED_FILE_NOT_FOUND:{destination}")
        doc_id = derive_document_id(spec.sha256)
        line = {
            "operation_date": spec.operation_date,
            "label": spec.piece_number,
            "debit_amount": None,
            "credit_amount": None,
            "currency": "EUR",
            "counterparty": spec.counterparty,
            "invoice_id": doc_id,
            "invoice_url": destination.relative_to(tenant_dir).as_posix(),
            "category": spec.category.value,
            "amount_ht": str(spec.ht),
            "amount_tva": str(spec.tva),
            "amount_ttc": str(spec.ttc),
            "status": DocumentStatus.VALIDE.value,
        }
        outcome = store.upsert_document_line(tenant, doc_id, line)
        lines.append({"document": spec.name, "document_id": doc_id, "outcome": outcome})
    return lines


def bank_accounts(line: dict[str, Any]) -> tuple[str, str]:
    debit = Decimal(str(line.get("debit_amount") or "0"))
    if debit > 0:
        return "401000", "512000"
    return "512000", "401000"


def reconcile(
    store: SqliteAccountingStore,
    tenant: str,
    specs: list[DocumentSpec],
    bank_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not bank_rows:
        return []
    lines = normalize_bank_rows(bank_rows, tenant)
    if len(lines) != len(bank_rows):
        raise SystemExit("INVALID_BANK_ROWS:empty rows are not supported")
    by_piece = {spec.piece_number.casefold(): spec for spec in specs}
    by_name = {spec.name.casefold(): spec for spec in specs}
    results: list[dict[str, Any]] = []
    for raw, line in zip(bank_rows, lines):
        inserted = store.add_line(tenant, line["line_id"], line)
        match = (
            by_piece.get(str(raw.get("match_piece") or "").casefold())
            or by_name.get(str(raw.get("match_file") or "").casefold())
        )
        decision = None
        if match is not None and inserted:
            debit_account, credit_account = bank_accounts(line)
            store.arbitrate(
                tenant,
                line["line_id"],
                {
                    "action": "ATTACH_INVOICE",
                    "status": "INVOICE_ATTACHED",
                    "invoice_id": derive_document_id(match.sha256),
                    "debit_account": debit_account,
                    "credit_account": credit_account,
                    "arbitrated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            decision = "INVOICE_ATTACHED"
        results.append(
            {
                "line_id": line["line_id"],
                "label": line["label"],
                "inserted": inserted,
                "matched_document": match.name if match is not None else None,
                "decision": decision,
            }
        )
    return results


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    specs, bank_rows = load_manifest(manifest_path)
    root = Path(args.root).resolve()
    tree = ensure_tree(args.tenant, root)
    ensure_no_collision(tree, args.tenant, specs)

    store = SqliteAccountingStore(args.db)
    service = FolderSyncService(args.tenant, store, root)
    inbox = tree.state_dir(FolderState.INBOX)
    tenant_dir = tree.root / args.tenant

    for spec in specs:
        shutil.copy2(spec.source, inbox / spec.name)
    injected = [spec.name for spec in specs]
    if args.stop_at == "inbox":
        return summary(
            args, tree, injected, [], None, None, [], [], None
        )

    extractor = (
        ManifestExtractor(specs) if args.extractor == "manifest" else None
    )
    ingestion = InboxIngestionService(
        args.tenant, store, root, extractor=extractor
    )
    ingestion_report = ingestion.ingest_inbox()
    if args.stop_at in {"ingestion", "a_completer"}:
        return summary(
            args,
            tree,
            injected,
            [],
            ingestion_report,
            None,
            [],
            [],
            None,
        )

    failures = [
        document
        for document in ingestion_report.documents
        if document.outcome in {OUTCOME_ERREUR, OUTCOME_DUPLICATE}
    ]
    if failures:
        first = failures[0]
        raise SystemExit(
            f"INGESTION_FAILED:{first.nom_fichier}:{first.detail or first.outcome}"
        )

    pending_hashes = {
        document.sha256
        for document in ingestion_report.documents
        if document.outcome == OUTCOME_A_COMPLETER
    }
    validation_report = None
    if pending_hashes:
        for spec in specs:
            if spec.sha256 in pending_hashes:
                service.workbook.align_statut(spec.sha256, DocumentStatus.VALIDE)
        validation_report = service.sync_human_to_pipeline()
        if validation_report.skipped_invalid:
            raise SystemExit("VALIDATION_REJECTED:check champs_manquants")

    enriched = enrich_store(store, args.tenant, specs, tenant_dir)
    reconciled = reconcile(store, args.tenant, specs, bank_rows)

    exporter = CategorySummaryExporter(store, export_dir=args.export_dir)
    exports: list[str] = []
    for category in sorted({spec.category for spec in specs}, key=lambda item: item.value):
        exports.append(str(exporter.export_category_summary(args.tenant, category)))

    return summary(
        args,
        tree,
        injected,
        [record_of(spec) for spec in specs],
        ingestion_report,
        validation_report,
        enriched,
        reconciled,
        exports,
    )


def summary(
    args: argparse.Namespace,
    tree: Any,
    injected: list[str],
    records: list[dict[str, Any]],
    ingestion_report: InboxIngestionReport | None,
    validation_report: Any,
    enriched: list[dict[str, Any]],
    reconciled: list[dict[str, Any]],
    exports: list[str] | None,
) -> dict[str, Any]:
    return {
        "tenant": args.tenant,
        "stop_at": args.stop_at,
        "extractor": args.extractor,
        "tree": {
            state.value: str(tree.state_dir(state))
            for state in FolderState
        },
        "injected": injected,
        "records": records,
        "ingestion": asdict(ingestion_report) if ingestion_report else None,
        "reports": {
            "ingestion": asdict(ingestion_report) if ingestion_report else None,
            "human_to_pipeline": asdict(validation_report) if validation_report else None,
        },
        "store_lines": enriched,
        "bank": reconciled,
        "exports": exports or [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR))
    parser.add_argument(
        "--stop-at",
        choices=STOP_CHOICES,
        default="valide",
        help="Stop the flow after the selected stage.",
    )
    parser.add_argument(
        "--extractor",
        choices=EXTRACTOR_CHOICES,
        default="pipeline",
        help=(
            "pipeline: real perception/OCR extraction; "
            "manifest: deterministic manifest-backed field extraction."
        ),
    )
    parser.add_argument("--json-out", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run(args)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
