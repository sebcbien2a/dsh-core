"""P24 - Portable self-learning accounting memory.

The memory is a plain, versioned JSON file (``config/learned_accounting_rules.json``)
so it can be synchronised between the local workstation and the Oracle server
without any Neo4j dependency. It stores:

* multi-company groups (Paintcar, Qrefinish, ...) and their LCR prefixes so a
  bank label can be lettered immediately;
* counterparty aliases (``HABITAT AUTOMATISME`` <-> ``SAS DIFFAM``, ...);
* the supplier / customer facts learnt from director arbitrations;
* an append-only decision journal.

Every validated arbitration (Excel ``_A_COMPLETER`` or Hermes) calls
:meth:`AccountingMemoryLearningService.remember_decision` so the same question
is never asked twice. Reads are cached per instance and writes are atomic.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

MEMORY_PATH_ENV = "DSH_ACCOUNTING_MEMORY_PATH"
MEMORY_FILENAME = "learned_accounting_rules.json"
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_PATH = _REPO_ROOT / "config" / MEMORY_FILENAME
_LEGAL_SUFFIXES = frozenset(
    {
        "SA",
        "SAS",
        "SASU",
        "SARL",
        "EURL",
        "SCI",
        "SELARL",
        "SNC",
        "GMBH",
        "SL",
        "LTD",
        "INC",
        "LLC",
    }
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def _key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", _fold(value).upper()).strip("_")


def normalize_counterparty_name(value: Any) -> str:
    """Return the stable display key used in the ledgers.

    ``Carrosserie Yucell`` becomes ``CARROSSERIE_YUCEL`` so the P24 client
    workbook always exposes the expected ``CARROSSERIE_YUCEL`` sheet.
    """
    text = _key(value)
    if "YUCEL" in text:
        return "CARROSSERIE_YUCEL"
    return text or "NON_AFFECTE"


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in re.split(r"[^a-z0-9]+", _fold(value))
        if token and token.upper() not in _LEGAL_SUFFIXES
    )


def _default_rules() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": None,
        "groups": {
            "PAINTCAR": {
                "canonical": "PAINTCAR",
                "members": [
                    "PAINTCAR",
                    "CRISOL COLOR",
                    "CRISOL COLOR S L",
                    "TOTCOLOR",
                    "GROUPE TOTCOLOR",
                ],
                "reference_prefixes": ["ILA"],
            },
            "QREFINISH": {
                "canonical": "QREFINISH",
                "members": ["QREFINISH", "Q REFINISH", "QREFINISH FRANCE"],
                "reference_prefixes": ["QREF"],
            },
        },
        "aliases": {
            "HABITAT AUTOMATISME": "SAS DIFFAM",
            "SAS DIFFAM": "SAS DIFFAM",
            "SMTRT": "SMTRT",
            "LIDL": "LIDL",
            "LE TEMPS DES CERISES": "LE TEMPS DES CERISES",
            "SASU INDIGO GALLERY": "LE TEMPS DES CERISES",
            "TRADEINN": "TRADEINN",
            "CARROSSERIE YUCELL": "CARROSSERIE_YUCEL",
            "CARROSSERIE YUCEL": "CARROSSERIE_YUCEL",
        },
        "suppliers": {
            "LIDL": {
                "invoice_numbers": ["F013481860"],
                "amount_ttc": "246.89",
                "nature": "OUTILLAGE",
            },
            "LE TEMPS DES CERISES": {
                "aliases": ["SASU INDIGO GALLERY"],
                "amounts_ttc": ["37.18", "528.76"],
                "amounts_tva": ["6.20", "88.13"],
                "nature": "FRAIS_REPRESENTATION_PRESIDENT",
            },
            "TRADEINN": {
                "amounts_ttc": ["302.39", "101.66"],
                "nature": "FRAIS_REPRESENTATION_PRESIDENT",
            },
            "SMTRT": {"source": "SMTRT.eml"},
            "QREFINISH": {"group": "QREFINISH"},
        },
        "decisions": [],
    }


@dataclass(frozen=True)
class LearnedDecision:
    """One arbitration appended to the portable memory journal."""

    counterparty: str
    document_number: str
    category: str = ""
    payment_source: str = ""
    counterparty_account: str = ""
    fiscal_year_target: str = ""
    deferred_month: str = ""
    report: str = ""
    decision: str = ""
    learned_at: str = ""


@dataclass
class AccountingMemoryLearningService:
    """Load, query and extend the portable accounting rule memory."""

    path: str | os.PathLike[str] | None = None
    _rules: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def memory_path(self) -> Path:
        raw = os.environ.get(MEMORY_PATH_ENV, "").strip()
        if self.path is not None:
            return Path(self.path)
        return Path(raw) if raw else DEFAULT_MEMORY_PATH

    def load(self) -> dict[str, Any]:
        if self._rules is not None:
            return self._rules
        path = self.memory_path
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
        else:
            payload = {}
        self._rules = self._merge_defaults(payload)
        if not path.is_file():
            self.save()
        return self._rules

    @staticmethod
    def _merge_defaults(payload: dict[str, Any]) -> dict[str, Any]:
        defaults = _default_rules()
        merged = {
            "version": int(payload.get("version") or defaults["version"]),
            "updated_at": payload.get("updated_at"),
            "groups": dict(defaults["groups"]),
            "aliases": dict(defaults["aliases"]),
            "suppliers": dict(defaults["suppliers"]),
            "decisions": list(payload.get("decisions") or []),
        }
        for key in ("groups", "aliases", "suppliers"):
            stored = payload.get(key)
            if isinstance(stored, dict):
                merged[key].update(stored)
        return merged

    def save(self) -> Path:
        rules = self.load() if self._rules is None else self._rules
        rules["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self.memory_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-p24")
        try:
            temporary.write_text(
                json.dumps(rules, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.replace(temporary, path)
            except OSError:
                shutil.copy2(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    # -- resolution ------------------------------------------------------

    def group_for(self, value: Any) -> str | None:
        rules = self.load()
        folded = _fold(value)
        if not folded:
            return None
        for group_name, group in rules["groups"].items():
            canonical = str(group.get("canonical") or group_name)
            members = [str(member) for member in group.get("members") or ()]
            for member in members:
                member_folded = _fold(member)
                if member_folded and (
                    member_folded == folded
                    or member_folded in folded
                    or folded in member_folded
                ):
                    return canonical
        return None

    def match_reference(self, value: Any) -> str | None:
        """Return the group matching an LCR label or an ``ILA`` reference."""
        rules = self.load()
        folded = _fold(value)
        if not folded:
            return None
        group = self.group_for(value)
        if group is not None:
            return group
        raw = str(value)
        for group_name, payload in rules["groups"].items():
            canonical = str(payload.get("canonical") or group_name)
            prefixes = [
                str(item)
                for item in (
                    payload.get("reference_prefixes")
                    or payload.get("prefixes")
                    or ()
                )
                if str(item).strip()
            ]
            for prefix in prefixes:
                if re.search(
                    rf"(?<![A-Z0-9]){re.escape(prefix)}[A-Z0-9]*",
                    raw,
                    re.IGNORECASE,
                ):
                    return canonical
        return None

    def canonical_name(self, name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        folded = _fold(text)
        if "yucel" in folded or "yuce" in folded:
            return "CARROSSERIE_YUCEL"
        group = self.group_for(text)
        if group:
            return group
        rules = self.load()
        for alias, canonical in rules["aliases"].items():
            alias_folded = _fold(alias)
            if alias_folded and (
                alias_folded == folded
                or alias_folded in folded
                or folded in alias_folded
                or set(_name_tokens(alias)) <= set(_name_tokens(text))
            ):
                return str(canonical)
        return text

    def aliases_for(self, name: str) -> tuple[str, ...]:
        canonical = self.canonical_name(name)
        rules = self.load()
        names = {str(name), canonical}
        for alias, target in rules["aliases"].items():
            if str(target) == canonical:
                names.add(str(alias))
        for group in rules["groups"].values():
            if str(group.get("canonical")) == canonical:
                names.update(str(item) for item in group.get("members", ()))
        return tuple(sorted(value for value in names if value))

    def matches_counterparty(self, left: Any, right: Any) -> bool:
        left_name = _key(self.canonical_name(left))
        right_name = _key(self.canonical_name(right))
        if not left_name or not right_name:
            return False
        if left_name == right_name:
            return True
        left_tokens = set(_name_tokens(left))
        right_tokens = set(_name_tokens(right))
        return bool(left_tokens and right_tokens and (left_tokens <= right_tokens or right_tokens <= left_tokens))

    # -- learning --------------------------------------------------------

    def remember_alias(self, alias: str, canonical: str) -> None:
        rules = self.load()
        if alias.strip() and canonical.strip():
            rules["aliases"][alias.strip()] = canonical.strip()
            self.save()

    def remember_group(
        self,
        canonical: str,
        members: Iterable[str],
        prefixes: Iterable[str] = (),
    ) -> None:
        rules = self.load()
        key = _key(canonical) or canonical.strip()
        group = rules["groups"].setdefault(
            key, {"canonical": canonical.strip(), "members": []}
        )
        group["canonical"] = canonical.strip()
        current = {str(item) for item in group.get("members") or ()}
        current.update(str(item).strip() for item in members if str(item).strip())
        group["members"] = sorted(current, key=str.casefold)
        if prefixes:
            stored = {
                str(item) for item in group.get("reference_prefixes") or ()
            }
            stored.update(
                str(item).strip().upper()
                for item in prefixes
                if str(item).strip()
            )
            group["reference_prefixes"] = sorted(stored)
        self.save()

    def remember_decision(
        self,
        *,
        counterparty: Any,
        document_number: Any = "",
        category: Any = "",
        payment_source: Any = "",
        counterparty_account: Any = "",
        fiscal_year_target: Any = "",
        deferred_month: Any = "",
        report: Any = "",
        decision: Any = "",
        aliases: Iterable[str] = (),
    ) -> None:
        rules = self.load()
        name = str(counterparty or "").strip()
        if name:
            canonical = self.canonical_name(name)
            for alias in aliases:
                if str(alias).strip():
                    rules["aliases"][str(alias).strip()] = canonical
            rules["aliases"].setdefault(name, canonical)
        rules["decisions"].append(
            {
                "counterparty": name,
                "canonical": self.canonical_name(name) if name else "",
                "document_number": str(document_number or ""),
                "category": str(category or ""),
                "payment_source": str(payment_source or ""),
                "counterparty_account": str(counterparty_account or ""),
                "fiscal_year_target": str(fiscal_year_target or ""),
                "deferred_month": str(deferred_month or ""),
                "report": str(report or ""),
                "decision": str(decision or ""),
                "learned_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save()

    def decision_count(self) -> int:
        return len(self.load().get("decisions") or [])


def _name_tokens(value: Any) -> tuple[str, ...]:
    return _tokens(value)


def load_default_memory() -> AccountingMemoryLearningService:
    return AccountingMemoryLearningService()


__all__ = [
    "AccountingMemoryLearningService",
    "DEFAULT_MEMORY_PATH",
    "LearnedDecision",
    "MEMORY_FILENAME",
    "MEMORY_PATH_ENV",
    "load_default_memory",
    "normalize_counterparty_name",
]
