"""P26 - Apply the real CARROSSERIE_YUCEL opening statement (06/10/2026).

The director's ground truth is written as a canonical
``RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.xlsx`` workbook, then ingested
into SQLite. The final outstanding balance must be exactly ``8 234,28 EUR``.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.opening_statement_ingestion_service import (  # noqa: E402
    OPENING_STATEMENT_STATUS_DUE,
    OPENING_STATEMENT_STATUS_PAID,
    OPENING_STATEMENT_STATUS_PRIOR,
    OpeningStatementIngestionService,
    OpeningStatementRow,
    write_opening_statement,
)
from services.sqlite_accounting_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    SqliteAccountingStore,
)

CLIENT = "CARROSSERIE_YUCEL"
STATEMENT_DATE = "2026-10-06"
STATEMENT_FILENAME = "RELEVE_OUVERTURE_CARROSSERIE_YUCEL_20261006.xlsx"
PURGE_REMITTANCES = ("90949", "92174", "93696", "93847")

YUCEL_OPENING_STATEMENT: tuple[OpeningStatementRow, ...] = (
    # Prior exercises already settled in 2025.
    OpeningStatementRow("FA16222", "2024-10-31", Decimal("1614.80"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16247", "2024-11-30", Decimal("757.86"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16262", "2024-12-31", Decimal("1347.20"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16277", "2025-01-31", Decimal("1680.58"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16292", "2025-02-28", Decimal("301.76"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    OpeningStatementRow("FA16306", "2025-03-31", Decimal("747.25"), Decimal("0.00"), True, status=OPENING_STATEMENT_STATUS_PRIOR),
    # 2025 invoices settled by the 2026 OFX virements (100 % lettering).
    OpeningStatementRow("FA16325", "2025-04-30", Decimal("1071.32"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16339", "2025-05-31", Decimal("928.22"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16347", "2025-06-30", Decimal("1112.95"), Decimal("0.00"), True, "2026-01-23", "2026-01-23", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16354", "2025-07-31", Decimal("718.43"), Decimal("0.00"), True, "2026-03-05", "2026-03-05", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16367", "2025-08-31", Decimal("1410.02"), Decimal("0.00"), True, "2026-03-05", "2026-03-05", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16378", "2025-09-30", Decimal("1753.45"), Decimal("0.00"), True, "2026-07-03", "2026-07-03", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16393", "2025-10-31", Decimal("1662.65"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16423", "2026-01-31", Decimal("950.76"), Decimal("0.00"), True, "2026-07-03", "2026-07-03", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16444", "2026-02-28", Decimal("654.92"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    OpeningStatementRow("FA16459", "2026-03-31", Decimal("750.14"), Decimal("0.00"), True, "2026-10-06", "2026-10-06", OPENING_STATEMENT_STATUS_PAID),
    # Outstanding invoices at 06/10/2026.
    OpeningStatementRow("FA16406", "2025-11-30", Decimal("2013.32"), Decimal("2013.32"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16413", "2025-12-31", Decimal("2054.85"), Decimal("2054.85"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16471", "2026-04-30", Decimal("680.27"), Decimal("680.27"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16484", "2026-05-30", Decimal("883.56"), Decimal("883.56"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16495", "2026-06-30", Decimal("917.33"), Decimal("917.33"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16508", "2026-07-31", Decimal("672.25"), Decimal("672.25"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16528", "2026-09-30", Decimal("1005.08"), Decimal("1005.08"), False, status=OPENING_STATEMENT_STATUS_DUE),
    OpeningStatementRow("FA16529", "2026-09-30", Decimal("7.62"), Decimal("7.62"), False, status=OPENING_STATEMENT_STATUS_DUE),
)

EXPECTED_BALANCE = Decimal("8234.28")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="APD")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--output",
        default=str(
            REPO_ROOT
            / "workspace"
            / "document_flow"
            / "APD"
            / STATEMENT_FILENAME
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output)
    write_opening_statement(output, YUCEL_OPENING_STATEMENT)
    store = SqliteAccountingStore(args.db)
    service = OpeningStatementIngestionService(
        store, args.tenant, client=CLIENT
    )
    report = service.ingest(output, purge_remittances=PURGE_REMITTANCES)
    print(f"Relevé d'ouverture : {output}")
    print(
        f"Lignes={report.rows} créées={report.created} "
        f"mises_à_jour={report.updated} purgées={report.purged}"
    )
    print(f"Solde restant dû = {report.balance_due} EUR")
    if report.balance_due != EXPECTED_BALANCE:
        print(
            f"ERREUR : solde attendu {EXPECTED_BALANCE} EUR, "
            f"obtenu {report.balance_due} EUR"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
