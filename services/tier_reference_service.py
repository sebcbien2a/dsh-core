"""P27 - Locked tier reference with aliases, SIREN/SIRET and IBAN matching.

The workbook ``REFERENTIEL_TIERS_APD.xlsx`` is the human master list of
clients, suppliers, service providers and internal entities. It is mirrored in
SQLite (``tier_reference``) and in the portable
``config/learned_accounting_rules.json``. The pipeline reads it first at every
stage so an alias, SIREN/SIRET, IBAN or role edited by the director is applied
immediately.
"""

from __future__ import annotations

import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from schemas.accounting_choices import (
    CATEGORY_CHOICES,
    SUBCATEGORY_CHOICES,
    excel_list,
)
from services.bank_reconciliation_service import (
    CLIENTS_ENCAISSEMENT_GROUPE,
    CLIENT_INVOICE_PATTERN,
    CLIENT_INVOICE_REFERENCE_PATTERN,
    INVALID_TIER_NAMES,
    is_remise_cheques,
    is_remise_encaissement,
)

TIER_REFERENCE_FILENAME = "REFERENTIEL_TIERS_APD.xlsx"
TIER_REFERENCE_ENV = "DSH_TIER_REFERENCE_PATH"
DEFAULT_TIER_REFERENCE_PATH = (
    Path("workspace")
    / "document_flow"
    / "APD"
    / "04_EXPORT_COMPTA"
    / "APD"
    / TIER_REFERENCE_FILENAME
)
ROOT_TIER_REFERENCE_PATH = (
    Path("workspace") / "document_flow" / "APD" / TIER_REFERENCE_FILENAME
)
ROLES = ("CLIENT", "FOURNISSEUR", "PRESTATAIRE", "INTERNE_EXCLUS")
STATUSES = ("AUTORISE", "A_AUTORISER")
COLUMNS: tuple[str, ...] = (
    "Nom Officiel Canonique",
    "ALIAS à regrouper (séparés par ;)",
    "Renommer en (Optionnel)",
    "SIREN / SIRET",
    "IBAN / RIB (séparés par ;)",
    "Rôle",
    "Catégorie par Défaut",
    "Sous-Catégorie",
    "Mode Règlement",
    "Statut",
)
P26_COLUMNS: tuple[str, ...] = (
    "Nom Officiel Canonique",
    "ALIAS à regrouper (séparés par ;)",
    "Renommer en (Optionnel)",
    "Rôle",
    "Catégorie par Défaut",
    "Sous-Catégorie",
    "Mode Règlement",
    "Statut",
)
LEGACY_COLUMNS: tuple[str, ...] = (
    "Nom Officiel (Canonique)",
    "Rôle (CLIENT / FOURNISSEUR / PRESTATAIRE / INTERNE_EXCLUS)",
    "Catégorie par Défaut",
    "Sous-Catégorie Frais",
    "ALIAS & Mots-clés Bancaires (séparés par ;)",
    "Préfixes Réf/Facture",
    "Mode Règlement",
    "Statut (AUTORISE / A_AUTORISER)",
)
ALIAS_TARGET_CREATE_SUPPLIER = "CREER_NOUVEAU_FOURNISSEUR"
ALIAS_TARGET_CREATE_CLIENT = "CREER_NOUVEAU_CLIENT"
ALIAS_TARGET_RESTAURANTS = "RESTAURANTS_REPAS"
ALIAS_TARGET_TOLLS = "PEAGES_AUTOROUTES"
ALIAS_TARGET_FUEL = "STATIONS_CARBURANT"


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _split(value: Any) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in re.split(r"[;,]", str(value or ""))
        if item.strip()
    )


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _iban(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _parse_siren_siret(value: Any) -> tuple[str, str]:
    """Split a ``SIREN / SIRET`` cell into its 9- and 14-digit identifiers."""
    siren = ""
    siret = ""
    for part in re.split(r"[;,/|]+", str(value or "")):
        digits = _digits(part)
        if len(digits) == 14 and not siret:
            siret = digits
            siren = siren or digits[:9]
        elif len(digits) == 9 and not siren:
            siren = digits
    if not siren and not siret:
        digits = _digits(value)
        if len(digits) >= 14:
            return digits[:9], digits[:14]
        if len(digits) >= 9:
            return digits[:9], ""
    return siren, siret


INVALID_TIER_NOISE: frozenset[str] = frozenset(
    {
        "COMMANDE",
        "CLIENT",
        "CLIENTS",
        "CLIENTS CHEQUES",
        "CLIENTS LCR",
        "FACTURE",
        "DEVIS",
        "AVOIR",
        "TOTAL",
        "CHEQUE",
        "LCR",
        "VIREMENT",
        "BANQUE",
        "CAISSE D'EPARGNE",
        "CAISSE DEPARGNE",
    }
)
PAYMENT_INSTRUMENT_PREFIXES: tuple[str, ...] = (
    "LCR ",
    "RLV ",
    "CHEQUE ",
    "CHQ ",
    "REMISE ",
)
def is_payment_instrument_label(value: Any) -> bool:
    """Return ``True`` for ``LCR ...`` / ``CHEQUE ...`` bank instruments."""
    text = str(value or "").strip().upper()
    return any(text.startswith(prefix) for prefix in PAYMENT_INSTRUMENT_PREFIXES)


def is_client_invoice_reference(value: Any) -> bool:
    """Return ``True`` for the APD customer numbering ``FA1xxxxx``."""
    return bool(CLIENT_INVOICE_REFERENCE_PATTERN.match(str(value or "").strip()))


def is_invalid_tier_noise(value: Any) -> bool:
    """Return ``True`` for generic OCR / bank words that are never a real tier."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not text:
        return True
    if text in INVALID_TIER_NAMES or text in INVALID_TIER_NOISE:
        return True
    if is_payment_instrument_label(text):
        return True
    return False


@dataclass(frozen=True)
class TierRecord:
    canonical: str
    role: str
    category: str = ""
    subcategory: str = ""
    aliases: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    payment_method: str = ""
    status: str = "AUTORISE"
    rename_to: str = ""
    siren: str = ""
    siret: str = ""
    iban: tuple[str, ...] = ()

    @property
    def alias_text(self) -> str:
        return "; ".join(self.aliases)

    @property
    def prefix_text(self) -> str:
        return "; ".join(self.prefixes)

    @property
    def iban_text(self) -> str:
        return "; ".join(self.iban)

    @property
    def authorized(self) -> bool:
        return self.status == "AUTORISE"

    def matches(self, value: Any) -> bool:
        text = _fold(value)
        if not text:
            return False
        if self.canonical == CLIENTS_ENCAISSEMENT_GROUPE and (
            is_remise_encaissement(value) or is_remise_cheques(value)
        ):
            return True
        prefix_match = any(
            re.search(
                rf"(?<![A-Z0-9]){re.escape(prefix)}[A-Z0-9]*",
                str(value),
                re.IGNORECASE,
            )
            for prefix in self.prefixes
            if prefix
        )
        if prefix_match:
            return True
        if is_invalid_tier_noise(value):
            return False
        candidates = [_fold(self.canonical), *(_fold(alias) for alias in self.aliases)]
        for candidate in candidates:
            if not candidate:
                continue
            if candidate == text:
                return True
            if len(candidate) <= 5 and re.fullmatch(r"[a-z0-9]+", candidate):
                if re.search(
                    rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", text
                ):
                    return True
                continue
            if candidate in text:
                return True
        digits = _digits(value)
        if self.siren and len(self.siren) >= 9 and self.siren in digits:
            return True
        if self.siret and len(self.siret) >= 14 and self.siret in digits:
            return True
        iban_text = _iban(value)
        if any(iban and iban in iban_text for iban in self.iban):
            return True
        return any(
            re.search(rf"(?<![A-Z0-9]){re.escape(prefix)}[A-Z0-9]*", str(value), re.IGNORECASE)
            for prefix in self.prefixes
            if prefix
        )


SEED_TIERS: tuple[TierRecord, ...] = (
    TierRecord(
        "APD_INTERNE",
        "INTERNE_EXCLUS",
        aliases=(
            "APD",
            "A.P.D",
            "ASSISTANCE PEINTURES DIFFUSION",
            "SAS ASSISTANCE PEINTURES DIFFUSION",
            "ASSISTANCE PEINTURE DIFFUSION",
            "ASSISTANCE PEINTURES DIFFUSION S",
        ),
        status="AUTORISE",
    ),
    TierRecord(
        "CARROSSERIE_YUCEL",
        "CLIENT",
        "VENTES_CLIENTS",
        aliases=("CARROSSERIE YUCELL", "SARL CARROSSERIE YUCE", "YUCEL"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "YDES_AUTO_ACCESSOIRES",
        "CLIENT",
        "VENTES_CLIENTS",
        aliases=("YDES AUTO ACCESSOIRES", "Ydes Auto Accessoires GP15"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        CLIENTS_ENCAISSEMENT_GROUPE,
        "CLIENT",
        "VENTES_CLIENTS",
        aliases=("CLIENTS LCR", "CLIENTS CHEQUES"),
        payment_method="LCR",
    ),
    TierRecord(
        "SOLTA_LR_COLOR",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=(
            "SOLTA",
            "SOLTA GROUPE",
            "SOLTA SAS",
            "LR COLOR",
            "LR COLOR 3",
            "LRCOLOR3",
            "VITACOLOR",
            "VITAMARINE",
            "RESIHOM",
        ),
        payment_method="VIREMENT",
        siren="327274155",
        siret="32727415500057",
        iban=("FR7610096182390008817130172",),
    ),
    TierRecord(
        "COLOR_STORE_FRANCE",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("COLOR STORE FRANCE", "COLOR STORE"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "CHOLLET",
        "CLIENT",
        "VENTES_CLIENTS",
        aliases=("CHOLLET",),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "PAINTCAR",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("CRISOL COLOR", "CRISOL COLOR S L", "TOTCOLOR", "GROUPE TOTCOLOR"),
        prefixes=("ILA",),
        payment_method="LCR",
    ),
    TierRecord(
        "QREFINISH",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("Q REFINISH", "QREFINISH FRANCE", "QREF"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "BOSCH_SIA",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=(
            "BOSCH",
            "BOSCH SA",
            "BOSCH FRANCE SA",
            "BOSCH SA FRANCE",
            "ROBERT BOSCH",
            "ROBERT BOSCH FRANCE",
            "SIA",
            "SIA ABRASIVES",
            "SIA ABRASIVES FRANCE",
        ),
        payment_method="PRELEVEMENT",
    ),
    TierRecord(
        "SAS_LES_EMAUX",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("SAS LES EMAUX", "LES EMAUX", "LES EMAUX DE L'ATLANTIQUE", "EM'A LES EMAUX DE L'ATLANTIQUE"),
        payment_method="LCR",
    ),
    TierRecord(
        "ANG_PEINTURE",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("ANG PEINTURE", "ANG PEINTURES"),
        payment_method="LCR",
    ),
    TierRecord(
        "SOCIETE_DISTRIB",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("SOCIETE DISTRIB",),
        payment_method="LCR",
    ),
    TierRecord(
        "NORMANDIE_FILTRE",
        "FOURNISSEUR",
        "ACHATS_MARCHANDISES",
        aliases=("NORMANDIE FILTRE FACTOR", "NORMANDIE FILTRE"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "SONIA_ATIA",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "HONORAIRES_JURIDIQUES",
        aliases=(
            "EI - ATIA SONIA / TALENT&CO",
            "ATIA SONIA",
            "TALENT&CO",
            "SONIA ATIA",
            "SONIA ALTIA",
        ),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "OPENROUTER",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "INFORMATIQUE_WEB",
        aliases=("OpenRouter, Inc", "OPENROUTER", "OPENROUTER.AI"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "AMAZON",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        aliases=("AMAZON PAYMENTS", "CB AMAZON PAYMENTS", "AMZ", "AMAZON EU", "AMAZON FR", "AMAZON BUSINESS"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "ACTION",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        aliases=("ACTION", "CB ACTION"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "LIDL",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        aliases=("LIDL", "CB LIDL"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "PEAGES_AUTOROUTES",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "TRANSPORT_PARKING",
        aliases=("VINCI", "AUTOROUTE", "EPR", "ASF", "ESCOTA", "APRR", "AREA", "SANEF", "SAPN", "COFIROUTE", "ULYS", "BIP AND GO", "PEAGE", "COMPAGNIE EIFFA", "EIFFAGE"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "RESTAURANTS_REPAS",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "RESTAURATION",
        aliases=("RESTAURANT", "RESTO", "BRASSERIE", "BISTROT", "PIZZERIA", "AUBERGE", "TRAITEUR", "BOULANGERIE", "MC DONALD", "MCDONALDS", "BURGER KING", "KFC", "SUSHI", "UBER EATS", "DELIVEROO", "CAFE", "GRILL", "PAILLOTE", "LE MARMARA", "LE RELAI DE TRE", "RELAI DE TRE"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "STATIONS_CARBURANT",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "CARBURANT",
        aliases=("TOTAL", "TOTALENERGIES", "ESSO", "SHELL", "BP", "AVIA", "VITO", "RUBIS", "STATION SERVICE", "DAC"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "PARKINGS_TRANSPORTS",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "TRANSPORT_PARKING",
        aliases=("INDIGO PARK", "EFFIA", "Q-PARK", "PAYBYPHONE", "EASYPARK", "PARKING", "SNCF", "AIR CORSICA", "AIR FRANCE", "CORSICA LINEA", "CORSICA FERRIES"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "BOXTAL",
        "FOURNISSEUR",
        "TRANSPORTS_LOGISTIQUE",
        aliases=("BOXTAL",),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "CHRONOPOST",
        "FOURNISSEUR",
        "TRANSPORTS_LOGISTIQUE",
        aliases=("CHRONOPOST",),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "TRANSPORTS_CATROUX",
        "FOURNISSEUR",
        "TRANSPORTS_LOGISTIQUE",
        aliases=("TRANSPORTS CATROUX", "CATROUX"),
        payment_method="VIREMENT",
    ),
    TierRecord(
        "SAS_DIFFAM",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        aliases=("HABITAT AUTOMATISME", "SAS DIFFAM", "DIFFAM"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "LE_TEMPS_DES_CERISES",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "FRAIS_REPRESENTATION_PRESIDENT",
        aliases=("LE TEMPS DES CERISES", "SASU INDIGO GALLERY", "INDIGO GALLERY"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "TRADEINN",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "FRAIS_REPRESENTATION_PRESIDENT",
        aliases=("TRADEINN", "DIVEINN", "TREKKINN", "MOTARDINN"),
        payment_method="CARTE_BANCAIRE",
    ),
)


class TierReferenceError(ValueError):
    """Invalid tier reference operation; ``code`` is stable."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class TierReferenceService:
    """Workbook + SQLite + JSON tier reference with alias resolution."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        store: Any | None = None,
        memory: Any | None = None,
        tenant_id: str = "APD",
    ) -> None:
        raw = os.environ.get(TIER_REFERENCE_ENV, "").strip()
        self._path = Path(path or (raw or DEFAULT_TIER_REFERENCE_PATH))
        self._store = store
        self._memory = memory
        self._tenant_id = tenant_id
        self._records: dict[str, TierRecord] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    def bootstrap(self, *, write_missing: bool = True) -> tuple[TierRecord, ...]:
        """Read the workbook first, then SQLite/memory; write missing seed."""
        records: dict[str, TierRecord] = {
            record.canonical: record for record in SEED_TIERS
        }
        workbook_paths: list[Path] = []
        if self._path.is_file():
            workbook_paths.append(self._path)
        root_path = ROOT_TIER_REFERENCE_PATH
        if (
            root_path.is_file()
            and root_path.resolve() != self._path.resolve()
        ):
            workbook_paths.append(root_path)
        workbook_canonicals: set[str] = set()
        for workbook_path in workbook_paths:
            for record in self._read_workbook(workbook_path):
                workbook_canonicals.add(record.canonical)
                base = records.get(record.canonical)
                if base is not None and not record.prefixes:
                    record = replace(record, prefixes=base.prefixes)
                existing = records.get(record.canonical)
                if existing is not None:
                    record = replace(
                        record,
                        aliases=tuple(
                            dict.fromkeys((*existing.aliases, *record.aliases))
                        ),
                    )
                records[record.canonical] = record
        missing_seed = [
            record.canonical
            for record in SEED_TIERS
            if record.canonical not in workbook_canonicals
        ]
        if self._store is not None:
            for record in self._read_store():
                records.setdefault(record.canonical, record)
        if self._memory is not None:
            for record in self._read_memory():
                records.setdefault(record.canonical, record)
        self._records = records
        self._loaded = True
        if not self._path.is_file():
            if write_missing:
                self.save_workbook()
        elif self._is_legacy_workbook():
            self.save_workbook()
        elif write_missing and missing_seed:
            self.save_workbook()
        self.apply_alias_merges()
        self._sync()
        return tuple(self._records.values())

    def _is_legacy_workbook(self) -> bool:
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(self._path, read_only=True)
            try:
                headers = [
                    str(cell.value or "")
                    for cell in next(workbook.active.iter_rows(max_row=1))
                ]
            finally:
                workbook.close()
        except Exception:  # noqa: BLE001
            return False
        return not (len(headers) > 3 and "SIREN" in headers[3].upper())

    def records(self) -> tuple[TierRecord, ...]:
        if not self._loaded:
            self.bootstrap()
        return tuple(self._records.values())

    def resolve(self, value: Any) -> TierRecord | None:
        text = str(value or "").strip()
        if not text:
            return None
        for record in self.records():
            if record.matches(text):
                return record
        return None

    def match_reference(self, value: Any) -> str | None:
        record = self.resolve(value)
        return record.canonical if record is not None else None

    def role_for(self, value: Any) -> str:
        record = self.resolve(value)
        return record.role if record is not None else ""

    def canonical_name(self, value: Any) -> str:
        record = self.resolve(value)
        return record.canonical if record is not None else str(value or "").strip()

    def is_internal(self, value: Any) -> bool:
        return self.role_for(value) == "INTERNE_EXCLUS"

    def alias_targets(self) -> tuple[str, ...]:
        return tuple(
            sorted(record.canonical for record in self.records())
        ) + (
            ALIAS_TARGET_CREATE_SUPPLIER,
            ALIAS_TARGET_CREATE_CLIENT,
            ALIAS_TARGET_RESTAURANTS,
            ALIAS_TARGET_TOLLS,
            ALIAS_TARGET_FUEL,
        )

    def learn_alias(self, alias: str, target: str) -> TierRecord:
        alias = str(alias or "").strip()
        target = str(target or "").strip()
        if not alias or not target:
            raise TierReferenceError("INVALID_ALIAS", f"{alias!r}->{target!r}")
        records = {record.canonical: record for record in self.records()}
        if target in {
            ALIAS_TARGET_CREATE_SUPPLIER,
            ALIAS_TARGET_CREATE_CLIENT,
            ALIAS_TARGET_RESTAURANTS,
            ALIAS_TARGET_TOLLS,
            ALIAS_TARGET_FUEL,
        }:
            canonical = re.sub(r"[^A-Z0-9]+", "_", _fold(alias).upper()).strip("_")
            role = (
                "CLIENT"
                if target == ALIAS_TARGET_CREATE_CLIENT
                else "FOURNISSEUR"
            )
            category = (
                "VENTES_CLIENTS"
                if role == "CLIENT"
                else "FRAIS_GENERAUX"
                if target
                in {
                    ALIAS_TARGET_RESTAURANTS,
                    ALIAS_TARGET_TOLLS,
                    ALIAS_TARGET_FUEL,
                }
                else "ACHATS_MARCHANDISES"
            )
            subcategory = {
                ALIAS_TARGET_RESTAURANTS: "RESTAURATION",
                ALIAS_TARGET_TOLLS: "TRANSPORT_PARKING",
                ALIAS_TARGET_FUEL: "CARBURANT",
            }.get(target, "")
            record = TierRecord(
                canonical or alias.upper(),
                role,
                category,
                subcategory,
                aliases=(alias,),
                status="AUTORISE",
            )
        else:
            base = records.get(target)
            if base is None:
                base = next(
                    (
                        record
                        for record in records.values()
                        if _fold(record.canonical) == _fold(target)
                    ),
                    None,
                )
            if base is None:
                raise TierReferenceError("UNKNOWN_TARGET", target)
            record = replace(
                base,
                aliases=tuple(dict.fromkeys((*base.aliases, alias))),
                status="AUTORISE",
            )
        records[record.canonical] = record
        self._records = records
        self.save_workbook()
        self._sync()
        return record

    def create_tier(
        self,
        canonical: str,
        role: str,
        category: str = "",
        subcategory: str = "",
        aliases: Iterable[str] = (),
        prefixes: Iterable[str] = (),
        payment_method: str = "",
        status: str = "AUTORISE",
    ) -> TierRecord:
        role = str(role or "").upper()
        if role not in ROLES:
            raise TierReferenceError("INVALID_ROLE", role)
        canonical = str(canonical or "").strip()
        if not canonical:
            raise TierReferenceError("INVALID_CANONICAL", canonical)
        record = TierRecord(
            canonical,
            role,
            category if category in CATEGORY_CHOICES else "",
            subcategory if subcategory in SUBCATEGORY_CHOICES else "",
            tuple(str(item).strip() for item in aliases if str(item).strip()),
            tuple(str(item).strip() for item in prefixes if str(item).strip()),
            str(payment_method or "").strip(),
            status if status in STATUSES else "A_AUTORISER",
        )
        records = {item.canonical: item for item in self.records()}
        records[canonical] = record
        self._records = records
        self.save_workbook()
        self._sync()
        return record

    def apply_alias_merges(self) -> tuple[str, ...]:
        """Merge rows moved to another row's alias column or renamed.

        The director can either move a canonical name from column A to the
        alias column B of another row, or fill ``Renommer en``. In both cases
        the source tier is merged into the target: SQLite counterparties are
        renamed, ``learned_accounting_rules.json`` learns the alias and the
        duplicate ledger sheet disappears.
        """
        records = {record.canonical: record for record in self.records()}
        merges: dict[str, str] = {}
        for record in records.values():
            target = str(record.rename_to or "").strip()
            if target and _fold(target) != _fold(record.canonical):
                merges[record.canonical] = target
                continue
            for other in records.values():
                if other.canonical == record.canonical:
                    continue
                if any(
                    _fold(alias) == _fold(record.canonical)
                    for alias in other.aliases
                ):
                    merges[record.canonical] = other.canonical
                    break
        if not merges:
            return ()
        merged: list[str] = []
        for source, target in merges.items():
            source_record = records.get(source)
            if source_record is None:
                continue
            target_record = records.get(target)
            if target_record is None:
                target_record = next(
                    (
                        record
                        for record in records.values()
                        if _fold(record.canonical) == _fold(target)
                    ),
                    None,
                )
            if target_record is None:
                continue
            aliases = tuple(
                dict.fromkeys(
                    (
                        *target_record.aliases,
                        source,
                        *source_record.aliases,
                    )
                )
            )
            records[target_record.canonical] = replace(
                target_record, aliases=aliases, rename_to=""
            )
            records.pop(source, None)
            self._rename_sqlite(source, target_record.canonical)
            for alias in (source, *source_record.aliases):
                self._rename_sqlite(alias, target_record.canonical)
            if self._memory is not None:
                try:
                    self._memory.remember_alias(source, target_record.canonical)
                    for alias in source_record.aliases:
                        self._memory.remember_alias(
                            alias, target_record.canonical
                        )
                except Exception:  # noqa: BLE001
                    pass
            merged.append(source)
        self._records = records
        self.save_workbook()
        self._sync()
        return tuple(merged)

    def _rename_sqlite(self, source: str, target: str) -> None:
        if self._store is None:
            return
        rename = getattr(self._store, "rename_counterparty", None)
        if not callable(rename):
            return
        try:
            rows = self._store.list_master_lines(self._tenant_id)
        except Exception:  # noqa: BLE001
            return
        for row in rows:
            name = str(row.get("counterparty") or "").strip()
            if not name:
                continue
            if _fold(name) == _fold(source):
                try:
                    rename(self._tenant_id, name, target)
                except Exception:  # noqa: BLE001
                    continue

    # -- persistence -----------------------------------------------------

    def save_workbook(self) -> Path:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_workbook(path)
        if path.resolve() == DEFAULT_TIER_REFERENCE_PATH.resolve():
            mirror = ROOT_TIER_REFERENCE_PATH
            if mirror.resolve() != path.resolve():
                mirror.parent.mkdir(parents=True, exist_ok=True)
                self._write_workbook(mirror)
        return path

    def _write_workbook(self, path: Path) -> None:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.utils import get_column_letter

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "REFERENTIEL_TIERS"
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="1F4E78")
        for column, header in enumerate(COLUMNS, start=1):
            cell = sheet.cell(row=1, column=column, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
        for row_index, record in enumerate(
            sorted(self.records(), key=lambda item: item.canonical.casefold()),
            start=2,
        ):
            siren_siret = "; ".join(
                value for value in (record.siren, record.siret) if value
            )
            values = (
                record.canonical,
                record.alias_text,
                record.rename_to,
                siren_siret,
                record.iban_text,
                record.role,
                record.category,
                record.subcategory,
                record.payment_method,
                record.status,
            )
            for column, value in enumerate(values, start=1):
                sheet.cell(row=row_index, column=column, value=value)
        last_row = max(sheet.max_row, 2)
        validations = {
            "F": '"' + ",".join(ROLES) + '"',
            "G": excel_list(CATEGORY_CHOICES),
            "H": excel_list(SUBCATEGORY_CHOICES),
            "J": '"' + ",".join(STATUSES) + '"',
        }
        for column, formula in validations.items():
            validation = DataValidation(
                type="list", formula1=formula, allow_blank=True
            )
            sheet.add_data_validation(validation)
            validation.add(f"{column}2:{column}{last_row}")
        sheet.freeze_panes = "A2"
        widths = (30, 62, 24, 22, 40, 20, 22, 26, 18, 18)
        for column, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = width
        temporary = path.with_name(path.name + ".tmp-p27")
        try:
            workbook.save(temporary)
            try:
                os.replace(temporary, path)
            except OSError:
                try:
                    shutil.copy2(temporary, path)
                except OSError:
                    fallback = path.with_name(
                        f"{path.stem}_MIS_A_JOUR{path.suffix}"
                    )
                    shutil.copy2(temporary, fallback)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_workbook(
        self, path: Path | None = None
    ) -> tuple[TierRecord, ...]:
        from openpyxl import load_workbook

        source = path or self.path
        if not source.is_file():
            return ()
        workbook = load_workbook(source, data_only=True)
        sheet = workbook.active
        records: list[TierRecord] = []
        try:
            headers = [
                str(cell.value or "").strip() for cell in sheet[1]
            ]
            p27_format = len(headers) > 3 and "SIREN" in headers[3].upper()
            p26_format = (
                not p27_format
                and len(headers) > 1
                and "ALIAS" in headers[1].upper()
            )
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                canonical = str(row[0]).strip()
                if not canonical:
                    continue
                siren = ""
                siret = ""
                iban: tuple[str, ...] = ()
                if p27_format:
                    aliases = _split(row[1])
                    rename_to = str(row[2] or "").strip()
                    siren, siret = _parse_siren_siret(row[3])
                    iban = tuple(
                        _iban(item) for item in _split(row[4]) if _iban(item)
                    )
                    role = str(row[5] or "").strip().upper()
                    category = str(row[6] or "").strip()
                    subcategory = str(row[7] or "").strip()
                    payment_method = str(row[8] or "").strip()
                    status = str(row[9] or "AUTORISE").strip().upper()
                    prefixes: tuple[str, ...] = ()
                elif p26_format:
                    aliases = _split(row[1])
                    rename_to = str(row[2] or "").strip()
                    role = str(row[3] or "").strip().upper()
                    category = str(row[4] or "").strip()
                    subcategory = str(row[5] or "").strip()
                    payment_method = str(row[6] or "").strip()
                    status = str(row[7] or "AUTORISE").strip().upper()
                    prefixes = ()
                else:
                    role = str(row[1] or "").strip().upper()
                    category = str(row[2] or "").strip()
                    subcategory = str(row[3] or "").strip()
                    aliases = _split(row[4])
                    prefixes = _split(row[5])
                    payment_method = str(row[6] or "").strip()
                    status = str(row[7] or "AUTORISE").strip().upper()
                    rename_to = ""
                if role not in ROLES:
                    continue
                records.append(
                    TierRecord(
                        canonical,
                        role,
                        category if category in CATEGORY_CHOICES else "",
                        subcategory
                        if subcategory in SUBCATEGORY_CHOICES
                        else "",
                        aliases,
                        prefixes,
                        payment_method,
                        status if status in STATUSES else "A_AUTORISER",
                        rename_to,
                        siren,
                        siret,
                        iban,
                    )
                )
        finally:
            workbook.close()
        return tuple(records)

    def _read_store(self) -> tuple[TierRecord, ...]:
        list_method = getattr(self._store, "list_tier_references", None)
        if not callable(list_method):
            return ()
        try:
            rows = list_method(self._tenant_id)
        except Exception:  # noqa: BLE001
            return ()
        records: list[TierRecord] = []
        for row in rows:
            records.append(
                TierRecord(
                    str(row.get("canonical") or ""),
                    str(row.get("role") or ""),
                    str(row.get("category") or ""),
                    str(row.get("subcategory") or ""),
                    _split(row.get("aliases")),
                    _split(row.get("prefixes")),
                    str(row.get("payment_method") or ""),
                    str(row.get("status") or "AUTORISE"),
                    "",
                    str(row.get("siren") or ""),
                    str(row.get("siret") or ""),
                    tuple(
                        _iban(item)
                        for item in _split(row.get("iban"))
                        if _iban(item)
                    ),
                )
            )
        return tuple(records)

    def _read_memory(self) -> tuple[TierRecord, ...]:
        getter = getattr(self._memory, "tier_records", None)
        if not callable(getter):
            return ()
        try:
            rows = getter()
        except Exception:  # noqa: BLE001
            return ()
        records: list[TierRecord] = []
        for row in rows:
            records.append(
                TierRecord(
                    str(row.get("canonical") or ""),
                    str(row.get("role") or ""),
                    str(row.get("category") or ""),
                    str(row.get("subcategory") or ""),
                    tuple(row.get("aliases") or ()),
                    tuple(row.get("prefixes") or ()),
                    str(row.get("payment_method") or ""),
                    str(row.get("status") or "AUTORISE"),
                    "",
                    str(row.get("siren") or ""),
                    str(row.get("siret") or ""),
                    tuple(row.get("iban") or ()),
                )
            )
        return tuple(records)

    def _sync(self) -> None:
        if self._store is not None:
            upsert = getattr(self._store, "upsert_tier_reference", None)
            if callable(upsert):
                for record in self.records():
                    try:
                        upsert(
                            self._tenant_id,
                            record.canonical,
                            {
                                "role": record.role,
                                "category": record.category,
                                "subcategory": record.subcategory,
                                "aliases": record.alias_text,
                                "prefixes": record.prefix_text,
                                "payment_method": record.payment_method,
                                "status": record.status,
                                "siren": record.siren,
                                "siret": record.siret,
                                "iban": record.iban_text,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        continue
        if self._memory is not None:
            remember = getattr(self._memory, "remember_tier", None)
            if callable(remember):
                for record in self.records():
                    try:
                        remember(record)
                    except Exception:  # noqa: BLE001
                        continue

    def unknown_tier_entries(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> tuple[tuple[str, Mapping[str, Any]], ...]:
        """Return ``(name, line)`` pairs for real unknown tiers only.

        Generic OCR words (``CLIENT``, ``FACTURE``, ...) and bank instruments
        (``LCR ...``, ``CHEQUE ...``) are never tiers: they are filtered out
        before the director is asked anything.
        """
        unknown: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            name = str(
                row.get("counterparty") or row.get("supplier") or ""
            ).strip()
            if not name:
                label = str(row.get("label") or "").strip()
                if is_invalid_tier_noise(label):
                    continue
                record = self.resolve(label)
                if record is None and label:
                    unknown.setdefault(label, row)
                continue
            if is_invalid_tier_noise(name):
                continue
            if self.resolve(name) is None:
                unknown.setdefault(name, row)
        return tuple(unknown.items())

    def unknown_tiers(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> tuple[str, ...]:
        return tuple(name for name, _row in self.unknown_tier_entries(rows))


__all__ = [
    "ALIAS_TARGET_CREATE_CLIENT",
    "ALIAS_TARGET_CREATE_SUPPLIER",
    "ALIAS_TARGET_FUEL",
    "ALIAS_TARGET_RESTAURANTS",
    "ALIAS_TARGET_TOLLS",
    "CLIENT_INVOICE_PATTERN",
    "CLIENT_INVOICE_REFERENCE_PATTERN",
    "COLUMNS",
    "DEFAULT_TIER_REFERENCE_PATH",
    "INVALID_TIER_NAMES",
    "INVALID_TIER_NOISE",
    "LEGACY_COLUMNS",
    "P26_COLUMNS",
    "PAYMENT_INSTRUMENT_PREFIXES",
    "ROLES",
    "ROOT_TIER_REFERENCE_PATH",
    "STATUSES",
    "SEED_TIERS",
    "TIER_REFERENCE_ENV",
    "TIER_REFERENCE_FILENAME",
    "TierRecord",
    "TierReferenceError",
    "TierReferenceService",
    "is_client_invoice_reference",
    "is_invalid_tier_noise",
    "is_payment_instrument_label",
]
