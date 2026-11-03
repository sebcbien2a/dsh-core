"""Hotel "Soirée Étape" parser and VAT splitter (CGI Art. 206).

A ``Soirée Étape`` package mixes two VAT regimes:

* the room / night ("nuitée", "hébergement") whose VAT is **not deductible**
  for the company;
* the dinner / breakfast / restaurant part whose VAT **is deductible**.

The parser is deterministic, offline and accent-insensitive. It accepts raw
text (native PDF text layer, OCR output, notes) or an already extracted row
mapping, and returns a :class:`HotelVatBreakdown` isolating both VAT amounts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

CGI_ARTICLE_206 = "CGI Art. 206"
HOTEL_EVENING_STAGE_MARKER = "SOIREE_ETAPE"
EXPENSE_SUBCATEGORY_HOTELLERIE = "HOTELLERIE"

_CENT = Decimal("0.01")
_AMOUNT_TOKEN = re.compile(
    r"(?<![\w])(?P<value>\d{1,3}(?:[ \u00a0\u202f.]\d{3})*(?:[,.]\d{1,2})?"
    r"|\d+(?:[,.]\d{1,2})?)(?![\w])"
)
_ACCOMMODATION = (
    "nuitee",
    "nuit ",
    "nuit,",
    "nuit.",
    "hebergement",
    "chambre",
    "room",
    "accommodation",
    "logement",
    "sleeping",
)
_MEALS = (
    "petit dejeuner",
    "petit-dejeuner",
    "petit dej",
    "petit-dej",
    "breakfast",
    "restauration",
    "restaurant",
    "repas",
    "diner",
    "dejeuner",
    "dinner",
    "meal",
    "food",
    "brasserie",
)
_TOTAL_LABELS = (
    "total",
    "net a payer",
    "montant total",
    "total ttc",
    "a payer",
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace("\u00a0", "").replace("\u202f", "").strip()
    if not text:
        return None
    text = text.replace(" ", "").replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class HotelVatBreakdown:
    """Isolated hotel / restaurant VAT amounts of one ``Soirée Étape`` bill."""

    evening_stage: bool
    ht_accommodation: Decimal
    ht_meals: Decimal
    tva_deductible: Decimal
    tva_non_deductible: Decimal
    ttc: Decimal
    hotel_place: str = ""
    source_text: str = ""

    @property
    def total_ht(self) -> Decimal:
        return (self.ht_accommodation + self.ht_meals).quantize(_CENT)

    @property
    def total_tva(self) -> Decimal:
        return (self.tva_deductible + self.tva_non_deductible).quantize(_CENT)

    def as_metadata(self) -> dict[str, Any]:
        """Return the P17 metadata written in producer payloads and stores."""
        return {
            "expense_subcategory": EXPENSE_SUBCATEGORY_HOTELLERIE,
            "hotel_evening_stage": self.evening_stage,
            "hotel_place": self.hotel_place,
            "ht_accommodation": str(self.ht_accommodation),
            "ht_breakfast_meals": str(self.ht_meals),
            "tva_deductible": str(self.tva_deductible),
            "tva_non_deductible": str(self.tva_non_deductible),
            "cgi_article": CGI_ARTICLE_206,
        }


@dataclass(frozen=True)
class _Number:
    value: Decimal
    position: int
    end: int
    percent: bool


def _numbers(line: str) -> list[_Number]:
    found: list[_Number] = []
    for match in _AMOUNT_TOKEN.finditer(line):
        value = _decimal(match.group("value"))
        if value is None:
            continue
        tail = line[match.end() : match.end() + 4].lstrip()
        found.append(
            _Number(
                value=value,
                position=match.start(),
                end=match.end(),
                percent=tail.startswith("%"),
            )
        )
    return found


def _amounts(numbers: list[_Number]) -> list[Decimal]:
    return [item.value for item in numbers if not item.percent]


def _rate(numbers: list[_Number]) -> Decimal | None:
    rates = [item.value for item in numbers if item.percent]
    if rates:
        return rates[-1]
    return None


def _value_after_keyword(
    line: str, numbers: list[_Number], keywords: tuple[str, ...]
) -> Decimal | None:
    folded = _fold(line)
    for keyword in keywords:
        index = folded.find(keyword)
        if index < 0:
            continue
        for number in numbers:
            if number.position >= index and not number.percent:
                return number.value
    return None


def _explicit_vat_amount(
    line: str, numbers: list[_Number], rate: Decimal | None, folded: str
) -> Decimal | None:
    index = folded.find("tva")
    if index < 0:
        return None
    candidates = [
        number
        for number in numbers
        if number.position > index and not number.percent
    ]
    if not candidates:
        return None
    # "TVA 10% sur 100,00 = 10,00": the amount after '=' / ':' wins.
    for match in re.finditer(r"[=:]", line[index:]):
        position = index + match.end()
        after = [number for number in candidates if number.position >= position]
        if after:
            return after[0].value
    if rate is not None:
        for first, second in zip(candidates, candidates[1:]):
            if abs(first.value * rate / Decimal("100") - second.value) <= _CENT:
                return second.value
    tail = folded[index:]
    if "base" in tail or "sur" in tail:
        return candidates[-1].value if len(candidates) > 1 else None
    return candidates[0].value


def _line_category(line: str) -> str | None:
    folded = _fold(line)
    if any(label in folded for label in _TOTAL_LABELS):
        return None
    if any(keyword in folded for keyword in _ACCOMMODATION):
        return "accommodation"
    if any(keyword in folded for keyword in _MEALS):
        return "meals"
    return None


def _line_breakdown(line: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return ``(ht, vat, ttc)`` for one labelled line when possible."""
    numbers = _numbers(line)
    amounts = _amounts(numbers)
    if not amounts:
        return None
    folded = _fold(line)
    rate = _rate(numbers)
    explicit_vat = _explicit_vat_amount(line, numbers, rate, folded)
    explicit_ht = _value_after_keyword(
        line, numbers, ("ht", "h.t", "hors taxe", "base")
    )
    explicit_ttc = _value_after_keyword(
        line, numbers, ("ttc", "toutes taxes", "total")
    )

    vat = explicit_vat
    ht = explicit_ht
    ttc = explicit_ttc
    if ttc is None and len(amounts) >= 3:
        ttc = amounts[2]
    if ttc is None and len(amounts) >= 2 and vat is None:
        first, second = amounts[0], amounts[1]
        if rate is not None and abs(first * rate / Decimal("100") - second) <= _CENT:
            ht, vat = first, second
        elif len(amounts) >= 3 and abs(first + second - amounts[2]) <= _CENT:
            ht, vat = first, second
        elif rate is not None:
            ttc = amounts[-1]
        elif second < first:
            ht, vat = first, second
        else:
            ht = first
            vat = (second - first).quantize(_CENT)
    if vat is None and rate is not None:
        if ttc is not None:
            ht = (ttc / (Decimal("1") + rate / Decimal("100"))).quantize(_CENT)
            vat = (ttc - ht).quantize(_CENT)
        else:
            base = ht if ht is not None else amounts[0]
            vat = (base * rate / Decimal("100")).quantize(_CENT)
            ht = base
    if ht is None:
        if ttc is not None and vat is not None:
            ht = (ttc - vat).quantize(_CENT)
        elif vat is not None and len(amounts) >= 2:
            ht = amounts[0]
        elif len(amounts) >= 2 and vat is not None:
            ht = amounts[0]
        else:
            ht = amounts[0]
    if vat is None:
        vat = Decimal("0.00")
    if ttc is None:
        ttc = (ht + vat).quantize(_CENT)
    return (
        ht.quantize(_CENT),
        vat.quantize(_CENT),
        ttc.quantize(_CENT),
    )


def _detect_marker(text: str) -> bool:
    folded = _fold(text)
    return "soiree etape" in folded or "soiree-etape" in folded


def _detect_multiple_rates(text: str) -> bool:
    rates = {
        amount
        for line in str(text).splitlines()
        for amount in (
            match.group(1).replace(",", ".")
            for match in re.finditer(
                r"tva\s*(?:a|:)?\s*(\d{1,2}(?:[.,]\d+)?)\s*%",
                _fold(line),
            )
        )
    }
    return len(rates) >= 2


def _detect_categories(text: str) -> bool:
    folded = _fold(text)
    has_accommodation = any(keyword in folded for keyword in _ACCOMMODATION)
    has_meals = any(keyword in folded for keyword in _MEALS)
    return has_accommodation and has_meals


def _extract_place(text: str) -> str:
    for line in str(text).splitlines():
        candidate = line.strip()
        folded = _fold(candidate)
        if not candidate or "tva" in folded or _numbers(candidate):
            continue
        if any(keyword in folded for keyword in _ACCOMMODATION):
            return candidate[:120]
    return ""


def _explicit_breakdown(
    value: Mapping[str, Any], source_text: str
) -> HotelVatBreakdown | None:
    deductible = _decimal(value.get("tva_deductible"))
    non_deductible = _decimal(value.get("tva_non_deductible"))
    if deductible is None and non_deductible is None:
        return None
    total_tva = _decimal(
        value.get("amount_tva")
        or value.get("tva")
        or value.get("vat_amount")
    )
    if deductible is None:
        deductible = max(
            Decimal("0.00"),
            (total_tva or Decimal("0.00")) - (non_deductible or Decimal("0.00")),
        )
    if non_deductible is None:
        non_deductible = max(
            Decimal("0.00"),
            (total_tva or Decimal("0.00")) - deductible,
        )
    accommodation = _decimal(value.get("ht_accommodation"))
    meals = _decimal(value.get("ht_breakfast_meals"))
    total_ht = _decimal(value.get("amount_ht") or value.get("ht"))
    if accommodation is None and meals is None:
        accommodation = total_ht
    accommodation = accommodation or Decimal("0.00")
    meals = meals or Decimal("0.00")
    ttc = _decimal(value.get("amount_ttc") or value.get("ttc"))
    if ttc is None:
        ttc = (accommodation + meals + deductible + non_deductible).quantize(_CENT)
    return HotelVatBreakdown(
        evening_stage=_detect_marker(source_text),
        ht_accommodation=accommodation,
        ht_meals=meals,
        tva_deductible=deductible.quantize(_CENT),
        tva_non_deductible=non_deductible.quantize(_CENT),
        ttc=ttc.quantize(_CENT),
        hotel_place=str(
            value.get("hotel_place") or value.get("counterparty") or ""
        ).strip(),
        source_text=source_text,
    )


def _mapping_text(value: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "raw_text",
        "text",
        "ocr_text",
        "description",
        "notes",
        "label",
        "hotel_place",
        "counterparty",
        "supplier",
        "document_type",
    ):
        item = value.get(key)
        if item is not None and str(item).strip():
            parts.append(str(item))
    for container_key in ("metadata", "invoice", "data"):
        container = value.get(container_key)
        if isinstance(container, Mapping):
            parts.append(_mapping_text(container))
    return "\n".join(parts)


def parse_hotel_evening_stage(
    value: str | Mapping[str, Any] | None,
) -> HotelVatBreakdown | None:
    """Parse a text / row and return its isolated hotel VAT breakdown.

    Returns ``None`` when the document is not a hotel ``Soirée Étape`` nor a
    bill exposing both accommodation and restaurant VAT components.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        text = _mapping_text(value)
        explicit = _explicit_breakdown(value, text)
        if explicit is not None:
            return explicit
    else:
        text = str(value)
    if not text.strip():
        return None

    marker = _detect_marker(text)
    multiple_rates = _detect_multiple_rates(text)
    categories = _detect_categories(text)
    if not (marker or multiple_rates or categories):
        return None

    accommodation_ht = Decimal("0.00")
    meals_ht = Decimal("0.00")
    deductible = Decimal("0.00")
    non_deductible = Decimal("0.00")
    ttc = Decimal("0.00")
    pending: str | None = None
    found_component = False

    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        category = _line_category(line)
        vat_only_line = False
        if category is None and pending is not None and "tva" in _fold(line):
            category = pending
            vat_only_line = True
            pending = None
        if category is None:
            if any(keyword in _fold(line) for keyword in _ACCOMMODATION + _MEALS):
                pending = "accommodation" if any(
                    keyword in _fold(line) for keyword in _ACCOMMODATION
                ) else "meals"
            continue
        numbers = _numbers(line)
        if not numbers:
            pending = category
            continue
        if vat_only_line:
            vat = _value_after_keyword(line, numbers, ("tva",))
            if vat is None:
                amounts = _amounts(numbers)
                vat = amounts[-1] if amounts else None
            if vat is None:
                pending = category
                continue
            breakdown = (Decimal("0.00"), vat, vat)
        else:
            breakdown = _line_breakdown(line)
        pending = None
        if breakdown is None:
            continue
        ht, vat, line_ttc = breakdown
        found_component = True
        ttc += line_ttc
        if category == "accommodation":
            accommodation_ht += ht
            non_deductible += vat
        else:
            meals_ht += ht
            deductible += vat

    if not found_component:
        # The mention exists but no labelled component was found: conservatively
        # keep every extracted VAT amount non-deductible (CGI Art. 206).
        amounts = _numbers(str(text).replace("\n", " "))
        total_vat = _value_after_keyword(
            str(text).replace("\n", " "), amounts, ("tva",)
        )
        if total_vat is not None:
            non_deductible = total_vat
            ttc = total_vat
        if not marker:
            return None

    return HotelVatBreakdown(
        evening_stage=marker,
        ht_accommodation=accommodation_ht.quantize(_CENT),
        ht_meals=meals_ht.quantize(_CENT),
        tva_deductible=deductible.quantize(_CENT),
        tva_non_deductible=non_deductible.quantize(_CENT),
        ttc=ttc.quantize(_CENT),
        hotel_place=_extract_place(text),
        source_text=text,
    )


def detect_hotel_evening_stage(value: str | Mapping[str, Any] | None) -> bool:
    """Return ``True`` when the mention "Soirée Étape" is present."""
    if value is None:
        return False
    text = _mapping_text(value) if isinstance(value, Mapping) else str(value)
    return _detect_marker(text)


def split_hotel_vat(
    value: str | Mapping[str, Any] | None,
) -> tuple[Decimal, Decimal]:
    """Return ``(deductible, non_deductible)`` VAT amounts (zero when absent)."""
    breakdown = parse_hotel_evening_stage(value)
    if breakdown is None:
        return Decimal("0.00"), Decimal("0.00")
    return breakdown.tva_deductible, breakdown.tva_non_deductible


parse_soiree_etape = parse_hotel_evening_stage
detect_soiree_etape = detect_hotel_evening_stage
split_soiree_etape_vat = split_hotel_vat


def hotel_vat_breakdown(
    value: str | Mapping[str, Any] | None,
) -> dict[str, Decimal] | None:
    """Return the breakdown as a plain decimal mapping for callers/tests."""
    breakdown = parse_hotel_evening_stage(value)
    if breakdown is None:
        return None
    return {
        "ht_accommodation": breakdown.ht_accommodation,
        "ht_meals": breakdown.ht_meals,
        "tva_deductible": breakdown.tva_deductible,
        "tva_non_deductible": breakdown.tva_non_deductible,
        "ttc": breakdown.ttc,
    }


__all__ = [
    "CGI_ARTICLE_206",
    "EXPENSE_SUBCATEGORY_HOTELLERIE",
    "HOTEL_EVENING_STAGE_MARKER",
    "HotelVatBreakdown",
    "detect_hotel_evening_stage",
    "detect_soiree_etape",
    "hotel_vat_breakdown",
    "parse_hotel_evening_stage",
    "parse_soiree_etape",
    "split_hotel_vat",
    "split_soiree_etape_vat",
]
