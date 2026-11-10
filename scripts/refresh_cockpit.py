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
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.folder_state_schema import (  # noqa: E402
    DocumentCategory,
    DocumentStatus,
    FolderState,
)
from services.accounting_spreadsheet_export_service import (  # noqa: E402
    CLIENT_LEDGER_FILENAME,
    CategoryOverviewExporter,
    CategorySummaryExporter,
    ClientLedgerExporter,
    ExpenseSubCategoryExporter,
    MasterTableExporter,
    PersonalExpenseNoteExporter,
    SupplierLedgerExporter,
    category_summary_is_mandatory,
)
from services.apd_four_stage_pipeline import ApdFourStagePipeline  # noqa: E402
from services.apd_p27quinquies_repair_service import (  # noqa: E402
    repair_p27quinquies,
)
from services.apd_p27nonies_repair_service import (  # noqa: E402
    repair_p27nonies,
)
from services.apd_p27sexies_repair_service import (  # noqa: E402
    repair_p27sexies,
)
from services.boxtal_paintcar_repair_service import (  # noqa: E402
    repair_boxtal_and_paintcar,
)
from services.balance_simulation_service import (  # noqa: E402
    BALANCE_FILENAME,
    BalanceSimulationService,
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
from services.folder_tree_service import (  # noqa: E402
    ensure_tree,
    migrate_validated_to_ged,
)
from services.inbox_ingestion_service import (  # noqa: E402
    UNIFIED_TENANT_IDS,
    InboxIngestionService,
    sync_workbooks_to_onedrive,
)
from services.obsidian_vault_service import VaultWriter  # noqa: E402
from services.onedrive_accounting_bridge_service import (  # noqa: E402
    OneDriveAccountingBridge,
    pull_from_onedrive,
)
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)
from services.tier_reference_service import (  # noqa: E402
    purge_duplicate_tier_workbooks,
)
from services.vat_simulation_service import (  # noqa: E402
    CA12_SIMULATION_FILENAME,
    VatSimulationService,
)

DOWNLOADS_PDF_NAMES: tuple[str, ...] = (
    "Y16V_SD_INVOICE202609243956114620.pdf",
    "_data_-_[archived__IC].pdf",
    "_data_-_[archived__IC][1].pdf",
    "boxtal_2600023334.pdf",
    "factures clients.pdf",
    "factures clients 011225 a 311026.pdf",
    "Bordereau_remise_deffet_encaissement.pdf",
)
MULTI_INVOICE_FALLBACKS: tuple[str, ...] = (
    "facture carrosserie yucel oct a mai.pdf",
    "FACTURE GP36 2023.pdf",
    "facture mai a octobre 24 garage du parc.pdf",
)
# These sources already exist in the internal document flow; when they are
# absent from ``~/Downloads`` the runner now reads them in place instead of
# reporting a missing file.
WORKSPACE_FALLBACK_NAMES: frozenset[str] = frozenset(
    {
        "factures clients 011225 a 311026.pdf",
        "Bordereau_remise_deffet_encaissement.pdf",
    }
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
    balance_path: Path | None = None
    readable_workbook: Path | None = None
    pending_pieces: tuple[str, ...] = ()
    vat_collected: Decimal | None = None
    vat_deductible: Decimal | None = None
    vat_net: Decimal | None = None
    vat_net_b: Decimal | None = None
    onedrive_shared_dir: Path | None = None
    onedrive_pulled: tuple[Path, ...] = ()
    onedrive_pushed_director: tuple[Path, ...] = ()
    onedrive_pushed_expert: tuple[Path, ...] = ()


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


def _workspace_source_path(
    name: str,
    workspace_root: str | os.PathLike[str],
    tenant_id: str,
    inbox: Path,
) -> Path | None:
    """Resolve a real source already present in the internal document flow."""
    tenant_dir = Path(workspace_root).expanduser() / tenant_id
    inbox_resolved = inbox.resolve()
    preferred = (
        tenant_dir / FolderState.VALIDE_PRET_FEC.value / "VENTES_CLIENTS" / name,
        tenant_dir / FolderState.VALIDE_PRET_FEC.value / "BANQUE_FINANCE" / name,
        tenant_dir / FolderState.EXPORT_COMPTA.value / "SOURCES_REEL" / name,
    )
    for candidate in preferred:
        if candidate.is_file():
            return candidate
    for candidate in sorted(tenant_dir.rglob(name)):
        if not candidate.is_file():
            continue
        try:
            if candidate.resolve().is_relative_to(inbox_resolved):
                continue
        except ValueError:
            continue
        return candidate
    return None


def copy_downloads_batch(
    downloads: Path,
    inbox: Path,
    *,
    workspace_root: str | os.PathLike[str] | None = None,
    tenant_id: str = DEFAULT_TENANT,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
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
        if (
            not source.is_file()
            and workspace_root is not None
            and name in WORKSPACE_FALLBACK_NAMES
        ):
            source = _workspace_source_path(name, workspace_root, tenant_id, inbox)
        if source is None or not source.is_file():
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
    ledger = SupplierLedgerExporter(
        store,
        export_dir=export_dir,
        strict_tiers=True,
        include_matched_pieces=True,
    ).export_supplier_ledger(tenant_id)
    exported.append(ledger)
    client_ledger = ClientLedgerExporter(
        store,
        export_dir=export_dir,
        strict_tiers=True,
        include_matched_pieces=True,
    ).export(tenant_id)
    exported.append(client_ledger)
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
        if (
            not store.list_category_lines(tenant_id, category)
            and not category_summary_is_mandatory(category)
        ):
            continue
        path = CategorySummaryExporter(
            store, export_dir=export_dir, include_payment_date=True
        ).export_category_summary(tenant_id, category)
        exported.append(path)
    paths = tuple(dict.fromkeys(exported))
    sync_workbooks_to_onedrive(paths, root=root)
    return paths


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
    # P27-septies - absolute Data priority: merge the director's OneDrive
    # edits before ensure_tree, any repair or any workbook write on A:\.
    pull_from_onedrive(tenant, root=root)
    tree = ensure_tree(tenant, root)
    try:
        purge_duplicate_tier_workbooks(tree.root)
    except Exception:  # noqa: BLE001 - cleanup never blocks the cockpit
        pass
    inbox = tree.state_dir(FolderState.INBOX).resolve()
    base_export = (
        Path(export_dir)
        if export_dir is not None
        else tree.state_dir(FolderState.EXPORT_COMPTA)
    )
    store = SqliteAccountingStore(db_path or DEFAULT_DB_PATH)
    try:
        migrate_validated_to_ged(tenant, root, store=store)
    except Exception:  # noqa: BLE001 - migration never blocks the cockpit
        pass
    try:
        repair_boxtal_and_paintcar(
            store,
            tenant,
            tree.root / tenant,
            base_export / tenant,
        )
    except Exception:  # noqa: BLE001 - repair never blocks the cockpit
        pass
    try:
        repair_p27quinquies(
            store,
            tenant,
            tree.root / tenant,
            base_export / tenant,
        )
    except Exception:  # noqa: BLE001 - repair never blocks the cockpit
        pass
    try:
        repair_p27sexies(
            store,
            tenant,
            tenant_dir=tree.root / tenant,
            export_dir=base_export / tenant,
        )
    except Exception:  # noqa: BLE001 - repair never blocks the cockpit
        pass
    try:
        repair_p27nonies(
            store,
            tenant,
            tenant_dir=tree.root / tenant,
            export_dir=base_export / tenant,
        )
    except Exception:  # noqa: BLE001 - repair never blocks the cockpit
        pass
    for unified_tenant in UNIFIED_TENANT_IDS:
        try:
            ensure_tree(unified_tenant, root)
        except Exception:  # noqa: BLE001 - a future company never blocks
            continue
    copied: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    ingested = 0
    if ingest:
        source_dir = Path(downloads).expanduser() if downloads else Path.home() / "Downloads"
        copied, missing = copy_downloads_batch(
            source_dir, inbox, workspace_root=tree.root, tenant_id=tenant
        )
        report = InboxIngestionService(tenant, store, root).process_inbox()
        ingested = report.scanned

    onedrive = OneDriveAccountingBridge(
        tenant,
        root=root,
        export_dir=base_export / tenant,
    )
    onedrive_pulled = onedrive.pull_director_files()
    pipeline = ApdFourStagePipeline(
        tenant,
        store=store,
        root=root,
        export_dir=base_export / tenant,
        year=year,
    )
    stage_reports = pipeline.run_all()
    onedrive_pushed_director = onedrive.push_director_files()
    onedrive_pushed_expert = onedrive.push_expert_workbooks()
    workbooks = tuple(
        dict.fromkeys(
            path for stage_report in stage_reports for path in stage_report.workbooks
        )
    )
    sync_workbooks_to_onedrive(workbooks, root=root)
    ca12_path = next(
        (
            path
            for path in workbooks
            if path.name == CA12_SIMULATION_FILENAME.format(year=year)
        ),
        None,
    )
    balance_path = next(
        (
            path
            for path in workbooks
            if path.name == BALANCE_FILENAME.format(year=year)
        ),
        None,
    )
    vat_collected: Decimal | None = None
    vat_deductible: Decimal | None = None
    vat_net: Decimal | None = None
    vat_net_b: Decimal | None = None
    try:
        service = VatSimulationService(
            store, export_dir=base_export, cash_basis=True
        )
        ca12 = service.simulate(tenant, year)
        vat_collected = ca12.scenario_a.collected_tva
        vat_deductible = ca12.scenario_a.deductible_total
        vat_net = ca12.scenario_a.net_tva
        vat_net_b = ca12.scenario_b.net_tva
        if ca12_path is None:
            ca12_path = service.write_report(ca12)
    except Exception:  # noqa: BLE001 - a missing CA12 never blocks the cockpit
        ca12_path = ca12_path
    try:
        balance = BalanceSimulationService(store, export_dir=base_export)
        balance_report = balance.simulate(tenant, year)
        if balance_path is None:
            balance_path = balance.write_report(balance_report)
    except Exception:  # noqa: BLE001 - a missing balance never blocks the cockpit
        balance_path = balance_path

    try:
        pending_pieces = tuple(
            row.filename
            for row in pipeline._readable_rows()
            if row.status_value == DocumentStatus.EN_ATTENTE.value
        )
    except Exception:  # noqa: BLE001 - the cockpit must always be written
        pending_pieces = ()

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
    return CockpitRefreshReport(
        cockpit_path=result.path.resolve(),
        inbox_path=inbox,
        copied=copied,
        missing=missing,
        ingested=ingested,
        workbooks=workbooks,
        ca12_path=ca12_path,
        balance_path=balance_path,
        readable_workbook=pipeline.readable_path,
        pending_pieces=pending_pieces,
        vat_collected=vat_collected,
        vat_deductible=vat_deductible,
        vat_net=vat_net,
        vat_net_b=vat_net_b,
        onedrive_shared_dir=onedrive.shared_dir,
        onedrive_pulled=onedrive_pulled,
        onedrive_pushed_director=onedrive_pushed_director,
        onedrive_pushed_expert=onedrive_pushed_expert,
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
    primary = next(
        (
            path
            for path in report.workbooks
            if path.name == "TABLEAU_MERE_PIECES_COMPTABLES.xlsx"
        ),
        report.workbooks[0] if report.workbooks else None,
    )
    if primary is not None:
        print(f"classeur: {primary.resolve()}")
    if report.ca12_path is not None:
        print(f"Simulation CA12 {args.year} : {report.ca12_path.resolve()}")
    if report.balance_path is not None:
        print(
            f"Simulation Bilan & Résultat {args.year} : "
            f"{report.balance_path.resolve()}"
        )
    if report.vat_collected is not None:
        print(
            f"TVA collectée {args.year} (scénario A) : "
            f"{report.vat_collected} EUR"
        )
    if report.vat_deductible is not None:
        print(
            f"TVA déductible révisée {args.year} (scénario A) : "
            f"{report.vat_deductible} EUR"
        )
    if report.vat_net is not None:
        print(
            f"CA12 {args.year} - TVA nette (scénario A) : "
            f"{report.vat_net} EUR"
        )
    if report.vat_net_b is not None:
        print(
            f"CA12 {args.year} - TVA nette (scénario B) : "
            f"{report.vat_net_b} EUR"
        )
    if report.readable_workbook is not None:
        print(
            f"État {report.readable_workbook.resolve()} : "
            f"{len(report.pending_pieces)} pièce(s) en attente"
        )
        for name in report.pending_pieces:
            print(f"  - {name}")
    if report.onedrive_shared_dir is not None:
        print(f"OneDrive partagé : {report.onedrive_shared_dir}")
        print(f"  - importés depuis OneDrive : {len(report.onedrive_pulled)}")
        print(
            f"  - publiés pilotage : {len(report.onedrive_pushed_director)}"
        )
        print(
            f"  - publiés expert-comptable : {len(report.onedrive_pushed_expert)}"
        )
    else:
        print("OneDrive non détecté : pont partagé ignoré.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
