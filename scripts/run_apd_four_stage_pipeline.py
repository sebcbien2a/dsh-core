"""P22 - CLI runner for the APD four-stage accounting pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.apd_four_stage_pipeline import (
    DEFAULT_TENANT,
    DEFAULT_YEAR,
    STAGE_NAMES,
    ApdFourStagePipeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--root", default=None)
    parser.add_argument("--db", default=None)
    parser.add_argument("--export-dir", default=None)
    parser.add_argument("--year", type=int, default=DEFAULT_YEAR)
    parser.add_argument("--stage", type=int, action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--pause", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected = args.stage or ([1, 2, 3, 4] if args.all or not args.stage else [])
    if not selected:
        selected = [1, 2, 3, 4]
    pipeline = ApdFourStagePipeline(
        args.tenant,
        root=args.root,
        db_path=args.db,
        export_dir=args.export_dir,
        year=args.year,
    )
    reports = pipeline.run_all(
        stages=selected,
        pause=(
            (lambda _stage: __import__("time").sleep(args.pause))
            if args.pause > 0
            else None
        ),
    )
    for report in reports:
        print(f"Étape {report.stage} - {STAGE_NAMES[report.stage]} : {report.status}")
        print(
            f"  traitées={report.processed} validées={report.validated} "
            f"en_attente={report.pending} déplacées={report.moved} "
            f"ofx={report.imported_ofx} rapprochées={report.reconciled}"
        )
        for message in report.messages:
            print(f"  - {message}")
        for path in report.workbooks:
            print(f"  classeur: {path}")
    print(f"Tableau de bord : {pipeline.readable_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
