"""Deterministic tests for the Qdrant collection audit and sanitization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tools.audit_qdrant_collections import (
    audit_qdrant_collections,
    sanitize_qdrant_collections,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


class FakeQdrantClient:
    def __init__(self, collections: dict[str, tuple[int, list[Any]]]) -> None:
        self._collections = collections
        self.deleted: list[tuple[str, Any]] = []

    def get_collections(self) -> Any:
        return SimpleNamespace(
            collections=[
                SimpleNamespace(name=name) for name in self._collections
            ]
        )

    def get_collection(self, name: str) -> Any:
        dimension, _ = self._collections[name]
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=SimpleNamespace(size=dimension)
                )
            )
        )

    def scroll(
        self,
        *,
        collection_name: str,
        limit: int,
        offset: Any,
        with_payload: bool,
        with_vectors: bool,
    ) -> tuple[list[Any], None]:
        return list(self._collections[collection_name][1]), None

    def delete(
        self,
        *,
        collection_name: str,
        points_selector: Any,
        wait: bool,
    ) -> None:
        self.deleted.append((collection_name, points_selector))


def _point(point_id: str, payload: dict[str, Any]) -> Any:
    return SimpleNamespace(id=point_id, payload=payload)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": "1.0.1",
        "tenant_id": "T1",
        "source_sha256": SHA_A,
        "doc_type": "INVOICE",
        "invoice_id": "INV-1",
        "company_id": "COMPANY-1",
        "currency": "EUR",
        "direction": "PURCHASE",
        "document_type": "INVOICE",
        "validation_status": "VALIDATED",
        "rule": {},
        "text": "Facture",
    }
    payload.update(overrides)
    return payload


def _collection(report: Any, name: str) -> Any:
    return next(item for item in report.collections if item.name == name)


def test_audit_reports_invalid_orphan_and_tenantless_points() -> None:
    client = FakeQdrantClient(
        {
            "invoices": (
                768,
                [
                    _point("p1", _payload()),
                    _point("p2", _payload(source_sha256=SHA_B)),
                    _point("p3", _payload(contract_version=None)),
                    _point("p4", _payload(tenant_id=None)),
                ],
            )
        }
    )

    report = audit_qdrant_collections(client, expected_dim=768)
    audit = _collection(report, "invoices")

    assert audit.exists is True
    assert audit.points_count == 4
    assert audit.conforming_count == 2
    assert set(audit.invalid_points) == {"p3", "p4"}
    assert set(audit.orphan_points) == {"p3"}
    assert set(audit.missing_tenant) == {"p4"}
    assert audit.verdict == "NON_CONFORMING"
    assert set(audit.purge_candidates) == {"p3", "p4"}
    assert report.to_dict()["totals"]["present"] == 1
    assert report.to_dict()["totals"]["points"] == 4
    assert report.to_dict()["totals"]["conforming"] == 2


def test_audit_detects_dimension_mismatch() -> None:
    client = FakeQdrantClient({"invoices": (384, [_point("p1", _payload())])})

    report = audit_qdrant_collections(client, expected_dim=768)
    audit = _collection(report, "invoices")

    assert audit.dimension == 384
    assert audit.dimension_ok is False
    assert audit.verdict == "DIM_MISMATCH"


def test_sanitize_dry_run_never_writes() -> None:
    client = FakeQdrantClient(
        {
            "invoices": (
                768,
                [
                    _point("p1", _payload()),
                    _point("p2", _payload(contract_version=None)),
                ],
            )
        }
    )

    report = audit_qdrant_collections(client)
    plan = sanitize_qdrant_collections(client, report, apply=False)

    assert plan.applied is False
    assert client.deleted == []
    actions = {action.collection: action.points for action in plan.actions}
    assert set(actions["invoices"]) == {"p2"}


def test_sanitize_apply_deletes_only_non_conforming_points() -> None:
    client = FakeQdrantClient(
        {
            "invoices": (
                768,
                [
                    _point("p1", _payload()),
                    _point("p2", _payload(contract_version=None)),
                    _point("p3", _payload(tenant_id=None)),
                ],
            ),
            "skills_catalog": (768, [_point("old", _payload())]),
        }
    )

    report = audit_qdrant_collections(client)
    plan = sanitize_qdrant_collections(client, report, apply=True)

    assert plan.applied is True
    deleted = {name: set(selector.points) for name, selector in client.deleted}
    assert deleted["invoices"] == {"p2", "p3"}
    assert deleted["skills_catalog"] == {"old"}
    assert "p1" not in deleted["invoices"]


def test_audit_flags_tenant_leak_outside_allowlist() -> None:
    client = FakeQdrantClient(
        {
            "invoices": (
                768,
                [
                    _point("p1", _payload(tenant_id="T1")),
                    _point("p2", _payload(tenant_id="T2")),
                ],
            )
        }
    )

    report = audit_qdrant_collections(
        client, expected_dim=768, tenant_allowlist=("T1",)
    )
    audit = _collection(report, "invoices")

    assert audit.tenant_leaks == ("p2",)
    assert audit.verdict == "TENANT_LEAK"
    assert audit.purge_candidates == ()


def test_audit_reports_duplicate_source_sha256() -> None:
    client = FakeQdrantClient(
        {
            "invoices": (
                768,
                [
                    _point("p1", _payload()),
                    _point("p2", _payload(invoice_id="INV-2")),
                ],
            )
        }
    )

    report = audit_qdrant_collections(client)
    audit = _collection(report, "invoices")

    assert audit.duplicate_source_sha256 == {SHA_A: ("p1", "p2")}


def test_audit_marks_absent_inventory_collections() -> None:
    client = FakeQdrantClient({})

    report = audit_qdrant_collections(client)
    audit = _collection(report, "invoices")

    assert audit.exists is False
    assert audit.verdict == "ABSENT"
    assert audit.points_count == 0
