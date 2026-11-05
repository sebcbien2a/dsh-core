"""P18 - CA12 VAT simulation runner for Assistance Peintures Diffusion.

Usage::

    python scripts/simulate_ca12_apd.py --tenant default --year 2026

The runner reads the validated lines of the tenant SQLite accounting store,
prints a comparative Scenario A / Scenario B table and writes
``workspace/exports/accounting/<tenant>/SIMULATION_CA12_<year>.xlsx``.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.sqlite_accounting_store import (  # noqa: E402
    AccountingStoreError,
    SqliteAccountingStore,
)
from services.vat_simulation_service import (  # noqa: E402
    Ca12SimulationReport,
    VatSimulationError,
    VatSimulationService,
    report_display_name,
    report_payload,
)

DEFAULT_TENANT = "APD"
DEFAULT_DB = REPO_ROOT / "data" / "accounting" / "accounting.sqlite3"
DEFAULT_EXPORT_DIR = REPO_ROOT / "workspace" / "exports" / "accounting"
LINE_WIDTH = 78


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Simulate the annual CA12 VAT declaration (APD)."
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR))
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also print the simulation payload as JSON.",
    )
    return parser


def _money(value: Decimal) -> str:
    return f"{value:>13,.2f}".replace(",", " ").replace(".", ",")


def _delta(value: Decimal) -> str:
    if value == 0:
        return f"{'0,00':>13}"
    sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):>12,.2f}".replace(",", " ").replace(".", ",")


def _row(label: str, first: str, second: str, delta: str) -> str:
    return f"{label:<40}{first}{second}{delta}"


def _print_report(report: Ca12SimulationReport, export_path: Path) -> None:
    a = report.scenario_a
    b = report.scenario_b
    title = report_display_name(report.tenant_id)
    print("=" * LINE_WIDTH)
    print(f"SIMULATION CA12 - {title} - EXERCICE {report.year}")
    print("=" * LINE_WIDTH)
    print(
        _row(
            "Rubrique",
            f"{'Scenario A':>13}",
            f"{'Scenario B':>13}",
            f"{'Ecart':>13}",
        )
    )
    print("-" * LINE_WIDTH)
    entries: tuple[tuple[str, Decimal, Decimal], ...] = (
        ("TVA collectee", a.collected_tva, b.collected_tva),
        (
            "TVA deductible - Achats marchandises",
            a.deductible_achats_marchandises,
            b.deductible_achats_marchandises,
        ),
        (
            "TVA deductible - Transports / logistique",
            a.deductible_transports,
            b.deductible_transports,
        ),
        (
            "TVA deductible - Frais generaux",
            a.deductible_frais_generaux,
            b.deductible_frais_generaux,
        ),
        (
            "TVA deductible - Biens et services",
            a.deductible_goods_services,
            b.deductible_goods_services,
        ),
        (
            "TVA deductible - Immobilisations",
            a.deductible_immobilisations,
            b.deductible_immobilisations,
        ),
        ("TVA deductible - Total", a.deductible_total, b.deductible_total),
        ("TVA nette", a.net_tva, b.net_tva),
        ("TVA due", a.tva_due, b.tva_due),
        ("Credit de TVA", a.credit_tva, b.credit_tva),
        (
            "TVA non deductible (reintegree)",
            a.non_deductible_tva,
            b.non_deductible_tva,
        ),
        ("Charges (base resultat imposable)", a.charges, b.charges),
    )
    for label, first, second in entries:
        print(_row(label, _money(first), _money(second), _delta(second - first)))
    print("-" * LINE_WIDTH)
    print(f"Pieces : A={a.pieces} / B={b.pieces}")
    print(
        f"Reports N+1 : {len(report.deferred_to_next_year)} piece(s) / "
        f"reports entrants N : {len(report.incoming_from_previous_year)} piece(s)"
    )
    print(
        "Impact sur le resultat imposable (A - B) : "
        f"{_money(report.taxable_result_impact).strip()}"
    )
    print("-" * LINE_WIDTH)
    print(f"Acomptes semestriels {report.next_year} :")
    for scenario, acomptes in (
        ("A", report.scenario_a.acomptes),
        ("B", report.scenario_b.acomptes),
    ):
        details = " / ".join(
            f"{item.period.capitalize()} {item.rate * 100:.0f}% = "
            f"{_money(item.amount).strip()} (echeance {item.due_date:%d/%m/%Y})"
            for item in acomptes
        )
        print(f"  Scenario {scenario} : {details}")
    print("=" * LINE_WIDTH)
    print(f"Rapport Excel : {export_path}")
    print("=" * LINE_WIDTH)


def run(args: argparse.Namespace) -> tuple[Ca12SimulationReport, Path]:
    store = SqliteAccountingStore(args.db)
    service = VatSimulationService(store, export_dir=args.export_dir)
    report = service.simulate(args.tenant, args.year)
    export_path = service.write_report(report)
    return report, export_path


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report, export_path = run(args)
    except (VatSimulationError, AccountingStoreError, ValueError) as exc:
        code = getattr(exc, "code", "SIMULATION_ERROR")
        print(f"{code}: {exc}", file=sys.stderr)
        return 2
    _print_report(report, export_path)
    if args.json:
        print(json.dumps(report_payload(report), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
