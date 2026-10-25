"""Local Tkinter tool to fill OCR ground truth by hand.

Run with the DSH-Core interpreter, for example:
    A:\\dsh-core\\.venv-v3\\Scripts\\python.exe -m benchmarks.validate_ocr_ground_truth <dataset.json>

The first source page is shown next to the frozen OCR text, rendered in memory
only. The tool is offline, never invokes a model and never prints OCR text.
"""

from __future__ import annotations

import argparse
import hashlib
import io
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


class SourceError(DatasetError):
    """Source missing, unsafe or altered; messages never contain paths."""


PREVIEW_DPI = 170
MIN_ZOOM = 0.1
MAX_ZOOM = 3.0
ZOOM_STEP = 1.25

HELP_STATES = (
    "KNOWN = champ présent, valeur lisible et attribution certaine\n"
    "ABSENT = champ absent du document\n"
    "UNREADABLE = champ présent mais illisible ou impossible à attribuer avec certitude"
)
HELP_WARNINGS = (
    "Numéro TVA ≠ SIRET  |  Ne pas déduire HT, TVA ou TTC\n"
    "Pour un tableau, utiliser les colonnes de la page source\n"
    "Si plusieurs nombres ne peuvent pas être rattachés avec certitude, "
    "choisir UNREADABLE pour le champ concerné"
)


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceError("Manifeste illisible.") from exc
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        raise SourceError("Manifeste invalide : 'entries' manquant.")
    return [entry for entry in entries if isinstance(entry, dict)]


def resolve_source(
    sha256: Any, entries: Sequence[Mapping[str, Any]], repo_root: str | Path
) -> tuple[bytes, Mapping[str, Any]]:
    """Find the manifest entry by exact hash and return its verified bytes."""
    if not isinstance(sha256, str) or not sha256:
        raise SourceError("Hash source absent du cas.")
    entry = next((item for item in entries if item.get("sha256") == sha256), None)
    if entry is None:
        raise SourceError("Aucune source du manifeste ne correspond au hash.")
    relative = entry.get("relative_path")
    if not isinstance(relative, str) or not relative:
        raise SourceError("Entrée du manifeste sans chemin.")
    root = Path(repo_root).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise SourceError("Chemin source hors du dépôt.") from exc
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise SourceError("Fichier source introuvable ou illisible.") from exc
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SourceError("Le hash de la source ne correspond plus.")
    return data, entry


def load_source_image(data: bytes, entry: Mapping[str, Any]) -> Any:
    """Render page 1 (PDF) or load the image in memory, with manifest rotation."""
    try:
        from PIL import Image

        if entry.get("media_type") == "application/pdf":
            import pymupdf

            document = pymupdf.open(stream=data, filetype="pdf")
            try:
                scale = PREVIEW_DPI / 72.0
                pixmap = document.load_page(0).get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), alpha=False
                )
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            finally:
                document.close()
        else:
            with Image.open(io.BytesIO(data)) as opened:
                image = opened.convert("RGB")
        rotation = int(entry.get("expected_orientation_degrees") or 0)
        if rotation % 360:
            image = image.rotate(
                rotation, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"
            )
        return image
    except Exception as exc:
        raise SourceError("Aperçu de la source impossible.") from exc


def clamp_zoom(zoom: float) -> float:
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom))


def fit_zoom(size: tuple[int, int], box: tuple[int, int]) -> float:
    width, height = size
    return clamp_zoom(min(box[0] / max(width, 1), box[1] / max(height, 1)))


def render_preview_png(image: Any, zoom: float) -> bytes:
    from PIL import Image

    zoom = clamp_zoom(zoom)
    size = (max(1, round(image.width * zoom)), max(1, round(image.height * zoom)))
    scaled = image if size == image.size else image.resize(size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    return buffer.getvalue()


def require_verified_source(verified: bool) -> None:
    if not verified:
        raise SourceError("Confirmation bloquée : source ou hash non vérifié.")


def run_gui(
    path: Path,
    dataset: dict[str, Any],
    entries: Sequence[Mapping[str, Any]],
    repo_root: Path,
) -> None:
    import base64
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("Validation humaine - vérité terrain OCR")
    root.geometry("1500x900")
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
    field_entries: dict[str, Any] = {}
    view: dict[str, Any] = {"image": None, "zoom": 1.0, "photo": None, "verified": False}

    header = tk.Label(root, anchor="w")
    header.pack(fill="x", padx=8, pady=(8, 0))
    help_label = tk.Label(
        root, text=HELP_STATES + "\n" + HELP_WARNINGS, justify="left", anchor="w", fg="#8a1c1c"
    )
    help_label.pack(fill="x", padx=8)
    status = tk.Label(root, anchor="w")
    status.pack(fill="x", padx=8)
    panes = tk.PanedWindow(root, orient="horizontal", sashrelief="raised")
    panes.pack(fill="both", expand=True, padx=8, pady=4)
    left = tk.Frame(panes)
    canvas = tk.Canvas(left, bg="#dddddd", width=760, height=520)
    vbar = tk.Scrollbar(left, orient="vertical", command=canvas.yview)
    hbar = tk.Scrollbar(left, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
    hbar.pack(side="bottom", fill="x")
    vbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    panes.add(left)
    text = tk.Text(panes, height=18, width=60, wrap="word")
    panes.add(text)
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
        field_entries[field] = entry

    def collect() -> dict[str, tuple[str | None, str | None]]:
        return {
            field: (state_vars[field].get() or None, field_entries[field].get())
            for field in FIELDS
        }

    def draw() -> None:
        canvas.delete("all")
        if view["image"] is None:
            return
        png = render_preview_png(view["image"], view["zoom"])
        view["photo"] = tk.PhotoImage(data=base64.b64encode(png))
        canvas.create_image(0, 0, anchor="nw", image=view["photo"])
        canvas.configure(scrollregion=canvas.bbox("all"))

    def fit() -> None:
        if view["image"] is not None:
            box = (max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 200))
            view["zoom"] = fit_zoom(view["image"].size, box)
            draw()

    def zoom_by(factor: float) -> None:
        view["zoom"] = clamp_zoom(view["zoom"] * factor)
        draw()

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
        try:
            data, entry = resolve_source(
                case.get("metadata", {}).get("source_sha256"), entries, repo_root
            )
            view["image"] = load_source_image(data, entry)
            view["verified"] = True
            status.config(text="Source vérifiée (SHA-256).", fg="#1c6b1c")
        except SourceError as exc:
            view["image"] = None
            view["verified"] = False
            status.config(text=f"Source non vérifiée : {exc}", fg="#8a1c1c")
        root.update_idletasks()
        fit()
        decisions = drafts.get(index["value"]) or case_decisions(case)
        for field in FIELDS:
            state, value = decisions[field]
            state_vars[field].set(state or "")
            field_entries[field].delete(0, "end")
            if value:
                field_entries[field].insert(0, value)

    def move(step: int) -> None:
        drafts[index["value"]] = collect()
        index["value"] = max(0, min(len(dataset["cases"]) - 1, index["value"] + step))
        render()

    def confirm() -> None:
        try:
            require_verified_source(view["verified"])
        except SourceError as exc:
            messagebox.showerror("Confirmation bloquée", str(exc))
            return
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
    tk.Button(buttons, text="Zoom +", command=lambda: zoom_by(ZOOM_STEP)).pack(side="left", padx=12)
    tk.Button(buttons, text="Zoom -", command=lambda: zoom_by(1 / ZOOM_STEP)).pack(side="left", padx=4)
    tk.Button(buttons, text="Ajuster", command=fit).pack(side="left", padx=4)
    render()
    root.mainloop()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validation humaine locale du dataset OCR.")
    parser.add_argument("dataset", help="Chemin du dataset JSON local.")
    parser.add_argument(
        "--manifest",
        default="tests/fixtures/orientation_corpus_manifest.json",
        help="Manifeste des sources (hash -> chemin relatif, orientation).",
    )
    parser.add_argument("--repo-root", default=".", help="Racine du dépôt des sources.")
    args = parser.parse_args(argv)
    try:
        path = check_dataset_path(args.dataset)
        dataset = load_dataset(path)
        manifest_entries = load_manifest(args.manifest)
    except DatasetError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    run_gui(path, dataset, manifest_entries, Path(args.repo_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
