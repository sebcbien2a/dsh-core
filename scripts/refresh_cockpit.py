"""P21 - Refresh the Obsidian cockpit and ingest the real Downloads batch.

The runner copies the known real-world PDFs from ``~/Downloads`` into the
unique ``00_INBOX``, lets :class:`InboxIngestionService` dispatch every format
(OFX, Sage, LCR bordereau, Bosch/Sia, Boxtal, grouped customer invoices), then
refreshes every Excel workbook with relative ``PIECES_JOINTES`` links and
relaunches the 2026 CA12 simulation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import DocumentCategory, FolderState  # noqa: E402
from services.accounting_spreadsheet_export_service import (  # noqa: E402
    CategoryOverviewExporter,
    CategorySummaryExporter,
    ExpenseSubCategoryExporter,
    MasterTableExporter,
    PersonalExpenseNoteExporter,
    SupplierLedgerExporter,
)
from services.customer_invoice_batch_importer import (  # noqa: E402
    export_customer_sales_workbook,
)
from services.document_flow_watcher import (  # noqa: E402
    COCKPIT_COMPTA_NOTE_NAME,
    COCKPIT_COMPTA_NOTE_LINKS,
    DEFAULT_TENANT,
    DocumentFlowWatcher,
)
from services.folder_tree_service import ensure_tree  # noqa: E402
from services.inbox_ingestion_service import InboxIngestionService  # noqa: E402
from services.obsidian_vault_service import VaultWriter  # noqa: E402
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)
from services.vat_simulation_service import VatSimulationService  # noqa: E402

DOWNLOADS_PDF_NAMES: tuple[str, ...] = (
    "Y16V_SD_INVOICE202609243956114620.pdf",
    "_data_-_[archived__IC].pdf",
    "_data_-_[archived__IC][1].pdf",
    "boxtal_2600023334.pdf",
    "factures clients.pdf",
)
MULTI_INVOICE_FALLBACKS: tuple[str, ...] = (
    "facture carrosserie yucel oct a mai.pdf",
    "FACTURE GP36 2023.pdf",
    "facture mai a octobre 24 garage du parc.pdf",
)
DEFAULT_EXPORT_DIR = REPO_ROOT / "workspace" / "exports" / "accounting"


@dataclass(frozen=True)
class CockpitRefreshReport:
    cockpit_path: Path
    inbox_path: Path
    copied: tuple[str, ...]
    missing: tuple[str, ...]
    ingested: int
    workbooks: tuple[Path, ...]
    ca12_path: Path | None


def _unique_destination(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    index = 1
    while True:
        candidate = directory / f"{Path(name).stem}__{index}{Path(name).suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def copy_downloads_batch(downloads: Path, inbox: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    copied: list[str] = []
    missing: list[str] = []
    names = list(DOWNLOADS_PDF_NAMES)
    for candidate in MULTI_INVOICE_FALLBACKS:
        if not (downloads / "factures clients.pdf").exists() and (
            downloads / candidate
        ).is_file():
            names.append(candidate)
            break
    for name in names:
        source = downloads / name
        if not source.is_file():
            missing.append(name)
            continue
        destination = _unique_destination(inbox, name)
        shutil.copy2(source, destination)
        copied.append(destination.name)
    return tuple(copied), tuple(missing)


def refresh_workbooks(
    tenant_id: str,
    store: SqliteAccountingStore,
    *,
    root: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] = DEFAULT_EXPORT_DIR,
) -> tuple[Path, ...]:
    exported: list[Path] = []
    sales = export_customer_sales_workbook(
        tenant_id,
        store=store,
        root=root,
        export_dir=Path(export_dir) / tenant_id,
    )
    exported.append(sales.path)
    ledger = SupplierLedgerExporter(store, export_dir=export_dir).export_supplier_ledger(
        tenant_id
    )
    exported.append(ledger)
    overview = CategoryOverviewExporter(
        store, root=root, export_dir=export_dir
    ).export(tenant_id)
    exported.append(overview)
    master = MasterTableExporter(
        store, root=root, export_dir=export_dir
    ).export(tenant_id)
    exported.extend((master.achats_path, master.banque_tva_path))
    expenses = ExpenseSubCategoryExporter(
        store, root=root, export_dir=export_dir
    ).export(tenant_id)
    exported.append(expenses.global_path)
    exported.extend(expenses.subtable_paths.values())
    notes = PersonalExpenseNoteExporter(store, export_dir=export_dir).export(
        tenant_id
    )
    exported.extend(notes.paths.values())
    for category in DocumentCategory:
        if not store.list_category_lines(tenant_id, category):
            continue
        path = CategorySummaryExporter(
            store, export_dir=export_dir
        ).export_category_summary(tenant_id, category)
        exported.append(path)
    return tuple(dict.fromkeys(exported))


def refresh_cockpit(
    *,
    tenant_id: str = DEFAULT_TENANT,
    root: str | os.PathLike[str] | None = None,
    downloads: str | os.PathLike[str] | None = None,
    vault_root: str | os.PathLike[str] | None = None,
    db_path: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    year: int = 2026,
    ingest: bool = True,
) -> CockpitRefreshReport:
    tenant = tenant_id
    tree = ensure_tree(tenant, root)
    inbox = tree.state_dir(FolderState.INBOX).resolve()
    base_export = (
        Path(export_dir)
        if export_dir is not None
        else tree.state_dir(FolderState.EXPORT_COMPTA)
    )
    store = SqliteAccountingStore(db_path or DEFAULT_DB_PATH)
    copied: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    ingested = 0
    if ingest:
        source_dir = Path(downloads).expanduser() if downloads else Path.home() / "Downloads"
        copied, missing = copy_downloads_batch(source_dir, inbox)
        if copied:
            report = InboxIngestionService(tenant, store, root).process_inbox()
            ingested = report.scanned

    watcher = DocumentFlowWatcher(
        tenant_id=tenant,
        root=root,
        store=store,
        export_dir=base_export,
        vault_root=vault_root,
        auto_export=False,
    )
    body = watcher._build_cockpit_body()
    writer = VaultWriter(vault_root)
    result = writer.write_compta_note(
        COCKPIT_COMPTA_NOTE_NAME,
        body=body,
        wikilinks=COCKPIT_COMPTA_NOTE_LINKS,
    )
    workbooks = refresh_workbooks(
        tenant, store, root=root, export_dir=base_export
    )
    ca12_path: Path | None = None
    try:
        service = VatSimulationService(store, export_dir=base_export)
        ca12 = service.simulate(tenant, year)
        ca12_path = service.write_report(ca12)
    except Exception:  # noqa: BLE001 - a missing CA12 never blocks the cockpit
        ca12_path = None
    return CockpitRefreshReport(
        cockpit_path=result.path.resolve(),
        inbox_path=inbox,
        copied=copied,
        missing=missing,
        ingested=ingested,
        workbooks=workbooks,
        ca12_path=ca12_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=None)
    parser.add_argument("--downloads", default=None)
    parser.add_argument("--vault-root", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--export-dir", default=None)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--no-ingest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = refresh_cockpit(
        tenant_id=args.tenant,
        root=args.root,
        downloads=args.downloads,
        vault_root=args.vault_root,
        db_path=args.db,
        export_dir=args.export_dir,
        year=args.year,
        ingest=not args.no_ingest,
    )
    print(f"Cockpit Obsidian : {report.cockpit_path}")
    print(f"Dossier 00_INBOX : {report.inbox_path}")
    print(f"Fichiers copies : {len(report.copied)}")
    for name in report.missing:
        print(f"Fichier absent (ignore) : {name}")
    print(f"Documents ingeres : {report.ingested}")
    print(f"Classeurs Excel : {len(report.workbooks)}")
    if report.ca12_path is not None:
        print(f"Simulation CA12 {args.year} : {report.ca12_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
