"""Banking statement producer (OFX / CSV) implementing ``InvoiceProducer``.

Bank statements are absorbed as positive-amount transactions with an explicit
debit/credit direction. Each transaction is projected onto the universal
``InvoiceSchema`` contract (``produce``/``produce_all``) and can also be
persisted as a canonical ``BankingRecord`` (``produce_records``).

OFX files are parsed natively (SGML or XML), with an optional ``ofxparse``
fast path when the library is installed. CSV files are parsed with pandas.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from schemas.banking_schema import (
    BankAccountReference,
    BankAccountType,
    BankCounterparty,
    BankingRecord,
    BankTransactionDirection,
    BankTransactionType,
    CounterpartyType,
    PaymentMethod,
    ReconciliationStatus,
)
from schemas.invoice_schema import InvoiceSchema
from services.invoice_producer_protocol import (
    InvoiceProducer,
    InvoiceProducerError,
    validate_produced_invoice,
)

OFX_SUFFIXES = {".ofx", ".qfx"}
CSV_SUFFIXES = {".csv"}

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y%m%d",
    "%Y/%m/%d",
)
_TRANSACTION_TYPE_MAP = {
    "CARD": BankTransactionType.CARD,
    "POS": BankTransactionType.CARD,
    "CHECK": BankTransactionType.CHEQUE,
    "CHEQUE": BankTransactionType.CHEQUE,
    "FEE": BankTransactionType.FEE,
    "SRVCHG": BankTransactionType.FEE,
    "INT": BankTransactionType.INTEREST,
    "INTEREST": BankTransactionType.INTEREST,
    "DIRECTDEBIT": BankTransactionType.DIRECT_DEBIT,
    "DIRECT_DEBIT": BankTransactionType.DIRECT_DEBIT,
    "REFUND": BankTransactionType.REFUND,
    "XFER": BankTransactionType.TRANSFER,
    "TRANSFER": BankTransactionType.TRANSFER,
    "DEP": BankTransactionType.TRANSFER,
    "CREDIT": BankTransactionType.TRANSFER,
    "DEBIT": BankTransactionType.TRANSFER,
}
_PAYMENT_METHOD_MAP = {
    "CARD": PaymentMethod.CARD,
    "POS": PaymentMethod.CARD,
    "CHECK": PaymentMethod.CHEQUE,
    "CHEQUE": PaymentMethod.CHEQUE,
    "DIRECTDEBIT": PaymentMethod.DIRECT_DEBIT,
    "DIRECT_DEBIT": PaymentMethod.DIRECT_DEBIT,
    "XFER": PaymentMethod.TRANSFER,
    "TRANSFER": PaymentMethod.TRANSFER,
    "DEP": PaymentMethod.TRANSFER,
    "CREDIT": PaymentMethod.TRANSFER,
    "DEBIT": PaymentMethod.TRANSFER,
    "CASH": PaymentMethod.CASH,
}
_DEBIT_WORDS = {"DEBIT", "D", "OUT", "WITHDRAWAL", "SORTIE"}
_CREDIT_WORDS = {"CREDIT", "C", "IN", "DEPOSIT", "ENTREE"}


@dataclass(frozen=True)
class BankTransaction:
    """Normalized bank transaction, independent from the source format."""

    booking_date: date
    amount: Decimal
    currency: str
    direction: BankTransactionDirection
    label: str
    counterparty: str | None = None
    reference: str | None = None
    value_date: date | None = None
    transaction_type: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


def _decode_bank_bytes(raw: bytes) -> str:
    """Decode UTF-8 (BOM tolerated), falling back to Windows-1252 then Latin-1."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _normalise_column(value: str) -> str:
    cleaned = _strip_accents(value).strip().lower()
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    return cleaned.strip("_")


def _parse_decimal(value: Any) -> Decimal:
    text = _strip_accents(str(value)).strip()
    text = text.replace("\u00a0", "").replace(" ", "")
    text = text.replace("€", "").replace("EUR", "").strip()
    if not text:
        raise InvoiceProducerError("INVALID_BANK_TRANSACTION", "montant vide")
    negative = text.startswith("-")
    text = text[1:] if text[:1] in "+-" else text
    if text[:1] in "+-":
        raise InvoiceProducerError(
            "INVALID_BANK_TRANSACTION", f"montant illisible: {value!r}"
        )
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif "." in text:
        head, _, tail = text.rpartition(".")
        if len(tail) == 3 and head:
            text = text.replace(".", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise InvoiceProducerError(
            "INVALID_BANK_TRANSACTION", f"montant illisible: {value!r}"
        ) from exc
    if not amount.is_finite():
        raise InvoiceProducerError(
            "INVALID_BANK_TRANSACTION", f"montant illisible: {value!r}"
        )
    return -amount if negative else amount


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise InvoiceProducerError("INVALID_BANK_TRANSACTION", "date vide")
    candidate = text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    digits = re.match(r"(\d{8})", text)
    if digits:
        try:
            return datetime.strptime(digits.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    raise InvoiceProducerError(
        "INVALID_BANK_TRANSACTION", f"date illisible: {value!r}"
    )


def _normalise_currency(value: Any, default: str) -> str:
    text = _strip_accents(str(value or default)).strip().upper()
    if len(text) != 3 or not text.isalpha():
        raise InvoiceProducerError(
            "INVALID_BANK_TRANSACTION", f"devise invalide: {value!r}"
        )
    return text


def _direction_from_text(value: Any) -> BankTransactionDirection | None:
    text = _strip_accents(str(value or "")).strip().upper()
    if text in _DEBIT_WORDS:
        return BankTransactionDirection.DEBIT
    if text in _CREDIT_WORDS:
        return BankTransactionDirection.CREDIT
    return None


def _find_column(columns: Sequence[str], aliases: Sequence[str]) -> str | None:
    normalised = {_normalise_column(column): column for column in columns}
    for alias in aliases:
        found = normalised.get(alias)
        if found is not None:
            return found
    return None


def _row_value(row: Any, column: str | None) -> str:
    if column is None:
        return ""
    value = row.get(column, "")
    if value is None:
        return ""
    return str(value).strip()


class BankingInvoiceProducer:
    """Produce canonical invoices and banking records from OFX/CSV files."""

    producer_name = "banking_ofx_csv"

    def __init__(
        self,
        source: str | Path,
        *,
        company_id: str = "DSH_CORE",
        account_id: UUID | None = None,
        account_iban: str | None = None,
        currency: str = "EUR",
        statement_id: UUID | None = None,
        import_batch_id: UUID | None = None,
    ) -> None:
        self.source = Path(source)
        self.company_id = company_id
        self.account_id = account_id or uuid5(
            NAMESPACE_URL, f"{company_id}:bank-account"
        )
        self.account_iban = account_iban
        self.currency = currency
        self.statement_id = statement_id
        self.import_batch_id = import_batch_id or uuid5(
            NAMESPACE_URL, f"{company_id}:{self.source.name}:import-batch"
        )
        self._transactions: list[BankTransaction] | None = None

    def produce(self) -> InvoiceSchema:
        invoices = self.produce_all()
        if not invoices:
            raise InvoiceProducerError(
                "NO_BANK_TRANSACTIONS",
                "aucune transaction bancaire exploitable",
                source=self.source,
            )
        return invoices[0]

    def produce_all(self) -> list[InvoiceSchema]:
        transactions = self.parse_transactions()
        if not transactions:
            raise InvoiceProducerError(
                "NO_BANK_TRANSACTIONS",
                "aucune transaction bancaire exploitable",
                source=self.source,
            )
        return [
            self._to_invoice(transaction, index)
            for index, transaction in enumerate(transactions)
        ]

    def produce_records(self) -> list[BankingRecord]:
        transactions = self.parse_transactions()
        if not transactions:
            raise InvoiceProducerError(
                "NO_BANK_TRANSACTIONS",
                "aucune transaction bancaire exploitable",
                source=self.source,
            )
        now = datetime.now(timezone.utc)
        account = BankAccountReference(
            account_id=self.account_id,
            company_id=self.company_id,
            account_type=BankAccountType.CHECKING,
            iban=self.account_iban,
            currency=self.currency,
        )
        records: list[BankingRecord] = []
        for index, transaction in enumerate(transactions):
            transaction_id = self._transaction_uuid(transaction, index)
            counterparty_type = (
                CounterpartyType.CUSTOMER
                if transaction.direction is BankTransactionDirection.CREDIT
                else CounterpartyType.SUPPLIER
            )
            counterparty = (
                BankCounterparty(
                    display_name=transaction.counterparty,
                    counterparty_type=counterparty_type,
                )
                if transaction.counterparty
                else None
            )
            records.append(
                BankingRecord(
                    transaction_id=transaction_id,
                    account=account,
                    booking_date=transaction.booking_date,
                    value_date=transaction.value_date,
                    direction=transaction.direction,
                    transaction_type=self._transaction_type(transaction),
                    payment_method=self._payment_method(transaction),
                    amount=transaction.amount,
                    currency=transaction.currency,
                    raw_label=transaction.label,
                    normalized_label=transaction.label.upper(),
                    bank_reference=transaction.reference,
                    counterparty=counterparty,
                    reconciliation_status=ReconciliationStatus.UNMATCHED,
                    unallocated_amount=transaction.amount,
                    statement_id=self.statement_id,
                    source_file=self.source.name,
                    import_batch_id=self.import_batch_id,
                    row_version=1,
                    content_hash=self._content_hash(transaction, index),
                    created_at=now,
                    updated_at=now,
                    metadata={
                        "producer": self.producer_name,
                        "bank_transaction": dict(transaction.raw),
                    },
                )
            )
        return records

    def parse_transactions(self) -> list[BankTransaction]:
        if not self.source.is_file():
            raise InvoiceProducerError(
                "SOURCE_NOT_FOUND",
                "le fichier source est introuvable",
                source=self.source,
            )
        if self._transactions is None:
            suffix = self.source.suffix.lower()
            if suffix in OFX_SUFFIXES:
                self._transactions = self._parse_ofx()
            elif suffix in CSV_SUFFIXES:
                self._transactions = self._parse_csv()
            else:
                raise InvoiceProducerError(
                    "UNSUPPORTED_BANKING_FORMAT",
                    f"format bancaire non supporté: {suffix or '<inconnu>'}",
                    source=self.source,
                )
        return list(self._transactions)

    def _parse_ofx(self) -> list[BankTransaction]:
        try:
            return self._parse_ofx_with_library()
        except ImportError:
            return self._parse_ofx_builtin()

    def _parse_ofx_with_library(self) -> list[BankTransaction]:
        from ofxparse import OfxParser

        with self.source.open("rb") as handle:
            document = OfxParser.parse(handle)
        statement = document.account.statement
        currency = _normalise_currency(
            getattr(statement, "currency", None), self.currency
        )
        transactions: list[BankTransaction] = []
        for transaction in statement.transactions:
            amount = Decimal(str(transaction.amount))
            if amount == 0:
                continue
            payee = getattr(transaction, "payee", None)
            counterparty = getattr(payee, "name", None) or (
                str(payee) if payee else None
            )
            memo = getattr(transaction, "memo", None)
            transactions.append(
                BankTransaction(
                    booking_date=_parse_date(transaction.date),
                    amount=abs(amount),
                    currency=currency,
                    direction=(
                        BankTransactionDirection.CREDIT
                        if amount > 0
                        else BankTransactionDirection.DEBIT
                    ),
                    label=str(memo or counterparty or "TRANSACTION SANS LIBELLE"),
                    counterparty=counterparty,
                    reference=getattr(transaction, "id", None),
                    transaction_type=getattr(transaction, "type", None),
                    raw={"source": "ofxparse"},
                )
            )
        return transactions

    def _parse_ofx_builtin(self) -> list[BankTransaction]:
        text = _decode_bank_bytes(self.source.read_bytes())
        blocks = re.findall(
            r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|</STMTTRNRS>|</OFX>|\Z)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        default_currency = self._ofx_value(text, "CURDEF") or self.currency
        transactions: list[BankTransaction] = []
        for block in blocks:
            amount_raw = self._ofx_value(block, "TRNAMT")
            if amount_raw is None:
                continue
            amount = _parse_decimal(amount_raw)
            if amount == 0:
                continue
            name = self._ofx_value(block, "NAME") or self._ofx_value(block, "PAYEE")
            memo = self._ofx_value(block, "MEMO")
            transactions.append(
                BankTransaction(
                    booking_date=_parse_date(self._ofx_value(block, "DTPOSTED")),
                    amount=abs(amount),
                    currency=_normalise_currency(
                        self._ofx_value(block, "CURRENCY") or default_currency,
                        self.currency,
                    ),
                    direction=(
                        BankTransactionDirection.CREDIT
                        if amount > 0
                        else BankTransactionDirection.DEBIT
                    ),
                    label=memo or name or "TRANSACTION SANS LIBELLE",
                    counterparty=name,
                    reference=self._ofx_value(block, "FITID"),
                    transaction_type=self._ofx_value(block, "TRNTYPE"),
                    raw={
                        "TRNTYPE": self._ofx_value(block, "TRNTYPE"),
                        "DTPOSTED": self._ofx_value(block, "DTPOSTED"),
                        "TRNAMT": amount_raw,
                        "FITID": self._ofx_value(block, "FITID"),
                        "NAME": name,
                        "MEMO": memo,
                    },
                )
            )
        return transactions

    def _parse_csv(self) -> list[BankTransaction]:
        columns, rows = self._read_csv_rows()
        if not columns:
            return []
        date_column = _find_column(
            columns,
            ("date", "date_operation", "booking_date", "operation_date", "date_ops"),
        )
        value_date_column = _find_column(columns, ("value_date", "date_valeur"))
        amount_column = _find_column(
            columns, ("amount", "montant", "transaction_amount", "valeur")
        )
        debit_column = _find_column(
            columns, ("debit", "montant_debit", "debit_eur")
        )
        credit_column = _find_column(
            columns, ("credit", "montant_credit", "credit_eur")
        )
        label_column = _find_column(
            columns,
            ("label", "libelle", "libelle_operation", "description", "memo"),
        )
        counterparty_column = _find_column(
            columns,
            ("tiers", "counterparty", "payee", "beneficiaire", "beneficiary", "name"),
        )
        currency_column = _find_column(columns, ("currency", "devise"))
        reference_column = _find_column(
            columns, ("reference", "ref", "fitid", "reference_operation")
        )
        direction_column = _find_column(columns, ("direction", "sens"))
        type_column = _find_column(
            columns, ("type", "trntype", "transaction_type", "nature")
        )
        if date_column is None:
            raise InvoiceProducerError(
                "INVALID_BANK_TRANSACTION",
                "colonne date absente du CSV",
                source=self.source,
            )
        if amount_column is None and debit_column is None and credit_column is None:
            raise InvoiceProducerError(
                "INVALID_BANK_TRANSACTION",
                "aucune colonne de montant dans le CSV",
                source=self.source,
            )

        transactions: list[BankTransaction] = []
        for row in rows:
            if not any(str(value).strip() for value in row.values()):
                continue
            explicit_direction = _direction_from_text(
                _row_value(row, direction_column)
            )
            amount_value = _row_value(row, amount_column)
            if amount_column is not None and amount_value:
                amount = _parse_decimal(amount_value)
                direction = explicit_direction or (
                    BankTransactionDirection.DEBIT
                    if amount < 0
                    else BankTransactionDirection.CREDIT
                )
                amount = abs(amount)
            else:
                debit = _row_value(row, debit_column)
                credit = _row_value(row, credit_column)
                if debit:
                    amount = abs(_parse_decimal(debit))
                    direction = BankTransactionDirection.DEBIT
                elif credit:
                    amount = abs(_parse_decimal(credit))
                    direction = BankTransactionDirection.CREDIT
                else:
                    continue
            if amount == 0:
                continue
            counterparty = _row_value(row, counterparty_column) or None
            label = (
                _row_value(row, label_column)
                or counterparty
                or "TRANSACTION SANS LIBELLE"
            )
            transactions.append(
                BankTransaction(
                    booking_date=_parse_date(_row_value(row, date_column)),
                    value_date=(
                        _parse_date(_row_value(row, value_date_column))
                        if value_date_column
                        and _row_value(row, value_date_column)
                        else None
                    ),
                    amount=amount,
                    currency=_normalise_currency(
                        _row_value(row, currency_column), self.currency
                    ),
                    direction=direction,
                    label=label,
                    counterparty=counterparty,
                    reference=_row_value(row, reference_column) or None,
                    transaction_type=_row_value(row, type_column) or None,
                    raw={str(key): str(value) for key, value in row.items()},
                )
            )
        return transactions

    def _read_csv_rows(self) -> tuple[list[str], list[dict[str, str]]]:
        import csv
        from io import StringIO

        try:
            import pandas as pd

            read_csv = getattr(pd, "read_csv", None)
            if read_csv is None:
                raise AttributeError("pandas.read_csv unavailable")
            frame = read_csv(
                StringIO(_decode_bank_bytes(self.source.read_bytes())),
                sep=None,
                engine="python",
                dtype=str,
                keep_default_na=False,
            )
            columns = [str(column) for column in frame.columns]
            rows = [
                {str(key): "" if value is None else str(value) for key, value in row.items()}
                for _, row in frame.iterrows()
            ]
            return columns, rows
        except (ImportError, AttributeError, ValueError, OSError, csv.Error):
            return self._read_csv_with_stdlib()

    def _read_csv_with_stdlib(self) -> tuple[list[str], list[dict[str, str]]]:
        import csv
        from io import StringIO

        text = _decode_bank_bytes(self.source.read_bytes())
        if not text.strip():
            return [], []
        try:
            delimiter = csv.Sniffer().sniff(
                text[:4096], delimiters=",;\t|"
            ).delimiter
        except csv.Error:
            delimiter = ";" if text.count(";") > text.count(",") else ","
        reader = csv.DictReader(StringIO(text, newline=""), delimiter=delimiter)
        columns = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
        return columns, rows

    def _to_invoice(self, transaction: BankTransaction, index: int) -> InvoiceSchema:
        is_sale = transaction.direction is BankTransactionDirection.CREDIT
        party = {
            "name": transaction.counterparty
            or transaction.label
            or "TIERS_INCONNU"
        }
        payload: dict[str, Any] = {
            "document_type": "UNKNOWN",
            "direction": "SALE" if is_sale else "PURCHASE",
            "customer" if is_sale else "supplier": party,
            "reference": {
                "invoice_number": transaction.reference,
                "issue_date": transaction.booking_date.isoformat(),
                "external_reference": transaction.reference,
            },
            "amount": {
                "currency": transaction.currency,
                "net_amount": str(transaction.amount),
                "gross_amount": str(transaction.amount),
            },
            "validation_status": "NEEDS_REVIEW",
            "metadata": {
                "source_kind": "bank_transaction",
                "source_file": self.source.name,
                "producer": self.producer_name,
                "transaction_index": index,
                "bank_direction": transaction.direction.value,
                "bank_label": transaction.label,
                "bank_reference": transaction.reference,
                "bank_transaction_type": transaction.transaction_type,
                "statement_id": str(self.statement_id) if self.statement_id else None,
            },
        }
        return validate_produced_invoice(payload, source=self.source)

    def _transaction_uuid(self, transaction: BankTransaction, index: int) -> UUID:
        seed = (
            f"{self.company_id}:{self.source.name}:{index}:"
            f"{transaction.booking_date.isoformat()}:{transaction.amount}:"
            f"{transaction.currency}:{transaction.reference}:{transaction.label}"
        )
        return uuid5(NAMESPACE_URL, seed)

    def _content_hash(self, transaction: BankTransaction, index: int) -> str:
        canonical = (
            f"{self._transaction_uuid(transaction, index)}|"
            f"{transaction.booking_date.isoformat()}|{transaction.amount}|"
            f"{transaction.currency}|{transaction.direction.value}|"
            f"{transaction.label}|{transaction.reference}"
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _transaction_type(transaction: BankTransaction) -> BankTransactionType:
        raw_type = _strip_accents(transaction.transaction_type or "").upper()
        raw_type = re.sub(r"[\s\-]+", "_", raw_type).strip("_")
        return _TRANSACTION_TYPE_MAP.get(raw_type, BankTransactionType.UNKNOWN)

    @staticmethod
    def _payment_method(transaction: BankTransaction) -> PaymentMethod:
        raw_type = _strip_accents(transaction.transaction_type or "").upper()
        raw_type = re.sub(r"[\s\-]+", "_", raw_type).strip("_")
        return _PAYMENT_METHOD_MAP.get(raw_type, PaymentMethod.UNKNOWN)

    @staticmethod
    def _ofx_value(block: str, tag: str) -> str | None:
        match = re.search(rf"<{tag}>\s*([^<\r\n]+)", block, re.IGNORECASE)
        if match is None:
            return None
        value = unescape(match.group(1).strip())
        return value or None


__all__ = [
    "BankTransaction",
    "BankingInvoiceProducer",
    "InvoiceProducer",
]
