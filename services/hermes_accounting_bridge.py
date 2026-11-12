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
    normalize_decision,
    normalize_exercise,
    normalize_month,
    normalize_payment_source,
)
from services.cockpit_decision_service import (
    decision_menu_for,
    decision_menu_payload,
    is_creation_choice,
    is_group_choice,
    is_internal_choice,
    is_lifecycle_choice,
    raw_tier_from_filename,
    resolve_tier_choice,
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
    "DEJA_PAYEE_2025": DocumentStatus.VALIDE,
    "DEJA PAYEE 2025": DocumentStatus.VALIDE,
    "IMPAYEE": DocumentStatus.VALIDE,
    "IMPAYÉE": DocumentStatus.VALIDE,
    "REPORTER_MOIS_SUIVANT": DocumentStatus.EN_ATTENTE,
    "REPORTER MOIS SUIVANT": DocumentStatus.EN_ATTENTE,
    "REJETE": DocumentStatus.REJETE,
    "REJETER": DocumentStatus.REJETE,
    "REJETÉ": DocumentStatus.REJETE,
    "REFUSER": DocumentStatus.REJETE,
    "KO": DocumentStatus.REJETE,
    "EN_ATTENTE": DocumentStatus.EN_ATTENTE,
    "ATTENDRE": DocumentStatus.EN_ATTENTE,
    "QUESTION": DocumentStatus.EN_ATTENTE,
}
LIFECYCLE_DECISIONS: frozenset[str] = frozenset(
    {
        "VALIDE",
        "DEJA_PAYEE_2025",
        "REPORTER_MOIS_SUIVANT",
        "IMPAYEE",
        "REJETE",
        "EN_ATTENTE",
    }
)
ARDOISE_FINAL_STATUSES: frozenset[str] = LIFECYCLE_DECISIONS - {
    "REPORTER_MOIS_SUIVANT"
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
    token = normalize_decision(value)
    if token in {"", "—", "-"}:
        token = "VALIDE"
    decision = VALID_DECISIONS.get(token) or VALID_DECISIONS.get(
        _text(value).upper()
    )
    if decision is None:
        raise HermesAccountingBridgeError("INVALID_DECISION", repr(value))
    return decision


def _decision_token(value: Any) -> str:
    """Return the official cockpit token, defaulting blank/dash to VALIDE."""
    return normalize_decision(value)


def target_is_real_file(filename: Any) -> bool:
    """``False`` for virtual ``.tier`` / ARDOISE rows that have no PDF."""
    name = str(filename or "").strip().upper()
    if not name:
        return False
    if name.startswith("ARDOISE_"):
        return False
    return not name.endswith(".TIER")


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

    def _decision_fields(
        self,
        filename: Any,
        supplier: Any,
        source: Any,
        current: Any = "",
    ) -> dict[str, Any]:
        """P27-decies - contextual decision menu for one pending line."""
        menu = decision_menu_for(
            filename,
            supplier,
            source,
            self._pipeline._tier_reference,
        )
        payload = decision_menu_payload(menu)
        current_token = _text(current).upper()
        if current_token in {"", "EN_ATTENTE", "PENDING"}:
            payload["selected"] = menu.default
        else:
            payload["selected"] = current_token
        return {
            "decision_profile": menu.profile,
            "decision_choices": list(menu.choices),
            "decision_default": menu.default,
            "decision_menu": payload,
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
                    **self._decision_fields(
                        row.nom_fichier,
                        supplier,
                        "BANQUE_OFX" if bank_line else "PIECE_PDF",
                        row.statut,
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
                        **self._decision_fields(
                            filename, supplier, source, decision
                        ),
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
        """Apply one human answer without ever blocking a direct validation.

        P27-decies - a blank / ``—`` answer defaults to ``VALIDE``. The single
        front selector may carry either a lifecycle answer or a contextual
        tier answer (``GROUPE:*``, internal flow, known tier, creation).
        """
        arbitration = dict(arbitration or {})
        token = _decision_token(decision)
        choice = _text(
            arbitration.get("tier_alias_target")
            or arbitration.get("tier_choice")
        )
        # The cockpit uses one selector: a lifecycle value may arrive through
        # the ``tier_alias_target`` field instead of the legacy decision field.
        if choice and normalize_decision(choice) in LIFECYCLE_DECISIONS:
            token = normalize_decision(choice)
            choice = ""
        row = self._find_row(doc_id)
        if row is not None:
            filename = row.nom_fichier
            category = (
                row.categorie_suggeree
                or DocumentCategory.DIVERS_A_CLASSER.value
            )
            sha = row.sha256
            line = self._store.get_line(
                self._tenant_id, derive_document_id(row.sha256)
            )
        else:
            line = self._find_line_for_doc(doc_id)
            if line is None:
                raise HermesAccountingBridgeError(
                    "DOCUMENT_NOT_FOUND", _text(doc_id)
                )
            filename = _text(
                line.get("invoice_url")
                or line.get("label")
                or doc_id
            )
            category = (
                _text(line.get("category"))
                or DocumentCategory.DIVERS_A_CLASSER.value
            )
            sha = ""
        if not line and sha:
            line = self._store.get_line(
                self._tenant_id, derive_document_id(sha)
            )
        tier_filename = row.nom_fichier if row is not None else _text(doc_id)
        raw_tier = raw_tier_from_filename(tier_filename)
        if not raw_tier:
            raw_tier = _text((line or {}).get("counterparty"))
        is_tier_aggregate = bool(raw_tier_from_filename(tier_filename))
        if is_tier_aggregate:
            targets = self._lines_for_tier(raw_tier, line)
        else:
            targets = [line] if line is not None else []
        if choice and not is_lifecycle_choice(choice):
            return self._apply_tier_choice(
                doc_id,
                choice,
                raw_tier,
                tier_filename,
                targets,
                row,
                filename,
                category,
                sha,
                arbitration,
            )
        return self._apply_lifecycle_decision(
            doc_id,
            token,
            targets,
            row,
            filename,
            category,
            sha,
            arbitration,
        )

    # -- decision internals ------------------------------------------------

    @staticmethod
    def _fold(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())

    def _find_line_for_doc(self, doc_id: str) -> dict[str, Any] | None:
        """Find a SQLite line by line id, invoice id, label or ARDOISE name."""
        line = self._find_store_line(doc_id)
        if line is not None:
            return line
        needle = _text(doc_id)
        ardoise_invoice = raw_tier_from_filename(needle)
        for candidate in self._store.list_master_lines(self._tenant_id):
            names = {
                _text(candidate.get("line_id")),
                _text(candidate.get("invoice_id")),
                _text(candidate.get("label")),
                Path(str(candidate.get("invoice_url") or "")).name,
            }
            if needle in {name for name in names if name}:
                return candidate
            if ardoise_invoice and _text(
                candidate.get("invoice_id")
            ) == ardoise_invoice:
                return candidate
            if ardoise_invoice and ardoise_invoice.casefold() in self._fold(
                f"{candidate.get('counterparty') or ''} "
                f"{candidate.get('label') or ''}"
            ):
                return candidate
        return None

    def _lines_for_tier(
        self, raw_tier: Any, fallback: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        needle = self._fold(raw_tier)
        if not needle:
            return [fallback] if fallback is not None else []
        rows = self._store.list_master_lines(self._tenant_id)
        for candidate in rows:
            if self._fold(candidate.get("line_id")) == needle:
                return [candidate]
        matches = [
            candidate
            for candidate in rows
            if needle
            in self._fold(
                f"{candidate.get('counterparty') or ''} "
                f"{candidate.get('label') or ''}"
            )
        ]
        if matches:
            return matches
        return [fallback] if fallback is not None else []

    def _arbitrated_values(
        self, arbitration: dict[str, Any]
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        category = normalize_category(arbitration.get("category"))
        if category:
            values["category"] = category
        if "expense_subcategory" in arbitration:
            subcategory = _text(arbitration.get("expense_subcategory"))
            if subcategory:
                try:
                    values["expense_subcategory"] = ExpenseSubCategory(
                        subcategory
                    ).value
                except ValueError:
                    pass
            else:
                values["expense_subcategory"] = None
        counterparty = _text(arbitration.get("counterparty"))
        if counterparty:
            values["counterparty"] = counterparty
        account = _text(arbitration.get("counterparty_account"))
        if account:
            values["counterparty_account"] = account
        payment_source = normalize_payment_source(
            arbitration.get("payment_source")
        )
        if payment_source:
            values["payment_source"] = payment_source
            if payment_source == "COMPTE_PERSO_DIRIGEANT":
                values["counterparty_account"] = "455000_DIRIGEANT"
        exercise = _text(
            arbitration.get("exercise_year")
            or arbitration.get("fiscal_year_target")
            or arbitration.get("exercice")
        )
        if exercise:
            values["exercise_year"] = exercise
            values["fiscal_year_target"] = exercise
        month = normalize_month(arbitration.get("month"))
        if month:
            values["deferred_month_override"] = (
                None if month == "NON" else month
            )
        report = _text(arbitration.get("report")).upper()
        if report:
            values["report"] = report
        reconciliation_status = _text(
            arbitration.get("reconciliation_status")
        )
        if reconciliation_status:
            values["reconciliation_status"] = reconciliation_status
        if "balance_due" in arbitration:
            values["balance_due"] = arbitration.get("balance_due")
        return values

    def _apply_lifecycle_decision(
        self,
        doc_id: str,
        token: str,
        targets: list[dict[str, Any]],
        row: Any,
        filename: str,
        category: str,
        sha: str,
        arbitration: dict[str, Any],
    ) -> dict[str, Any]:
        if token == "REJETE":
            status = DocumentStatus.REJETE
        elif token in {"EN_ATTENTE", "REPORTER_MOIS_SUIVANT"}:
            status = DocumentStatus.EN_ATTENTE
        else:
            status = DocumentStatus.VALIDE
        values = self._arbitrated_values(arbitration)
        destination: Path | None = None
        if (
            status is DocumentStatus.VALIDE
            and target_is_real_file(filename)
        ):
            source = (
                self._file_for_row(row)
                if row is not None
                else self._find_file(filename)
            )
            if source is not None:
                destination = _move(
                    source,
                    self._destination_for_valid(filename, category),
                )
        archived: Path | None = None
        if status is DocumentStatus.REJETE and target_is_real_file(filename):
            source = (
                self._file_for_row(row)
                if row is not None
                else self._find_file(filename)
            )
            if source is not None:
                archive = (
                    self._tree.state_dir(FolderState.ARCHIVE_ERREUR)
                    / "REJET_MANUEL"
                )
                archived = _move(source, archive / Path(filename).name)
        for line in targets:
            line_id = _text(line.get("line_id"))
            if not line_id:
                continue
            updated = dict(line)
            updated.pop("line_id", None)
            updated["status"] = status.value
            updated.update(values)
            if token == "DEJA_PAYEE_2025":
                updated["reconciliation_status"] = "DEJA_PAYEE_2025"
                updated["reconciliation_date"] = (
                    updated.get("reconciliation_date")
                    or updated.get("operation_date")
                )
                updated["balance_due"] = "0.00"
            elif token == "IMPAYEE":
                updated["reconciliation_status"] = "IMPAYEE"
                updated["balance_due"] = (
                    updated.get("amount_ttc")
                    or updated.get("balance_due")
                )
            elif token == "REPORTER_MOIS_SUIVANT":
                updated["reconciliation_status"] = None
                updated["report"] = "OUI"
            elif status is DocumentStatus.VALIDE:
                current_reconciliation = _text(
                    updated.get("reconciliation_status")
                ).upper()
                if current_reconciliation in {
                    "",
                    "EN_ATTENTE_REGLEMENT",
                    "VALIDE",
                    "EN_ATTENTE",
                }:
                    updated["reconciliation_status"] = "VALIDE"
            if destination is not None:
                try:
                    updated["invoice_url"] = destination.resolve().relative_to(
                        self._tenant_dir.resolve()
                    ).as_posix()
                except ValueError:
                    updated["invoice_url"] = destination.name
            if status is DocumentStatus.REJETE:
                deactivate = getattr(
                    self._store, "deactivate_rejected_document", None
                )
                if callable(deactivate):
                    try:
                        deactivate(self._tenant_id, line_id)
                    except Exception:  # noqa: BLE001
                        pass
            self._store.upsert_document_line(
                self._tenant_id, line_id, updated
            )
        if row is not None:
            self._align(row.sha256, status)
            try:
                self._workbook.update_p17_metadata(
                    row.sha256,
                    expense_subcategory=values.get("expense_subcategory"),
                    payment_source=values.get("payment_source"),
                    counterparty_account=values.get("counterparty_account"),
                    fiscal_year_target=values.get("fiscal_year_target"),
                    deferred_month_override=values.get(
                        "deferred_month_override", "NON"
                    )
                    or "NON",
                    report=values.get("report") or "NON",
                )
            except (
                WorkbookLockedError,
                AcompleterWorkbookError,
                PermissionError,
                OSError,
            ):
                pass
        try:
            self._pipeline._repair_piece_links()
            self._pipeline._write_readable_views()
        except Exception:  # noqa: BLE001 - decision already persisted
            pass
        self._push_onedrive()
        return {
            "doc_id": _text(doc_id),
            "decision": token,
            "status": status.value,
            "destination": str(destination or archived)
            if (destination or archived)
            else None,
            "lines_updated": len(targets),
        }

    def _apply_tier_choice(
        self,
        doc_id: str,
        choice: str,
        raw_tier: str,
        tier_filename: str,
        targets: list[dict[str, Any]],
        row: Any,
        filename: str,
        category: str,
        sha: str,
        arbitration: dict[str, Any],
    ) -> dict[str, Any]:
        reference = self._pipeline._tier_reference
        try:
            resolution = resolve_tier_choice(choice, raw_tier, reference)
        except ValueError as exc:
            return {
                "doc_id": _text(doc_id),
                "decision": DocumentStatus.EN_ATTENTE.value,
                "status": DocumentStatus.EN_ATTENTE.value,
                "message": str(exc),
                "destination": None,
            }
        if resolution is None:
            return self._apply_lifecycle_decision(
                doc_id,
                _decision_token(choice),
                targets,
                row,
                filename,
                category,
                sha,
                arbitration,
            )
        canonical = resolution.canonical
        # A creation answer must materialise the new canonical tier first.
        if is_creation_choice(choice) and raw_tier:
            try:
                record = reference.learn_alias(raw_tier, choice)
                canonical = record.canonical
            except Exception:  # noqa: BLE001 - an invalid alias is reported
                return {
                    "doc_id": _text(doc_id),
                    "decision": DocumentStatus.EN_ATTENTE.value,
                    "status": DocumentStatus.EN_ATTENTE.value,
                    "message": "Création de tiers impossible.",
                    "destination": None,
                }
        elif raw_tier and resolution.learn_alias and not str(
            tier_filename
        ).upper().startswith(("INSTRUMENT__", "ARDOISE_")):
            try:
                reference.learn_alias(raw_tier, canonical)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._pipeline._apply_tier_reference()
        except Exception:  # noqa: BLE001 - the explicit update below wins
            pass
        arbitrated = dict(arbitration)
        arbitrated.update(
            {
                "counterparty": canonical,
                "category": resolution.category or category,
                "expense_subcategory": resolution.subcategory,
                "counterparty_account": resolution.account,
                "reconciliation_status": "VALIDE",
            }
        )
        if not targets and row is not None:
            self._align(row.sha256, DocumentStatus.VALIDE)
            try:
                self._pipeline._write_readable_views()
            except Exception:  # noqa: BLE001
                pass
            self._push_onedrive()
            return {
                "doc_id": _text(doc_id),
                "decision": DocumentStatus.VALIDE.value,
                "status": DocumentStatus.VALIDE.value,
                "tier_alias_target": choice,
                "destination": None,
                "lines_updated": 0,
            }
        result = self._apply_lifecycle_decision(
            doc_id,
            "VALIDE",
            targets,
            row,
            filename,
            resolution.category or category,
            sha,
            arbitrated,
        )
        result["tier_alias_target"] = choice
        return result

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
            counterparty = _text(arb.get("counterparty"))
            if counterparty:
                updated["counterparty"] = counterparty
            subcategory = str(arb.get("expense_subcategory") or "").strip()
            if subcategory:
                try:
                    updated["expense_subcategory"] = ExpenseSubCategory(
                        subcategory
                    ).value
                except ValueError:
                    pass
            elif "expense_subcategory" in arb:
                updated["expense_subcategory"] = None
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
            exercise_year = arb.get("exercise_year")
            if fiscal_year is not None and str(fiscal_year).strip():
                updated["fiscal_year_target"] = str(fiscal_year)
            if exercise_year is not None and str(exercise_year).strip():
                updated["exercise_year"] = str(exercise_year)
            elif fiscal_year is not None and str(fiscal_year).strip():
                updated["exercise_year"] = str(fiscal_year)
            month = arb.get("month")
            if month:
                month_value = normalize_month(month)
                if month_value:
                    updated["deferred_month_override"] = (
                        None if month_value == "NON" else month_value
                    )
            reconciliation_status = _text(arb.get("reconciliation_status"))
            if reconciliation_status:
                updated["reconciliation_status"] = reconciliation_status
            if "balance_due" in arb:
                updated["balance_due"] = arb.get("balance_due")
            report = _text(arb.get("report"))
            if report:
                updated["report"] = report
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
    "LIFECYCLE_DECISIONS",
    "VALID_DECISIONS",
]
