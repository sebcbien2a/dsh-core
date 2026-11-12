"""P27-decies - contextual decision menus for the cockpit and ``_A_COMPLETER``.

The director must never see an irrelevant "Créer nouveau fournisseur /
Restaurant / Péage" answer on a line whose tier already exists in
``REFERENTIEL_TIERS_APD.xlsx`` or on an overdue-invoice slate row
(``ARDOISE_FA1xxxxx.tier``). Conversely, an unknown bank operation
(``TIER__*.tier`` / ``INSTRUMENT__*.tier``) gets one single decision selector
that groups:

* automatic general-expense answers (``GROUPE:RESTAURANTS_REPAS`` ...);
* internal treasury flows;
* every known client and supplier of the locked reference;
* the two tier-creation answers.

The same helper feeds the web selector, the Excel data validation and the
decision resolver in :class:`services.hermes_accounting_bridge`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from schemas.accounting_choices import (
    CATEGORY_ACCOUNTS,
    DECISION_CREATION_CHOICES,
    DECISION_GROUPS,
    DECISION_INTERNAL_CHOICES,
    DECISION_SIMPLE_CHOICES,
    normalize_category,
    normalize_decision,
)

SIMPLE_PROFILE = "TIERS_CONNU"
ARDOISE_PROFILE = "ARDOISE"
BANK_UNKNOWN_PROFILE = "BANQUE_INCONNUE"
STANDARD_PROFILE = "STANDARD"
DECISION_GROUP_PREFIX = "GROUPE:"

# Group answer -> (generic supplier, category, sub-category, accounting account)
DECISION_GROUP_TARGETS: dict[str, tuple[str, str, str, str]] = {
    "GROUPE:RESTAURANTS_REPAS": (
        "RESTAURANTS_REPAS",
        "FRAIS_GENERAUX",
        "RESTAURATION",
        "622600",
    ),
    "GROUPE:PEAGES_AUTOROUTES": (
        "PEAGES_AUTOROUTES",
        "FRAIS_GENERAUX",
        "TRANSPORT_PARKING",
        "622600",
    ),
    "GROUPE:STATIONS_CARBURANT": (
        "STATIONS_CARBURANT",
        "FRAIS_GENERAUX",
        "CARBURANT",
        "622600",
    ),
    "GROUPE:PARKINGS_TRANSPORTS": (
        "PARKINGS_TRANSPORTS",
        "FRAIS_GENERAUX",
        "TRANSPORT_PARKING",
        "622600",
    ),
    "GROUPE:ASSURANCES_SOCIETE": (
        "CAISSE_D_EPARGNE_ASSURANCE",
        "FRAIS_GENERAUX",
        "ASSURANCES_SOCIETE",
        "616000",
    ),
}
# Internal-flow decision -> (counterparty, category, sub-category, account)
INTERNAL_DECISION_TARGETS: dict[str, tuple[str, str, str, str]] = {
    "PAIEMENT_INTERNE_SOCIETE": (
        "APD_INTERNE",
        "PAIEMENT_INTERNE_SOCIETE",
        "",
        "580000",
    ),
    "PAIEMENT_INTERNE_PERSO_REMBOURSEMENT": (
        "APD_INTERNE",
        "PAIEMENT_INTERNE_PERSO_REMBOURSEMENT",
        "",
        "455100",
    ),
}
# The collection pseudo-tier is never a real supplier/client answer.
NON_SELECTABLE_TIERS: frozenset[str] = frozenset(
    {"APD_INTERNE", "CLIENTS_ENCAISSEMENT_GROUPE"}
)


@dataclass(frozen=True)
class DecisionMenu:
    """One contextual decision dropdown."""

    profile: str
    choices: tuple[str, ...]
    groups: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def default(self) -> str:
        return "VALIDE"


@dataclass(frozen=True)
class TierChoice:
    """Resolved answer for an unknown bank tier."""

    canonical: str
    role: str
    category: str
    subcategory: str
    account: str
    learn_alias: bool = True


def _fold(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def is_ardoise_filename(value: Any) -> bool:
    name = str(value or "").strip().upper()
    return name.startswith("ARDOISE_") and name.endswith(".TIER")


def is_unknown_bank_filename(value: Any) -> bool:
    name = str(value or "").strip().upper()
    return name.startswith(("TIER__", "INSTRUMENT__"))


def resolve_tier_reference(tier_reference: Any = None) -> Any:
    if tier_reference is not None:
        return tier_reference
    try:
        from services.tier_reference_service import TierReferenceService

        service = TierReferenceService()
        service.bootstrap(write_missing=False)
        return service
    except Exception:  # noqa: BLE001 - the reference is optional
        return None


def known_tier_names(tier_reference: Any = None) -> tuple[str, ...]:
    """Return every selectable client / supplier of the locked reference."""
    reference = resolve_tier_reference(tier_reference)
    if reference is None:
        return ()
    try:
        records = reference.records()
    except Exception:  # noqa: BLE001
        return ()
    return tuple(
        sorted(
            record.canonical
            for record in records
            if record.role in {"CLIENT", "FOURNISSEUR", "PRESTATAIRE"}
            and record.canonical not in NON_SELECTABLE_TIERS
        )
    )


def known_tier_record(value: Any, tier_reference: Any = None) -> Any:
    reference = resolve_tier_reference(tier_reference)
    if reference is None or not str(value or "").strip():
        return None
    try:
        return reference.resolve(value)
    except Exception:  # noqa: BLE001
        return None


def full_decision_choices(tier_reference: Any = None) -> tuple[str, ...]:
    """Return the complete unknown-bank selector (groups + tiers + creation)."""
    return tuple(
        dict.fromkeys(
            (
                *DECISION_SIMPLE_CHOICES,
                *DECISION_GROUPS,
                *DECISION_INTERNAL_CHOICES,
                *known_tier_names(tier_reference),
                *DECISION_CREATION_CHOICES,
            )
        )
    )


def decision_menu_for(
    filename: Any = "",
    supplier: Any = "",
    source: Any = "",
    tier_reference: Any = None,
    *,
    is_bank: bool | None = None,
) -> DecisionMenu:
    """Build the minimal relevant menu for one pending line.

    * ``ARDOISE_*`` and every tier already present in the reference only get
      ``VALIDE / DEJA_PAYEE_2025 / REPORTER_MOIS_SUIVANT / IMPAYEE``;
    * an unknown bank tier gets the full grouped selector;
    * any other unknown document gets the same full selector, because creating
      a tier is a legitimate answer there.
    """
    name = str(filename or "")
    record = known_tier_record(supplier, tier_reference)
    if is_ardoise_filename(name):
        return DecisionMenu(ARDOISE_PROFILE, DECISION_SIMPLE_CHOICES)
    if record is not None:
        return DecisionMenu(SIMPLE_PROFILE, DECISION_SIMPLE_CHOICES)
    bank = (
        is_unknown_bank_filename(name)
        if is_bank is None
        else bool(is_bank)
    )
    profile = BANK_UNKNOWN_PROFILE if bank else STANDARD_PROFILE
    return DecisionMenu(
        profile,
        full_decision_choices(tier_reference),
        (
            ("Frais Généraux automatiques", DECISION_GROUPS),
            ("Flux internes", DECISION_INTERNAL_CHOICES),
            (
                "Clients et fournisseurs connus",
                known_tier_names(tier_reference),
            ),
            ("Création de tiers", DECISION_CREATION_CHOICES),
        ),
    )


def decision_menu_payload(menu: DecisionMenu) -> dict[str, Any]:
    return {
        "profile": menu.profile,
        "choices": list(menu.choices),
        "default": menu.default,
        "groups": [
            {"label": label, "choices": list(choices)}
            for label, choices in menu.groups
        ],
    }


def group_target(choice: Any) -> tuple[str, str, str, str] | None:
    token = str(choice or "").strip().upper()
    return DECISION_GROUP_TARGETS.get(token)


def internal_target(choice: Any) -> tuple[str, str, str, str] | None:
    token = str(choice or "").strip().upper()
    return INTERNAL_DECISION_TARGETS.get(token)


def is_group_choice(value: Any) -> bool:
    return str(value or "").strip().upper() in DECISION_GROUP_TARGETS


def is_internal_choice(value: Any) -> bool:
    return str(value or "").strip().upper() in INTERNAL_DECISION_TARGETS


def is_creation_choice(value: Any) -> bool:
    return str(value or "").strip().upper() in DECISION_CREATION_CHOICES


def is_lifecycle_choice(value: Any) -> bool:
    return normalize_decision(value) in {
        "VALIDE",
        "DEJA_PAYEE_2025",
        "REPORTER_MOIS_SUIVANT",
        "IMPAYEE",
        "REJETE",
        "EN_ATTENTE",
    }


def _account_for(category: str, fallback: str = "") -> str:
    return CATEGORY_ACCOUNTS.get(category, fallback)


def resolve_tier_choice(
    choice: Any,
    raw_tier: Any,
    tier_reference: Any = None,
) -> TierChoice | None:
    """Resolve one menu answer into supplier / category / sub-category.

    ``None`` means the answer is a lifecycle decision, not a tier mapping.
    Unknown canonical names raise ``ValueError`` so the cockpit can show a
    readable error instead of silently losing the line.
    """
    token = str(choice or "").strip().upper()
    if not token or is_lifecycle_choice(token):
        return None
    reference = resolve_tier_reference(tier_reference)
    group = group_target(token)
    if group is not None:
        canonical, category, subcategory, account = group
        return TierChoice(
            canonical, "FOURNISSEUR", category, subcategory, account, True
        )
    internal = internal_target(token)
    if internal is not None:
        canonical, category, subcategory, account = internal
        return TierChoice(
            canonical, "INTERNE_EXCLUS", category, subcategory, account, True
        )
    if is_creation_choice(token):
        role = "CLIENT" if token == "CREER_NOUVEAU_CLIENT" else "FOURNISSEUR"
        category = "VENTES_CLIENTS" if role == "CLIENT" else "ACHATS_MARCHANDISES"
        return TierChoice(
            "",
            role,
            category,
            "",
            _account_for(category),
            True,
        )
    record = known_tier_record(token, reference)
    if record is None:
        raise ValueError(f"UNKNOWN_TIER_CHOICE:{token}")
    category = normalize_category(record.category) or record.category or ""
    return TierChoice(
        record.canonical,
        record.role,
        category,
        record.subcategory,
        _account_for(category),
        True,
    )


def raw_tier_from_filename(filename: Any) -> str:
    """Extract the human tier name / OFX line id from a ``.tier`` filename."""
    name = str(filename or "").strip()
    if not name.upper().endswith(".TIER"):
        return ""
    stem = name[: -len(".tier")]
    if stem.upper().startswith("TIER__"):
        return stem[len("TIER__") :]
    if stem.upper().startswith("INSTRUMENT__"):
        return stem[len("INSTRUMENT__") :]
    if stem.upper().startswith("ARDOISE_"):
        return stem[len("ARDOISE_") :]
    return stem


def choices_for_rows(
    rows: Iterable[Any],
) -> dict[str, tuple[str, ...]]:
    """Convenience helper returning ``filename -> choices`` for a row batch."""
    return {
        str(getattr(row, "filename", "") or ""): decision_menu_for(
            getattr(row, "filename", ""),
            getattr(row, "supplier", ""),
            "",
        ).choices
        for row in rows
    }


__all__ = [
    "ARDOISE_PROFILE",
    "BANK_UNKNOWN_PROFILE",
    "DECISION_GROUP_PREFIX",
    "DECISION_GROUP_TARGETS",
    "DecisionMenu",
    "INTERNAL_DECISION_TARGETS",
    "NON_SELECTABLE_TIERS",
    "SIMPLE_PROFILE",
    "STANDARD_PROFILE",
    "TierChoice",
    "choices_for_rows",
    "decision_menu_for",
    "decision_menu_payload",
    "full_decision_choices",
    "group_target",
    "internal_target",
    "is_ardoise_filename",
    "is_creation_choice",
    "is_group_choice",
    "is_internal_choice",
    "is_lifecycle_choice",
    "is_unknown_bank_filename",
    "known_tier_names",
    "known_tier_record",
    "raw_tier_from_filename",
    "resolve_tier_choice",
    "resolve_tier_reference",
]