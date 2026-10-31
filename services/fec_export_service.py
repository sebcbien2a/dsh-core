"""Generate a French FEC export from normalized accounting entries."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

_CENT = Decimal("0.01")


class FecEntry(BaseModel):
    """One normalized line in the official 18-column FEC format."""

    model_config = ConfigDict(extra="forbid")

    COLUMNS: ClassVar[tuple[str, ...]] = (
        "JournalCode",
        "JournalLib",
        "EcritureNum",
        "EcritureDate",
        "CompteNum",
        "CompteLib",
        "CompAuxNum",
        "CompAuxLib",
        "PieceRef",
        "PieceDate",
        "EcritureLib",
        "Debit",
        "Credit",
        "EcritureLet",
        "DateLet",
        "ValidDate",
        "Montantdevise",
        "Idevise",
    )

    JournalCode: str
    JournalLib: str
    EcritureNum: str
    EcritureDate: date
    CompteNum: str
    CompteLib: str
    CompAuxNum: str = ""
    CompAuxLib: str = ""
    PieceRef: str
    PieceDate: date
    EcritureLib: str
    Debit: Decimal = Decimal("0.00")
    Credit: Decimal = Decimal("0.00")
    EcritureLet: str = ""
    DateLet: date | None = None
    ValidDate: date | None = None
    Montantdevise: Decimal | None = None
    Idevise: str = ""

    @field_validator("EcritureDate", "PieceDate", "DateLet", "ValidDate", mode="before")
    @classmethod
    def _parse_fec_date(cls, value: date | str | None) -> date | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                try:
                    return datetime.strptime(value, "%Y%m%d").date()
                except ValueError as exc:
                    raise ValueError("date must use YYYY-MM-DD or YYYYMMDD") from exc
        return value

    @field_validator("Debit", "Credit", "Montantdevise")
    @classmethod
    def _finite_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("amount must be finite")
        return value


class UnbalancedAccountingEntryError(ValueError):
    """Raised when an accounting writing does not balance to the cent."""

    def __init__(self, writing_number: str, debit: Decimal, credit: Decimal) -> None:
        self.writing_number = writing_number
        self.debit = debit
        self.credit = credit
        super().__init__(
            f"Writing {writing_number!r} is unbalanced: "
            f"debit={debit:.2f}, credit={credit:.2f}"
        )


class FecGenerator:
    """Validate balance and render entries in regulatory column order."""

    @staticmethod
    def generate_fec(entries: list[FecEntry], delimiter: str = "\t") -> str:
        if len(delimiter) != 1 or delimiter in "\r\n":
            raise ValueError("delimiter must be one non-newline character")

        totals: dict[str, list[Decimal]] = defaultdict(
            lambda: [Decimal("0.00"), Decimal("0.00")]
        )
        for entry in entries:
            totals[entry.EcritureNum][0] += entry.Debit.quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            totals[entry.EcritureNum][1] += entry.Credit.quantize(
                _CENT, rounding=ROUND_HALF_UP
            )

        for writing_number, (debit, credit) in totals.items():
            if debit != credit:
                raise UnbalancedAccountingEntryError(
                    writing_number, debit, credit
                )

        output = StringIO(newline="")
        writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
        writer.writerow(FecEntry.COLUMNS)
        for entry in entries:
            writer.writerow(
                [
                    getattr(entry, column).strftime("%Y%m%d")
                    if column in {"EcritureDate", "PieceDate", "DateLet", "ValidDate"}
                    and getattr(entry, column) is not None
                    else (
                        f"{getattr(entry, column):.2f}"
                        if column in {"Debit", "Credit", "Montantdevise"}
                        and getattr(entry, column) is not None
                        else getattr(entry, column) or ""
                    )
                    for column in FecEntry.COLUMNS
                ]
            )
        return output.getvalue()
