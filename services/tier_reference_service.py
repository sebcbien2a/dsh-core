"""P25 - Locked tier reference with aliases and explicit authorization.

The workbook ``REFERENTIEL_TIERS_APD.xlsx`` is the human master list of
clients, suppliers, service providers and internal entities. It is mirrored in
SQLite (``tier_reference``) and in the portable
``config/learned_accounting_rules.json``. The pipeline reads it first at every
stage so an alias or role edited by the director is applied immediately.
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
ROLES = ("CLIENT", "FOURNISSEUR", "PRESTATAIRE", "INTERNE_EXCLUS")
STATUSES = ("AUTORISE", "A_AUTORISER")
COLUMNS: tuple[str, ...] = (
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

    @property
    def alias_text(self) -> str:
        return "; ".join(self.aliases)

    @property
    def prefix_text(self) -> str:
        return "; ".join(self.prefixes)

    @property
    def authorized(self) -> bool:
        return self.status == "AUTORISE"

    def matches(self, value: Any) -> bool:
        text = _fold(value)
        if not text:
            return False
        candidates = [_fold(self.canonical), *( _fold(alias) for alias in self.aliases)]
        if any(candidate and (candidate == text or candidate in text) for candidate in candidates):
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
        prefixes=("FA16",),
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
        aliases=("ROBERT BOSCH", "ROBERT BOSCH FRANCE", "SIA ABRASIVES", "BOSCH"),
        payment_method="PRELEVEMENT",
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
        aliases=("VINCI", "AUTOROUTE", "EPR", "ASF", "ESCOTA", "APRR", "AREA", "SANEF", "SAPN", "COFIROUTE", "ULYS", "BIP AND GO", "PEAGE"),
        payment_method="CARTE_BANCAIRE",
    ),
    TierRecord(
        "RESTAURANTS_REPAS",
        "FOURNISSEUR",
        "FRAIS_GENERAUX",
        "RESTAURATION",
        aliases=("RESTAURANT", "RESTO", "BRASSERIE", "BISTROT", "PIZZERIA", "AUBERGE", "TRAITEUR", "BOULANGERIE", "MC DONALD", "MCDONALDS", "BURGER KING", "KFC", "SUSHI", "UBER EATS", "DELIVEROO", "CAFE", "GRILL", "PAILLOTE"),
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
        if self._path.is_file():
            for record in self._read_workbook():
                records[record.canonical] = record
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
        self._sync()
        return tuple(self._records.values())

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

    # -- persistence -----------------------------------------------------

    def save_workbook(self) -> Path:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.datavalidation import DataValidation

        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            try:
                existing = self._read_workbook()
                for record in existing:
                    self._records.setdefault(record.canonical, record)
            except Exception:  # noqa: BLE001
                pass
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
            values = (
                record.canonical,
                record.role,
                record.category,
                record.subcategory,
                record.alias_text,
                record.prefix_text,
                record.payment_method,
                record.status,
            )
            for column, value in enumerate(values, start=1):
                sheet.cell(row=row_index, column=column, value=value)
        last_row = max(sheet.max_row, 2)
        validations = {
            "B": '"' + ",".join(ROLES) + '"',
            "C": excel_list(CATEGORY_CHOICES),
            "D": excel_list(SUBCATEGORY_CHOICES),
            "H": '"' + ",".join(STATUSES) + '"',
        }
        for column, formula in validations.items():
            validation = DataValidation(
                type="list", formula1=formula, allow_blank=True
            )
            sheet.add_data_validation(validation)
            validation.add(f"{column}2:{column}{last_row}")
        sheet.freeze_panes = "A2"
        widths = (30, 20, 22, 26, 62, 22, 18, 18)
        from openpyxl.utils import get_column_letter

        for column, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(column)].width = width
        temporary = path.with_name(path.name + ".tmp-p25")
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
        return path

    def _read_workbook(self) -> tuple[TierRecord, ...]:
        from openpyxl import load_workbook

        if not self.path.is_file():
            return ()
        workbook = load_workbook(self.path, data_only=True)
        sheet = workbook.active
        records: list[TierRecord] = []
        try:
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if not row or not row[0]:
                    continue
                canonical = str(row[0]).strip()
                role = str(row[1] or "").strip().upper()
                if not canonical or role not in ROLES:
                    continue
                records.append(
                    TierRecord(
                        canonical,
                        role,
                        str(row[2] or "").strip()
                        if str(row[2] or "").strip() in CATEGORY_CHOICES
                        else "",
                        str(row[3] or "").strip()
                        if str(row[3] or "").strip() in SUBCATEGORY_CHOICES
                        else "",
                        _split(row[4]),
                        _split(row[5]),
                        str(row[6] or "").strip(),
                        str(row[7] or "AUTORISE").strip().upper()
                        if str(row[7] or "").strip().upper() in STATUSES
                        else "A_AUTORISER",
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

    def unknown_tiers(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> tuple[str, ...]:
        unknown: dict[str, None] = {}
        for row in rows:
            name = str(
                row.get("counterparty") or row.get("supplier") or ""
            ).strip()
            if not name:
                label = str(row.get("label") or "").strip()
                record = self.resolve(label)
                if record is None and label:
                    unknown.setdefault(label, None)
                continue
            if self.resolve(name) is None:
                unknown.setdefault(name, None)
        return tuple(unknown)


__all__ = [
    "ALIAS_TARGET_CREATE_CLIENT",
    "ALIAS_TARGET_CREATE_SUPPLIER",
    "ALIAS_TARGET_FUEL",
    "ALIAS_TARGET_RESTAURANTS",
    "ALIAS_TARGET_TOLLS",
    "COLUMNS",
    "DEFAULT_TIER_REFERENCE_PATH",
    "ROLES",
    "STATUSES",
    "SEED_TIERS",
    "TIER_REFERENCE_ENV",
    "TIER_REFERENCE_FILENAME",
    "TierRecord",
    "TierReferenceError",
    "TierReferenceService",
]
