"""P22 - Hermès accounting bridge (Telegram-ready structured commands).

The Telegram agent never parses Excel by itself. It calls these four stable
commands:

* :meth:`HermesAccountingBridge.status_summary`
* :meth:`HermesAccountingBridge.list_pending_questions`
* :meth:`HermesAccountingBridge.decide_document`
* :meth:`HermesAccountingBridge.run_stage`

Every method returns JSON-serialisable dictionaries so the same payload can be
used by the local web interface, a CLI and Telegram.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from schemas.folder_state_schema import DocumentCategory, DocumentStatus, FolderState
from services.acompleter_workbook_service import (
    AcompleterWorkbook,
    AcompleterWorkbookError,
    WorkbookLockedError,
    derive_document_id,
)
from schemas.accounting_choices import (
    normalize_category,
    normalize_exercise,
    normalize_month,
    normalize_payment_source,
)
from services.apd_evidence_rules import build_business_explanation, evaluate_evidence
from services.apd_four_stage_pipeline import (
    ApdFourStagePipeline,
    DEFAULT_TENANT,
    DEFAULT_YEAR,
)
from services.folder_tree_service import ensure_tree, validate_tenant_id
from services.pdf_splitter_and_linker import PURCHASES_SUBDIR, SALES_SUBDIR, relative_link
from services.sqlite_accounting_store import DEFAULT_DB_PATH, SqliteAccountingStore

VALID_DECISIONS = {
    "VALIDE": DocumentStatus.VALIDE,
    "VALIDER": DocumentStatus.VALIDE,
    "VALIDÉ": DocumentStatus.VALIDE,
    "ACCEPTER": DocumentStatus.VALIDE,
    "OK": DocumentStatus.VALIDE,
    "REJETE": DocumentStatus.REJETE,
    "REJETER": DocumentStatus.REJETE,
    "REJETÉ": DocumentStatus.REJETE,
    "REFUSER": DocumentStatus.REJETE,
    "KO": DocumentStatus.REJETE,
    "EN_ATTENTE": DocumentStatus.EN_ATTENTE,
    "ATTENDRE": DocumentStatus.EN_ATTENTE,
    "QUESTION": DocumentStatus.EN_ATTENTE,
}


class HermesAccountingBridgeError(ValueError):
    """Invalid Hermès command; ``code`` is stable for Telegram replies."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:  # noqa: BLE001
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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


def _normalise_decision(value: Any) -> DocumentStatus:
    if isinstance(value, DocumentStatus):
        return value
    text = _text(value).upper()
    decision = VALID_DECISIONS.get(text)
    if decision is None:
        raise HermesAccountingBridgeError("INVALID_DECISION", repr(value))
    return decision


class HermesAccountingBridge:
    """Structured accounting facade for the Hermès Telegram bot."""

    def __init__(
        self,
        tenant_id: str = DEFAULT_TENANT,
        store: SqliteAccountingStore | None = None,
        root: str | os.PathLike[str] | None = None,
        export_dir: str | os.PathLike[str] | None = None,
        *,
        db_path: str | os.PathLike[str] | None = None,
        year: int = DEFAULT_YEAR,
        pipeline: ApdFourStagePipeline | None = None,
    ) -> None:
        self._tenant_id = validate_tenant_id(tenant_id)
        self._tree = ensure_tree(self._tenant_id, root)
        self._tenant_dir = self._tree.root / self._tenant_id
        self._store = (
            store
            if store is not None
            else SqliteAccountingStore(db_path or DEFAULT_DB_PATH)
        )
        self._export_dir = (
            Path(export_dir)
            if export_dir is not None
            else self._tree.state_dir(FolderState.EXPORT_COMPTA) / self._tenant_id
        )
        self._pipeline = pipeline or ApdFourStagePipeline(
            self._tenant_id,
            store=self._store,
            root=self._tree.root,
            export_dir=self._export_dir,
            year=year,
        )
        self._workbook = AcompleterWorkbook(self._tenant_dir)
        self._year = int(year)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def pipeline(self) -> ApdFourStagePipeline:
        return self._pipeline

    @property
    def workbook(self) -> AcompleterWorkbook:
        return self._workbook

    def _rows(self) -> list[Any]:
        try:
            return self._workbook.read_rows()
        except (WorkbookLockedError, AcompleterWorkbookError):
            return []

    def status_summary(self) -> dict[str, Any]:
        rows = self._rows()
        pending = [row for row in rows if row.statut == DocumentStatus.EN_ATTENTE.value]
        validated = [row for row in rows if row.statut == DocumentStatus.VALIDE.value]
        rejected = [row for row in rows if row.statut == DocumentStatus.REJETE.value]
        store_rows = self._store.list_master_lines(self._tenant_id)
        store_pending = sum(
            1
            for row in store_rows
            if _text(row.get("status") or row.get("raw_status")).upper()
            == DocumentStatus.EN_ATTENTE.value
        )
        return {
            "tenant": self._tenant_id,
            "year": self._year,
            "pending_questions": len(pending),
            "pending": len(pending),
            "validated": len(validated),
            "rejected": len(rejected),
            "store_pending": store_pending,
            "tenant_dir": str(self._tenant_dir),
            "export_dir": str(self._export_dir),
            "readable_workbook": str(
                self._tree.state_dir(FolderState.A_COMPLETER) / "_A_COMPLETER.xlsx"
            ),
            "last_stages": self._pipeline.snapshot().get("last_stages", {}),
        }

    def list_pending_questions(self) -> list[dict[str, Any]]:
        questions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self._rows():
            if row.statut != DocumentStatus.EN_ATTENTE.value:
                continue
            if row.sha256 in seen:
                continue
            seen.add(row.sha256)
            path = self._file_for_row(row)
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
            evidence = evaluate_evidence(path) if path is not None else None
            supplier = (
                _text((line or {}).get("counterparty"))
                or (evidence.supplier if evidence is not None else "")
                or "FOURNISSEUR À CONFIRMER"
            )
            reason, action = build_business_explanation(
                row.nom_fichier,
                supplier=supplier if supplier != "FOURNISSEUR À CONFIRMER" else None,
            )
            if evidence is not None and evidence.status is DocumentStatus.EN_ATTENTE:
                reason, action = evidence.reason, evidence.action
            number = _text(
                (line or {}).get("invoice_id")
                or (evidence.document_number if evidence is not None else "")
                or row.nom_fichier
            )
            category = _text(
                (line or {}).get("category")
                or (evidence.category.value if evidence is not None else "")
                or row.categorie_suggeree
            )
            piece = (
                relative_link(path, self._tenant_dir / "_A_COMPLETER.xlsx")
                if path is not None
                else _text((line or {}).get("invoice_url"))
            )
            questions.append(
                {
                    "doc_id": row.sha256,
                    "id_document": row.id_document or derive_document_id(row.sha256),
                    "filename": row.nom_fichier,
                    "nom_fichier": row.nom_fichier,
                    "supplier": supplier,
                    "fournisseur": supplier,
                    "document_number": number,
                    "numero_piece": number,
                    "document_date": (
                        _text((line or {}).get("operation_date")) or None
                    ),
                    "date_piece": _date((line or {}).get("operation_date")),
                    "amount_ht": _decimal((line or {}).get("amount_ht")),
                    "ht": _decimal((line or {}).get("amount_ht")),
                    "amount_tva": _decimal((line or {}).get("amount_tva")),
                    "tva": _decimal((line or {}).get("amount_tva")),
                    "amount_ttc": _decimal((line or {}).get("amount_ttc")),
                    "ttc": _decimal((line or {}).get("amount_ttc")),
                    "category": category,
                    "categorie": category,
                    "question": reason,
                    "pourquoi": reason,
                    "why": reason,
                    "reason": reason,
                    "advice": action,
                    "conseil": action,
                    "action": action,
                    "piece_pdf": piece,
                    "link": piece,
                }
            )
        return questions

    def decide_document(
        self,
        doc_id: str,
        decision: Any,
        arbitration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = _normalise_decision(decision)
        arbitration = dict(arbitration or {})
        row = self._find_row(doc_id)
        if row is None:
            line = self._find_store_line(doc_id)
            if line is None:
                raise HermesAccountingBridgeError("DOCUMENT_NOT_FOUND", _text(doc_id))
            filename = _text(line.get("invoice_url") or line.get("label"))
            category = _text(line.get("category")) or DocumentCategory.DIVERS_A_CLASSER.value
            sha = ""
            row = None
        else:
            filename = row.nom_fichier
            category = row.categorie_suggeree or DocumentCategory.DIVERS_A_CLASSER.value
            sha = row.sha256
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
        if not line and sha:
            line = self._store.get_line(self._tenant_id, derive_document_id(sha))
        source = self._file_for_row(row) if row is not None else self._find_file(filename)
        if target is DocumentStatus.VALIDE:
            destination = self._destination_for_valid(filename, category)
            if source is not None:
                destination = _move(source, destination)
            self._update_store_status(
                line,
                target,
                destination,
                category,
                sha,
                filename,
                arbitration=arbitration,
            )
            if row is not None:
                self._align(row.sha256, target)
            try:
                self._pipeline._repair_piece_links()
                self._pipeline._write_readable_views()
            except Exception:  # noqa: BLE001 - decision already persisted
                pass
            return {
                "doc_id": _text(doc_id),
                "decision": target.value,
                "status": target.value,
                "destination": str(destination) if destination else None,
            }
        if target is DocumentStatus.REJETE:
            archive = self._tree.state_dir(FolderState.ARCHIVE_ERREUR) / "REJET_MANUEL"
            destination = archive / Path(filename).name if filename else None
            if source is not None and destination is not None:
                destination = _move(source, destination)
            if line is not None:
                line_id = _text(line.get("line_id") or "")
                if line_id:
                    deactivate = getattr(
                        self._store, "deactivate_rejected_document", None
                    )
                    if callable(deactivate):
                        try:
                            deactivate(self._tenant_id, line_id)
                        except Exception:  # noqa: BLE001
                            pass
            if row is not None:
                self._align(row.sha256, target)
            return {
                "doc_id": _text(doc_id),
                "decision": target.value,
                "status": target.value,
                "destination": str(destination) if destination else None,
            }
            if row is not None:
                self._align(row.sha256, target)
                try:
                    self._workbook.update_p17_metadata(
                        row.sha256,
                        expense_subcategory=arbitration.get(
                            "expense_subcategory"
                        )
                        or None,
                        payment_source=normalize_payment_source(
                            arbitration.get("payment_source")
                        )
                        or None,
                        counterparty_account=arbitration.get(
                            "counterparty_account"
                        )
                        or None,
                        fiscal_year_target=arbitration.get("exercice")
                        or arbitration.get("fiscal_year_target")
                        or None,
                        deferred_month_override=normalize_month(
                            arbitration.get("month")
                        )
                        or "NON",
                        report=str(
                            arbitration.get("report") or "NON"
                        ).upper(),
                    )
                except (
                    WorkbookLockedError,
                    AcompleterWorkbookError,
                    PermissionError,
                    OSError,
                ):
                    pass
        return {
            "doc_id": _text(doc_id),
            "decision": target.value,
            "status": target.value,
            "destination": None,
        }

    def run_stage(self, stage: int) -> dict[str, Any]:
        report = self._pipeline.run_stage(stage)
        return report.as_dict()

    def run_all(self) -> list[dict[str, Any]]:
        return [report.as_dict() for report in self._pipeline.run_all()]

    # -- internals -------------------------------------------------------

    def _find_row(self, doc_id: str) -> Any | None:
        needle = _text(doc_id).lower()
        for row in self._rows():
            candidates = {
                row.sha256.lower(),
                _text(row.id_document).lower(),
                row.nom_fichier.lower(),
                Path(row.nom_fichier).stem.lower(),
            }
            if needle in candidates:
                return row
        return None

    def _find_store_line(self, doc_id: str) -> dict[str, Any] | None:
        needle = _text(doc_id)
        get_line = getattr(self._store, "get_line", None)
        if callable(get_line):
            line = get_line(self._tenant_id, needle)
            if line is not None:
                return line
        for line in self._store.list_master_lines(self._tenant_id):
            if needle in {
                _text(line.get("line_id")),
                _text(line.get("invoice_id")),
                _text(line.get("label")),
            }:
                return line
        return None

    def _file_for_row(self, row: Any) -> Path | None:
        if row is None:
            return None
        candidates = [
            self._tree.state_dir(FolderState.A_COMPLETER) / row.nom_fichier,
            self._tenant_dir / row.nom_fichier,
        ]
        for category in DocumentCategory:
            candidates.append(
                self._tree.category_dir(FolderState.VALIDE_PRET_FEC, category)
                / row.nom_fichier
            )
        candidates.append(self._tree.state_dir(FolderState.ARCHIVE_ERREUR) / row.nom_fichier)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return self._find_file(row.nom_fichier)

    def _find_file(self, filename: str) -> Path | None:
        name = Path(_text(filename)).name
        if not name:
            return None
        for state in (
            FolderState.A_COMPLETER,
            FolderState.ARCHIVE_ERREUR,
            FolderState.VALIDE_PRET_FEC,
        ):
            directory = self._tree.state_dir(state)
            if not directory.is_dir():
                continue
            for candidate in directory.rglob(name):
                if candidate.is_file():
                    return candidate
        return None

    def _destination_for_valid(self, filename: str, category: str) -> Path:
        try:
            selected = DocumentCategory(category)
        except ValueError:
            selected = DocumentCategory.DIVERS_A_CLASSER
        return self._tree.category_dir(FolderState.VALIDE_PRET_FEC, selected) / Path(
            filename
        ).name

    def _update_store_status(
        self,
        line: dict[str, Any] | None,
        target: DocumentStatus,
        destination: Path | None,
        category: str,
        sha: str,
        filename: str = "",
        arbitration: dict[str, Any] | None = None,
    ) -> None:
        arb = arbitration or {}
        if line is not None:
            line_id = _text(line.get("line_id"))
            if not line_id:
                return
            updated = dict(line)
            updated.pop("line_id", None)
            updated["status"] = target.value
            if target is DocumentStatus.VALIDE:
                updated["reconciliation_status"] = "VALIDE"
            updated_category = normalize_category(arb.get("category"))
            if updated_category:
                updated["category"] = updated_category
            payment_source = normalize_payment_source(
                arb.get("payment_source")
            )
            if payment_source:
                updated["payment_source"] = payment_source
                if payment_source == "COMPTE_PERSO_DIRIGEANT":
                    updated["counterparty_account"] = "455000_DIRIGEANT"
            account = _text(arb.get("counterparty_account"))
            if account:
                updated["counterparty_account"] = account
            fiscal_year = arb.get("fiscal_year_target")
            if fiscal_year is not None and str(fiscal_year).strip():
                updated["fiscal_year_target"] = str(fiscal_year)
            month = arb.get("month")
            if month:
                month_value = normalize_month(month)
                if month_value:
                    updated["deferred_month_override"] = (
                        None if month_value == "NON" else month_value
                    )
            if category and not updated_category:
                updated["category"] = category
            if destination is not None:
                try:
                    updated["invoice_url"] = destination.resolve().relative_to(
                        self._tenant_dir.resolve()
                    ).as_posix()
                except ValueError:
                    updated["invoice_url"] = destination.name
            self._store.upsert_document_line(
                self._tenant_id, line_id, updated
            )
            return
        if sha:
            self._store.upsert_document_line(
                self._tenant_id,
                derive_document_id(sha),
                {
                    "label": Path(filename).stem if filename else "",
                    "category": category,
                    "status": target.value,
                },
            )


    def _align(self, sha: str, target: DocumentStatus) -> None:
        try:
            self._workbook.align_statut(sha, target)
        except (WorkbookLockedError, AcompleterWorkbookError):
            return


__all__ = [
    "HermesAccountingBridge",
    "HermesAccountingBridgeError",
    "VALID_DECISIONS",
]
