"""P24 - Single source of truth for the accounting dropdown choices.

Both the Excel ``_A_COMPLETER.xlsx`` data validations and the local web cockpit
must expose exactly the same lists. Any change here is reflected everywhere.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

CATEGORY_CHOICES: tuple[str, ...] = (
    "ACHATS_MARCHANDISES",
    "FRAIS_GENERAUX",
    "TRANSPORTS_LOGISTIQUE",
    "IMMOBILISATIONS",
    "VENTES_CLIENTS",
    "DIVERS_A_CLASSER",
)
PAYMENT_SOURCE_CHOICES: tuple[str, ...] = (
    "BANQUE_PRO",
    "CCA_455_DIRIGEANT",
    "COMPTE_PERSO",
    "LCR",
    "VIREMENT",
)
EXERCISE_CHOICES: tuple[str, ...] = ("2025", "2026", "2027")
DEFERRED_MONTH_CHOICES: tuple[str, ...] = (
    "NON",
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
)
REPORT_CHOICES: tuple[str, ...] = ("NON", "OUI")
STATUS_CHOICES: tuple[str, ...] = ("EN_ATTENTE", "VALIDE", "REJETE")
SUBCATEGORY_CHOICES: tuple[str, ...] = (
    "RESTAURATION",
    "CARBURANT",
    "TRANSPORT_PARKING",
    "HOTELLERIE",
    "INFORMATIQUE_WEB",
    "HONORAIRES_JURIDIQUES",
    "FRAIS_REPRESENTATION_PRESIDENT",
)
COUNTERPARTY_ACCOUNT_CHOICES: tuple[str, ...] = (
    "607000",
    "622600",
    "623800",
    "455000_DIRIGEANT",
)

PCG_BY_CATEGORY: dict[str, str] = {
    "ACHATS_MARCHANDISES": "607000",
    "FRAIS_GENERAUX": "622600",
    "TRANSPORTS_LOGISTIQUE": "622600",
    "IMMOBILISATIONS": "622600",
    "VENTES_CLIENTS": "411000",
    "DIVERS_A_CLASSER": "622600",
}

_LABELS: dict[str, str] = {
    "ACHATS MARCHANDISES": "ACHATS_MARCHANDISES",
    "ACHATS_MARCHANDISE": "ACHATS_MARCHANDISES",
    "FRAIS GENERAUX": "FRAIS_GENERAUX",
    "FRAIS GENERAL": "FRAIS_GENERAUX",
    "TRANSPORTS LOGISTIQUE": "TRANSPORTS_LOGISTIQUE",
    "TRANSPORT LOGISTIQUE": "TRANSPORTS_LOGISTIQUE",
    "IMMOBILISATIONS": "IMMOBILISATIONS",
    "IMMOBILISATION": "IMMOBILISATIONS",
    "VENTES CLIENTS": "VENTES_CLIENTS",
    "VENTE CLIENT": "VENTES_CLIENTS",
    "DIVERS A CLASSER": "DIVERS_A_CLASSER",
    "DIVERS": "DIVERS_A_CLASSER",
}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().upper()


def normalize_category(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    if text in CATEGORY_CHOICES:
        return text
    # Accept "Frais Généraux 2026", "FRAIS_GENERAUX - 2026", ...
    for label, code in _LABELS.items():
        if text == label or text.startswith(label + " ") or label in text:
            return code
    return None


def extract_year(value: Any) -> str | None:
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return match.group(1) if match else None


def normalize_payment_source(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    if text in {"BANQUE_PRO", "LCR", "VIREMENT", "BANQUE"}:
        return "BANQUE_PRO"
    if text in {
        "CCA_455_DIRIGEANT",
        "COMPTE_PERSO",
        "COMPTE_PERSO_DIRIGEANT",
        "455000_DIRIGEANT",
        "DIRIGEANT",
    }:
        return "COMPTE_PERSO_DIRIGEANT"
    return None


def normalize_exercise(value: Any) -> str | None:
    text = str(value or "").strip()
    if text in EXERCISE_CHOICES:
        return text
    year = extract_year(text)
    return year if year in EXERCISE_CHOICES else None


def normalize_month(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    if text == "NON":
        return "NON"
    if text.isdigit():
        text = text.zfill(2)
    return text if text in DEFERRED_MONTH_CHOICES else None


def normalize_report(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    if text in {"NON", "NO", "N"}:
        return "NON"
    if text in {"OUI", "YES", "O", "Y"}:
        return "OUI"
    return None


def normalize_status(value: Any) -> str | None:
    text = _fold(value)
    if not text:
        return None
    if "VALIDE" in text or text in {"VALIDER", "OK", "ACCEPTER"}:
        return "VALIDE"
    if "REJET" in text or text in {"REJETER", "KO", "REFUSER"}:
        return "REJETE"
    if "ATTENTE" in text or text in {"QUESTION", "ATTENDRE"}:
        return "EN_ATTENTE"
    return None


def excel_list(choices: Iterable[str]) -> str:
    """Return the openpyxl ``formula1`` inline-list syntax."""
    return '"' + ",".join(str(choice) for choice in choices) + '"'


def as_dict() -> dict[str, list[str]]:
    return {
        "categorie": list(CATEGORY_CHOICES),
        "sous_categorie": list(SUBCATEGORY_CHOICES),
        "source_paiement": list(PAYMENT_SOURCE_CHOICES),
        "compte_contrepartie": list(COUNTERPARTY_ACCOUNT_CHOICES),
        "exercice_cible": list(EXERCISE_CHOICES),
        "mois_decale": list(DEFERRED_MONTH_CHOICES),
        "report": list(REPORT_CHOICES),
        "statut": list(STATUS_CHOICES),
    }


__all__ = [
    "CATEGORY_CHOICES",
    "COUNTERPARTY_ACCOUNT_CHOICES",
    "DEFERRED_MONTH_CHOICES",
    "EXERCISE_CHOICES",
    "PAYMENT_SOURCE_CHOICES",
    "PCG_BY_CATEGORY",
    "REPORT_CHOICES",
    "STATUS_CHOICES",
    "SUBCATEGORY_CHOICES",
    "as_dict",
    "excel_list",
    "extract_year",
    "normalize_category",
    "normalize_exercise",
    "normalize_month",
    "normalize_payment_source",
    "normalize_report",
    "normalize_status",
]
