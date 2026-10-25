"""Isolated local JSON repository keyed by SupplierIdentity.supplier_id.

Independent from the legacy SIRET-indexed repository. Nothing here deduces
identifiers, merges suppliers, accesses the network or computes amounts.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import UUID

from schemas.supplier_identity_schema import SupplierIdentity

CONFIRMED_IDENTIFIER_COLLISION = "CONFIRMED_IDENTIFIER_COLLISION"
_FORMAT_VERSION = 1

IdentifierKey = tuple[str | None, str, str]


class SupplierIdentityRepositoryError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


class SupplierIdentityRepository:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    # --- persistence -------------------------------------------------

    def _load(self) -> dict[str, SupplierIdentity]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            records = payload["suppliers"]
            if not isinstance(records, dict):
                raise TypeError("suppliers must be an object")
            suppliers: dict[str, SupplierIdentity] = {}
            for key, record in records.items():
                identity = SupplierIdentity.model_validate(record)
                if str(identity.supplier_id) != key:
                    raise ValueError("storage key differs from supplier_id")
                suppliers[key] = identity
            return suppliers
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SupplierIdentityRepositoryError("INVALID_STORAGE", str(exc)) from exc

    def _save(self, suppliers: dict[str, SupplierIdentity]) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "suppliers": {
                key: identity.model_dump(mode="json") for key, identity in suppliers.items()
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        backup = self._path.with_name(self._path.name + ".bak")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        if self._path.exists():
            shutil.copy2(self._path, backup)
        os.replace(tmp, self._path)

    # --- identifier index --------------------------------------------

    @staticmethod
    def _confirmed_keys(identity: SupplierIdentity) -> set[IdentifierKey]:
        return {
            (item.country, item.identifier_type, item.normalized_value)
            for item in identity.identifiers
            if item.human_confirmed and item.normalized_value
        }

    def _check_collisions(
        self, suppliers: dict[str, SupplierIdentity], candidate: SupplierIdentity
    ) -> None:
        if not candidate.active:
            return
        mine = self._confirmed_keys(candidate)
        if not mine:
            return
        for key, other in suppliers.items():
            if key == str(candidate.supplier_id) or not other.active:
                continue
            shared = mine & self._confirmed_keys(other)
            if shared:
                country, id_type, value = sorted(shared, key=repr)[0]
                raise SupplierIdentityRepositoryError(
                    CONFIRMED_IDENTIFIER_COLLISION,
                    f"{country or '-'}/{id_type}/{value} already used by {other.supplier_id}",
                )

    @staticmethod
    def _validated(identity: SupplierIdentity) -> SupplierIdentity:
        return SupplierIdentity.model_validate(identity.model_dump(mode="json"))

    # --- public API ---------------------------------------------------

    def create(self, identity: SupplierIdentity) -> SupplierIdentity:
        identity = self._validated(identity)
        suppliers = self._load()
        key = str(identity.supplier_id)
        if key in suppliers:
            raise SupplierIdentityRepositoryError("SUPPLIER_ALREADY_EXISTS", key)
        self._check_collisions(suppliers, identity)
        suppliers[key] = identity
        self._save(suppliers)
        return identity

    def get(self, supplier_id: UUID | str) -> SupplierIdentity | None:
        return self._load().get(str(UUID(str(supplier_id))))

    def update(self, identity: SupplierIdentity) -> SupplierIdentity:
        identity = self._validated(identity)
        suppliers = self._load()
        key = str(identity.supplier_id)
        if key not in suppliers:
            raise SupplierIdentityRepositoryError("SUPPLIER_NOT_FOUND", key)
        self._check_collisions(suppliers, identity)
        suppliers[key] = identity
        self._save(suppliers)
        return identity

    def deactivate(self, supplier_id: UUID | str) -> SupplierIdentity:
        suppliers = self._load()
        key = str(UUID(str(supplier_id)))
        if key not in suppliers:
            raise SupplierIdentityRepositoryError("SUPPLIER_NOT_FOUND", key)
        suppliers[key] = suppliers[key].model_copy(update={"active": False})
        self._save(suppliers)
        return suppliers[key]

    def list_active(self) -> list[SupplierIdentity]:
        return [item for item in self._load().values() if item.active]

    def find_by_confirmed_identifier(
        self, identifier_type: str, normalized_value: str, country: str | None = None
    ) -> SupplierIdentity | None:
        wanted: IdentifierKey = (
            country.strip().upper() if country else None,
            identifier_type.strip().upper(),
            normalized_value.strip(),
        )
        for identity in self._load().values():
            if identity.active and wanted in self._confirmed_keys(identity):
                return identity
        return None

    def suggest_by_name(self, name: str) -> list[SupplierIdentity]:
        """Read-only candidates by case-insensitive name equality; never merges."""
        wanted = name.strip().casefold()
        if not wanted:
            return []
        return [
            identity
            for identity in self._load().values()
            if identity.active
            and any(candidate.strip().casefold() == wanted for candidate in identity.all_names())
        ]
