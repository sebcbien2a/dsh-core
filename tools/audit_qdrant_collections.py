"""Audit and sanitize Qdrant collections against the invoice vector contract."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas.vector_payload_schema import CONTRACT_VERSION, InvoiceVectorPayloadV1

logger = logging.getLogger(__name__)

EXPECTED_DIMENSION = 768
BLACKLISTED_COLLECTIONS = ("skills_catalog", "mem0migrations", "mem0_memories")
INVENTORY_COLLECTIONS = (
    "agent_skills",
    "document_bus",
    "invoice_documents",
    "mem0",
    "mem0migrations",
    "research_cache",
    "skills_catalog",
    "teacher_student_metrics",
    "invoices",
    "episodic_memory",
    "mem0_memories",
)
SCROLL_PAGE_SIZE = 256

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CollectionAudit:
    name: str
    exists: bool
    blacklisted: bool
    dimension: int | None
    dimension_ok: bool
    points_count: int
    conforming_count: int
    invalid_points: tuple[str, ...] = ()
    orphan_points: tuple[str, ...] = ()
    missing_tenant: tuple[str, ...] = ()
    invalid_source_sha: tuple[str, ...] = ()
    tenant_leaks: tuple[str, ...] = ()
    duplicate_source_sha256: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    purge_candidates: tuple[str, ...] = ()
    verdict: str = "OK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "exists": self.exists,
            "blacklisted": self.blacklisted,
            "dimension": self.dimension,
            "dimension_ok": self.dimension_ok,
            "points_count": self.points_count,
            "conforming_count": self.conforming_count,
            "invalid_points": list(self.invalid_points),
            "orphan_points": list(self.orphan_points),
            "missing_tenant": list(self.missing_tenant),
            "invalid_source_sha": list(self.invalid_source_sha),
            "tenant_leaks": list(self.tenant_leaks),
            "duplicate_source_sha256": {
                key: list(value)
                for key, value in self.duplicate_source_sha256.items()
            },
            "purge_candidates": list(self.purge_candidates),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class AuditReport:
    generated_at: str
    expected_dimension: int
    tenant_allowlist: tuple[str, ...] | None
    collections: tuple[CollectionAudit, ...]

    def to_dict(self) -> dict[str, Any]:
        non_conforming = sum(
            1
            for collection in self.collections
            if collection.verdict
            in {"NON_CONFORMING", "DIM_MISMATCH", "TENANT_LEAK", "BLACKLISTED"}
        )
        return {
            "generated_at": self.generated_at,
            "contract_version": CONTRACT_VERSION,
            "expected_dimension": self.expected_dimension,
            "tenant_allowlist": (
                list(self.tenant_allowlist)
                if self.tenant_allowlist is not None
                else None
            ),
            "totals": {
                "collections": len(self.collections),
                "present": sum(1 for c in self.collections if c.exists),
                "points": sum(c.points_count for c in self.collections),
                "conforming": sum(c.conforming_count for c in self.collections),
                "non_conforming_collections": non_conforming,
                "purge_candidates": sum(
                    len(c.purge_candidates) for c in self.collections
                ),
            },
            "collections": [collection.to_dict() for collection in self.collections],
        }


@dataclass(frozen=True)
class SanitizationAction:
    collection: str
    points: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"collection": self.collection, "points": list(self.points)}


@dataclass(frozen=True)
class SanitizationPlan:
    applied: bool
    actions: tuple[SanitizationAction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "actions": [action.to_dict() for action in self.actions],
            "total_points": sum(len(action.points) for action in self.actions),
        }


def audit_qdrant_collections(
    client: Any,
    *,
    expected_dim: int = EXPECTED_DIMENSION,
    tenant_allowlist: Sequence[str] | None = None,
) -> AuditReport:
    """Inspect collections without mutating them."""

    allowlist = (
        tuple(tenant_allowlist) if tenant_allowlist is not None else None
    )
    existing = _existing_collection_names(client)
    names = list(existing)
    for name in INVENTORY_COLLECTIONS:
        if name not in names:
            names.append(name)

    audits = tuple(
        _audit_collection(
            client,
            name,
            exists=name in existing,
            expected_dim=expected_dim,
            tenant_allowlist=allowlist,
        )
        for name in names
    )
    return AuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        expected_dimension=expected_dim,
        tenant_allowlist=allowlist,
        collections=audits,
    )


def sanitize_qdrant_collections(
    client: Any,
    report: AuditReport,
    *,
    apply: bool = False,
) -> SanitizationPlan:
    """Delete strictly non-conforming points, or only plan the deletions."""

    actions: list[SanitizationAction] = []
    for audit in report.collections:
        if not audit.purge_candidates:
            continue
        action = SanitizationAction(
            collection=audit.name, points=audit.purge_candidates
        )
        actions.append(action)
        if not apply:
            continue
        points_selector = _point_ids_selector(list(action.points))
        client.delete(
            collection_name=action.collection,
            points_selector=points_selector,
            wait=True,
        )
        logger.info(
            "Deleted %d non-conforming points from %s",
            len(action.points),
            action.collection,
        )
    return SanitizationPlan(applied=apply, actions=tuple(actions))


def _audit_collection(
    client: Any,
    name: str,
    *,
    exists: bool,
    expected_dim: int,
    tenant_allowlist: tuple[str, ...] | None,
) -> CollectionAudit:
    blacklisted = name in BLACKLISTED_COLLECTIONS
    if not exists:
        return CollectionAudit(
            name=name,
            exists=False,
            blacklisted=blacklisted,
            dimension=None,
            dimension_ok=False,
            points_count=0,
            conforming_count=0,
            verdict="ABSENT",
        )

    info = client.get_collection(name)
    dimension = _vector_size(info)
    dimension_ok = dimension == expected_dim
    points = _scroll_points(client, name)

    invalid: list[str] = []
    orphans: list[str] = []
    missing_tenant: list[str] = []
    invalid_sha: list[str] = []
    tenant_leaks: list[str] = []
    duplicates: dict[str, list[str]] = {}
    conforming = 0
    point_ids: list[str] = []

    for point in points:
        point_id = str(point.id)
        point_ids.append(point_id)
        payload = point.payload if isinstance(point.payload, Mapping) else {}
        contract_version = payload.get("contract_version")
        tenant_id = payload.get("tenant_id")
        source_sha = payload.get("source_sha256")

        if contract_version != CONTRACT_VERSION:
            orphans.append(point_id)
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            missing_tenant.append(point_id)
        elif tenant_allowlist is not None and tenant_id not in tenant_allowlist:
            tenant_leaks.append(point_id)
        if not isinstance(source_sha, str) or not _SHA256_PATTERN.fullmatch(
            source_sha
        ):
            invalid_sha.append(point_id)
        else:
            duplicates.setdefault(source_sha, []).append(point_id)

        try:
            InvoiceVectorPayloadV1.model_validate(dict(payload))
        except Exception:
            invalid.append(point_id)
        else:
            conforming += 1

    duplicate_groups = {
        sha: tuple(ids) for sha, ids in duplicates.items() if len(ids) > 1
    }
    purge_candidates = (
        tuple(point_ids) if blacklisted else tuple(invalid)
    )
    verdict = _verdict(
        blacklisted=blacklisted,
        dimension_ok=dimension_ok,
        invalid=invalid,
        tenant_leaks=tenant_leaks,
    )

    return CollectionAudit(
        name=name,
        exists=True,
        blacklisted=blacklisted,
        dimension=dimension,
        dimension_ok=dimension_ok,
        points_count=len(points),
        conforming_count=conforming,
        invalid_points=tuple(invalid),
        orphan_points=tuple(orphans),
        missing_tenant=tuple(missing_tenant),
        invalid_source_sha=tuple(invalid_sha),
        tenant_leaks=tuple(tenant_leaks),
        duplicate_source_sha256=duplicate_groups,
        purge_candidates=purge_candidates,
        verdict=verdict,
    )


def _verdict(
    *,
    blacklisted: bool,
    dimension_ok: bool,
    invalid: Sequence[str],
    tenant_leaks: Sequence[str],
) -> str:
    if blacklisted:
        return "BLACKLISTED"
    if not dimension_ok:
        return "DIM_MISMATCH"
    if invalid:
        return "NON_CONFORMING"
    if tenant_leaks:
        return "TENANT_LEAK"
    return "OK"


def _existing_collection_names(client: Any) -> set[str]:
    response = client.get_collections()
    collections = getattr(response, "collections", response)
    names: set[str] = set()
    for collection in collections:
        name = getattr(collection, "name", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _vector_size(info: Any) -> int | None:
    vectors = getattr(
        getattr(getattr(info, "config", None), "params", None), "vectors", None
    )
    size = getattr(vectors, "size", None)
    if size is None and isinstance(vectors, Mapping):
        size = next(
            (getattr(value, "size", None) for value in vectors.values()), None
        )
    return size


def _scroll_points(client: Any, name: str) -> list[Any]:
    points: list[Any] = []
    offset = None
    while True:
        batch, next_offset = client.scroll(
            collection_name=name,
            limit=SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if next_offset is None or next_offset == offset:
            break
        offset = next_offset
    return points


def _point_ids_selector(point_ids: list[str]) -> Any:
    from qdrant_client.models import PointIdsList

    return PointIdsList(points=point_ids)


def _build_client(url: str) -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(url=url, timeout=5.0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit and sanitize Qdrant collections for DSH-Core."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant endpoint (default: QDRANT_URL or localhost).",
    )
    parser.add_argument(
        "--expected-dim",
        type=int,
        default=EXPECTED_DIMENSION,
        help="Expected dense vector dimension.",
    )
    parser.add_argument(
        "--tenant-allowlist",
        default=None,
        help="Comma separated tenant_id values allowed to exist.",
    )
    parser.add_argument(
        "--out-dir",
        default="workspace/qdrant/audit",
        help="Directory receiving the JSON audit report.",
    )
    parser.add_argument(
        "--sanitize",
        action="store_true",
        help="Build a sanitization plan (dry-run unless --apply is set).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the sanitization plan (requires --sanitize).",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.sanitize:
        parser.error("--apply requires --sanitize")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    allowlist = None
    if args.tenant_allowlist:
        allowlist = tuple(
            value.strip()
            for value in args.tenant_allowlist.split(",")
            if value.strip()
        )

    try:
        client = _build_client(args.url)
    except Exception as exc:
        logger.error("Qdrant client unavailable: %s", exc)
        return 2

    try:
        report = audit_qdrant_collections(
            client,
            expected_dim=args.expected_dim,
            tenant_allowlist=allowlist,
        )
        plan = (
            sanitize_qdrant_collections(client, report, apply=args.apply)
            if args.sanitize
            else None
        )
    except Exception as exc:
        logger.error("Qdrant audit failed: %s", exc)
        return 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    payload: dict[str, Any] = {"report": report.to_dict()}
    if plan is not None:
        payload["sanitization"] = plan.to_dict()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"qdrant-audit-{stamp}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    totals = payload["report"]["totals"]
    logger.info(
        "Collections=%d present=%d points=%d conforming=%d purge_candidates=%d",
        totals["collections"],
        totals["present"],
        totals["points"],
        totals["conforming"],
        totals["purge_candidates"],
    )
    if plan is not None:
        logger.info(
            "Sanitization mode=%s planned_points=%d",
            "apply" if plan.applied else "dry-run",
            plan.to_dict()["total_points"],
        )
    logger.info("Report written to %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
