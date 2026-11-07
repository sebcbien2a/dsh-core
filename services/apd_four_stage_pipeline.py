"""P22 - APD four-stage accounting pipeline.

The manager's validated sequence is explicit and sequential:

1. Vision & mother table 1: read the pieces, auto-validate evidence, isolate
   doubts in ``02_A_COMPLETER/_A_COMPLETER.xlsx`` with a clickable PDF link.
2. Bank reconciliation: import OFX statements, letter supplier debits and
   customer remittances, and feed ``TABLEAU_MERE_BANQUE_TVA.xlsx``.
3. Ventilation & coherence: refresh every sector workbook and verify that each
   line has a real PDF in ``PIECES_JOINTES`` and a valid relative link.
4. Simulations: produce ``SIMULATION_CA12_2026.xlsx`` and
   ``SIMULATION_BILAN_RESULTAT_2026.xlsx``.

Every stage can be run alone (Hermès / Telegram, local web UI, CLI) and the
whole sequence is available through ``run_all`` with optional pauses.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

from schemas.folder_state_schema import (
    DEFERRED_MONTHS,
    DIRIGEANT_COUNTERPART_ACCOUNT,
    DocumentCategory,
    DocumentStatus,
    ExpenseSubCategory,
    FolderState,
    PaymentSource,
)
from services.acompleter_readable_service import (
    READABLE_FILENAME,
    ReadableRow,
    write_readable_workbook,
)
from services.acompleter_workbook_service import (
    AcompleterWorkbook,
    AcompleterWorkbookError,
    WorkbookLockedError,
    derive_document_id,
)
from services.accounting_memory_learning_service import (
    AccountingMemoryLearningService,
)
from services.tier_reference_service import (
    TIER_REFERENCE_FILENAME,
    TierReferenceError,
    TierReferenceService,
)
from services.apd_evidence_rules import (
    EvidenceDecision,
    NON_ACCOUNTABLE_DOCUMENT_TYPES,
    build_business_explanation,
    evaluate_evidence,
)
from services.balance_simulation_service import (
    BALANCE_FILENAME,
    BalanceSimulationService,
)
from services.folder_tree_service import (
    ensure_tree,
    migrate_default_to_apd,
    validate_tenant_id,
)
from services.ofx_bank_statement_parser import (
    import_ofx_statement,
    parse_ofx_statement,
)
from services.payment_instrument_reconciliation_service import (
    UnmatchedPaymentInstrument,
    reconcile_payment_instruments,
)
from services.pdf_splitter_and_linker import (
    PIECE_LINK_HEADER,
    PURCHASES_SUBDIR,
    SALES_SUBDIR,
    copy_unit_piece,
    find_piece_for_document,
    relative_link,
)
from services.sqlite_accounting_store import DEFAULT_DB_PATH, SqliteAccountingStore
from services.vat_simulation_service import CA12_SIMULATION_FILENAME, VatSimulationService

DEFAULT_TENANT = "APD"
DEFAULT_YEAR = 2026
STAGE_NAMES = {
    1: "Vision & Tableau Mère 1",
    2: "Rapprochement Bancaire",
    3: "Ventilation & Contrôle de Cohérence",
    4: "Simulations TVA & Bilan",
}
PURCHASE_CATEGORIES = frozenset(
    {
        DocumentCategory.ACHATS_MARCHANDISES.value,
        DocumentCategory.TRANSPORTS_LOGISTIQUE.value,
        DocumentCategory.FRAIS_GENERAUX.value,
        DocumentCategory.BANQUE_FINANCE.value,
        DocumentCategory.FISCAL_SOCIAL.value,
        DocumentCategory.IMMOBILISATIONS.value,
    }
)
PAID_STATUSES = frozenset({"PAYEE_RAPPROCHEE", "PAYE", "SOLDEE"})
PCG_PURCHASES_GOODS = "607000"
PCG_SERVICES = "622600"
PCG_SENSITIVE_EXPENSES = "623800"
UNIFIED_TENANT_PREPARATION = ("HOLDING_APD", "SCI_APD")
_CHUNK = 1 << 16
_COHERENCE_FILENAME = "RAPPORT_COHERENCE_PIECES.xlsx"


class ApdPipelineError(ValueError):
    """Invalid pipeline operation; ``code`` is stable for callers."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class StageReport:
    stage: int
    name: str
    status: str = "OK"
    processed: int = 0
    validated: int = 0
    pending: int = 0
    moved: int = 0
    imported_ofx: int = 0
    reconciled: int = 0
    workbooks: tuple[Path, ...] = ()
    messages: tuple[str, ...] = ()
    details: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "name": self.name,
            "status": self.status,
            "processed": self.processed,
            "validated": self.validated,
            "pending": self.pending,
            "moved": self.moved,
            "imported_ofx": self.imported_ofx,
            "reconciled": self.reconciled,
            "workbooks": [str(path) for path in self.workbooks],
            "messages": list(self.messages),
            "details": list(self.details),
        }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fold(value: Any) -> str:
    text = str(value or "").casefold()
    return (
        text.replace("à", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ç", "c")
    )


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}__{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _move(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = _unique_path(destination)
    try:
        os.replace(source, destination)
    except OSError:
        shutil.move(str(source), str(destination))
    return destination


def _tenant_relative(path: Path, tenant_dir: Path) -> str:
    try:
        return path.resolve().relative_to(tenant_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _store_piece_relative(path: Path, export_dir: Path) -> str:
    try:
        return path.resolve().relative_to(export_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _parse_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return None


def _row_document_number(row: dict[str, Any]) -> str:
    for key in ("invoice_id", "piece_number", "label", "line_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _supplier_tokens(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    tokens = [token for token in re.split(r"[^a-z0-9]+", folded) if len(token) >= 3]
    return tuple(tokens)


class ApdFourStagePipeline:
    """Sequential four-stage APD cockpit, callable stage by stage."""

    def __init__(
        self,
        tenant_id: str = DEFAULT_TENANT,
        *,
        store: SqliteAccountingStore | None = None,
        root: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
        db_path: str | os.PathLike[str] | None = None,
        year: int = DEFAULT_YEAR,
        enable_tier_workflow: bool | None = None,
    ) -> None:
        self._tenant_id = validate_tenant_id(tenant_id)
        self._enable_tier_workflow = (
            root is None if enable_tier_workflow is None else enable_tier_workflow
        )
        self._tree = ensure_tree(self._tenant_id, root)
        self._tenant_dir = self._tree.root / self._tenant_id
        self._store = store if store is not None else SqliteAccountingStore(db_path or DEFAULT_DB_PATH)
        migrate_default_to_apd(root, store=self._store)
        self._export_dir = (
            Path(export_dir)
            if export_dir is not None
            else self._tree.state_dir(FolderState.EXPORT_COMPTA) / self._tenant_id
        )
        self._export_dir.mkdir(parents=True, exist_ok=True)
        self._memory = AccountingMemoryLearningService()
        self._tier_reference = TierReferenceService(
            self._export_dir / TIER_REFERENCE_FILENAME,
            store=self._store,
            memory=self._memory,
            tenant_id=self._tenant_id,
        )
        self._tier_reference.bootstrap()
        self._year = int(year)
        self._workbook = AcompleterWorkbook(self._tenant_dir)
        self._a_completer_dir = self._tree.state_dir(FolderState.A_COMPLETER)
        self._last_reports: dict[int, StageReport] = {}
        self._unmatched_instruments: tuple[UnmatchedPaymentInstrument, ...] = ()
        for sibling in UNIFIED_TENANT_PREPARATION:
            try:
                ensure_tree(sibling, root)
            except Exception:  # noqa: BLE001 - a missing future company never blocks
                continue

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def store(self) -> SqliteAccountingStore:
        return self._store

    @property
    def tree(self) -> Any:
        return self._tree

    @property
    def tenant_dir(self) -> Path:
        return self._tenant_dir

    @property
    def export_dir(self) -> Path:
        return self._export_dir

    @property
    def year(self) -> int:
        return self._year

    @property
    def workbook(self) -> AcompleterWorkbook:
        return self._workbook

    @property
    def readable_path(self) -> Path:
        return self._a_completer_dir / READABLE_FILENAME

    # -- P24 arbitration columns & human decisions -----------------------

    def _arbitration_defaults(
        self,
        line: dict[str, Any] | None,
        *,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, str]:
        from services.accounting_spreadsheet_export_service import (
            _infer_expense_subcategory,
            is_personal_expense,
            payment_source_of,
        )

        source = dict(line or {})
        category_value = str(
            source.get("category") or category or DocumentCategory.DIVERS_A_CLASSER.value
        )
        inferred = _infer_expense_subcategory(source) if line else None
        sub = (
            subcategory
            or (inferred.value if inferred is not None else "")
            or str(source.get("expense_subcategory") or "")
        )
        personal = bool(line and is_personal_expense(source))
        payment = payment_source_of(source) if line else ""
        if personal or payment == PaymentSource.COMPTE_PERSO_DIRIGEANT.value:
            payment_source = PaymentSource.COMPTE_PERSO_DIRIGEANT.value
            account = DIRIGEANT_COUNTERPART_ACCOUNT
        else:
            payment_source = payment or PaymentSource.BANQUE_PRO.value
            if category_value == DocumentCategory.ACHATS_MARCHANDISES.value:
                account = PCG_PURCHASES_GOODS
            elif sub in {
                ExpenseSubCategory.RESTAURATION.value,
                ExpenseSubCategory.HOTELLERIE.value,
                ExpenseSubCategory.FRAIS_REPRESENTATION_PRESIDENT.value,
                ExpenseSubCategory.TRANSPORT_PARKING.value,
            }:
                account = PCG_SENSITIVE_EXPENSES
            else:
                account = PCG_SERVICES
        raw_date = str(source.get("operation_date") or "")[:10]
        operation_year: int | None = None
        if raw_date:
            try:
                operation_year = date.fromisoformat(raw_date).year
            except ValueError:
                operation_year = None
        target_text = str(source.get("fiscal_year_target") or "").strip()
        if not target_text.isdigit():
            target_text = str(operation_year or self._year)
        month = str(source.get("deferred_month_override") or "").strip()
        if not month:
            month = "NON"
        report = (
            "OUI"
            if operation_year is not None and int(target_text) != operation_year
            else "NON"
        )
        mode = str(source.get("fiscal_year_mode") or "ACTUEL").strip() or "ACTUEL"
        marker = str(source.get("is_classified_marker") or "").strip()
        if not marker:
            marker = (
                f"IDENTIFIEE_CLASSEE:{sub}"
                if sub
                else "NON_CLASSEE"
            )
        return {
            "expense_subcategory": sub,
            "payment_source": payment_source,
            "counterparty_account": account,
            "fiscal_year_target": target_text,
            "deferred_month": month,
            "report": report,
            "fiscal_year_mode": mode,
            "is_classified_marker": marker,
        }

    def _readable_extra(
        self,
        sha: str,
        line: dict[str, Any] | None,
        *,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, str]:
        defaults = self._arbitration_defaults(
            line, category=category, subcategory=subcategory
        )
        existing: dict[str, str] = {}
        try:
            existing = self._workbook.read_p17_metadata(sha) or {}
        except (WorkbookLockedError, AcompleterWorkbookError, PermissionError, OSError):
            existing = {}
        return {
            "payment_source": (
                existing.get("Source de Paiement")
                or defaults["payment_source"]
            ),
            "counterparty_account": (
                existing.get("Compte de Contrepartie")
                or defaults["counterparty_account"]
            ),
            "fiscal_year_target": (
                existing.get("Exercice Cible")
                or defaults["fiscal_year_target"]
            ),
            "deferred_month": (
                existing.get("Mois Décalé") or defaults["deferred_month"]
            ),
            "report": existing.get("Report") or defaults["report"],
            "tier_alias_target": (
                existing.get("Rattacher comme ALIAS de (ou NOUVEAU_TIERS)")
                or ""
            ),
        }

    def prefill_arbitration_columns(self) -> int:
        """Fill the five P24 arbitration columns for every pending row."""
        try:
            rows = self._workbook.read_rows()
        except (
            WorkbookLockedError,
            AcompleterWorkbookError,
            PermissionError,
            OSError,
        ):
            return 0
        written = 0
        for row in rows:
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
            defaults = self._arbitration_defaults(
                line, category=row.categorie_suggeree or None
            )
            try:
                existing = self._workbook.read_p17_metadata(row.sha256) or {}
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                existing = {}
            merged = {
                key: existing.get(header) or value
                for key, header, value in (
                    ("expense_subcategory", "Sous-catégorie Frais", defaults["expense_subcategory"]),
                    ("payment_source", "Source de Paiement", defaults["payment_source"]),
                    (
                        "counterparty_account",
                        "Compte de Contrepartie",
                        defaults["counterparty_account"],
                    ),
                    (
                        "fiscal_year_target",
                        "Exercice Cible",
                        defaults["fiscal_year_target"],
                    ),
                    (
                        "deferred_month_override",
                        "Mois Décalé",
                        defaults["deferred_month"],
                    ),
                    ("report", "Report", defaults["report"]),
                    (
                        "fiscal_year_mode",
                        "Mode Exercice Fiscal",
                        defaults["fiscal_year_mode"],
                    ),
                    (
                        "is_classified_marker",
                        "Repère Classement",
                        defaults["is_classified_marker"],
                    ),
                )
            }
            try:
                changed = self._workbook.update_p17_metadata(
                    row.sha256,
                    expense_subcategory=merged["expense_subcategory"] or None,
                    is_classified_marker=merged["is_classified_marker"],
                    fiscal_year_mode=merged["fiscal_year_mode"],
                    fiscal_year_target=merged["fiscal_year_target"],
                    deferred_month_override=merged["deferred_month_override"],
                    payment_source=merged["payment_source"],
                    counterparty_account=merged["counterparty_account"],
                    report=merged["report"],
                )
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                continue
            if changed:
                written += 1
        return written

    def apply_human_decisions(self) -> int:
        """Read the director's Excel decisions and persist them to the ledger."""
        try:
            rows = self._workbook.read_rows()
        except (
            WorkbookLockedError,
            AcompleterWorkbookError,
            PermissionError,
            OSError,
        ):
            rows = []
        applied = 0
        for row in rows:
            if row.nom_fichier.startswith("TIER__") and row.nom_fichier.endswith(".tier"):
                try:
                    metadata = self._workbook.read_p17_metadata(row.sha256) or {}
                except (
                    WorkbookLockedError,
                    AcompleterWorkbookError,
                    PermissionError,
                    OSError,
                ):
                    metadata = {}
                target = str(
                    metadata.get(
                        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)"
                    )
                    or ""
                ).strip()
                if target:
                    name = row.nom_fichier[len("TIER__") : -len(".tier")]
                    try:
                        self._tier_reference.learn_alias(name, target)
                    except TierReferenceError:
                        continue
                    self._apply_tier_reference()
                    try:
                        self._workbook.align_statut(
                            row.sha256, DocumentStatus.VALIDE.value
                        )
                    except (
                        WorkbookLockedError,
                        AcompleterWorkbookError,
                        PermissionError,
                        OSError,
                    ):
                        pass
                    self._memory.remember_decision(
                        counterparty=name,
                        decision=DocumentStatus.VALIDE.value,
                    )
                    applied += 1
                continue
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
            if line is None:
                continue
            try:
                metadata = self._workbook.read_p17_metadata(row.sha256) or {}
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                metadata = {}
            status = str(row.statut or "").strip().upper()
            if status == DocumentStatus.REJETE.value:
                self._store.deactivate_rejected_document(
                    self._tenant_id, row.sha256
                )
                self._archive_human_rejected(row.nom_fichier)
                self._memory.remember_decision(
                    counterparty=line.get("counterparty"),
                    document_number=line.get("invoice_id"),
                    category=line.get("category"),
                    decision=DocumentStatus.REJETE.value,
                )
                applied += 1
                continue
            if status not in {
                DocumentStatus.VALIDE.value,
                DocumentStatus.EN_ATTENTE.value,
            }:
                continue
            updated = dict(line)
            updated.pop("line_id", None)
            updated["status"] = status
            if status == DocumentStatus.VALIDE.value:
                updated["reconciliation_status"] = "VALIDE"
            category = str(
                row.categorie_suggeree or line.get("category") or ""
            ).strip()
            try:
                updated["category"] = DocumentCategory(category).value
            except ValueError:
                pass
            subcategory = str(metadata.get("Sous-catégorie Frais") or "").strip()
            if subcategory:
                try:
                    updated["expense_subcategory"] = ExpenseSubCategory(
                        subcategory
                    ).value
                except ValueError:
                    pass
            payment_source = str(
                metadata.get("Source de Paiement") or ""
            ).strip()
            if payment_source:
                updated["payment_source"] = payment_source
            account = str(metadata.get("Compte de Contrepartie") or "").strip()
            if account:
                updated["counterparty_account"] = account
            target = str(metadata.get("Exercice Cible") or "").strip()
            if target.isdigit():
                updated["fiscal_year_target"] = target
            month = str(metadata.get("Mois Décalé") or "").strip().upper()
            updated["deferred_month_override"] = (
                month if month in DEFERRED_MONTHS else None
            )
            mode = str(metadata.get("Mode Exercice Fiscal") or "").strip().upper()
            if mode:
                updated["fiscal_year_mode"] = mode
            marker = str(metadata.get("Repère Classement") or "").strip()
            if marker:
                updated["is_classified_marker"] = marker
            report_value = str(metadata.get("Report") or "").strip().upper()
            if report_value:
                updated["report"] = report_value
            if status == DocumentStatus.VALIDE.value:
                self._move_human_validated(row, updated)
            self._store.upsert_document_line(
                self._tenant_id,
                derive_document_id(row.sha256),
                updated,
            )
            if status == DocumentStatus.VALIDE.value:
                self._memory.remember_decision(
                    counterparty=updated.get("counterparty"),
                    document_number=updated.get("invoice_id"),
                    category=updated.get("category"),
                    payment_source=payment_source,
                    counterparty_account=account,
                    fiscal_year_target=target,
                    deferred_month=month if month in DEFERRED_MONTHS else "",
                    report=metadata.get("Report") or "NON",
                    decision=DocumentStatus.VALIDE.value,
                )
            applied += 1
        applied += self._apply_readable_decisions()
        return applied

    def _apply_readable_decisions(self) -> int:
        """Apply the director's edits made in ``02_A_COMPLETER`` directly."""
        from types import SimpleNamespace

        from services.acompleter_readable_service import (
            read_readable_decisions,
        )

        decisions = read_readable_decisions(self.readable_path)
        applied = 0
        for decision in decisions:
            if decision.filename.startswith("TIER__") and decision.filename.endswith(".tier"):
                target = str(decision.tier_alias_target or "").strip()
                if target:
                    name = decision.filename[len("TIER__") : -len(".tier")]
                    try:
                        self._tier_reference.learn_alias(name, target)
                    except TierReferenceError:
                        continue
                    self._apply_tier_reference()
                    try:
                        self._workbook.align_statut(
                            decision.sha256, DocumentStatus.VALIDE.value
                        )
                    except (
                        WorkbookLockedError,
                        AcompleterWorkbookError,
                        PermissionError,
                        OSError,
                    ):
                        pass
                    self._memory.remember_decision(
                        counterparty=name,
                        decision=DocumentStatus.VALIDE.value,
                    )
                    applied += 1
                continue
            line = None
            if decision.sha256:
                line = self._store.get_line(
                    self._tenant_id, derive_document_id(decision.sha256)
                )
            if line is None and decision.filename:
                for candidate in self._store.list_master_lines(self._tenant_id):
                    invoice_name = Path(
                        str(candidate.get("invoice_url") or "")
                    ).name
                    if (
                        invoice_name == decision.filename
                        or str(candidate.get("label") or "")
                        == decision.filename
                    ):
                        line = candidate
                        break
            if line is None:
                continue
            line_id = str(line.get("line_id") or "")
            if not line_id:
                continue
            status = decision.status or DocumentStatus.EN_ATTENTE.value
            updated = dict(line)
            updated.pop("line_id", None)
            updated["status"] = status
            if status == DocumentStatus.VALIDE.value:
                updated["reconciliation_status"] = "VALIDE"
            if decision.category:
                try:
                    updated["category"] = DocumentCategory(
                        decision.category
                    ).value
                except ValueError:
                    pass
            if decision.payment_source:
                updated["payment_source"] = decision.payment_source
                if (
                    decision.payment_source
                    == PaymentSource.COMPTE_PERSO_DIRIGEANT.value
                ):
                    updated["counterparty_account"] = (
                        DIRIGEANT_COUNTERPART_ACCOUNT
                    )
                elif decision.counterparty_account:
                    updated["counterparty_account"] = (
                        decision.counterparty_account
                    )
            elif decision.counterparty_account:
                updated["counterparty_account"] = (
                    decision.counterparty_account
                )
            if decision.fiscal_year_target:
                updated["fiscal_year_target"] = decision.fiscal_year_target
            if decision.deferred_month:
                updated["deferred_month_override"] = (
                    None
                    if decision.deferred_month == "NON"
                    else decision.deferred_month
                )
            if decision.report:
                updated["report"] = decision.report
            changed = status != str(line.get("status") or "").strip().upper()
            for key in (
                "category",
                "payment_source",
                "counterparty_account",
                "fiscal_year_target",
                "deferred_month_override",
                "report",
            ):
                if str(updated.get(key) or "") != str(line.get(key) or ""):
                    changed = True
            if not changed and status == DocumentStatus.EN_ATTENTE.value:
                continue
            if status == DocumentStatus.REJETE.value:
                self._store.deactivate_rejected_document(
                    self._tenant_id, decision.sha256
                )
                self._archive_human_rejected(decision.filename)
            elif status == DocumentStatus.VALIDE.value:
                stub = SimpleNamespace(
                    nom_fichier=decision.filename,
                    categorie_suggeree=str(
                        updated.get("category") or ""
                    ),
                )
                self._move_human_validated(stub, updated)
            self._store.upsert_document_line(
                self._tenant_id, line_id, updated
            )
            try:
                if decision.sha256:
                    self._workbook.align_statut(decision.sha256, status)
                    self._workbook.update_p17_metadata(
                        decision.sha256,
                    expense_subcategory=updated.get("expense_subcategory")
                    or None,
                    payment_source=updated.get("payment_source") or None,
                    counterparty_account=updated.get("counterparty_account")
                    or None,
                    fiscal_year_target=updated.get("fiscal_year_target")
                    or None,
                    deferred_month_override=updated.get(
                        "deferred_month_override"
                    )
                    or "NON",
                    report=updated.get("report") or "NON",
                )
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                pass
            if status == DocumentStatus.VALIDE.value:
                self._memory.remember_decision(
                    counterparty=updated.get("counterparty"),
                    document_number=updated.get("invoice_id"),
                    category=updated.get("category"),
                    payment_source=updated.get("payment_source") or "",
                    counterparty_account=updated.get(
                        "counterparty_account"
                    )
                    or "",
                    fiscal_year_target=updated.get("fiscal_year_target")
                    or "",
                    deferred_month=updated.get("deferred_month_override")
                    or "",
                    report=updated.get("report") or "NON",
                    decision=DocumentStatus.VALIDE.value,
                )
            applied += 1
        return applied

    def _move_human_validated(
        self, row: Any, updated: dict[str, Any]
    ) -> None:
        category_text = str(
            updated.get("category")
            or row.categorie_suggeree
            or DocumentCategory.DIVERS_A_CLASSER.value
        )
        try:
            category = DocumentCategory(category_text)
        except ValueError:
            category = DocumentCategory.DIVERS_A_CLASSER
        candidates = [
            self._a_completer_dir / row.nom_fichier,
            self._tenant_dir / row.nom_fichier,
        ]
        invoice_url = str(updated.get("invoice_url") or "").strip()
        if invoice_url:
            candidates.append(self._tenant_dir / invoice_url)
        source = next(
            (path for path in candidates if path.is_file()), None
        )
        if source is None:
            return
        try:
            destination = _move(
                source,
                self._tree.category_dir(FolderState.VALIDE_PRET_FEC, category)
                / source.name,
            )
        except OSError:
            return
        updated["invoice_url"] = _tenant_relative(destination, self._tenant_dir)
        side = (
            SALES_SUBDIR
            if category is DocumentCategory.VENTES_CLIENTS
            else PURCHASES_SUBDIR
        )
        operation_date = None
        raw_date = str(updated.get("operation_date") or "")[:10]
        if raw_date:
            try:
                operation_date = date.fromisoformat(raw_date)
            except ValueError:
                operation_date = None
        try:
            piece = self._copy_piece_once(
                destination,
                side=side,
                document_type=str(updated.get("document_type") or "FACTURE"),
                document_number=str(
                    updated.get("invoice_id") or destination.stem
                ),
                document_date=operation_date,
                party=str(updated.get("counterparty") or ""),
            )
            updated["piece_pdf"] = _store_piece_relative(piece, self._export_dir)
        except OSError:
            pass

    def _archive_human_rejected(self, filename: str) -> None:
        candidates = [
            self._a_completer_dir / filename,
            self._tenant_dir / filename,
        ]
        source = next(
            (path for path in candidates if path.is_file()), None
        )
        if source is None:
            return
        try:
            _move(
                source,
                self._tree.state_dir(FolderState.ARCHIVE_ERREUR)
                / "REJET_MANUEL"
                / source.name,
            )
        except OSError:
            return

    def _apply_tier_reference(self) -> int:
        """Apply referential roles/categories/aliases to every store line."""
        tiers = self._tier_reference
        changed = 0
        for row in self._store.list_master_lines(self._tenant_id):
            line_id = str(row.get("line_id") or "")
            if not line_id:
                continue
            name = str(
                row.get("counterparty") or row.get("supplier") or ""
            ).strip()
            label = str(row.get("label") or "").strip()
            record = tiers.resolve(name) or tiers.resolve(label)
            if record is None:
                continue
            line = self._store.get_line(self._tenant_id, line_id)
            if line is None:
                continue
            updated = dict(line)
            updated.pop("line_id", None)
            if record.role == "INTERNE_EXCLUS":
                updated["category"] = DocumentCategory.DIVERS_A_CLASSER.value
                updated["is_classified_marker"] = "INTERNE_EXCLUS"
            else:
                if record.category:
                    updated["category"] = record.category
                if record.subcategory:
                    updated["expense_subcategory"] = record.subcategory
                updated["is_classified_marker"] = (
                    f"TIER:{record.canonical}:{record.role}"
                )
            differs = any(
                str(updated.get(key) or "") != str(line.get(key) or "")
                for key in (
                    "category",
                    "expense_subcategory",
                    "is_classified_marker",
                    "counterparty",
                )
            )
            if differs:
                self._store.upsert_document_line(
                    self._tenant_id, line_id, updated
                )
                changed += 1
        return changed

    def _write_unknown_tier_rows(self) -> int:
        if not self._enable_tier_workflow:
            return 0
        tiers = self._tier_reference
        rows = self._store.list_master_lines(self._tenant_id)
        unknown = tiers.unknown_tier_entries(rows)
        written = 0
        for name, _line in unknown:
            safe_name = str(name).replace("/", "_").replace("\\", "_").strip()
            if not safe_name:
                continue
            filename = f"TIER__{safe_name}.tier"
            digest = hashlib.sha256(
                f"tier:{name}".encode("utf-8")
            ).hexdigest()
            try:
                self._workbook.upsert(
                    digest,
                    filename,
                    [],
                    DocumentCategory.DIVERS_A_CLASSER,
                )
                written += 1
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                continue
        return written

    def _sync_mirror(self, paths: Iterable[Path]) -> None:
        try:
            from services.inbox_ingestion_service import (
                sync_workbooks_to_onedrive,
            )

            sync_workbooks_to_onedrive(paths, root=self._tree.root)
        except Exception:  # noqa: BLE001 - mirror is best effort
            return

    def run_stage(self, stage: int) -> StageReport:
        if stage not in STAGE_NAMES:
            raise ApdPipelineError("INVALID_STAGE", repr(stage))
        dispatch = {
            1: self.stage1_vision_and_mother_table,
            2: self.stage2_bank_reconciliation,
            3: self.stage3_ventilation_and_coherence,
            4: self.stage4_simulations,
        }
        self._tier_reference.bootstrap()
        self._apply_tier_reference()
        report = dispatch[stage]()
        self._last_reports[stage] = report
        return report

    def run_all(
        self,
        *,
        stages: Iterable[int] = (1, 2, 3, 4),
        pause: Callable[[int], None] | None = None,
    ) -> tuple[StageReport, ...]:
        reports: list[StageReport] = []
        selected = tuple(stages)
        for index, stage in enumerate(selected):
            reports.append(self.run_stage(stage))
            if pause is not None and index < len(selected) - 1:
                pause(stage)
        return tuple(reports)

    def snapshot(self) -> dict[str, Any]:
        rows = self._read_workbook_rows()
        pending = [row for row in rows if row.statut == DocumentStatus.EN_ATTENTE.value]
        return {
            "tenant": self._tenant_id,
            "year": self._year,
            "tenant_dir": str(self._tenant_dir),
            "export_dir": str(self._export_dir),
            "readable_workbook": str(self.readable_path),
            "pending_questions": len(pending),
            "validated_rows": sum(
                1 for row in rows if row.statut == DocumentStatus.VALIDE.value
            ),
            "last_stages": {
                str(stage): report.as_dict() for stage, report in self._last_reports.items()
            },
        }

    # -- stage 1 ---------------------------------------------------------

    def stage1_vision_and_mother_table(self) -> StageReport:
        from services.accounting_cleanup_service import run_cleanup

        run_cleanup(
            self._store,
            self._tenant_id,
            self._tenant_dir,
            self._export_dir,
        )
        self._repair_stale_lock()
        human_applied = self.apply_human_decisions()
        decisions = self._collect_evidence_decisions()
        processed = validated = pending = moved = 0
        messages: list[str] = []
        detail_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path, sha, decision in decisions:
            if sha in seen:
                continue
            seen.add(sha)
            processed += 1
            if decision.is_valid:
                try:
                    source_rel, store_piece = self._validate_evidence_piece(
                        path, sha, decision
                    )
                except Exception as exc:  # noqa: BLE001 - one piece never blocks
                    messages.append(f"{path.name}:VALIDATION_FAILED:{exc}")
                    pending += 1
                    continue
                self._persist_decision_line(sha, path, decision, source_rel, store_piece)
                self._mark_workbook(sha, path.name, decision, source_rel)
                validated += 1
                moved += 1
                detail_rows.append(
                    {
                        "file": path.name,
                        "rule": decision.rule,
                        "status": "VALIDE",
                        "category": decision.category.value,
                        "piece": source_rel,
                    }
                )
            else:
                rel_source = _tenant_relative(path, self._tenant_dir)
                self._mark_workbook(sha, path.name, decision, rel_source)
                self._persist_pending_line(sha, path, decision, rel_source)
                pending += 1
                detail_rows.append(
                    {
                        "file": path.name,
                        "rule": decision.rule,
                        "status": "EN_ATTENTE",
                        "category": decision.category.value,
                        "piece": rel_source,
                    }
                )
        generic_pending = self._write_generic_pending_rows(seen)
        pending += generic_pending
        pending += self._write_unknown_tier_rows()
        self.prefill_arbitration_columns()
        self._write_readable_views()
        sales = self._export_sales_workbook()
        master = self._export_master_tables()
        ledgers = self._export_auxiliary_ledgers()
        workbooks = tuple(
            path
            for path in (
                sales,
                master.achats_path,
                master.banque_tva_path,
                *ledgers,
            )
            if path is not None
        )
        self._sync_mirror(workbooks)
        messages.append(f"Pièces traitées par règles d'évidence : {processed}")
        messages.append(f"Décisions humaines appliquées : {human_applied}")
        messages.append(f"Validations automatiques : {validated}")
        messages.append(f"Questions en attente : {pending}")
        return StageReport(
            stage=1,
            name=STAGE_NAMES[1],
            processed=processed,
            validated=validated,
            pending=pending,
            moved=moved,
            workbooks=workbooks,
            messages=tuple(messages),
            details=tuple(detail_rows),
        )

    def _collect_evidence_decisions(self) -> list[tuple[Path, str, EvidenceDecision]]:
        found: list[tuple[Path, str, EvidenceDecision]] = []
        seen: set[str] = set()
        for path in self._evidence_candidates():
            if not path.is_file() or path.suffix.casefold() != ".pdf":
                continue
            try:
                sha = _file_sha256(path)
            except OSError:
                continue
            if sha in seen:
                continue
            seen.add(sha)
            try:
                decision = evaluate_evidence(path)
            except Exception:  # noqa: BLE001 - a bad PDF never blocks the pass
                decision = None
            if decision is not None:
                found.append((path, sha, decision))
        return found

    def _evidence_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for state in (FolderState.A_COMPLETER, FolderState.ARCHIVE_ERREUR):
            directory = self._tree.state_dir(state)
            if directory.is_dir():
                candidates.extend(
                    path for path in directory.rglob("*.pdf") if path.is_file()
                )
        valid_dir = self._tree.state_dir(FolderState.VALIDE_PRET_FEC)
        if valid_dir.is_dir():
            for path in valid_dir.rglob("*.pdf"):
                if not path.is_file():
                    continue
                name = path.name.casefold()
                if (
                    name.startswith("facture_free_")
                    or "ila" in name
                    or re.match(r"^w\d+w\d+o\d+\.pdf$", name)
                    or "avoir cholet" in name
                    or "262105" in name
                    or "order_conf" in name
                    or "diffam" in name
                ):
                    candidates.append(path)
        return sorted(dict.fromkeys(candidates), key=lambda item: str(item).casefold())

    def _validate_evidence_piece(
        self,
        source: Path,
        sha: str,
        decision: EvidenceDecision,
    ) -> tuple[str, str | None]:
        category = decision.category
        destination_dir = self._tree.category_dir(
            FolderState.VALIDE_PRET_FEC, category
        )
        destination = destination_dir / source.name
        source_resolved = source.resolve()
        try:
            inside = source_resolved.is_relative_to(destination_dir.resolve())
        except ValueError:
            inside = False
        moved_path = source
        if not inside:
            existing = None
            if destination.is_file():
                try:
                    if _file_sha256(destination) == sha:
                        existing = destination
                except OSError:
                    existing = None
            if existing is not None:
                moved_path = existing
            else:
                try:
                    moved_path = _move(source, destination)
                except OSError:
                    # The source can be locked by OneDrive, an antivirus or an
                    # open reader. ``_move`` may already have copied the bytes
                    # before failing to remove the source; reuse that copy,
                    # otherwise copy the validated piece and still persist the
                    # business decision.
                    try:
                        if destination.is_file() and _file_sha256(destination) == sha:
                            moved_path = destination
                        else:
                            destination = _unique_path(destination)
                            shutil.copy2(source, destination)
                            moved_path = destination
                    except OSError:
                        moved_path = source
        side = SALES_SUBDIR if category is DocumentCategory.VENTES_CLIENTS else PURCHASES_SUBDIR
        try:
            piece = self._copy_piece_once(
                moved_path,
                side=side,
                document_type=decision.document_type,
                document_number=decision.document_number,
                document_date=decision.document_date,
                party=decision.supplier,
            )
        except OSError:
            piece = None
        source_rel = _tenant_relative(moved_path, self._tenant_dir)
        store_piece = (
            _store_piece_relative(piece, self._export_dir)
            if piece is not None
            else None
        )
        return source_rel, store_piece

    def _copy_piece_once(
        self,
        source: Path,
        *,
        side: str,
        document_type: str,
        document_number: str,
        document_date: date | None,
        party: str,
    ) -> Path:
        root = self._export_dir / "PIECES_JOINTES" / (
            SALES_SUBDIR if side == SALES_SUBDIR else PURCHASES_SUBDIR
        )
        root.mkdir(parents=True, exist_ok=True)
        from services.pdf_splitter_and_linker import (
            normalized_purchase_filename,
            normalized_sales_filename,
        )

        if side == SALES_SUBDIR:
            filename = normalized_sales_filename(
                document_number, document_date, party
            )
        else:
            filename = normalized_purchase_filename(
                party, document_type, document_number, document_date
            )
        target = root / filename
        if target.is_file():
            try:
                if _file_sha256(target) == _file_sha256(source):
                    return target
            except OSError:
                pass
            target = _unique_path(target)
        temporary = target.with_name(target.name + ".tmp-p22")
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
        return target

    def _persist_decision_line(
        self,
        sha: str,
        source: Path,
        decision: EvidenceDecision,
        source_rel: str,
        store_piece: str | None,
    ) -> None:
        non_accountable = (
            decision.document_type in NON_ACCOUNTABLE_DOCUMENT_TYPES
            or not decision.is_accountable
        )
        line: dict[str, Any] = {
            "operation_date": (
                decision.document_date.isoformat()
                if decision.document_date is not None
                else None
            ),
            "label": decision.document_number or source.stem,
            "currency": "EUR",
            "counterparty": decision.supplier,
            "invoice_id": decision.document_number,
            "invoice_url": source_rel,
            "category": decision.category.value,
            "amount_ht": None if non_accountable or decision.amount_ht is None else str(decision.amount_ht),
            "amount_tva": None if non_accountable or decision.amount_tva is None else str(decision.amount_tva),
            "amount_ttc": None if non_accountable or decision.amount_ttc is None else str(decision.amount_ttc),
            "document_type": decision.document_type,
            "expense_subcategory": decision.expense_subcategory,
            "payment_method": decision.payment_method,
            "payment_reference": decision.payment_method,
            "piece_pdf": store_piece,
            "status": DocumentStatus.VALIDE.value,
            "source_status": "NON_COMPTABLE" if non_accountable else "VALIDE_AUTOMATIQUE",
            "balance_due": (
                "0.00"
                if decision.already_paid
                or decision.payment_method in {"CARTE_BANCAIRE", "PRELEVEMENT_SEPA"}
                else None
            ),
            "already_paid_on_invoice": "1" if decision.already_paid else None,
            "reconciliation_status": (
                "PAYEE_RAPPROCHEE" if decision.already_paid else None
            ),
            "reconciliation_reference": (
                "REGLEMENT_VALIDE" if decision.already_paid else None
            ),
        }
        if decision.document_type == "AVOIR":
            line["document_type"] = "AVOIR"
        self._store.upsert_document_line(self._tenant_id, derive_document_id(sha), line)

    def _persist_pending_line(
        self,
        sha: str,
        source: Path,
        decision: EvidenceDecision,
        source_rel: str,
    ) -> None:
        line = {
            "operation_date": (
                decision.document_date.isoformat()
                if decision.document_date is not None
                else None
            ),
            "label": decision.document_number or source.stem,
            "currency": "EUR",
            "counterparty": decision.supplier,
            "invoice_id": decision.document_number,
            "invoice_url": source_rel,
            "category": decision.category.value,
            "amount_ht": None if decision.amount_ht is None else str(decision.amount_ht),
            "amount_tva": None if decision.amount_tva is None else str(decision.amount_tva),
            "amount_ttc": None if decision.amount_ttc is None else str(decision.amount_ttc),
            "document_type": decision.document_type,
            "status": DocumentStatus.EN_ATTENTE.value,
        }
        self._store.upsert_document_line(self._tenant_id, derive_document_id(sha), line)

    def _mark_workbook(
        self,
        sha: str,
        filename: str,
        decision: EvidenceDecision,
        piece_rel: str,
    ) -> None:
        try:
            self._workbook.upsert(
                sha,
                filename,
                [],
                decision.category,
                piece_pdf=piece_rel,
            )
            rows = {row.sha256: row for row in self._workbook.read_rows()}
            current = rows.get(sha)
            if current is None:
                return
            if current.statut == DocumentStatus.EN_ATTENTE.value:
                self._workbook.align_statut(sha, decision.status)
            if decision.expense_subcategory:
                try:
                    ExpenseSubCategory(decision.expense_subcategory)
                except ValueError:
                    pass
                else:
                    self._workbook.update_p17_metadata(
                        sha, expense_subcategory=decision.expense_subcategory
                    )
        except WorkbookLockedError:
            return
        except AcompleterWorkbookError:
            return

    def _write_generic_pending_rows(self, seen: set[str]) -> int:
        pending = 0
        for path in sorted(self._a_completer_dir.glob("*.pdf")):
            try:
                sha = _file_sha256(path)
            except OSError:
                continue
            if sha in seen:
                continue
            seen.add(sha)
            line = self._store.get_line(self._tenant_id, derive_document_id(sha))
            supplier = str((line or {}).get("counterparty") or "")
            category = str((line or {}).get("category") or DocumentCategory.DIVERS_A_CLASSER.value)
            try:
                category_value = DocumentCategory(category)
            except ValueError:
                category_value = DocumentCategory.DIVERS_A_CLASSER
            reason, action = build_business_explanation(
                path.name, supplier=supplier or None
            )
            try:
                self._workbook.upsert(
                    sha,
                    path.name,
                    [],
                    category_value,
                    piece_pdf=_tenant_relative(path, self._tenant_dir),
                )
            except (WorkbookLockedError, AcompleterWorkbookError):
                pass
            pending += 1
        return pending

    def _write_readable_views(self) -> None:
        self.prefill_arbitration_columns()
        rows = self._readable_rows()
        pending_rows = [
            row
            for row in rows
            if row.status_value == DocumentStatus.EN_ATTENTE.value
        ]
        try:
            write_readable_workbook(self.readable_path, pending_rows)
        except Exception:  # noqa: BLE001 - manager copy is best effort
            pass
        try:
            self._workbook.write_readable_rows(rows, update_status=True)
        except (WorkbookLockedError, AcompleterWorkbookError, OSError):
            pass

    def _readable_rows(self) -> list[ReadableRow]:
        rows: list[ReadableRow] = []
        seen: set[str] = set()
        for path, sha, decision in self._collect_evidence_decisions():
            if sha in seen:
                continue
            seen.add(sha)
            piece_rel = self._piece_rel_for_sha(sha, path)
            line = self._store.get_line(
                self._tenant_id, derive_document_id(sha)
            )
            rows.append(
                ReadableRow(
                    sha256=sha,
                    filename=path.name,
                    supplier=decision.supplier,
                    document_number=decision.document_number,
                    document_date=decision.document_date,
                    amount_ht=decision.amount_ht,
                    amount_tva=decision.amount_tva,
                    amount_ttc=decision.amount_ttc,
                    category=decision.category,
                    reason=decision.reason,
                    action=decision.action,
                    status=decision.status,
                    piece_pdf=piece_rel,
                    rule=decision.rule,
                    extra=self._readable_extra(
                        sha,
                        line,
                        category=decision.category.value,
                        subcategory=decision.expense_subcategory,
                    ),
                )
            )
        for path in sorted(self._a_completer_dir.glob("*.pdf")):
            try:
                sha = _file_sha256(path)
            except OSError:
                continue
            if sha in seen:
                continue
            seen.add(sha)
            line = self._store.get_line(self._tenant_id, derive_document_id(sha))
            supplier = str((line or {}).get("counterparty") or "") or "FOURNISSEUR À CONFIRMER"
            category_value = str(
                (line or {}).get("category") or DocumentCategory.DIVERS_A_CLASSER.value
            )
            reason, action = build_business_explanation(
                path.name, supplier=supplier if supplier != "FOURNISSEUR À CONFIRMER" else None
            )
            rows.append(
                ReadableRow(
                    sha256=sha,
                    filename=path.name,
                    supplier=supplier,
                    document_number=str((line or {}).get("invoice_id") or path.stem),
                    document_date=None,
                    amount_ht=_parse_decimal((line or {}).get("amount_ht")),
                    amount_tva=_parse_decimal((line or {}).get("amount_tva")),
                    amount_ttc=_parse_decimal((line or {}).get("amount_ttc")),
                    category=category_value,
                    reason=reason,
                    action=action,
                    status=DocumentStatus.EN_ATTENTE,
                    piece_pdf=_tenant_relative(path, self._tenant_dir),
                    extra=self._readable_extra(
                        sha, line, category=category_value
                    ),
                )
            )
        for row in self._read_workbook_rows():
            if row.sha256 in seen:
                continue
            seen.add(row.sha256)
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
            if line is None:
                continue
            status = str(row.statut or DocumentStatus.EN_ATTENTE.value).upper()
            if status == DocumentStatus.VALIDE.value:
                reason = "Pièce déjà validée et classée ; aucune action requise."
                action = "VALIDÉ — pièce archivée dans le grand livre."
            elif status == DocumentStatus.REJETE.value:
                reason = "Pièce rejetée par le dirigeant ; elle reste exclue des calculs."
                action = "REJETÉ — aucune action requise."
            else:
                reason, action = build_business_explanation(
                    row.nom_fichier,
                    supplier=str(line.get("counterparty") or "") or None,
                )
            rows.append(
                ReadableRow(
                    sha256=row.sha256,
                    filename=row.nom_fichier,
                    supplier=str(line.get("counterparty") or "FOURNISSEUR À CONFIRMER"),
                    document_number=str(
                        line.get("invoice_id") or row.nom_fichier
                    ),
                    document_date=None,
                    amount_ht=_parse_decimal(line.get("amount_ht")),
                    amount_tva=_parse_decimal(line.get("amount_tva")),
                    amount_ttc=_parse_decimal(line.get("amount_ttc")),
                    category=str(
                        line.get("category")
                        or row.categorie_suggeree
                        or DocumentCategory.DIVERS_A_CLASSER.value
                    ),
                    reason=reason,
                    action=action,
                    status=status,
                    piece_pdf=self._piece_rel_for_sha(
                        row.sha256, self._tenant_dir / row.nom_fichier
                    ),
                    extra=self._readable_extra(
                        row.sha256,
                        line,
                        category=str(
                            line.get("category")
                            or row.categorie_suggeree
                            or DocumentCategory.DIVERS_A_CLASSER.value
                        ),
                    ),
                )
            )
        known = {row.filename for row in rows}
        if not self._enable_tier_workflow:
            return rows
        for name, line in self._tier_reference.unknown_tier_entries(
            self._store.list_master_lines(self._tenant_id)
        ):
            safe_name = str(name).replace("/", "_").replace("\\", "_").strip()
            if not safe_name:
                continue
            filename = f"TIER__{safe_name}.tier"
            if filename in known:
                continue
            digest = hashlib.sha256(
                f"tier:{name}".encode("utf-8")
            ).hexdigest()
            document_date = None
            raw_date = str(line.get("operation_date") or "")[:10]
            if raw_date:
                try:
                    document_date = date.fromisoformat(raw_date)
                except ValueError:
                    document_date = None
            piece_pdf = str(line.get("piece_pdf") or "").strip() or None
            if piece_pdf is None:
                invoice_url = str(line.get("invoice_url") or "").strip()
                if invoice_url:
                    piece_pdf = invoice_url
            amount_ttc = _parse_decimal(line.get("amount_ttc"))
            if amount_ttc is None:
                amount_ttc = _parse_decimal(
                    line.get("debit_amount") or line.get("credit_amount")
                )
            rows.append(
                ReadableRow(
                    sha256=digest,
                    filename=filename,
                    supplier=str(name),
                    document_number=str(
                        line.get("invoice_id")
                        or line.get("label")
                        or line.get("line_id")
                        or ""
                    ),
                    document_date=document_date,
                    amount_ht=_parse_decimal(line.get("amount_ht")),
                    amount_tva=_parse_decimal(line.get("amount_tva")),
                    amount_ttc=amount_ttc,
                    category=str(
                        line.get("category")
                        or DocumentCategory.DIVERS_A_CLASSER.value
                    ),
                    reason=(
                        "Tiers inconnu du référentiel verrouillé : aucun onglet "
                        "ne sera créé avant autorisation."
                    ),
                    action=(
                        "Choisir un rattachement ALIAS ou créer un nouveau tiers."
                    ),
                    status=DocumentStatus.EN_ATTENTE,
                    piece_pdf=piece_pdf,
                    extra={
                        "payment_source": "BANQUE_PRO",
                        "counterparty_account": "622600",
                        "fiscal_year_target": str(self._year),
                        "deferred_month": "NON",
                        "report": "NON",
                        "tier_alias_target": "",
                    },
                )
            )
        for instrument in self._unmatched_instruments:
            filename = f"INSTRUMENT__{instrument.line_id}.tier"
            if filename in known:
                continue
            digest = hashlib.sha256(
                f"instrument:{instrument.line_id}".encode("utf-8")
            ).hexdigest()
            document_date = None
            raw_date = str(instrument.operation_date or "")[:10]
            if raw_date:
                try:
                    document_date = date.fromisoformat(raw_date)
                except ValueError:
                    document_date = None
            rows.append(
                ReadableRow(
                    sha256=digest,
                    filename=filename,
                    supplier="INSTRUMENT DE PAIEMENT",
                    document_number=instrument.label,
                    document_date=document_date,
                    amount_ht=None,
                    amount_tva=None,
                    amount_ttc=instrument.amount,
                    category=DocumentCategory.DIVERS_A_CLASSER,
                    reason=(
                        f"LCR / Chèque de {instrument.amount} € du "
                        f"{instrument.operation_date} sans facture "
                        "correspondante : sélectionner le Fournisseur/Client "
                        "concerné."
                    ),
                    action=(
                        "Choisir le tiers concerné ou déposer la facture "
                        "manquante."
                    ),
                    status=DocumentStatus.EN_ATTENTE,
                    extra={
                        "payment_source": "BANQUE_PRO",
                        "counterparty_account": "622600",
                        "fiscal_year_target": str(self._year),
                        "deferred_month": "NON",
                        "report": "NON",
                        "tier_alias_target": "",
                    },
                )
            )
        return rows

    def _piece_rel_for_sha(self, sha: str, fallback: Path) -> str:
        line = self._store.get_line(self._tenant_id, derive_document_id(sha))
        piece = str((line or {}).get("piece_pdf") or "").strip()
        if piece:
            candidate = self._export_dir / piece
            if candidate.is_file():
                return _tenant_relative(candidate, self._tenant_dir)
        invoice_url = str((line or {}).get("invoice_url") or "").strip()
        if invoice_url:
            candidate = self._tenant_dir / invoice_url
            if candidate.is_file():
                return invoice_url
        return _tenant_relative(fallback, self._tenant_dir)

    def _read_workbook_rows(self) -> list[Any]:
        try:
            return self._workbook.read_rows()
        except (WorkbookLockedError, AcompleterWorkbookError):
            return []

    # -- stage 2 ---------------------------------------------------------

    def stage2_bank_reconciliation(self) -> StageReport:
        imported = 0
        messages: list[str] = []
        statements = []
        for path in self._ofx_paths():
            try:
                statement = parse_ofx_statement(path.read_bytes())
                report = import_ofx_statement(self._store, self._tenant_id, statement)
                imported += report.created
                statements.append(statement)
            except Exception as exc:  # noqa: BLE001
                messages.append(f"{path.name}:OFX_ERROR:{exc}")
        reconciled = self._reconcile_supplier_debits(statements)
        customer_reconciled = 0
        try:
            from services.customer_invoice_reconciliation import (
                reconcile_customer_invoices,
            )

            bordereaux = self._bordereau_paths()
            report = reconcile_customer_invoices(
                self._store,
                self._tenant_id,
                bordereaux=bordereaux,
            )
            customer_reconciled = report.reconciled_invoices
            messages.append(
                f"Lettrage clients : {report.reconciled_invoices} facture(s), "
                f"{len(report.errors)} erreur(s)."
            )
        except Exception as exc:  # noqa: BLE001 - customer lettering never blocks
            messages.append(f"LETTRAGE_CLIENTS_IGNORE:{exc}")
        instrument_reconciled = 0
        try:
            instrument_report = reconcile_payment_instruments(
                self._store, self._tenant_id
            )
            self._unmatched_instruments = instrument_report.unmatched
            instrument_reconciled = len(instrument_report.matched)
            messages.append(
                f"LCR/Chèques rapprochés : {instrument_reconciled}, "
                f"sans facture : {len(instrument_report.unmatched)}."
            )
        except Exception as exc:  # noqa: BLE001 - instrument lettering never blocks
            messages.append(f"LETTRAGE_LCR_CHEQUE_IGNORE:{exc}")
        master = self._export_master_tables()
        workbooks = (
            master.banque_tva_path,
            master.achats_path,
            *self._export_auxiliary_ledgers(),
        )
        self._sync_mirror(workbooks)
        messages.append(f"Lignes OFX importées : {imported}")
        messages.append(f"Débits fournisseurs rapprochés : {reconciled}")
        return StageReport(
            stage=2,
            name=STAGE_NAMES[2],
            processed=len(statements),
            imported_ofx=imported,
            reconciled=reconciled + customer_reconciled + instrument_reconciled,
            workbooks=workbooks,
            messages=tuple(messages),
        )

    def _ofx_paths(self) -> list[Path]:
        paths: list[Path] = []
        sources = self._tree.state_dir(FolderState.EXPORT_COMPTA) / "SOURCES_REEL"
        if sources.is_dir():
            paths.extend(sources.glob("*.ofx"))
            paths.extend(sources.glob("*.qfx"))
        inbox = self._tree.state_dir(FolderState.INBOX)
        if inbox.is_dir():
            paths.extend(inbox.glob("*.ofx"))
            paths.extend(inbox.glob("*.qfx"))
        return sorted(dict.fromkeys(paths), key=lambda item: item.name.casefold())

    def _bordereau_paths(self) -> tuple[Path, ...]:
        paths: list[Path] = []
        for state in (FolderState.EXPORT_COMPTA, FolderState.VALIDE_PRET_FEC):
            directory = self._tree.state_dir(state)
            if not directory.is_dir():
                continue
            for path in directory.rglob("*.pdf"):
                name = path.name.casefold()
                if "bordereau" in name or "remise" in name:
                    paths.append(path)
        return tuple(sorted(dict.fromkeys(paths), key=lambda item: str(item).casefold()))

    def _reconcile_supplier_debits(self, statements: Iterable[Any]) -> int:
        transactions = []
        for statement in statements:
            transactions.extend(statement.transactions)
        if not transactions:
            transactions = [
                type(
                    "Transaction",
                    (),
                    {
                        "amount": -(
                            _parse_decimal(row.get("debit_amount")) or Decimal("0")
                        ),
                        "operation_date": date.fromisoformat(
                            str(row.get("operation_date"))[:10]
                        )
                        if row.get("operation_date")
                        else None,
                        "fitid": str(row.get("fitid") or row.get("line_id") or ""),
                        "label": str(row.get("label") or ""),
                        "memo": str(row.get("memo") or ""),
                    },
                )()
                for row in self._bank_rows_with_debit()
            ]
        matched = 0
        used_fitids: set[str] = set()
        rows = self._store.list_master_lines(self._tenant_id)
        for row in rows:
            status = str(row.get("raw_status") or row.get("status") or "").upper()
            if status != "VALIDE":
                continue
            if str(row.get("category") or "") not in PURCHASE_CATEGORIES:
                continue
            if str(row.get("reconciliation_status") or "").upper() in PAID_STATUSES:
                continue
            amount = _parse_decimal(row.get("amount_ttc"))
            if amount is None or amount <= 0:
                continue
            supplier = str(row.get("counterparty") or row.get("supplier") or "")
            token_values = _supplier_tokens(supplier)
            number = _row_document_number(row)
            invoice_date = None
            raw_date = str(row.get("operation_date") or "")[:10]
            if raw_date:
                try:
                    invoice_date = date.fromisoformat(raw_date)
                except ValueError:
                    invoice_date = None
            candidate = None
            best_score: int | None = None
            for transaction in transactions:
                tx_amount = getattr(transaction, "amount", None)
                if tx_amount is None or tx_amount >= 0:
                    continue
                if (-tx_amount).quantize(Decimal("0.01")) != amount:
                    continue
                fitid = str(getattr(transaction, "fitid", "") or "")
                if fitid and fitid in used_fitids:
                    continue
                label = _fold(
                    f"{getattr(transaction, 'label', '')} "
                    f"{getattr(transaction, 'memo', '')}"
                )
                number_match = bool(number and _fold(number) in label)
                token_match = bool(
                    token_values and any(token in label for token in token_values)
                )
                if not number_match and not token_match:
                    continue
                score = 0
                if number_match:
                    score += 6
                if token_match:
                    score += 3
                operation_date = getattr(transaction, "operation_date", None)
                if invoice_date is not None and isinstance(operation_date, date):
                    delta = abs((operation_date - invoice_date).days)
                    if delta > 60:
                        continue
                    score += max(0, 4 - delta // 10)
                if best_score is None or score > best_score:
                    best_score = score
                    candidate = transaction
            if candidate is None:
                continue
            fitid = str(getattr(candidate, "fitid", "") or "")
            if fitid:
                used_fitids.add(fitid)
            line = self._store.get_line(self._tenant_id, str(row.get("line_id")))
            if line is None:
                continue
            updated = dict(line)
            operation_date = getattr(candidate, "operation_date", None)
            fitid = str(getattr(candidate, "fitid", "") or "")
            updated.update(
                {
                    "status": str(
                        line.get("status") or line.get("raw_status") or "VALIDE"
                    ),
                    "reconciliation_status": "PAYEE_RAPPROCHEE",
                    "reconciliation_date": (
                        operation_date.isoformat()
                        if hasattr(operation_date, "isoformat")
                        else str(operation_date or "")
                    ),
                    "reconciliation_reference": fitid,
                    "reconciliation_amount": str(amount),
                    "fitid": fitid,
                    "payment_reference": fitid,
                    "payment_method": "CARTE_BANCAIRE",
                    "already_paid_on_invoice": "1",
                    "balance_due": "0.00",
                }
            )
            updated.pop("line_id", None)
            self._store.upsert_document_line(
                self._tenant_id, str(row.get("line_id")), updated
            )
            matched += 1
        return matched

    def _bank_rows_with_debit(self) -> list[dict[str, Any]]:
        return [
            row
            for row in self._store.list_master_lines(self._tenant_id)
            if row.get("debit_amount") not in (None, "")
        ]

    # -- stage 3 ---------------------------------------------------------

    def stage3_ventilation_and_coherence(self) -> StageReport:
        human_applied = self.apply_human_decisions()
        repaired = self._repair_piece_links()
        self._write_readable_views()
        workbooks = self._refresh_workbooks()
        self._sync_mirror(workbooks)
        issues = self._coherence_issues()
        report_path = self._write_coherence_report(issues)
        if report_path is not None:
            workbooks = tuple(dict.fromkeys((*workbooks, report_path)))
        messages = [
            f"Classeurs rafraîchis : {len(workbooks)}",
            f"Décisions humaines appliquées : {human_applied}",
            f"Liens PDF réparés : {repaired}",
            f"Anomalies de pièces : {len(issues)}",
        ]
        return StageReport(
            stage=3,
            name=STAGE_NAMES[3],
            processed=len(workbooks),
            pending=len(issues),
            workbooks=workbooks,
            messages=tuple(messages),
            details=tuple(issues[:50]),
        )

    def _refresh_workbooks(self) -> tuple[Path, ...]:
        from services.accounting_spreadsheet_export_service import (
            CategoryOverviewExporter,
            CategorySummaryExporter,
            ClientLedgerExporter,
            ExpenseSubCategoryExporter,
            MasterTableExporter,
            PersonalExpenseNoteExporter,
            SupplierLedgerExporter,
        )
        from services.customer_invoice_batch_importer import (
            export_customer_sales_workbook,
        )

        paths: list[Path] = []
        try:
            paths.append(
                export_customer_sales_workbook(
                    self._tenant_id,
                    store=self._store,
                    root=self._tree.root,
                    export_dir=self._export_dir,
                ).path
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            paths.append(
                SupplierLedgerExporter(
                    self._store,
                    export_dir=self._export_dir.parent,
                    strict_tiers=True,
                    include_matched_pieces=True,
                ).export_supplier_ledger(self._tenant_id)
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            paths.append(
                ClientLedgerExporter(
                    self._store,
                    export_dir=self._export_dir.parent,
                    strict_tiers=True,
                    include_matched_pieces=True,
                ).export(self._tenant_id)
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            paths.append(
                CategoryOverviewExporter(
                    self._store,
                    root=self._tree.root,
                    export_dir=self._export_dir.parent,
                ).export(self._tenant_id)
            )
        except Exception:  # noqa: BLE001
            pass
        try:
            master = MasterTableExporter(
                self._store,
                root=self._tree.root,
                export_dir=self._export_dir,
            ).export(self._tenant_id)
            paths.extend((master.achats_path, master.banque_tva_path))
        except Exception:  # noqa: BLE001
            pass
        try:
            expenses = ExpenseSubCategoryExporter(
                self._store,
                root=self._tree.root,
                export_dir=self._export_dir,
            ).export(self._tenant_id)
            paths.append(expenses.global_path)
            paths.extend(expenses.subtable_paths.values())
        except Exception:  # noqa: BLE001
            pass
        try:
            notes = PersonalExpenseNoteExporter(
                self._store, export_dir=self._export_dir.parent
            ).export(self._tenant_id)
            paths.extend(notes.paths.values())
        except Exception:  # noqa: BLE001
            pass
        for category in DocumentCategory:
            try:
                if not self._store.list_category_lines(self._tenant_id, category):
                    continue
                paths.append(
                    CategorySummaryExporter(
                        self._store,
                        export_dir=self._export_dir.parent,
                        include_payment_date=True,
                    ).export_category_summary(self._tenant_id, category)
                )
            except Exception:  # noqa: BLE001
                continue
        return tuple(dict.fromkeys(paths))

    def _repair_piece_links(self) -> int:
        repaired = 0
        for row in self._store.list_master_lines(self._tenant_id):
            status = str(row.get("raw_status") or row.get("status") or "").upper()
            if status not in {"VALIDE", "EN_ATTENTE"}:
                continue
            document_type = str(row.get("document_type") or "").upper()
            if document_type == "OFX":
                continue
            if row.get("debit_amount") not in (None, "") or row.get(
                "credit_amount"
            ) not in (None, ""):
                continue
            line_id = str(row.get("line_id") or "")
            if not line_id:
                continue
            piece = str(row.get("piece_pdf") or "").strip()
            if piece and (self._export_dir / piece).is_file():
                continue
            number = _row_document_number(row)
            side = (
                SALES_SUBDIR
                if str(row.get("category") or "") == DocumentCategory.VENTES_CLIENTS.value
                else PURCHASES_SUBDIR
            )
            found = find_piece_for_document(
                self._export_dir, number, side=side
            )
            if found is not None:
                line = self._store.get_line(self._tenant_id, line_id)
                if line is not None:
                    updated = dict(line)
                    updated.pop("line_id", None)
                    updated["status"] = status
                    updated["piece_pdf"] = _store_piece_relative(
                        found, self._export_dir
                    )
                    self._store.upsert_document_line(
                        self._tenant_id, line_id, updated
                    )
                    repaired += 1
                continue
            source: Path | None = None
            url = str(row.get("invoice_url") or "").strip()
            if url:
                candidate = self._tenant_dir / url
                if candidate.is_file():
                    source = candidate
            if source is None:
                continue
            operation_date = None
            raw_date = str(row.get("operation_date") or "")[:10]
            if raw_date:
                try:
                    operation_date = date.fromisoformat(raw_date)
                except ValueError:
                    operation_date = None
            try:
                copied = self._copy_piece_once(
                    source,
                    side=side,
                    document_type=document_type or "FACTURE",
                    document_number=number,
                    document_date=operation_date,
                    party=str(row.get("counterparty") or row.get("supplier") or ""),
                )
            except Exception:  # noqa: BLE001
                continue
            line = self._store.get_line(self._tenant_id, line_id)
            if line is None:
                continue
            updated = dict(line)
            updated.pop("line_id", None)
            updated["status"] = status
            updated["piece_pdf"] = _store_piece_relative(copied, self._export_dir)
            self._store.upsert_document_line(self._tenant_id, line_id, updated)
            repaired += 1
        return repaired

    def _coherence_issues(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for row in self._store.list_master_lines(self._tenant_id):
            status = str(row.get("raw_status") or row.get("status") or "").upper()
            if status not in {"VALIDE", "EN_ATTENTE"}:
                continue
            if str(row.get("document_type") or "").upper() == "OFX":
                continue
            if row.get("debit_amount") not in (None, "") or row.get(
                "credit_amount"
            ) not in (None, ""):
                continue
            piece = str(row.get("piece_pdf") or "").strip()
            url = str(row.get("invoice_url") or "").strip()
            candidates = []
            if piece:
                candidates.append(self._export_dir / piece)
            if url:
                candidates.append(self._tenant_dir / url)
            if not any(path.is_file() for path in candidates):
                issues.append(
                    {
                        "line_id": str(row.get("line_id") or ""),
                        "supplier": str(row.get("counterparty") or ""),
                        "document": _row_document_number(row),
                        "status": status,
                        "issue": "PDF_INTROUVABLE",
                        "piece_pdf": piece,
                        "invoice_url": url,
                    }
                )
                continue
            if piece:
                try:
                    link = relative_link(self._export_dir / piece, self._export_dir)
                except Exception:
                    link = ""
                if not link or link.startswith("/") or re.match(r"^[A-Za-z]:", link):
                    issues.append(
                        {
                            "line_id": str(row.get("line_id") or ""),
                            "supplier": str(row.get("counterparty") or ""),
                            "document": _row_document_number(row),
                            "status": status,
                            "issue": "LIEN_RELATIF_INVALIDE",
                            "piece_pdf": piece,
                            "invoice_url": url,
                        }
                    )
        return issues

    def _write_coherence_report(self, issues: list[dict[str, Any]]) -> Path | None:
        try:
            from openpyxl import Workbook

            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "COHERENCE"
            headers = (
                "Ligne",
                "Fournisseur",
                "N° Pièce",
                "Statut",
                "Anomalie",
                "Pièce PDF",
                "Chemin source",
            )
            sheet.append(list(headers))
            for issue in issues:
                sheet.append(
                    [
                        issue.get("line_id", ""),
                        issue.get("supplier", ""),
                        issue.get("document", ""),
                        issue.get("status", ""),
                        issue.get("issue", ""),
                        issue.get("piece_pdf", ""),
                        issue.get("invoice_url", ""),
                    ]
                )
            path = self._export_dir / _COHERENCE_FILENAME
            temporary = path.with_name(path.name + ".tmp-p22")
            workbook.save(temporary)
            workbook.close()
            os.replace(temporary, path)
            return path
        except Exception:  # noqa: BLE001 - report is informative only
            return None

    # -- stage 4 ---------------------------------------------------------

    def stage4_simulations(self) -> StageReport:
        workbooks: list[Path] = []
        messages: list[str] = []
        ca12_path: Path | None = None
        balance_path: Path | None = None
        try:
            service = VatSimulationService(
                self._store,
                export_dir=self._export_dir.parent,
                cash_basis=True,
            )
            report = service.simulate(self._tenant_id, self._year)
            ca12_path = service.write_report(report)
            workbooks.append(ca12_path)
            messages.append(
                f"CA12 {self._year} : TVA nette {report.scenario_a.net_tva} EUR"
            )
        except Exception as exc:  # noqa: BLE001 - CA12 never blocks the balance
            messages.append(f"CA12_IGNOREE:{exc}")
        try:
            balance = BalanceSimulationService(
                self._store, export_dir=self._export_dir.parent
            )
            balance_report = balance.simulate(self._tenant_id, self._year)
            balance_path = balance.write_report(balance_report)
            workbooks.append(balance_path)
            messages.append(
                f"Résultat d'exploitation estimé : "
                f"{balance_report.operating_result} EUR"
            )
        except Exception as exc:  # noqa: BLE001
            messages.append(f"BILAN_IGNORE:{exc}")
        expected_ca12 = (
            self._export_dir / CA12_SIMULATION_FILENAME.format(year=self._year)
        )
        expected_balance = self._export_dir / BALANCE_FILENAME.format(year=self._year)
        if ca12_path is None and expected_ca12.is_file():
            ca12_path = expected_ca12
            workbooks.append(ca12_path)
        if balance_path is None and expected_balance.is_file():
            balance_path = expected_balance
            workbooks.append(balance_path)
        self._sync_mirror(workbooks)
        return StageReport(
            stage=4,
            name=STAGE_NAMES[4],
            processed=2,
            workbooks=tuple(dict.fromkeys(workbooks)),
            messages=tuple(messages),
        )

    # -- shared export helpers -------------------------------------------

    def _export_sales_workbook(self) -> Path | None:
        try:
            from services.customer_invoice_batch_importer import (
                export_customer_sales_workbook,
            )

            report = export_customer_sales_workbook(
                self._tenant_id,
                store=self._store,
                root=self._tree.root,
                export_dir=self._export_dir,
            )
            return report.path
        except Exception:  # noqa: BLE001
            return None

    def _export_master_tables(self) -> Any:
        from services.accounting_spreadsheet_export_service import (
            MasterTableExporter,
        )

        return MasterTableExporter(
            self._store, root=self._tree.root, export_dir=self._export_dir
        ).export(self._tenant_id)

    def _export_auxiliary_ledgers(self) -> tuple[Path, ...]:
        from services.accounting_spreadsheet_export_service import (
            ClientLedgerExporter,
            SupplierLedgerExporter,
        )

        paths: list[Path] = []
        try:
            paths.append(
                SupplierLedgerExporter(
                    self._store,
                    export_dir=self._export_dir.parent,
                    strict_tiers=True,
                    include_matched_pieces=True,
                ).export_supplier_ledger(self._tenant_id)
            )
        except Exception:  # noqa: BLE001 - one ledger never blocks the stage
            pass
        try:
            paths.append(
                ClientLedgerExporter(
                    self._store,
                    export_dir=self._export_dir.parent,
                    strict_tiers=True,
                    include_matched_pieces=True,
                ).export(self._tenant_id)
            )
        except Exception:  # noqa: BLE001
            pass
        return tuple(paths)

    def _repair_stale_lock(self) -> None:
        lock = self._workbook.find_lock_file()
        if lock is None:
            return
        try:
            age = __import__("time").time() - lock.stat().st_mtime
        except OSError:
            return
        if age >= 60:
            try:
                lock.unlink()
            except OSError:
                pass


def run_stage(
    stage: int,
    *,
    tenant_id: str = DEFAULT_TENANT,
    root: str | os.PathLike[str] | None = None,
    db_path: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    year: int = DEFAULT_YEAR,
) -> StageReport:
    pipeline = ApdFourStagePipeline(
        tenant_id,
        root=root,
        db_path=db_path,
        export_dir=export_dir,
        year=year,
    )
    return pipeline.run_stage(stage)


def run_all_stages(
    *,
    tenant_id: str = DEFAULT_TENANT,
    root: str | os.PathLike[str] | None = None,
    db_path: str | os.PathLike[str] | None = None,
    export_dir: str | os.PathLike[str] | None = None,
    year: int = DEFAULT_YEAR,
    pause_seconds: float = 0,
) -> tuple[StageReport, ...]:
    import time

    pipeline = ApdFourStagePipeline(
        tenant_id,
        root=root,
        db_path=db_path,
        export_dir=export_dir,
        year=year,
    )
    pause = None
    if pause_seconds and pause_seconds > 0:
        pause = lambda _stage: time.sleep(pause_seconds)  # noqa: E731
    return pipeline.run_all(pause=pause)


__all__ = [
    "ApdFourStagePipeline",
    "ApdPipelineError",
    "DEFAULT_TENANT",
    "DEFAULT_YEAR",
    "FourStagePipeline",
    "STAGE_NAMES",
    "StageReport",
    "run_all_stages",
    "run_stage",
]

FourStagePipeline = ApdFourStagePipeline
