"""Load approved Obsidian rules from Markdown YAML frontmatter.

The registry keeps an in-memory index of approved rules and supports hot reload
of added, modified and deleted Markdown files through file mtime fingerprints.
No graph, vector or relational database dependency is introduced at this stage.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

import yaml
from pydantic import ValidationError

from schemas.rule_registry_schema import BusinessRuleModel, RuleStatus

logger = logging.getLogger(__name__)

_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)

_NO_FRONTMATTER = object()

_TENANT_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")


class RuleRegistryError(ValueError):
    """Raised when a rules directory or an approved rule is invalid."""

    def __init__(self, code: str, message: str = "", *, path: Path | None = None) -> None:
        detail = f"{code}: {message}" if message else code
        if path is not None:
            detail = f"{detail} [{path}]"
        super().__init__(detail)
        self.code = code
        self.path = path


class RuleRegistryService:
    """In-memory registry of approved rules loaded from Obsidian Markdown files."""

    def __init__(self) -> None:
        self._rules: dict[str, BusinessRuleModel] = {}
        self._directory: Path | None = None
        self._file_fingerprints: dict[Path, float] = {}
        self._path_to_rule_id: dict[Path, str] = {}
        self._ignored_rules: dict[Path, object] = {}
        self._tenant_base: Path | None = None
        self._common_layer: RuleRegistryService = None  # type: ignore[assignment]
        self._tenant_layers: dict[str, RuleRegistryService] = {}

    @property
    def ignored_rules(self) -> Mapping[Path, object]:
        """Read-only view of rejected rule files mapped to their raw status."""
        return MappingProxyType(dict(self._ignored_rules))

    def load_rules(self, directory: Path) -> list[BusinessRuleModel]:
        """Parse every Markdown rule under ``directory`` and replace the registry.

        Files without frontmatter are ignored. Rules whose raw status is not
        exactly ``approved`` are ignored with a warning and recorded in
        :attr:`ignored_rules` for audit purposes; their frontmatter must still
        be parseable YAML (a draft with broken YAML raises ``INVALID_YAML``).
        Approved rules are validated with ``BusinessRuleModel``; duplicates and
        malformed approved rules raise ``RuleRegistryError``. The scan state is
        committed atomically only on success so that
        :meth:`reload_changed_rules` can later compute deltas.
        """
        root = Path(directory)
        if not root.is_dir():
            raise RuleRegistryError("RULES_DIRECTORY_NOT_FOUND", str(root))

        fingerprints = self._scan_fingerprints(root)
        loaded: dict[str, BusinessRuleModel] = {}
        path_to_rule_id: dict[Path, str] = {}
        ignored: dict[Path, object] = {}
        for path in sorted(root.rglob("*.md")):
            rule, raw_status = self._load_single(path)
            if rule is None:
                if raw_status is not _NO_FRONTMATTER:
                    ignored[path] = raw_status
                continue
            if rule.rule_id in loaded:
                raise RuleRegistryError(
                    "DUPLICATE_RULE_ID",
                    f"rule_id {rule.rule_id!r} is defined more than once",
                    path=path,
                )
            loaded[rule.rule_id] = rule
            path_to_rule_id[path] = rule.rule_id

        self._rules = loaded
        self._directory = root
        self._file_fingerprints = fingerprints
        self._path_to_rule_id = path_to_rule_id
        self._ignored_rules = ignored
        return self._ordered(loaded.values())

    def reload_changed_rules(self) -> tuple[int, int, int]:
        """Reload only added, modified and deleted Markdown files.

        Returns ``(added, modified, deleted)`` counts of Markdown *files* in the
        delta, including files rejected because of their status and files
        without frontmatter. Only files in the delta are re-parsed. The registry
        state, ignored-rule tracking and fingerprints are committed atomically:
        if any file fails to parse or validate, nothing changes and the
        exception propagates. When the delta is empty, ``(0, 0, 0)`` is
        returned without touching the state.
        """
        if self._directory is None:
            raise RuleRegistryError(
                "RULES_NOT_LOADED",
                "load_rules() must be called before reload_changed_rules()",
            )
        root = self._directory
        if not root.is_dir():
            raise RuleRegistryError("RULES_DIRECTORY_NOT_FOUND", str(root))

        fingerprints = self._scan_fingerprints(root)
        previous = self._file_fingerprints
        added = set(fingerprints) - set(previous)
        deleted = set(previous) - set(fingerprints)
        modified = {
            path
            for path in set(fingerprints) & set(previous)
            if fingerprints[path] != previous[path]
        }
        if not added and not modified and not deleted:
            return (0, 0, 0)

        rules = dict(self._rules)
        path_to_rule_id = dict(self._path_to_rule_id)
        ignored = dict(self._ignored_rules)

        for path in added | modified | deleted:
            rule_id = path_to_rule_id.pop(path, None)
            if rule_id is not None:
                rules.pop(rule_id, None)
            ignored.pop(path, None)

        for path in sorted(added | modified):
            rule, raw_status = self._load_single(path)
            if rule is None:
                if raw_status is not _NO_FRONTMATTER:
                    ignored[path] = raw_status
                continue
            if rule.rule_id in rules:
                raise RuleRegistryError(
                    "DUPLICATE_RULE_ID",
                    f"rule_id {rule.rule_id!r} is defined more than once",
                    path=path,
                )
            rules[rule.rule_id] = rule
            path_to_rule_id[path] = rule.rule_id

        self._rules = rules
        self._path_to_rule_id = path_to_rule_id
        self._ignored_rules = ignored
        self._file_fingerprints = fingerprints
        return (len(added), len(modified), len(deleted))

    # ------------------------------------------------------------------
    # Multi-tenant hierarchy: <base>/common + <base>/tenants/<tenant_id>
    # ------------------------------------------------------------------

    def load_tenant_rules(
        self, base_directory: Path, tenant_id: str
    ) -> list[BusinessRuleModel]:
        """Load the common rules and the private rules of one tenant.

        ``<base>/common`` is the shared baseline and ``<base>/tenants/<id>``
        holds the tenant-specific rules; a tenant rule sharing a ``rule_id``
        with a common rule overrides it for that tenant only. Missing common or
        tenant directories are tolerated (empty layer). Other tenants' state is
        never touched, except that a different ``base_directory`` resets them.
        Returns the effective rules of the tenant.
        """
        self._validate_tenant_id(tenant_id)
        base = Path(base_directory)
        if not base.is_dir():
            raise RuleRegistryError("RULES_DIRECTORY_NOT_FOUND", str(base))

        if self._tenant_base != base:
            self._tenant_base = base
            self._tenant_layers = {}
            self._common_layer = RuleRegistryService()
        if self._common_layer._directory is None:
            self._load_layer(self._common_layer, base / "common")

        layer = RuleRegistryService()
        self._load_layer(layer, base / "tenants" / tenant_id)
        self._tenant_layers[tenant_id] = layer
        return self.get_effective_rules(tenant_id)

    def reload_tenant_rules(self, tenant_id: str) -> tuple[int, int, int]:
        """Hot-reload only the private directory of ``tenant_id``.

        Neither the common layer nor any other tenant is re-scanned. Returns
        ``(added, modified, deleted)`` file counts for that tenant.
        """
        layer = self._require_tenant(tenant_id)
        assert self._tenant_base is not None
        directory = self._tenant_base / "tenants" / tenant_id
        if layer._directory is None:
            if not directory.is_dir():
                return (0, 0, 0)
            fresh = RuleRegistryService()
            fresh.load_rules(directory)
            self._tenant_layers[tenant_id] = fresh
            return (len(fresh._file_fingerprints), 0, 0)
        if not directory.is_dir():
            deleted = len(layer._file_fingerprints)
            self._tenant_layers[tenant_id] = RuleRegistryService()
            return (0, 0, deleted)
        return layer.reload_changed_rules()

    def reload_common_rules(self) -> tuple[int, int, int]:
        """Hot-reload the common layer only (affects every tenant)."""
        if self._tenant_base is None:
            raise RuleRegistryError(
                "RULES_NOT_LOADED", "load_tenant_rules() must be called first"
            )
        common = self._tenant_base / "common"
        layer = self._common_layer
        if layer._directory is None:
            if not common.is_dir():
                return (0, 0, 0)
            self._load_layer(layer, common)
            return (len(layer._file_fingerprints), 0, 0)
        if not common.is_dir():
            deleted = len(layer._file_fingerprints)
            self._common_layer = RuleRegistryService()
            return (0, 0, deleted)
        return layer.reload_changed_rules()

    def get_effective_rules(self, tenant_id: str) -> list[BusinessRuleModel]:
        """Common rules overridden by the tenant's private rules, ordered."""
        return self._ordered(self._effective(tenant_id).values())

    def unload_tenant(self, tenant_id: str) -> None:
        self._validate_tenant_id(tenant_id)
        self._tenant_layers.pop(tenant_id, None)

    @property
    def loaded_tenants(self) -> frozenset[str]:
        return frozenset(self._tenant_layers)

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> str:
        if (
            not isinstance(tenant_id, str)
            or tenant_id in (".", "..")
            or _TENANT_ID.fullmatch(tenant_id) is None
        ):
            raise RuleRegistryError("INVALID_TENANT_ID", repr(tenant_id))
        return tenant_id

    @staticmethod
    def _load_layer(layer: "RuleRegistryService", directory: Path) -> None:
        if directory.is_dir():
            layer.load_rules(directory)

    def _require_tenant(self, tenant_id: str) -> "RuleRegistryService":
        self._validate_tenant_id(tenant_id)
        layer = self._tenant_layers.get(tenant_id)
        if layer is None:
            raise RuleRegistryError("TENANT_NOT_LOADED", tenant_id)
        return layer

    def _effective(self, tenant_id: str | None) -> dict[str, BusinessRuleModel]:
        if tenant_id is None:
            return self._rules
        layer = self._require_tenant(tenant_id)
        merged = dict(self._common_layer._rules)
        merged.update(layer._rules)
        return merged

    def get_rule(
        self, rule_id: str, tenant_id: str | None = None
    ) -> BusinessRuleModel | None:
        return self._effective(tenant_id).get(rule_id)

    def get_rules_by_domain(
        self, domain: str, tenant_id: str | None = None
    ) -> list[BusinessRuleModel]:
        key = domain.strip().casefold()
        return self._ordered(
            rule
            for rule in self._effective(tenant_id).values()
            if rule.domain.casefold() == key
        )

    def get_applicable_rules(
        self, node_name: str, tenant_id: str | None = None
    ) -> list[BusinessRuleModel]:
        node = node_name.strip()
        return self._ordered(
            rule
            for rule in self._effective(tenant_id).values()
            if node in rule.applies_to_nodes
        )

    def _load_single(
        self, path: Path
    ) -> tuple[BusinessRuleModel | None, object]:
        """Parse one Markdown file, returning the rule or its rejected status.

        The second element is :data:`_NO_FRONTMATTER` when the file carries no
        frontmatter, the raw status when the file is rejected for its status,
        and ``None`` when a rule was successfully loaded.
        """
        frontmatter = self._extract_frontmatter(path)
        if frontmatter is None:
            return None, _NO_FRONTMATTER
        data = self._parse_yaml(frontmatter, path)
        if not isinstance(data, dict):
            raise RuleRegistryError(
                "INVALID_FRONTMATTER",
                "YAML frontmatter must be a mapping",
                path=path,
            )
        raw_status = data.get("status")
        if raw_status != RuleStatus.APPROVED.value:
            rule_id = data.get("rule_id")
            label = rule_id if isinstance(rule_id, str) else path.stem
            logger.warning(
                "Ignoring rule %s: status %r is not exactly 'approved' (%s)",
                label,
                raw_status,
                path,
            )
            return None, raw_status
        try:
            rule = BusinessRuleModel.model_validate(data)
        except ValidationError as exc:
            raise RuleRegistryError("INVALID_RULE", str(exc), path=path) from exc
        return rule, None

    @staticmethod
    def _scan_fingerprints(root: Path) -> dict[Path, float]:
        return {path: path.stat().st_mtime for path in root.rglob("*.md")}

    @staticmethod
    def _extract_frontmatter(path: Path) -> str | None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RuleRegistryError(
                "INVALID_ENCODING", "rule file is not valid UTF-8", path=path
            ) from exc
        content = text.lstrip()
        if not content.startswith("---"):
            return None
        match = _FRONTMATTER.match(content)
        if match is None:
            raise RuleRegistryError(
                "INVALID_FRONTMATTER",
                "frontmatter must start with '---' and end with '---'",
                path=path,
            )
        return match.group("body")

    @staticmethod
    def _parse_yaml(frontmatter: str, path: Path) -> object:
        try:
            return yaml.safe_load(frontmatter)
        except (yaml.YAMLError, ValueError) as exc:
            # PyYAML leaks ValueError for impossible timestamps (e.g. 2024-02-30).
            raise RuleRegistryError("INVALID_YAML", str(exc), path=path) from exc

    @staticmethod
    def _ordered(rules: Iterable[BusinessRuleModel]) -> list[BusinessRuleModel]:
        return sorted(rules, key=lambda rule: (rule.priority, rule.rule_id))
