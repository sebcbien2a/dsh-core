"""Local Tkinter tool to fill OCR ground truth by hand.

Run with the DSH-Core interpreter, for example:
    A:\\dsh-core\\.venv-v3\\Scripts\\python.exe -m benchmarks.validate_ocr_ground_truth <dataset.json>

The tool is offline, never invokes a model and never prints OCR text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.extractor_benchmark_runner import FIELDS, MONEY_FIELDS, _normalize

KNOWN = "known"
ABSENT = "absent"
UNREADABLE = "unreadable"
STATES = (KNOWN, ABSENT, UNREADABLE)
STATUS_KEY = "ground_truth_status"
VALIDATED_AT_KEY = "ground_truth_validated_at"
_SIRET = re.compile(r"^\d{14}$")


class DatasetError(ValueError):
    """Raised for an unusable dataset; messages never contain OCR text."""


def check_dataset_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.suffix.lower() != ".json":
        raise DatasetError("Le dataset doit être un fichier .json.")
    if not candidate.is_file():
        raise DatasetError("Le fichier dataset est introuvable.")
    return candidate


def validate_dataset(dataset: Any) -> None:
    if not isinstance(dataset, dict) or not isinstance(dataset.get("cases"), list):
        raise DatasetError("Structure de dataset invalide : 'cases' manquant.")
    if not dataset["cases"]:
        raise DatasetError("Le dataset ne contient aucun cas.")
    seen: set[str] = set()
    for index, case in enumerate(dataset["cases"], start=1):
        label = f"cas #{index}"
        if not isinstance(case, dict):
            raise DatasetError(f"{label} invalide.")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise DatasetError(f"{label} : identifiant absent ou dupliqué.")
        seen.add(case_id)
        if not isinstance(case.get("ocr_text"), str) or not case["ocr_text"].strip():
            raise DatasetError(f"{case_id} : texte OCR absent.")
        truth = case.get("ground_truth")
        if not isinstance(truth, dict) or set(truth) != set(FIELDS):
            raise DatasetError(f"{case_id} : les six champs de vérité terrain sont requis.")
        if not isinstance(case.get("metadata"), dict):
            raise DatasetError(f"{case_id} : metadata absentes.")


def load_dataset(path: str | Path) -> dict[str, Any]:
    source = check_dataset_path(path)
    try:
        dataset = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError("Le fichier n'est pas un JSON lisible.") from exc
    validate_dataset(dataset)
    return dataset


def validate_field(field: str, state: str, value: str | None) -> tuple[bool, str | None]:
    """Validate one decision without ever correcting the typed value."""
    if field not in FIELDS:
        return False, "Champ inconnu."
    if state not in STATES:
        return False, "État requis : KNOWN, ABSENT ou UNREADABLE."
    if state != KNOWN:
        return True, None
    text = (value or "").strip()
    if not text:
        return False, "Valeur requise pour KNOWN."
    if field == "fournisseur":
        return True, None
    if field == "siret":
        if _SIRET.match(text):
            return True, None
        return False, "SIRET : exactement 14 chiffres."
    if _normalize(field, text) is None:
        if field == "date":
            return False, "Date : YYYY-MM-DD ou DD/MM/YYYY."
        if field in MONEY_FIELDS:
            return False, "Montant invalide."
    return True, None


def validate_decisions(decisions: Mapping[str, tuple[str | None, str | None]]) -> dict[str, str]:
    """Return per-field errors; an empty dict means the case can be confirmed."""
    errors: dict[str, str] = {}
    for field in FIELDS:
        state, value = decisions.get(field, (None, None))
        ok, message = validate_field(field, state or "", value)
        if not ok:
            errors[field] = message or "Invalide."
    return errors


def apply_decisions(
    case: dict[str, Any],
    decisions: Mapping[str, tuple[str | None, str | None]],
    now: datetime | None = None,
) -> None:
    errors = validate_decisions(decisions)
    if errors:
        raise DatasetError("Décisions invalides : " + ", ".join(sorted(errors)))
    truth: dict[str, Any] = {}
    status: dict[str, str] = {}
    for field in FIELDS:
        state, value = decisions[field]
        status[field] = state
        truth[field] = (value or "").strip() if state == KNOWN else None
    case["ground_truth"] = truth
    metadata = case["metadata"]
    metadata[STATUS_KEY] = status
    metadata[VALIDATED_AT_KEY] = (now or datetime.now()).replace(microsecond=0).isoformat()
    metadata["reviewed_by_human"] = True


def case_decisions(case: Mapping[str, Any]) -> dict[str, tuple[str | None, str | None]]:
    """Previously saved human decisions only; never a model-derived value."""
    metadata = case.get("metadata", {})
    status = metadata.get(STATUS_KEY) if metadata.get("reviewed_by_human") is True else None
    if not isinstance(status, dict):
        return {field: (None, None) for field in FIELDS}
    truth = case.get("ground_truth", {})
    return {field: (status.get(field), truth.get(field)) for field in FIELDS}


def is_confirmed(case: Mapping[str, Any]) -> bool:
    metadata = case.get("metadata", {})
    status = metadata.get(STATUS_KEY)
    return (
        metadata.get("reviewed_by_human") is True
        and isinstance(status, dict)
        and all(status.get(field) in STATES for field in FIELDS)
    )


def progress(dataset: Mapping[str, Any]) -> tuple[int, int]:
    cases = dataset["cases"]
    return sum(1 for case in cases if is_confirmed(case)), len(cases)


def first_unconfirmed_index(dataset: Mapping[str, Any]) -> int:
    for index, case in enumerate(dataset["cases"]):
        if not is_confirmed(case):
            return index
    return 0


def save_dataset_atomic(path: str | Path, dataset: Mapping[str, Any]) -> None:
    """Write a `.bak` copy, then replace the file atomically via a temp file."""
    target = Path(path)
    serialized = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
    json.loads(serialized)
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    descriptor, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def run_gui(path: Path, dataset: dict[str, Any]) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Validation humaine - vérité terrain OCR")
    labels = {
        "fournisseur": "Fournisseur",
        "date": "Date",
        "ht": "HT",
        "tva": "TVA",
        "ttc": "TTC",
        "siret": "SIRET",
    }
    index = {"value": first_unconfirmed_index(dataset)}
    drafts: dict[int, dict[str, tuple[str | None, str | None]]] = {}
    state_vars = {field: tk.StringVar(value="") for field in FIELDS}
    entries: dict[str, Any] = {}

    header = tk.Label(root, anchor="w")
    header.pack(fill="x", padx=8, pady=(8, 0))
    text = tk.Text(root, height=18, width=100, wrap="word")
    text.pack(fill="both", expand=True, padx=8, pady=8)
    form = tk.Frame(root)
    form.pack(fill="x", padx=8)
    for row, field in enumerate(FIELDS):
        tk.Label(form, text=labels[field], width=12, anchor="w").grid(row=row, column=0)
        for column, state in enumerate(STATES, start=1):
            tk.Radiobutton(
                form, text=state.upper(), variable=state_vars[field], value=state
            ).grid(row=row, column=column)
        entry = tk.Entry(form, width=40)
        entry.grid(row=row, column=4, padx=4, pady=1)
        entries[field] = entry

    def collect() -> dict[str, tuple[str | None, str | None]]:
        return {
            field: (state_vars[field].get() or None, entries[field].get())
            for field in FIELDS
        }

    def render() -> None:
        case = dataset["cases"][index["value"]]
        done, total = progress(dataset)
        marker = "confirmé" if is_confirmed(case) else "à valider"
        header.config(
            text=f"{case['id']} ({index['value'] + 1}/{total}) - {marker} - "
            f"{done}/{total} cas confirmés"
        )
        text.config(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", case["ocr_text"])
        text.config(state="disabled")
        decisions = drafts.get(index["value"]) or case_decisions(case)
        for field in FIELDS:
            state, value = decisions[field]
            state_vars[field].set(state or "")
            entries[field].delete(0, "end")
            if value:
                entries[field].insert(0, value)

    def move(step: int) -> None:
        drafts[index["value"]] = collect()
        index["value"] = max(0, min(len(dataset["cases"]) - 1, index["value"] + step))
        render()

    def confirm() -> None:
        decisions = collect()
        errors = validate_decisions(decisions)
        if errors:
            messagebox.showerror(
                "Validation refusée",
                "\n".join(f"{labels[f]} : {m}" for f, m in errors.items()),
            )
            return
        if not messagebox.askyesno("Confirmation", "Confirmer la validation de ce cas ?"):
            return
        apply_decisions(dataset["cases"][index["value"]], decisions)
        drafts.pop(index["value"], None)
        try:
            save_dataset_atomic(path, dataset)
        except OSError:
            messagebox.showerror("Sauvegarde", "Échec de la sauvegarde locale.")
            return
        if index["value"] < len(dataset["cases"]) - 1:
            move(1)
        else:
            render()

    buttons = tk.Frame(root)
    buttons.pack(pady=8)
    tk.Button(buttons, text="Précédent", command=lambda: move(-1)).pack(side="left", padx=4)
    tk.Button(buttons, text="Confirmer ce cas", command=confirm).pack(side="left", padx=4)
    tk.Button(buttons, text="Suivant", command=lambda: move(1)).pack(side="left", padx=4)
    render()
    root.mainloop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validation humaine locale du dataset OCR.")
    parser.add_argument("dataset", help="Chemin du dataset JSON local.")
    args = parser.parse_args(argv)
    try:
        path = check_dataset_path(args.dataset)
        dataset = load_dataset(path)
    except DatasetError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    run_gui(path, dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
