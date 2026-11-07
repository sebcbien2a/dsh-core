"""P26 - Read-only repository split audit: ``dsh-core`` vs ``apd-compta``.

The script never moves, renames or deletes a file. It walks the working tree,
classifies every file into one of three buckets and writes
``docs/AUDIT_SEPARATION_DSH_CORE_VS_COMPTA.md``:

1. the tri-system ``dsh-core`` backbone (harness, Hermes, Codex, 4D memory,
   Qdrant/Neo4j/Mem0/Obsidian, vision, OptSkill, audit);
2. the ``apd-compta`` business application (invoice/OFX ingestion,
   reconciliation, CA12 VAT, cockpit, tier reference, accounting rules);
3. tests and obsolete temporary artefacts to purge.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "AUDIT_SEPARATION_DSH_CORE_VS_COMPTA.md"

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".venv-v3",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".pytest_tmp",
        "qdrant_storage",
        "neo4j_data",
        "open-webui_data",
        "n8n_data",
        "langfuse_db",
        "backup",
        "LOCAL_APPDATA_FONTCONFIG_CACHE",
        ".obsidian",
        ".vscode",
        ".opencode",
        ".tmp.driveupload",
    }
)
TEMP_SUFFIXES = frozenset(
    {
        ".log",
        ".tmp",
        ".bak",
        ".pyc",
        ".pyo",
        ".orig",
        ".rej",
        ".swp",
        ".swo",
        ".~",
    }
)
TEMP_NAMES = frozenset(
    {
        "Ollama",
        "Sans titre.base",
        "Thumbs.db",
        "desktop.ini",
        ".DS_Store",
    }
)
DSH_CORE_TOKENS = (
    "harness",
    "hermes",
    "codex",
    "memory",
    "mem0",
    "qdrant",
    "neo4j",
    "obsidian",
    "vision",
    "optskill",
    "audit",
    "perception",
    "document_pipeline",
    "document_perception",
    "invoice_agent",
    "invoice_indexer",
    "ocr_engine",
    "model_router",
    "orchestrator",
    "router",
    "skills",
    "knowledge",
    "repositories",
    "validators",
    "schemas",
    "backend",
    "api_routes",
    "frontend",
    "docker",
    "config",
    "agents",
    "benchmarks",
    "tools",
    "dsh_harness",
    "memory_manager",
    "mcp_",
    "n8n_api",
    "anythingllm",
    "gmail_",
    "langfuse",
    "rag",
    "vault",
    "declarative",
    "instructions_task",
    "obsidian_vault",
    "rule_registry",
    "tenant",
    "multitenant",
    "persistence",
    "graph_repository",
    "neo4j_service",
    "qdrant_service",
    "embedding",
    "llamaindex",
    "nuextract",
    "profiler",
    "skill_creator",
)
APD_COMPTA_TOKENS = (
    "accounting",
    "compta",
    "apd",
    "invoice",
    "facture",
    "ofx",
    "lcr",
    "tva",
    "ca12",
    "vat",
    "cockpit",
    "tier",
    "referentiel",
    "rapprochement",
    "reconciliation",
    "fec",
    "balance",
    "supplier",
    "fournisseur",
    "client",
    "spreadsheet",
    "acompleter",
    "folder",
    "inbox",
    "bank",
    "banque",
    "payment",
    "paiement",
    "expense",
    "hotel",
    "customer",
    "sales",
    "ventes",
    "grand_livre",
    "ledger",
    "export",
    "pipeline",
    "four_stage",
    "hermes_accounting",
    "apd_control",
    "apd_evidence",
    "apd_four_stage",
    "opening_statement",
    "releve",
    "yucel",
    "bosch",
    "boxtal",
    "sage",
    "gmail_apd",
    "fetch_apd",
    "simulate_ca12",
    "refresh_cockpit",
    "run_apd",
    "run_site_builder",
    "personal_expense",
    "note_de_frais",
    "payment_method",
    "counterparty",
    "tva_dedup",
    "fiscal_year",
    "expense_subcategory",
    "hotel_expense",
    "customer_invoice",
    "bank_statement",
    "inbox_ingestion",
    "folder_sync",
    "folder_tree",
    "document_flow",
    "vat_simulation",
    "balance_simulation",
    "accounting_cleanup",
    "accounting_memory",
    "accounting_spreadsheet",
    "supplier_memory",
    "tier_reference",
    "apd_control_panel",
    "apd_four_stage_pipeline",
    "apd_evidence_rules",
    "apd_control_panel",
    "hermes_accounting_bridge",
    "pdf_splitter",
    "acompleter_readable",
    "acompleter_workbook",
    "customer_invoice_batch",
    "bank_statement_parser",
    "vat_simulation_service",
    "balance_simulation_service",
    "opening_statement_ingestion",
)


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    bucket: str
    reason: str


def _relative(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _is_temporary(relative: str, path: Path) -> bool:
    parts = relative.split("/")
    if any(part in EXCLUDED_DIRS for part in parts):
        return True
    if path.name in TEMP_NAMES:
        return True
    if path.suffix.lower() in TEMP_SUFFIXES:
        return True
    if path.name.startswith("~$"):
        return True
    if "__" in path.stem and path.suffix.lower() in {".xlsx", ".xlsm"}:
        return True
    if relative.startswith("tests/") or path.name.startswith("test_"):
        return True
    if relative.startswith("workspace/exports/"):
        return True
    if relative.startswith("workspace/document_flow/") and path.suffix.lower() in {
        ".tmp",
        ".bak",
    }:
        return True
    return False


def _score(relative: str, tokens: tuple[str, ...]) -> int:
    lowered = relative.casefold()
    return sum(1 for token in tokens if token in lowered)


def classify(path: Path) -> FileEntry:
    relative = _relative(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if _is_temporary(relative, path):
        return FileEntry(relative, size, "temporaire", "test / artefact obsolète")
    dsh_score = _score(relative, DSH_CORE_TOKENS)
    apd_score = _score(relative, APD_COMPTA_TOKENS)
    if apd_score > dsh_score:
        return FileEntry(relative, size, "apd-compta", "métier comptable APD")
    if dsh_score > 0:
        return FileEntry(relative, size, "dsh-core", "socle tri-système")
    if relative.startswith("docs/"):
        return FileEntry(relative, size, "dsh-core", "documentation du socle")
    if relative.startswith("scripts/"):
        return FileEntry(relative, size, "dsh-core", "outillage du socle")
    return FileEntry(relative, size, "dsh-core", "racine / non classé")


def walk(root: Path) -> list[FileEntry]:
    entries: list[FileEntry] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in EXCLUDED_DIRS and not name.startswith(".git")
        ]
        for name in files:
            path = Path(current) / name
            entries.append(classify(path))
    return sorted(entries, key=lambda item: item.path.casefold())


def _table(entries: list[FileEntry]) -> str:
    lines = [
        "| Fichier | Taille (o) | Justification |",
        "|---|---:|---|",
    ]
    for entry in entries:
        lines.append(
            f"| `{entry.path}` | {entry.size} | {entry.reason} |"
        )
    return "\n".join(lines)


def build_report(entries: list[FileEntry]) -> str:
    dsh = [entry for entry in entries if entry.bucket == "dsh-core"]
    apd = [entry for entry in entries if entry.bucket == "apd-compta"]
    temp = [entry for entry in entries if entry.bucket == "temporaire"]
    total_size = sum(entry.size for entry in entries)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""# Audit de séparation `dsh-core` vs `apd-compta`

> Généré automatiquement par `scripts/audit_dsh_vs_compta_map.py` le {generated}.
> **Aucun fichier n'a été déplacé, renommé ou supprimé.**

## 1. Synthèse

| Indicateur | Valeur |
|---|---:|
| Fichiers analysés | {len(entries)} |
| Taille totale | {total_size} octets |
| Socle `dsh-core` | {len(dsh)} |
| Application `apd-compta` | {len(apd)} |
| Tests / artefacts à purger | {len(temp)} |

## 2. Socle tri-système `dsh-core`

Harnais, Hermes, Codex, mémoire 4D (Qdrant / Neo4j / Mem0 / Obsidian),
Vision, OptSkill et Audit.

{_table(dsh)}

## 3. Application métier `apd-compta`

Ingestion factures / OFX, rapprochement, TVA CA12, Cockpit, référentiel
tiers et règles comptables.

{_table(apd)}

## 4. Tests et artefacts temporaires obsolètes à purger

{_table(temp)}

## 5. Recommandations de découpage

1. Extraire `services/accounting_*`, `services/apd_*`, `services/*invoice*`,
   `services/*bank*`, `services/*tier*`, `services/*vat*` et
   `services/opening_statement_*` dans un paquet `apd_compta/`.
2. Conserver dans `dsh_core/` le harnais, Hermes, Codex, la mémoire 4D,
   la perception documentaire, OptSkill et l'audit.
3. Déplacer les tests métier dans `apd_compta/tests/` et les tests du socle
   dans `dsh_core/tests/`.
4. Purger les fichiers listés en section 4 (logs, sauvegardes, doublons
   `__N.xlsx`, caches) après validation du dirigeant.
5. Garder `workspace/document_flow/APD/` comme point de montage unique des
   données métier, jamais comme code.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    output = Path(args.output)
    entries = walk(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(entries), encoding="utf-8")
    counts = {
        bucket: sum(1 for entry in entries if entry.bucket == bucket)
        for bucket in ("dsh-core", "apd-compta", "temporaire")
    }
    print(f"Rapport écrit : {output}")
    print(
        "dsh-core={dsh-core} apd-compta={apd-compta} temporaire={temporaire}".format(
            **counts
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
