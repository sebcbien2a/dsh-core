"""Multi-tenant isolation harness (P10.1), fully offline.

Contract v1.0.2: ``invoice_id``, ``point_id`` and ``line_id`` are derived from
``tenant_id`` + ``source_sha256`` and an invalid ``tenant_id`` is rejected
before any Neo4j or Qdrant write.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest
from pydantic import ValidationError

from schemas.invoice_schema import InvoiceSchema
from schemas.vector_payload_schema import InvoiceVectorPayloadV1
from services.persistence_manager import (
    Neo4jGraphStore,
    PersistenceManager,
    QdrantVectorStore,
)
from services.rule_registry_service import RuleRegistryService

SHA = "a" * 64
TENANTS_20 = [f"CLIENT_{index:02d}" for index in range(1, 21)]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class GraphSpy:
    def __init__(self) -> None:
        self.records: list[Any] = []

    def upsert_invoice(self, record) -> None:
        self.records.append(record)


class VectorSpy:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, dict[str, Any]]] = []

    def ensure_collection(self, vector_size: int) -> None:
        pass

    def upsert(self, point_id, vector, payload) -> None:
        self.upserts.append((point_id, dict(payload)))


def _embedder(text: str) -> list[float]:
    return [0.1, 0.2, 0.3, 0.4]


def _invoice(
    *,
    company_id: str = "COMPANY-1",
    supplier: str = "DUPONT SARL",
    number: str = "F-001",
) -> InvoiceSchema:
    return InvoiceSchema.model_validate(
        {
            "document_type": "INVOICE",
            "direction": "PURCHASE",
            "company_id": company_id,
            "supplier": {"name": supplier, "siret": "40138136300019"},
            "customer": {"name": "CLIENT BETA"},
            "reference": {"invoice_number": number, "issue_date": "2026-10-01"},
            "lines": [
                {
                    "description": "Peinture",
                    "quantite": "2",
                    "prix_unitaire_ht": "30.00",
                    "total_ht": "60.00",
                    "taux_tva": "20",
                }
            ],
            "amount": {
                "currency": "EUR",
                "net_amount": "60.00",
                "tax_amount": "12.00",
                "gross_amount": "72.00",
            },
            "metadata": {},
            "validation_status": "VALIDATED",
        }
    )


def _manager(graph=None, vector=None) -> PersistenceManager:
    return PersistenceManager(
        graph_store=graph, vector_store=vector, embedder=_embedder
    )


def _persist_same_invoice_for_two_tenants():
    graph, vector = GraphSpy(), VectorSpy()
    manager = _manager(graph, vector)
    results = [
        manager.persist_invoice(_invoice(), source_sha256=SHA, tenant_id=tenant)
        for tenant in ("TENANT_A", "TENANT_B")
    ]
    return graph, vector, results


# ------------------------------------------- A / B isolation (same invoice)


def test_same_invoice_payloads_carry_their_own_tenant():
    _, vector, results = _persist_same_invoice_for_two_tenants()
    assert [r.vector_status for r in results] == ["INDEXED", "INDEXED"]
    tenants = [payload["tenant_id"] for _, payload in vector.upserts]
    assert tenants == ["TENANT_A", "TENANT_B"]
    for _, payload in vector.upserts:
        InvoiceVectorPayloadV1.model_validate(payload)


def test_homonym_supplier_gets_distinct_ids_per_tenant():
    graph, vector, _ = _persist_same_invoice_for_two_tenants()
    a, b = graph.records
    assert a.supplier_properties["name"] == b.supplier_properties["name"]
    assert a.supplier_id != b.supplier_id
    payload_supplier_ids = [p["supplier_id"] for _, p in vector.upserts]
    assert payload_supplier_ids == [a.supplier_id, b.supplier_id]


def test_graph_records_are_bound_to_their_tenant():
    graph, _, _ = _persist_same_invoice_for_two_tenants()
    assert [r.tenant_id for r in graph.records] == ["TENANT_A", "TENANT_B"]


def test_cypher_parameters_are_scoped_per_tenant():
    graph, _, _ = _persist_same_invoice_for_two_tenants()
    built = [Neo4jGraphStore.build_upsert(r) for r in graph.records]
    (query_a, params_a), (query_b, params_b) = built
    assert query_a == query_b
    assert "$tenant_id" in query_a
    assert "TENANT_A" not in query_a and "TENANT_B" not in query_a
    assert params_a["tenant_id"] == "TENANT_A"
    assert params_b["tenant_id"] == "TENANT_B"
    assert params_a["supplier_id"] != params_b["supplier_id"]
    assert "TENANT_B" not in json.dumps(params_a, default=str)
    assert "TENANT_A" not in json.dumps(params_b, default=str)


def test_cypher_links_invoice_to_tenant_via_owns():
    graph, _, _ = _persist_same_invoice_for_two_tenants()
    query, _ = Neo4jGraphStore.build_upsert(graph.records[0])
    assert "MERGE (t)-[:OWNS]->(i)" in query


def test_tenant_a_data_never_appears_in_tenant_b_payload():
    _, vector, _ = _persist_same_invoice_for_two_tenants()
    blob_b = json.dumps(vector.upserts[1][1], default=str)
    assert "TENANT_A" not in blob_b


def test_same_invoice_must_not_share_qdrant_point_id():
    _, vector, _ = _persist_same_invoice_for_two_tenants()
    assert vector.upserts[0][0] != vector.upserts[1][0]


def test_same_file_must_not_share_neo4j_invoice_key():
    graph, _, _ = _persist_same_invoice_for_two_tenants()
    query, params = Neo4jGraphStore.build_upsert(graph.records[0])
    assert "MERGE (i:Invoice {invoice_id: $invoice_id})" in query
    ids = [Neo4jGraphStore.build_upsert(r)[1]["invoice_id"] for r in graph.records]
    assert ids[0] != ids[1]


def test_same_file_must_not_share_line_ids():
    graph, _, _ = _persist_same_invoice_for_two_tenants()
    lines_a = {line.line_id for line in graph.records[0].line_items}
    lines_b = {line.line_id for line in graph.records[1].line_items}
    assert lines_a.isdisjoint(lines_b)


# ------------------------------------------------- tenant_id validation


@pytest.mark.parametrize(
    "bad",
    ["", " ", "   ", "A B", "a/b", "a;b", "x" * 65, "é", "t\n", "a\x00", "'; DROP"],
)
def test_schema_rejects_empty_or_malformed_tenant(bad):
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(_valid_payload(tenant_id=bad))


def test_schema_rejects_missing_tenant():
    payload = _valid_payload()
    del payload["tenant_id"]
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(payload)


@pytest.mark.parametrize("bad", [None, 12, ["A"], {"a": 1}])
def test_schema_rejects_non_string_tenant(bad):
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(_valid_payload(tenant_id=bad))


@pytest.mark.parametrize("good", ["A", "TENANT_A", "client-01", "a.b:c", "x" * 64])
def test_schema_accepts_well_formed_tenant(good):
    assert InvoiceVectorPayloadV1.model_validate(
        _valid_payload(tenant_id=good)
    ).tenant_id == good


def test_schema_forbids_extra_cross_tenant_fields():
    with pytest.raises(ValidationError):
        InvoiceVectorPayloadV1.model_validate(
            _valid_payload(tenant_scope="*")
        )


@pytest.mark.parametrize("bad", [" ", "A B", "a/b", "x" * 65])
def test_manager_never_indexes_malformed_tenant(bad):
    vector = VectorSpy()
    result = _manager(None, vector).persist_invoice(
        _invoice(), source_sha256=SHA, tenant_id=bad
    )
    assert result.vector_status == "FAILED"
    assert vector.upserts == []


@pytest.mark.parametrize("bad", ["A B", "a/b", "x" * 65])
def test_manager_never_writes_graph_for_malformed_tenant(bad):
    graph = GraphSpy()
    _manager(graph, None).persist_invoice(
        _invoice(), source_sha256=SHA, tenant_id=bad
    )
    assert graph.records == []


def test_explicit_empty_tenant_is_rejected():
    vector = VectorSpy()
    result = _manager(None, vector).persist_invoice(
        _invoice(), source_sha256=SHA, tenant_id=""
    )
    assert result.vector_status == "FAILED"


@pytest.mark.parametrize("bad", ["", " ", "A B", "a/b", "x" * 65, "t\n"])
def test_invalid_explicit_tenant_is_rejected_before_any_write(bad):
    graph, vector = GraphSpy(), VectorSpy()
    result = _manager(graph, vector).persist_invoice(
        _invoice(), source_sha256=SHA, tenant_id=bad
    )
    assert (result.graph_status, result.vector_status) == ("FAILED", "FAILED")
    assert result.errors and result.errors[0].startswith("tenant:")
    assert graph.records == [] and vector.upserts == []


def test_invalid_tenant_rejected_even_without_stores():
    result = _manager(None, None).persist_invoice(
        _invoice(), source_sha256=SHA, tenant_id=""
    )
    assert (result.graph_status, result.vector_status) == ("FAILED", "FAILED")


def test_ids_embed_tenant_and_source_hash():
    from uuid import NAMESPACE_URL, uuid5

    graph, vector, results = _persist_same_invoice_for_two_tenants()
    for tenant, record, result in zip(("TENANT_A", "TENANT_B"), graph.records, results):
        assert record.invoice_id == str(uuid5(NAMESPACE_URL, f"{tenant}:{SHA}"))
        assert result.invoice_id == record.invoice_id
        assert record.line_items[0].line_id == str(
            uuid5(NAMESPACE_URL, f"{tenant}:{SHA}:0")
        )


def test_global_invoice_sha_constraint_is_dropped_and_replaced():
    from services.persistence_manager import (
        NEO4J_LEGACY_CONSTRAINT_DROPS,
        NEO4J_UNIQUE_CONSTRAINTS,
    )

    assert NEO4J_LEGACY_CONSTRAINT_DROPS == (
        "DROP CONSTRAINT dsh_invoice_source_sha256 IF EXISTS",
    )
    assert not any("source_sha256" in s for s in NEO4J_UNIQUE_CONSTRAINTS)
    assert any("i.invoice_id IS UNIQUE" in s for s in NEO4J_UNIQUE_CONSTRAINTS)


def test_tenant_fallback_is_never_empty_in_payload():
    vector = VectorSpy()
    _manager(None, vector).persist_invoice(_invoice(), source_sha256=SHA)
    assert vector.upserts[0][1]["tenant_id"] == "COMPANY-1"


# ------------------------------------------------------- 20 tenants


def _persist_20(graph, vector):
    manager = _manager(graph, vector)
    for index, tenant in enumerate(TENANTS_20):
        invoice = _invoice(
            company_id=tenant,
            supplier="DUPONT SARL",
            number=f"F-{index:03d}",
        )
        sha = f"{index:064x}"
        result = manager.persist_invoice(invoice, source_sha256=sha, tenant_id=tenant)
        assert result.vector_status == "INDEXED"


def test_20_tenants_payloads_and_graph_records_are_disjoint():
    graph, vector = GraphSpy(), VectorSpy()
    _persist_20(graph, vector)
    assert [p["tenant_id"] for _, p in vector.upserts] == TENANTS_20
    assert len({pid for pid, _ in vector.upserts}) == 20
    assert len({r.supplier_id for r in graph.records}) == 20
    assert len({r.source_sha256 for r in graph.records}) == 20
    all_line_ids = [line.line_id for r in graph.records for line in r.line_items]
    assert len(all_line_ids) == len(set(all_line_ids))


def test_20_tenants_no_foreign_tenant_id_in_any_artifact():
    graph, vector = GraphSpy(), VectorSpy()
    _persist_20(graph, vector)
    for record, (_, payload) in zip(graph.records, vector.upserts):
        own = record.tenant_id
        _, params = Neo4jGraphStore.build_upsert(record)
        blobs = json.dumps(params, default=str) + json.dumps(payload, default=str)
        for other in TENANTS_20:
            if other != own:
                assert other not in blobs


def test_20_tenants_cypher_text_is_tenant_free():
    graph, vector = GraphSpy(), VectorSpy()
    _persist_20(graph, vector)
    queries = {Neo4jGraphStore.build_upsert(r)[0] for r in graph.records}
    assert len(queries) == 1
    assert not any(tenant in next(iter(queries)) for tenant in TENANTS_20)


@pytest.fixture
def qdrant_20():
    qdrant_client = pytest.importorskip("qdrant_client")
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = qdrant_client.QdrantClient(":memory:")
    store = QdrantVectorStore(client, collection_name="iso_test")
    _persist_20(GraphSpy(), store)

    def only(tenant: str) -> Filter:
        return Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant))]
        )

    yield client, only
    client.close()


def _scroll(client, flt=None):
    points, _ = client.scroll(
        collection_name="iso_test", scroll_filter=flt, limit=100, with_payload=True
    )
    return points


def test_20_tenants_read_is_scoped_by_filter(qdrant_20):
    client, only = qdrant_20
    assert len(_scroll(client)) == 20
    for tenant in TENANTS_20:
        points = _scroll(client, only(tenant))
        assert len(points) == 1
        assert points[0].payload["tenant_id"] == tenant
        assert points[0].payload["company_id"] == tenant


def test_20_tenants_vector_search_is_scoped_by_filter(qdrant_20):
    client, only = qdrant_20
    for tenant in TENANTS_20:
        hits = client.query_points(
            collection_name="iso_test",
            query=[0.1, 0.2, 0.3, 0.4],
            query_filter=only(tenant),
            limit=50,
        ).points
        assert [h.payload["tenant_id"] for h in hits] == [tenant]


def test_20_tenants_unknown_tenant_sees_nothing(qdrant_20):
    client, only = qdrant_20
    assert _scroll(client, only("CLIENT_99")) == []


def test_20_tenants_delete_is_scoped_and_leaves_others_intact(qdrant_20):
    from qdrant_client.models import FilterSelector

    client, only = qdrant_20
    victim = "CLIENT_07"
    client.delete(
        collection_name="iso_test",
        points_selector=FilterSelector(filter=only(victim)),
    )
    remaining = {p.payload["tenant_id"] for p in _scroll(client)}
    assert remaining == set(TENANTS_20) - {victim}
    assert _scroll(client, only(victim)) == []
    for tenant in TENANTS_20:
        if tenant != victim:
            assert len(_scroll(client, only(tenant))) == 1


def test_20_tenants_rewrite_of_one_tenant_does_not_touch_others(qdrant_20):
    client, only = qdrant_20
    before = {
        t: _scroll(client, only(t))[0].payload for t in TENANTS_20 if t != "CLIENT_03"
    }
    store = QdrantVectorStore(client, collection_name="iso_test")
    _manager(None, store).persist_invoice(
        _invoice(company_id="CLIENT_03", number="F-NEW"),
        source_sha256="f" * 64,
        tenant_id="CLIENT_03",
    )
    for tenant, payload in before.items():
        assert _scroll(client, only(tenant))[0].payload == payload


def test_20_tenants_importing_identical_invoice_keep_20_points():
    qdrant_client = pytest.importorskip("qdrant_client")
    client = qdrant_client.QdrantClient(":memory:")
    try:
        store = QdrantVectorStore(client, collection_name="same")
        manager = _manager(None, store)
        for tenant in TENANTS_20:
            manager.persist_invoice(
                _invoice(), source_sha256=SHA, tenant_id=tenant
            )
        points, _ = client.scroll(collection_name="same", limit=100)
        assert len(points) == 20
    finally:
        client.close()


# ------------------------------------------------ rule registry isolation


def _rule(rule_id: str) -> str:
    return (
        "---\n"
        f"rule_id: {rule_id}\nversion: '1.0'\nstatus: approved\ndomain: tva\n"
        "applies_to_nodes: [rule_evaluation]\npriority: 1\n"
        "conditions:\n  - {field: tax_amount, operator: eq, value: 0}\n"
        "deterministic_actions:\n  - {action: flag, parameters: {}}\n"
        "---\nbody\n"
    )


def test_registry_instances_do_not_share_state_between_tenants(tmp_path):
    rules = {}
    services = {}
    for tenant in TENANTS_20:
        directory = tmp_path / tenant
        directory.mkdir()
        (directory / "rule.md").write_text(_rule(f"RULE-{tenant}"), encoding="utf-8")
        service = RuleRegistryService()
        service.load_rules(directory)
        services[tenant] = service
        rules[tenant] = f"RULE-{tenant}"

    for tenant, service in services.items():
        assert service.get_rule(rules[tenant]) is not None
        for other, rule_id in rules.items():
            if other != tenant:
                assert service.get_rule(rule_id) is None
        assert [r.rule_id for r in service.get_applicable_rules("rule_evaluation")] == [
            rules[tenant]
        ]


def test_registry_reload_of_one_tenant_does_not_affect_another(tmp_path):
    for tenant in ("TENANT_A", "TENANT_B"):
        (tmp_path / tenant).mkdir()
        (tmp_path / tenant / "r.md").write_text(_rule(f"R-{tenant}"), encoding="utf-8")
    service_a, service_b = RuleRegistryService(), RuleRegistryService()
    service_a.load_rules(tmp_path / "TENANT_A")
    service_b.load_rules(tmp_path / "TENANT_B")
    (tmp_path / "TENANT_A" / "r.md").unlink()
    service_a.load_rules(tmp_path / "TENANT_A")
    assert service_a.get_rule("R-TENANT_A") is None
    assert service_b.get_rule("R-TENANT_B") is not None


def test_registry_has_no_tenant_awareness_so_isolation_is_by_directory():
    # Documents the P10 gap: the registry only isolates through the directory
    # it is loaded from, never through a tenant_id parameter.
    assert not hasattr(RuleRegistryService(), "tenant_id")


# ------------------------------------------------------------------ helpers


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contract_version": "1.0.1",
        "tenant_id": "TENANT_A",
        "source_sha256": SHA,
        "doc_type": "INVOICE",
        "invoice_id": "inv-1",
        "company_id": "COMPANY-1",
        "currency": "EUR",
        "direction": "PURCHASE",
        "document_type": "INVOICE",
        "validation_status": "VALIDATED",
        "text": "Facture F-001",
    }
    payload.update(overrides)
    return payload
