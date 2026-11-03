"""P19 - Payment method contracts shared by OFX parsing and payment rules.

The bank parser classifies a statement line into a precise :class:`BankPaymentMethod`
(``LCR_ENCAISSEMENT_LOT``, ``CARTE_BANCAIRE`` ...), while a counterparty rule
stores the coarser habit of a customer or supplier (:class:`ExpectedPaymentMethod`
``LCR``, ``VIREMENT``, ``PRELEVEMENT``, ``CB``, ``CHEQUE``). The helpers below
normalise both vocabularies and decide whether an observed line matches the
expected habit of its counterparty.
"""

from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Any


class PaymentMethodError(ValueError):
    """Invalid payment method value; ``code`` is stable for callers and logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class ExpectedPaymentMethod(str, Enum):
    """Usual settlement method recorded for a customer or supplier."""

    LCR = "LCR"
    VIREMENT = "VIREMENT"
    PRELEVEMENT = "PRELEVEMENT"
    CB = "CB"
    CHEQUE = "CHEQUE"


class BankPaymentMethod(str, Enum):
    """Precise channel inferred from a Caisse d'Epargne OFX line."""

    LCR_ENCAISSEMENT_LOT = "LCR_ENCAISSEMENT_LOT"
    LCR_FOURNISSEUR_DEBIT = "LCR_FOURNISSEUR_DEBIT"
    REMISE_CHEQUES = "REMISE_CHEQUES"
    PRELEVEMENT_SEPA = "PRELEVEMENT_SEPA"
    VIREMENT = "VIREMENT"
    CARTE_BANCAIRE = "CARTE_BANCAIRE"
    CHEQUE_EMIS = "CHEQUE_EMIS"
    FRAIS_BANCAIRES = "FRAIS_BANCAIRES"
    AUTRE = "AUTRE"


EXPECTED_PAYMENT_METHODS: tuple[str, ...] = tuple(
    method.value for method in ExpectedPaymentMethod
)
BANK_PAYMENT_METHODS: tuple[str, ...] = tuple(
    method.value for method in BankPaymentMethod
)

_EXPECTED_ALIASES: dict[str, ExpectedPaymentMethod] = {
    "LCR": ExpectedPaymentMethod.LCR,
    "LCR ENCAISSEMENT": ExpectedPaymentMethod.LCR,
    "LCR ENCAISSEMENT LOT": ExpectedPaymentMethod.LCR,
    "LCR FOURNISSEUR": ExpectedPaymentMethod.LCR,
    "LCR FOURNISSEUR DEBIT": ExpectedPaymentMethod.LCR,
    "VIREMENT": ExpectedPaymentMethod.VIREMENT,
    "VIR": ExpectedPaymentMethod.VIREMENT,
    "VIR SEPA": ExpectedPaymentMethod.VIREMENT,
    "VIR INST": ExpectedPaymentMethod.VIREMENT,
    "PRELEVEMENT": ExpectedPaymentMethod.PRELEVEMENT,
    "PRELEVEMENT SEPA": ExpectedPaymentMethod.PRELEVEMENT,
    "PRLV": ExpectedPaymentMethod.PRELEVEMENT,
    "PRLV SEPA": ExpectedPaymentMethod.PRELEVEMENT,
    "CB": ExpectedPaymentMethod.CB,
    "CARTE": ExpectedPaymentMethod.CB,
    "CARTE BANCAIRE": ExpectedPaymentMethod.CB,
    "CARTE BANCAIRE CB": ExpectedPaymentMethod.CB,
    "CHEQUE": ExpectedPaymentMethod.CHEQUE,
    "CHQ": ExpectedPaymentMethod.CHEQUE,
    "CHEQUE EMIS": ExpectedPaymentMethod.CHEQUE,
    "REMISE CHEQUES": ExpectedPaymentMethod.CHEQUE,
}

_FAMILY_BY_BANK_METHOD: dict[str, ExpectedPaymentMethod] = {
    BankPaymentMethod.LCR_ENCAISSEMENT_LOT.value: ExpectedPaymentMethod.LCR,
    BankPaymentMethod.LCR_FOURNISSEUR_DEBIT.value: ExpectedPaymentMethod.LCR,
    BankPaymentMethod.REMISE_CHEQUES.value: ExpectedPaymentMethod.CHEQUE,
    BankPaymentMethod.PRELEVEMENT_SEPA.value: ExpectedPaymentMethod.PRELEVEMENT,
    BankPaymentMethod.VIREMENT.value: ExpectedPaymentMethod.VIREMENT,
    BankPaymentMethod.CARTE_BANCAIRE.value: ExpectedPaymentMethod.CB,
    BankPaymentMethod.CHEQUE_EMIS.value: ExpectedPaymentMethod.CHEQUE,
}

_SPACES = re.compile(r"\s+")


def fold_payment_label(value: Any) -> str:
    """Upper-case ASCII label used for payment-method comparisons."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _SPACES.sub(" ", text).strip().upper()


def normalize_counterparty_name(value: Any) -> str:
    """Stable case-insensitive key for a customer or supplier name."""
    return _SPACES.sub(" ", fold_payment_label(value).casefold()).strip()


def normalize_expected_payment_method(
    value: ExpectedPaymentMethod | str | None,
) -> str:
    """Return one of :data:`EXPECTED_PAYMENT_METHODS` or raise."""
    if value is None:
        raise PaymentMethodError("INVALID_PAYMENT_METHOD", "none")
    if isinstance(value, ExpectedPaymentMethod):
        return value.value
    text = fold_payment_label(value)
    if not text:
        raise PaymentMethodError("INVALID_PAYMENT_METHOD", "empty")
    direct = _EXPECTED_ALIASES.get(text)
    if direct is not None:
        return direct.value
    try:
        return ExpectedPaymentMethod(text).value
    except ValueError as exc:
        family = _FAMILY_BY_BANK_METHOD.get(text)
        if family is not None:
            return family.value
        raise PaymentMethodError("INVALID_PAYMENT_METHOD", text) from exc


def normalize_bank_payment_method(value: BankPaymentMethod | str | None) -> str:
    """Return a canonical :class:`BankPaymentMethod` value (``AUTRE`` if unknown)."""
    if value is None:
        return BankPaymentMethod.AUTRE.value
    if isinstance(value, BankPaymentMethod):
        return value.value
    text = fold_payment_label(value)
    if not text:
        return BankPaymentMethod.AUTRE.value
    try:
        return BankPaymentMethod(text).value
    except ValueError:
        return BankPaymentMethod.AUTRE.value


def expected_family_for_bank_method(
    value: BankPaymentMethod | str | None,
) -> str | None:
    """Return the coarse expected method family of an observed bank channel."""
    canonical = normalize_bank_payment_method(value)
    family = _FAMILY_BY_BANK_METHOD.get(canonical)
    return family.value if family is not None else None


def payment_method_matches(
    expected: ExpectedPaymentMethod | str | None,
    actual: BankPaymentMethod | str | None,
) -> bool:
    """True when the observed channel realises the counterparty's habit."""
    if expected is None or actual is None:
        return False
    try:
        expected_value = normalize_expected_payment_method(expected)
    except PaymentMethodError:
        return False
    if fold_payment_label(actual) == expected_value:
        return True
    family = expected_family_for_bank_method(actual)
    return family is not None and family == expected_value


__all__ = [
    "BANK_PAYMENT_METHODS",
    "BankPaymentMethod",
    "EXPECTED_PAYMENT_METHODS",
    "ExpectedPaymentMethod",
    "PaymentMethodError",
    "expected_family_for_bank_method",
    "fold_payment_label",
    "normalize_bank_payment_method",
    "normalize_counterparty_name",
    "normalize_expected_payment_method",
    "payment_method_matches",
]
