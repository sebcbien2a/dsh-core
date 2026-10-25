"""Local Tkinter tool to confirm a proposed supplier record.

Run with the DSH-Core interpreter, for example:
    A:\\dsh-core\\.venv-v3\\Scripts\\python.exe -m tools.validate_supplier_identity party.json
        --source-sha256 <64 hex> --suppliers S.json --proposals P.json --journal J.json

It only wires existing components: invoice_party_to_supplier_identity,
SupplierRegistrationService.propose, SupplierRegistrationRepository and
SupplierRegistrationCoordinator.execute. Nothing is written before the human
explicitly confirms; the PENDING proposal is persisted first, then the
coordinator runs. No automatic merge, no deduction of country or identifiers,
no network access, no model.

The pure functions (parse_form, preview, submit, ...) hold all the logic and are
tested without opening a window; run_gui only collects widget values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from repositories.supplier_identity_repository import SupplierIdentityRepository
from repositories.supplier_registration_repository import SupplierRegistrationRepository
from schemas.invoice_schema import InvoiceParty
from schemas.supplier_identity_schema import (
    FieldValue,
    PaymentProfile,
    SupplierAddress,
    SupplierIdentifier,
    SupplierIdentity,
)
from services.invoice_party_supplier_identity_adapter import invoice_party_to_supplier_identity
from services.supplier_registration_coordinator import (
    SupplierRegistrationCoordinator,
    SupplierRegistrationCoordinatorError,
    SupplierTransactionJournal,
)
from services.supplier_registration_service import (
    HumanRegistrationDecision,
    IdentifierConflict,
    ProposalStatus,
    RegistrationAction,
    SupplierRegistrationService,
)

COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LINE_SPLIT = re.compile(r"[\n|]")
_IDENTIFIER_FIELDS = (
    "identifier_type",
    "scope",
    "country",
    "local_label",
    "raw_value",
    "normalized_value",
)
_ADDRESS_FIELDS = ("address_type", "postal_code", "city", "region", "country")
_PAYMENT_FIELDS = (
    "payment_terms_label",
    "delay_days",
    "calculation_rule",
    "payment_method",
    "issue_date",
    "due_date",
)


class SupplierFormError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class StoragePaths:
    suppliers: Path
    proposals: Path
    journal: Path


@dataclass(frozen=True)
class ParsedForm:
    candidate: SupplierIdentity
    confirmed_keys: list[tuple[str | None, str, str]]
    accepted_aliases: list[str]


@dataclass(frozen=True)
class Preview:
    suggestions: list[SupplierIdentity]
    conflicts: list[IdentifierConflict]


# --- loading and conversion ---------------------------------------------


def load_invoice_party(path: str | Path) -> InvoiceParty:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return InvoiceParty.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise SupplierFormError("INVALID_PARTY_FILE", str(exc)) from exc


def build_candidate(party: InvoiceParty, country: str | None = None) -> SupplierIdentity:
    return invoice_party_to_supplier_identity(party, country=country)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def form_from_candidate(candidate: SupplierIdentity) -> dict[str, Any]:
    """Initial editable form; no identifier is pre-confirmed, no alias pre-accepted."""
    return {
        "legal_name": candidate.legal_name,
        "country": candidate.country or "",
        "printed_names": list(candidate.printed_names),
        "aliases": [{"alias": alias, "accepted": False} for alias in candidate.aliases],
        "identifiers": [
            {**{k: getattr(item, k) or "" for k in _IDENTIFIER_FIELDS}, "confirm": False}
            for item in candidate.identifiers
        ],
        "addresses": [
            {
                **{k: getattr(item, k) or "" for k in _ADDRESS_FIELDS},
                "lines": " | ".join(item.lines),
            }
            for item in candidate.addresses
        ],
        "payment_profiles": [
            {k: "" if getattr(item, k) is None else str(getattr(item, k)) for k in _PAYMENT_FIELDS}
            for item in candidate.payment_profiles
        ],
        "custom_fields": [
            {"key": key, "value": value.raw or ""} for key, value in candidate.custom_fields.items()
        ],
    }


def has_unsaved_draft(baseline: dict[str, Any], current: dict[str, Any]) -> bool:
    return baseline != current


# --- form parsing ---------------------------------------------------------


def parse_form(base: SupplierIdentity, form: dict[str, Any]) -> ParsedForm:
    try:
        return _parse_form(base, form)
    except SupplierFormError:
        raise
    except ValueError as exc:
        raise SupplierFormError("INVALID_FORM", str(exc)) from exc


def _parse_form(base: SupplierIdentity, form: dict[str, Any]) -> ParsedForm:
    legal_name = _text(form.get("legal_name"))
    if legal_name is None:
        raise SupplierFormError("LEGAL_NAME_REQUIRED")

    identifiers: list[SupplierIdentifier] = []
    keys: list[tuple[str | None, str, str]] = []
    for row in form.get("identifiers", []):
        values = {name: _text(row.get(name)) for name in _IDENTIFIER_FIELDS}
        confirm = bool(row.get("confirm"))
        if not any(values.values()) and not confirm:
            continue
        if values["identifier_type"] is None:
            raise SupplierFormError("IDENTIFIER_TYPE_REQUIRED")
        scope = values.pop("scope")
        identifier = SupplierIdentifier(
            **values,
            **({"scope": scope} if scope else {}),
            human_confirmed=False,
            is_primary=False,
        )
        if confirm:
            if not identifier.normalized_value:
                raise SupplierFormError("CONFIRMED_IDENTIFIER_REQUIRES_NORMALIZED_VALUE")
            keys.append(
                (identifier.country, identifier.identifier_type, identifier.normalized_value)
            )
        identifiers.append(identifier)

    aliases: list[str] = []
    accepted: list[str] = []
    for row in form.get("aliases", []):
        alias = _text(row.get("alias"))
        if alias is None:
            continue
        aliases.append(alias)
        if row.get("accepted"):
            accepted.append(alias)

    addresses = []
    for row in form.get("addresses", []):
        values = {name: _text(row.get(name)) for name in _ADDRESS_FIELDS}
        lines = [p.strip() for p in _LINE_SPLIT.split(str(row.get("lines") or "")) if p.strip()]
        if not lines and not any(values.values()):
            continue
        address_type = values.pop("address_type")
        addresses.append(
            SupplierAddress(
                **values,
                **({"address_type": address_type} if address_type else {}),
                lines=lines,
                human_confirmed=False,
            )
        )

    profiles = []
    for row in form.get("payment_profiles", []):
        values = {name: _text(row.get(name)) for name in _PAYMENT_FIELDS}
        if not any(values.values()):
            continue
        if values["delay_days"] is not None:
            values["delay_days"] = int(values["delay_days"])
        profiles.append(PaymentProfile(**values, human_confirmed=False))

    custom_fields: dict[str, FieldValue] = {}
    for row in form.get("custom_fields", []):
        key, value = _text(row.get("key")), _text(row.get("value"))
        if key is None and value is None:
            continue
        if key is None:
            raise SupplierFormError("CUSTOM_FIELD_KEY_REQUIRED")
        if key in custom_fields:
            raise SupplierFormError("DUPLICATE_CUSTOM_FIELD", key)
        custom_fields[key] = FieldValue(raw=value)

    candidate = SupplierIdentity(
        supplier_id=base.supplier_id,
        legal_name=legal_name,
        printed_names=[t for t in (_text(v) for v in form.get("printed_names", [])) if t],
        aliases=aliases,
        country=_text(form.get("country")),
        identifiers=identifiers,
        addresses=addresses,
        payment_profiles=profiles,
        custom_fields=custom_fields,
    )
    return ParsedForm(candidate=candidate, confirmed_keys=keys, accepted_aliases=accepted)


# --- storage, preview and submission ------------------------------------


def open_components(
    paths: StoragePaths,
) -> tuple[SupplierIdentityRepository, SupplierRegistrationRepository, SupplierRegistrationCoordinator]:
    identities = SupplierIdentityRepository(paths.suppliers)
    proposals = SupplierRegistrationRepository(paths.proposals)
    coordinator = SupplierRegistrationCoordinator(
        identities, proposals, SupplierTransactionJournal(paths.journal)
    )
    return identities, proposals, coordinator


def preview(
    identities: SupplierIdentityRepository, candidate: SupplierIdentity, source_sha256: str
) -> Preview:
    """Read-only: existing-supplier suggestions and confirmed-identifier collisions."""
    proposal = SupplierRegistrationService(identities).propose(candidate, source_sha256)
    found = (identities.get(item) for item in proposal.suggested_supplier_ids)
    return Preview(
        suggestions=[item for item in found if item is not None],
        conflicts=list(proposal.identifier_conflicts),
    )


def update_unsupported_fields(
    candidate: SupplierIdentity, target: SupplierIdentity
) -> list[str]:
    fields = (
        ("legal_name", "legal_name"),
        ("printed_names", "printed_names"),
        ("addresses", "addresses"),
        ("payment_profiles", "payment_profiles"),
        ("custom_fields", "custom_fields"),
        ("country", "country"),
    )
    return [label for attr, label in fields if getattr(candidate, attr) != getattr(target, attr)]


def submit(
    paths: StoragePaths,
    parsed: ParsedForm,
    source_sha256: str,
    *,
    action: RegistrationAction,
    justification: str | None,
    human_confirmed: bool,
    target_supplier_id: UUID | None = None,
    base_candidate: SupplierIdentity | None = None,
) -> str:
    """Validate, persist the PENDING proposal, then run the coordinator."""
    action = RegistrationAction(action)
    if not human_confirmed:
        raise SupplierFormError("HUMAN_CONFIRMATION_REQUIRED")
    if not (justification or "").strip():
        raise SupplierFormError("JUSTIFICATION_REQUIRED")
    if action == RegistrationAction.UPDATE and target_supplier_id is None:
        raise SupplierFormError("TARGET_SUPPLIER_REQUIRED")
    if action != RegistrationAction.UPDATE and target_supplier_id is not None:
        raise SupplierFormError("TARGET_ONLY_FOR_UPDATE")
    if not _SHA256.fullmatch(source_sha256):
        raise SupplierFormError("INVALID_SOURCE_SHA256")
    if action != RegistrationAction.CANCEL and not set(parsed.accepted_aliases) <= set(parsed.candidate.aliases):
        raise SupplierFormError("ACCEPTED_ALIAS_NOT_IN_CANDIDATE")
    if action != RegistrationAction.CANCEL:
        available_keys = {
            (item.country, item.identifier_type, item.normalized_value)
            for item in parsed.candidate.identifiers
            if item.normalized_value
        }
        if not set(parsed.confirmed_keys) <= available_keys:
            raise SupplierFormError("CONFIRMED_IDENTIFIER_NOT_IN_CANDIDATE")

    identities, proposals, coordinator = open_components(paths)
    candidate = base_candidate if action == RegistrationAction.CANCEL and base_candidate else parsed.candidate
    if action == RegistrationAction.UPDATE:
        target = identities.get(target_supplier_id)
        if target is None:
            raise SupplierFormError("UPDATE_TARGET_NOT_FOUND")
        if not target.active:
            raise SupplierFormError("UPDATE_TARGET_INACTIVE")
        unsupported = update_unsupported_fields(parsed.candidate, target)
        if unsupported:
            raise SupplierFormError(
                "UPDATE_UNSUPPORTED_FIELD_CHANGE",
                ", ".join(unsupported),
            )
    proposal = SupplierRegistrationService(identities).propose(candidate, source_sha256)
    proposals.create(proposal)

    is_cancel = action == RegistrationAction.CANCEL
    decision = HumanRegistrationDecision(
        proposal_id=proposal.proposal_id,
        action=action,
        target_supplier_id=target_supplier_id,
        confirmed_identifier_keys=[] if is_cancel else list(parsed.confirmed_keys),
        accepted_aliases=[] if is_cancel else list(parsed.accepted_aliases),
        justification=justification,
        human_confirmed=True,
    )
    try:
        result = coordinator.execute(proposal.proposal_id, decision)
    except SupplierRegistrationCoordinatorError as exc:
        if exc.code == RECOVERY_REQUIRED:
            return RECOVERY_REQUIRED
        raise
    return CANCELLED if result.status == ProposalStatus.CANCELLED else COMPLETED


# --- command line ---------------------------------------------------------


def _sha256_arg(value: str) -> str:
    if not _SHA256.fullmatch(value):
        raise argparse.ArgumentTypeError("source_sha256 must be 64 lowercase hex characters")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Confirm a proposed supplier record.")
    parser.add_argument("party_json", help="JSON file containing one InvoiceParty")
    parser.add_argument("--source-sha256", required=True, type=_sha256_arg)
    parser.add_argument("--country", default=None, help="optional ISO 3166-1 alpha-2 code")
    parser.add_argument("--suppliers", required=True, type=Path, help="suppliers JSON storage")
    parser.add_argument("--proposals", required=True, type=Path, help="proposals JSON storage")
    parser.add_argument("--journal", required=True, type=Path, help="transaction journal storage")
    return parser.parse_args(argv)


# --- Tkinter layer (collects widget values only) ----------------------------


class _RowsEditor:
    """Editable table: Entry or check columns, add and visual remove of rows."""

    def __init__(self, tk: Any, parent: Any, columns: list[tuple[str, str, int, str]], rows: list):
        self._tk = tk
        self._columns = columns
        self.frame = tk.Frame(parent)
        self._body = tk.Frame(self.frame)
        self._body.pack(fill="x")
        self._rows: list[tuple[list[Any], dict[str, Any]]] = []
        self._next = 1
        for col, (_, label, _, _) in enumerate(columns):
            tk.Label(self._body, text=label).grid(row=0, column=col)
        tk.Button(self.frame, text="Ajouter", command=self.add_row).pack(anchor="w", pady=2)
        for row in rows:
            self.add_row(row)

    def add_row(self, values: dict[str, Any] | None = None) -> None:
        tk = self._tk
        values = values or {}
        variables: dict[str, Any] = {}
        widgets: list[Any] = []
        grid_row = self._next
        self._next += 1
        for col, (key, _, width, kind) in enumerate(self._columns):
            if kind == "check":
                var = tk.BooleanVar(value=bool(values.get(key, False)))
                widget = tk.Checkbutton(self._body, variable=var)
            else:
                var = tk.StringVar(value=str(values.get(key) or ""))
                widget = tk.Entry(self._body, textvariable=var, width=width)
            widget.grid(row=grid_row, column=col, padx=2, pady=1)
            variables[key] = var
            widgets.append(widget)
        entry = (widgets, variables)
        button = tk.Button(self._body, text="Supprimer", command=lambda: self._remove(entry))
        button.grid(row=grid_row, column=len(self._columns))
        widgets.append(button)
        self._rows.append(entry)

    def _remove(self, entry: tuple[list[Any], dict[str, Any]]) -> None:
        for widget in entry[0]:
            widget.destroy()
        self._rows.remove(entry)

    def collect(self) -> list[dict[str, Any]]:
        return [{key: var.get() for key, var in variables.items()} for _, variables in self._rows]


def run_gui(base: SupplierIdentity, paths: StoragePaths, source_sha256: str) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    identities, _, _ = open_components(paths)
    initial = form_from_candidate(base)
    root = tk.Tk()
    root.title("Confirmation fournisseur")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    def tab(title: str) -> Any:
        frame = tk.Frame(notebook)
        notebook.add(frame, text=title)
        return frame

    identity_tab = tab("Identité")
    legal_name = tk.StringVar(value=initial["legal_name"])
    country = tk.StringVar(value=initial["country"])
    tk.Label(identity_tab, text="Nom légal").pack(anchor="w")
    tk.Entry(identity_tab, textvariable=legal_name, width=60).pack(anchor="w")
    tk.Label(identity_tab, text="Pays (ISO 2, facultatif)").pack(anchor="w")
    tk.Entry(identity_tab, textvariable=country, width=6).pack(anchor="w")
    tk.Label(identity_tab, text="Noms imprimés").pack(anchor="w")
    printed = _RowsEditor(
        tk, identity_tab, [("value", "Nom", 50, "entry")], [{"value": v} for v in initial["printed_names"]]
    )
    printed.frame.pack(anchor="w")
    tk.Label(identity_tab, text="Aliases (cocher pour accepter)").pack(anchor="w")
    aliases = _RowsEditor(
        tk,
        identity_tab,
        [("alias", "Alias", 40, "entry"), ("accepted", "Accepter", 0, "check")],
        initial["aliases"],
    )
    aliases.frame.pack(anchor="w")

    identifiers = _RowsEditor(
        tk,
        tab("Identifiants"),
        [
            ("identifier_type", "Type", 10, "entry"),
            ("scope", "Scope", 10, "entry"),
            ("country", "Pays", 4, "entry"),
            ("local_label", "Libellé local", 14, "entry"),
            ("raw_value", "Valeur brute", 20, "entry"),
            ("normalized_value", "Valeur normalisée", 20, "entry"),
            ("confirm", "Confirmer humainement", 0, "check"),
        ],
        initial["identifiers"],
    )
    identifiers.frame.pack(anchor="w")
    addresses = _RowsEditor(
        tk,
        tab("Adresses"),
        [
            ("address_type", "Type", 10, "entry"),
            ("lines", "Lignes (séparées par |)", 40, "entry"),
            ("postal_code", "Code postal", 10, "entry"),
            ("city", "Ville", 14, "entry"),
            ("region", "Région", 12, "entry"),
            ("country", "Pays", 4, "entry"),
        ],
        initial["addresses"],
    )
    addresses.frame.pack(anchor="w")
    payments = _RowsEditor(
        tk,
        tab("Paiement"),
        [
            ("payment_terms_label", "Libellé", 20, "entry"),
            ("delay_days", "Délai (jours)", 6, "entry"),
            ("calculation_rule", "Règle", 16, "entry"),
            ("payment_method", "Mode", 12, "entry"),
            ("issue_date", "Émission AAAA-MM-JJ", 12, "entry"),
            ("due_date", "Échéance AAAA-MM-JJ", 12, "entry"),
        ],
        initial["payment_profiles"],
    )
    payments.frame.pack(anchor="w")
    customs = _RowsEditor(
        tk,
        tab("Champs personnalisés"),
        [("key", "Clé (snake_case)", 24, "entry"), ("value", "Valeur", 40, "entry")],
        initial["custom_fields"],
    )
    customs.frame.pack(anchor="w")

    decision_tab = tab("Décision")
    state: dict[str, Any] = {"suggestions": [], "done": False}

    def collect_form() -> dict[str, Any]:
        return {
            "legal_name": legal_name.get(),
            "country": country.get(),
            "printed_names": [row["value"] for row in printed.collect()],
            "aliases": aliases.collect(),
            "identifiers": identifiers.collect(),
            "addresses": addresses.collect(),
            "payment_profiles": payments.collect(),
            "custom_fields": customs.collect(),
        }

    baseline = collect_form()

    tk.Label(decision_tab, text="Fournisseurs existants suggérés (sélection explicite pour UPDATE)").pack(
        anchor="w"
    )
    suggestion_list = tk.Listbox(decision_tab, height=5, width=90, exportselection=False)
    suggestion_list.pack(anchor="w")
    tk.Label(decision_tab, text="Collisions d'identifiants confirmés").pack(anchor="w")
    conflict_list = tk.Listbox(decision_tab, height=4, width=90, exportselection=False)
    conflict_list.pack(anchor="w")

    def refresh() -> None:
        try:
            shown = preview(identities, parse_form(base, collect_form()).candidate, source_sha256)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Prévisualisation impossible", str(exc))
            return
        state["suggestions"] = shown.suggestions
        suggestion_list.delete(0, "end")
        for item in shown.suggestions:
            suggestion_list.insert("end", f"{item.legal_name} — {item.supplier_id}")
        conflict_list.delete(0, "end")
        for item in shown.conflicts:
            country_code, id_type, value = item.identifier_key
            conflict_list.insert("end", f"{country_code or '-'}/{id_type}/{value} → {item.supplier_id}")

    tk.Button(decision_tab, text="Actualiser suggestions et collisions", command=refresh).pack(anchor="w")

    action_var = tk.StringVar(value="")
    for action in RegistrationAction:
        tk.Radiobutton(decision_tab, text=action.value, variable=action_var, value=action.value).pack(
            anchor="w"
        )
    tk.Label(
        decision_tab,
        text="UPDATE enregistre uniquement les identifiants confirmés et les aliases acceptés. "
        "Les autres champs ne sont pas modifiés.",
        wraplength=650,
        justify="left",
    ).pack(anchor="w")
    tk.Label(
        decision_tab,
        text="CANCEL : aucune donnée fournisseur ne sera enregistrée.",
    ).pack(anchor="w")
    human_var = tk.BooleanVar(value=False)
    tk.Checkbutton(decision_tab, text="Je confirme explicitement cette décision", variable=human_var).pack(
        anchor="w"
    )
    justification_var = tk.StringVar()
    tk.Label(decision_tab, text="Justification").pack(anchor="w")
    tk.Entry(decision_tab, textvariable=justification_var, width=80).pack(anchor="w")

    def execute() -> None:
        if state["done"]:
            return
        try:
            if not action_var.get():
                raise SupplierFormError("ACTION_REQUIRED")
            action = RegistrationAction(action_var.get())
            target = None
            if action == RegistrationAction.UPDATE:
                selected = suggestion_list.curselection()
                if not selected:
                    raise SupplierFormError("TARGET_SUPPLIER_REQUIRED")
                target = state["suggestions"][selected[0]].supplier_id
                parsed = parse_form(base, collect_form())
            elif action == RegistrationAction.CANCEL:
                parsed = ParsedForm(candidate=base, confirmed_keys=[], accepted_aliases=[])
            else:
                parsed = parse_form(base, collect_form())
            prompt = (
                "Annuler cette proposition ? Aucune donnée fournisseur ne sera enregistrée."
                if action == RegistrationAction.CANCEL
                else "Persister cette décision fournisseur ?"
            )
            if not messagebox.askyesno("Confirmation", prompt):
                return
            result = submit(
                paths,
                parsed,
                source_sha256,
                action=action,
                justification=justification_var.get(),
                human_confirmed=human_var.get(),
                target_supplier_id=target,
                base_candidate=base,
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("Décision refusée", str(exc))
            return
        state["done"] = True
        execute_button.config(state="disabled")
        show = messagebox.showwarning if result == RECOVERY_REQUIRED else messagebox.showinfo
        show("Résultat", result)

    execute_button = tk.Button(decision_tab, text="Exécuter", command=execute)
    execute_button.pack(anchor="w", pady=6)

    def on_close() -> None:
        if not state["done"] and has_unsaved_draft(baseline, collect_form()):
            if not messagebox.askyesno(
                "Brouillon non confirmé",
                "Les saisies non confirmées seront perdues, rien n'est sauvegardé. Fermer ?",
            ):
                return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = StoragePaths(args.suppliers, args.proposals, args.journal)
    try:
        base = build_candidate(load_invoice_party(args.party_json), args.country)
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    return run_gui(base, paths, args.source_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
