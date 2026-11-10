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

from schemas.folder_state_schema import (
    DocumentCategory,
    DocumentStatus,
    ExpenseSubCategory,
    FolderState,
)
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
from services.folder_tree_service import (
    GED_SUBDIRS,
    ensure_tree,
    validate_tenant_id,
    validated_destination,
)
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
        self._workbook = AcompleterWorkbook(
            self._tree.state_dir(FolderState.A_COMPLETER) / "_TECHNIQUE"
        )
        self._year = int(year)
        self._onedrive = None
        try:
            from services.onedrive_accounting_bridge_service import (
                OneDriveAccountingBridge,
            )

            self._onedrive = OneDriveAccountingBridge(
                self._tenant_id,
                root=self._tree.root,
                export_dir=self._export_dir,
            )
        except Exception:  # noqa: BLE001 - OneDrive is optional
            self._onedrive = None

    def pull_from_onedrive(self) -> None:
        if self._onedrive is None:
            return
        try:
            self._onedrive.pull_from_onedrive()
        except Exception:  # noqa: BLE001 - OneDrive is best effort
            return

    def _pull_onedrive(self) -> None:
        """Backward-compatible alias of :meth:`pull_from_onedrive`."""
        self.pull_from_onedrive()

    def _push_onedrive(self) -> None:
        if self._onedrive is None:
            return
        try:
            self._onedrive.push_director_files()
            self._onedrive.push_expert_workbooks()
        except Exception:  # noqa: BLE001 - OneDrive is best effort
            return

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
        readable = self._readable_pending_questions()
        if readable:
            return readable
        questions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self._rows():
            if row.statut != DocumentStatus.EN_ATTENTE.value:
                continue
            if row.sha256 in seen:
                continue
            if row.nom_fichier.startswith("TIER__") and row.nom_fichier.endswith(
                ".tier"
            ):
                tier_name = row.nom_fichier[
                    len("TIER__") : -len(".tier")
                ]
                try:
                    resolved = self._pipeline._tier_reference.resolve(
                        tier_name
                    )
                except Exception:  # noqa: BLE001
                    resolved = None
                if resolved is not None:
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
            document_type = _text((line or {}).get("document_type")).upper()
            line_id = _text((line or {}).get("line_id")).upper()
            bank_line = (
                document_type in {"OFX", "QFX"}
                or line_id.startswith("OFX-")
                or (line or {}).get("debit_amount") not in (None, "")
                or (line or {}).get("credit_amount") not in (None, "")
            )
            subcategory = _text((line or {}).get("expense_subcategory"))
            if not subcategory:
                try:
                    from services.accounting_spreadsheet_export_service import (
                        _infer_expense_subcategory,
                    )

                    inferred = (
                        _infer_expense_subcategory(line or {})
                        if line
                        else None
                    )
                    subcategory = inferred.value if inferred is not None else ""
                except Exception:  # noqa: BLE001
                    subcategory = ""
            amount_ttc = _decimal((line or {}).get("amount_ttc"))
            if amount_ttc is None:
                amount_ttc = _decimal(
                    (line or {}).get("debit_amount")
                    or (line or {}).get("credit_amount")
                )
            raw_date = _text((line or {}).get("operation_date"))
            date_label = ""
            if raw_date:
                parsed_date = _date(raw_date)
                date_label = (
                    parsed_date.strftime("%d/%m/%Y")
                    if parsed_date is not None
                    else raw_date
                )
            label = _text(
                (line or {}).get("label")
                or (line or {}).get("invoice_id")
                or number
            )
            piece = (
                relative_link(path, self._tenant_dir / "_A_COMPLETER.xlsx")
                if path is not None
                else _text((line or {}).get("invoice_url"))
            )
            try:
                metadata = self._workbook.read_p17_metadata(row.sha256) or {}
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                metadata = {}
            questions.append(
                {
                    "doc_id": row.sha256,
                    "id_document": row.id_document or derive_document_id(row.sha256),
                    "filename": row.nom_fichier,
                    "nom_fichier": row.nom_fichier,
                    "fichier_ref": row.nom_fichier,
                    "supplier": supplier,
                    "fournisseur": supplier,
                    "document_number": number,
                    "numero_piece": number,
                    "libelle": label,
                    "label": label,
                    "document_date": raw_date or None,
                    "date_piece": date_label,
                    "date": date_label,
                    "source": "BANQUE_OFX" if bank_line else "PIECE_PDF",
                    "amount_ht": _decimal((line or {}).get("amount_ht")),
                    "ht": _decimal((line or {}).get("amount_ht")),
                    "amount_tva": _decimal((line or {}).get("amount_tva")),
                    "tva": _decimal((line or {}).get("amount_tva")),
                    "amount_ttc": amount_ttc,
                    "ttc": amount_ttc,
                    "montant_ttc": amount_ttc,
                    "category": category,
                    "categorie": category,
                    "subcategory": subcategory,
                    "sous_categorie": subcategory,
                    "question": reason,
                    "pourquoi": reason,
                    "why": reason,
                    "reason": reason,
                    "advice": action,
                    "conseil": action,
                    "action": action,
                    "decision": row.statut,
                    "statut": row.statut,
                    "piece_pdf": piece,
                    "link": piece,
                    "tier_alias_target": (
                        metadata.get(
                            "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)"
                        )
                        or ""
                    )
                    if row.nom_fichier.endswith(".tier")
                    else "",
                    "tier_alias_choices": list(
                        self._pipeline._tier_reference.alias_targets()
                    ),
                }
            )
        return questions

    def _readable_pending_questions(self) -> list[dict[str, Any]]:
        """Mirror ``_A_COMPLETER.xlsx`` exactly for the web cockpit."""
        from openpyxl import load_workbook

        path = (
            self._tree.state_dir(FolderState.A_COMPLETER) / "_A_COMPLETER.xlsx"
        )
        if not path.is_file():
            return []
        try:
            workbook = load_workbook(path, data_only=True)
        except Exception:  # noqa: BLE001 - a locked workbook falls back
            return []
        try:
            sheet = workbook.active
            headers = {
                _text(cell.value): cell.column
                for cell in next(sheet.iter_rows(min_row=1, max_row=1))
                if _text(cell.value)
            }
            required = ("Fichier / Réf", "Fournisseur / Tiers")
            if not all(name in headers for name in required):
                return []
            alias_choices = list(
                self._pipeline._tier_reference.alias_targets()
            )

            def value(row: Any, name: str) -> Any:
                column = headers.get(name)
                if column is None:
                    return None
                return row[column - 1].value

            questions: list[dict[str, Any]] = []
            for row in sheet.iter_rows(min_row=2):
                filename = _text(value(row, "Fichier / Réf"))
                sha = _text(value(row, "SHA-256"))
                if not filename and not sha:
                    continue
                supplier = _text(value(row, "Fournisseur / Tiers"))
                label = _text(value(row, "Libellé / N° Pièce")) or filename
                date_label = _text(value(row, "Date"))
                source = _text(value(row, "Source")) or (
                    "BANQUE_OFX"
                    if filename.upper().startswith(
                        ("TIER__", "INSTRUMENT__")
                    )
                    else "PIECE_PDF"
                )
                category = _text(value(row, "Catégorie"))
                subcategory = _text(value(row, "Sous-Catégorie"))
                payment_source = _text(value(row, "Source de paiement"))
                exercise = _text(value(row, "Exercice Année"))
                reason = _text(value(row, "Pourquoi en attente"))
                action = _text(value(row, "Action attendue"))
                decision = _text(value(row, "Décision")) or (
                    DocumentStatus.EN_ATTENTE.value
                )
                alias_target = _text(
                    value(
                        row,
                        "Rattacher comme ALIAS de (ou NOUVEAU_TIERS)",
                    )
                )
                amount_ttc = _decimal(value(row, "Montant TTC (€)"))
                piece = ""
                column = headers.get("Fichier / Réf")
                if column is not None:
                    link = row[column - 1].hyperlink
                    if link is not None and link.target:
                        target = str(link.target)
                        if "SOURCE_OFX" not in target.upper():
                            piece = target
                doc_id = filename or sha
                questions.append(
                    {
                        "doc_id": doc_id,
                        "id_document": doc_id,
                        "filename": filename,
                        "nom_fichier": filename,
                        "fichier_ref": filename,
                        "supplier": supplier,
                        "fournisseur": supplier,
                        "document_number": label,
                        "numero_piece": label,
                        "libelle": label,
                        "label": label,
                        "date_piece": date_label,
                        "date": date_label,
                        "document_date": date_label,
                        "source": source,
                        "amount_ttc": amount_ttc,
                        "ttc": amount_ttc,
                        "montant_ttc": amount_ttc,
                        "category": category,
                        "categorie": category,
                        "subcategory": subcategory,
                        "sous_categorie": subcategory,
                        "question": reason,
                        "pourquoi": reason,
                        "why": reason,
                        "reason": reason,
                        "advice": action,
                        "conseil": action,
                        "action": action,
                        "decision": decision,
                        "statut": decision,
                        "exercise_year": exercise,
                        "source_paiement": payment_source,
                        "piece_pdf": piece,
                        "link": piece,
                        "tier_alias_target": alias_target,
                        "tier_alias_choices": alias_choices,
                    }
                )
            return questions
        finally:
            workbook.close()

    def decide_document(
        self,
        doc_id: str,
        decision: Any,
        arbitration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = _normalise_decision(decision)
        arbitration = dict(arbitration or {})
        row = self._find_row(doc_id)
        if (
            row is not None
            and row.nom_fichier.startswith("TIER__")
            and row.nom_fichier.endswith(".tier")
        ):
            target = str(
                arbitration.get("tier_alias_target")
                or arbitration.get("category")
                or ""
            ).strip()
            if target:
                name = row.nom_fichier[len("TIER__") : -len(".tier")]
                self._pipeline._tier_reference.learn_alias(name, target)
                self._pipeline._apply_tier_reference()
                self._align(row.sha256, DocumentStatus.VALIDE)
                try:
                    self._pipeline._write_readable_views()
                except Exception:  # noqa: BLE001 - decision already persisted
                    pass
                self._push_onedrive()
                return {
                    "doc_id": _text(doc_id),
                    "decision": DocumentStatus.VALIDE.value,
                    "status": DocumentStatus.VALIDE.value,
                    "tier_alias_target": target,
                    "destination": None,
                }
            return {
                "doc_id": _text(doc_id),
                "decision": DocumentStatus.EN_ATTENTE.value,
                "status": DocumentStatus.EN_ATTENTE.value,
                "message": "Choisissez un rattachement ALIAS.",
                "destination": None,
            }
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
        self.pull_from_onedrive()
        report = self._pipeline.run_stage(stage)
        self._push_onedrive()
        return report.as_dict()

    def run_all(self) -> list[dict[str, Any]]:
        self.pull_from_onedrive()
        reports = [report.as_dict() for report in self._pipeline.run_all()]
        self._push_onedrive()
        return reports

    def apply_excel_changes(self) -> dict[str, Any]:
        """Re-read the director's Excel edits and apply them immediately.

        Reads ``_A_COMPLETER.xlsx`` and ``REFERENTIEL_TIERS_APD.xlsx`` from
        disk, merges aliases, applies human decisions, then refreshes the
        readable workbook and the accounting exports.
        """
        self.pull_from_onedrive()
        tiers = self._pipeline._tier_reference
        tiers.bootstrap()
        merged = tiers.apply_alias_merges()
        applied = self._pipeline.apply_human_decisions()
        self._pipeline._apply_tier_reference()
        self._pipeline._write_readable_views()
        refreshed: list[str] = []
        try:
            from services.accounting_spreadsheet_export_service import (
                ClientLedgerExporter,
                SupplierLedgerExporter,
            )

            client = ClientLedgerExporter(
                self._store,
                export_dir=self._export_dir,
                strict_tiers=True,
                include_matched_pieces=True,
            ).export(self._tenant_id)
            supplier = SupplierLedgerExporter(
                self._store,
                export_dir=self._export_dir,
                strict_tiers=True,
                include_matched_pieces=True,
            ).export_supplier_ledger(self._tenant_id)
            refreshed = [str(client), str(supplier)]
        except Exception:  # noqa: BLE001 - refresh is best effort
            refreshed = []
        self._push_onedrive()
        return {
            "aliases_merged": list(merged),
            "decisions_applied": applied,
            "workbooks_refreshed": refreshed,
            "tier_reference": str(tiers.path),
            "readable_workbook": str(self._pipeline.readable_path),
        }

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
        for subdir in GED_SUBDIRS:
            candidates.append(self._tree.ged_dir(subdir) / row.nom_fichier)
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
            for candidate in directory.rglob("*"):
                if candidate.is_file() and candidate.name.casefold() == name.casefold():
                    return candidate
        return None

    def _destination_for_valid(self, filename: str, category: str) -> Path:
        try:
            selected = DocumentCategory(category)
        except ValueError:
            selected = DocumentCategory.DIVERS_A_CLASSER
        return validated_destination(
            self._tree,
            Path(filename).name,
            category=selected,
            document_type="FACTURE",
            invoice_number=Path(filename).stem,
            label=Path(filename).stem,
        )

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
            subcategory = str(arb.get("expense_subcategory") or "").strip()
            if subcategory:
                try:
                    updated["expense_subcategory"] = ExpenseSubCategory(
                        subcategory
                    ).value
                except ValueError:
                    pass
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
