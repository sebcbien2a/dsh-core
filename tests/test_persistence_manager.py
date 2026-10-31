"""Tests for the dual Neo4j/Qdrant persistence manager."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

import pytest

from schemas.invoice_schema import InvoiceSchema
from schemas.vector_payload_schema import InvoiceVectorPayloadV1
from services.persistence_manager import (
    NEO4J_LEGACY_CONSTRAINT_DROPS,
    InvoiceGraphRecord,
    Neo4jGraphStore,
    PersistenceManager,
    PersistenceResult,
    QdrantVectorStore,
    deterministic_embedding,
)
from services.tva_zero_rule_evaluator import RuleDecision

SOURCE_SHA = "a" * 64


class FakeGraphStore:
    def __init__(self) -> None:
        self.records: list[InvoiceGraphRecord] = []

    def upsert_invoice(self, record: InvoiceGraphRecord) -> None:
        self.records.append(record)


class FakeVectorStore:
    def __init__(self) -> None:
        self.sizes: list[int] = []
        self.upserts: list[tuple[str, list[float], Mapping[str, Any]]] = []

    def ensure_collection(self, vector_size: int) -> None:
        self.sizes.append(vector_size)

    def upsert(
        self, point_id: str, vector: Sequence[float], payload: Mapping[str, Any]
    ) -> None:
        self.upserts.append((point_id, list(vector), payload))


class FakeEmbedder:
    last_source = "fake"

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension
        self.texts: list[str] = []

    def __call__(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.1] * self.dimension


class FailingGraphStore:
    def upsert_invoice(self, record: InvoiceGraphRecord) -> None:
        raise RuntimeError("neo4j down")


class FailingVectorStore:
    def ensure_collection(self, vector_size: int) -> None:
        raise RuntimeError("qdrant down")

    def upsert(
        self, point_id: str, vector: Sequence[float], payload: Mapping[str, Any]
    ) -> None:
        raise RuntimeError("qdrant down")


class FailingEmbedder:
    last_source = None

    def __call__(self, text: str) -> list[float]:
        raise RuntimeError("embedding down")


class RecordingResult:
    def __init__(self) -> None:
        self.consume_calls = 0

    def consume(self) -> None:
        self.consume_calls += 1


class RecordingSession:
    def __init__(self, driver: "RecordingDriver", database: str | None) -> None:
        self.driver = driver
        self.database = database
        self.closed = False
        self.exited_with: tuple[Any, Any, Any] | None = None

    def __enter__(self) -> "RecordingSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.exited_with = (exc_type, exc, tb)
        self.closed = True
        return False

    def run(self, statement: str, parameters: Any = None) -> RecordingResult:
        self.driver.runs.append((statement, parameters, self.database))
        if self.driver.fail_on is not None and self.driver.fail_on(statement):
            raise self.driver.error
        result = RecordingResult()
        self.driver.results.append(result)
        return result


class RecordingDriver:
    def __init__(self, fail_on: Callable[[str], bool] | None = None) -> None:
        self.fail_on = fail_on
        self.error = RuntimeError("transaction failed")
        self.runs: list[tuple[str, Any, str | None]] = []
        self.results: list[RecordingResult] = []
        self.sessions: list[RecordingSession] = []

    def session(self, database: str | None = None) -> RecordingSession:
        session = RecordingSession(self, database)
        self.sessions.append(session)
        return session


def _is_upsert_statement(statement: str) -> bool:
    return "MERGE (t:Tenant" in statement


def _invoice(
    direction: str = "PURCHASE",
    *,
    company_id: str = "COMPANY-1",
    invoice_number: str = "F-2026-001",
) -> InvoiceSchema:
    is_sale = direction == "SALE"
    payload = {
        "document_type": "INVOICE",
        "direction": direction,
        "company_id": company_id,
        "supplier": {"name": "FOURNISSEUR ALPHA", "siret": "40138136300019"},
        "customer": {"name": "CLIENT BETA"},
        "reference": {
            "invoice_number": invoice_number,
            "issue_date": "2026-10-01",
        },
        "lines": [
            {
                "description": "Peinture marine",
                "quantite": "2",
                "prix_unitaire_ht": "30.00",
                "total_ht": "60.00",
                "taux_tva": "20",
            },
            {
                "description": "Pinceau plat",
                "quantite": "4",
                "prix_unitaire_ht": "10.00",
                "total_ht": "40.00",
                "taux_tva": "20",
            },
        ],
        "amount": {
            "currency": "EUR",
            "net_amount": "100.00",
            "tax_amount": "20.00",
            "gross_amount": "120.00",
        },
        "metadata": {"supplier_country": "FR"},
        "validation_status": "VALIDATED",
    }
    return InvoiceSchema.model_validate(payload)


def _decision() -> RuleDecision:
    return RuleDecision(
        status="VALIDATED",
        reason="ZERO_VAT_DISAMBIGUATED",
        rule_id="RULE-TVA-ZERO-001",
        rule_version="1.0.0",
        vat_regime="INTRACOM_AUTOLIQUIDATION",
        include_in_vat_balance=True,
    )


def test_persist_invoice_writes_graph_and_vector() -> None:
    graph = FakeGraphStore()
    vector = FakeVectorStore()
    embedder = FakeEmbedder()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=vector,
        embedder=embedder,
    )

    result = manager.persist_invoice(
        _invoice(),
        rule_decision=_decision(),
        source_file="facture.pdf",
        source_sha256=SOURCE_SHA,
    )

    assert isinstance(result, PersistenceResult)
    assert result.graph_status == "PERSISTED"
    assert result.vector_status == "INDEXED"
    assert result.embedding_source == "fake"
    assert result.errors == ()
    assert len(graph.records) == 1
    record = graph.records[0]
    assert record.tenant_id == "COMPANY-1"
    assert record.source_sha256 == SOURCE_SHA
    assert record.supplier_id is not None
    assert record.supplier_properties["name"] == "FOURNISSEUR ALPHA"
    assert record.supplier_properties["siren"] == "401381363"
    assert record.supplier_properties["country"] == "FR"
    assert record.invoice_properties["document_number"] == "F-2026-001"
    assert record.invoice_properties["gross_amount"] == "120.00"
    assert record.invoice_properties["source_sha256"] == SOURCE_SHA
    assert record.invoice_properties["contract_version"] == "1.0.1"
    assert record.rule_outcome == {
        "rule_id": "RULE-TVA-ZERO-001",
        "outcome": "VALIDATED",
        "reason": "ZERO_VAT_DISAMBIGUATED",
    }
    assert len(record.line_items) == 2
    first_line = record.line_items[0]
    assert first_line.line_index == 0
    assert first_line.description == "Peinture marine"
    assert first_line.quantity == "2"
    assert first_line.unit_price == "30.00"
    assert first_line.total_amount == "60.00"
    assert first_line.tax_rate == "20"
    assert first_line.line_id == manager.build_line_id("COMPANY-1", SOURCE_SHA, 0)
    assert record.line_items[1].line_id == manager.build_line_id("COMPANY-1", SOURCE_SHA, 1)

    assert vector.sizes == [8]
    assert len(vector.upserts) == 1
    point_id, embedding, payload = vector.upserts[0]
    assert point_id == result.point_id
    assert embedding == [0.1] * 8
    assert payload["invoice_id"] == result.invoice_id
    assert payload["supplier_name"] == "FOURNISSEUR ALPHA"
    assert payload["rule"]["rule_id"] == "RULE-TVA-ZERO-001"
    assert "FOURNISSEUR ALPHA" in payload["text"]
    assert "F-2026-001" in embedder.texts[0]


def test_sale_invoice_has_no_supplier_node() -> None:
    graph = FakeGraphStore()
    manager = PersistenceManager(
        graph_store=graph, vector_store=None, embedder=FakeEmbedder()
    )
    payload = _invoice("SALE").model_dump(mode="json")
    payload.pop("supplier")

    result = manager.persist_invoice(
        InvoiceSchema.model_validate(payload), source_sha256=SOURCE_SHA
    )

    assert result.graph_status == "PERSISTED"
    record = graph.records[0]
    assert record.supplier_id is None
    assert record.supplier_properties == {}
    assert record.invoice_properties["direction"] == "SALE"


def test_missing_backends_degrade_without_raising() -> None:
    manager = PersistenceManager(
        graph_store=None, vector_store=None, embedder=FakeEmbedder()
    )

    result = manager.persist_invoice(_invoice())

    assert result.graph_status == "UNAVAILABLE"
    assert result.vector_status == "UNAVAILABLE"
    assert any("neo4j" in error for error in result.errors)
    assert any("qdrant" in error for error in result.errors)


def test_graph_failure_does_not_block_vector_indexing() -> None:
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=FailingGraphStore(),
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

    assert result.graph_status == "FAILED"
    assert result.vector_status == "INDEXED"
    assert any("neo4j down" in error for error in result.errors)
    assert len(vector.upserts) == 1


def test_vector_failure_reports_failed_status() -> None:
    graph = FakeGraphStore()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=FailingVectorStore(),
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

    assert result.graph_status == "PERSISTED"
    assert result.vector_status == "FAILED"
    assert any("qdrant down" in error for error in result.errors)


def test_embedding_failure_skips_vector_write() -> None:
    graph = FakeGraphStore()
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=vector,
        embedder=FailingEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

    assert result.graph_status == "PERSISTED"
    assert result.vector_status == "FAILED"
    assert any("embedding down" in error for error in result.errors)
    assert vector.upserts == []


def test_ids_are_deterministic() -> None:
    manager = PersistenceManager(graph_store=None, vector_store=None)
    first = manager.build_invoice_id(_invoice())
    second = manager.build_invoice_id(_invoice())
    other = manager.build_invoice_id(_invoice("SALE"))

    assert first == second
    assert first != other
    assert manager.build_point_id(_invoice()) == manager.build_point_id(_invoice())


def test_ids_are_derived_from_tenant_and_source_sha256() -> None:
    from uuid import NAMESPACE_URL, uuid5

    manager = PersistenceManager(graph_store=None, vector_store=None)
    invoice = _invoice()
    invoice_id = manager.build_invoice_id(
        invoice, tenant_id="TENANT_A", source_sha256=SOURCE_SHA
    )
    assert invoice_id == str(uuid5(NAMESPACE_URL, f"TENANT_A:{SOURCE_SHA}"))
    assert manager.build_line_id("TENANT_A", SOURCE_SHA, 3) == str(
        uuid5(NAMESPACE_URL, f"TENANT_A:{SOURCE_SHA}:3")
    )
    other = manager.build_invoice_id(
        invoice, tenant_id="TENANT_B", source_sha256=SOURCE_SHA
    )
    assert other != invoice_id
    assert manager.build_point_id(
        invoice, tenant_id="TENANT_A", source_sha256=SOURCE_SHA
    ) != manager.build_point_id(
        invoice, tenant_id="TENANT_B", source_sha256=SOURCE_SHA
    )


def test_persist_invoices_bulk() -> None:
    graph = FakeGraphStore()
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    results = manager.persist_invoices(
        [_invoice(), _invoice("SALE")], source_sha256=SOURCE_SHA
    )

    assert [result.graph_status for result in results] == ["PERSISTED", "PERSISTED"]
    assert [result.vector_status for result in results] == ["INDEXED", "INDEXED"]
    assert len(graph.records) == 2
    assert len(vector.upserts) == 2


def test_qdrant_in_memory_integration() -> None:
    qdrant_client = pytest.importorskip("qdrant_client")
    client = qdrant_client.QdrantClient(":memory:")
    try:
        store = QdrantVectorStore(client, collection_name="test_invoices")
        manager = PersistenceManager(
            graph_store=None,
            vector_store=store,
            embedder=FakeEmbedder(dimension=4),
            collection_name="test_invoices",
        )

        result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

        assert result.vector_status == "INDEXED"
        points, _ = client.scroll(
            collection_name="test_invoices", limit=10, with_payload=True
        )
        assert len(points) == 1
        payload = points[0].payload
        assert payload["invoice_id"] == result.invoice_id
        assert payload["supplier_name"] == "FOURNISSEUR ALPHA"
        contract = InvoiceVectorPayloadV1.model_validate(payload)
        assert contract.contract_version == "1.0.1"
        assert contract.tenant_id == "COMPANY-1"
        assert contract.source_sha256 == SOURCE_SHA
        assert contract.doc_type == "INVOICE"
        assert contract.point_id == result.point_id
    finally:
        client.close()


def test_vector_payload_matches_contract_v1() -> None:
    graph = FakeGraphStore()
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    manager.persist_invoice(
        _invoice(),
        rule_decision=_decision(),
        source_sha256=SOURCE_SHA,
        tenant_id="TENANT-ALPHA",
    )

    payload = vector.upserts[0][2]
    parsed = InvoiceVectorPayloadV1.model_validate(dict(payload))
    record = graph.records[0]
    assert parsed.contract_version == "1.0.1"
    assert parsed.tenant_id == "TENANT-ALPHA"
    assert parsed.source_sha256 == SOURCE_SHA
    assert parsed.doc_type == "INVOICE"
    assert parsed.supplier_id == record.supplier_id
    assert parsed.invoice_number == "F-2026-001"
    assert parsed.company_id == "COMPANY-1"
    assert parsed.rule["rule_id"] == "RULE-TVA-ZERO-001"


def test_persist_invoice_derives_tenant_from_company_id() -> None:
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=None,
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

    assert result.vector_status == "INDEXED"
    assert vector.upserts[0][2]["tenant_id"] == "COMPANY-1"


def test_persist_invoice_requires_source_sha256() -> None:
    graph = FakeGraphStore()
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=graph,
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(
        _invoice(), source_file="missing-facture.pdf"
    )

    assert result.graph_status == "FAILED"
    assert result.vector_status == "FAILED"
    assert any("neo4j" in error and "source_sha256" in error for error in result.errors)
    assert any("qdrant" in error and "source_sha256" in error for error in result.errors)
    assert graph.records == []
    assert vector.upserts == []


def test_persist_invoice_rejects_invalid_source_sha256() -> None:
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=None,
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256="not-a-sha")

    assert result.graph_status == "UNAVAILABLE"
    assert result.vector_status == "FAILED"
    assert any("source_sha256" in error for error in result.errors)
    assert vector.upserts == []


def test_graph_record_matches_unified_ontology() -> None:
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(
        _invoice(),
        rule_decision=_decision(),
        source_sha256=SOURCE_SHA,
        tenant_id="TENANT-ALPHA",
    )

    assert record.tenant_id == "TENANT-ALPHA"
    assert record.source_sha256 == SOURCE_SHA
    assert record.supplier_id is not None
    assert record.invoice_properties == {
        "invoice_id": record.invoice_id,
        "source_sha256": SOURCE_SHA,
        "document_number": "F-2026-001",
        "issue_date": "2026-10-01",
        "gross_amount": "120.00",
        "net_amount": "100.00",
        "tax_amount": "20.00",
        "currency": "EUR",
        "direction": "PURCHASE",
        "validation_status": "VALIDATED",
        "contract_version": "1.0.1",
    }
    assert record.rule_outcome == {
        "rule_id": "RULE-TVA-ZERO-001",
        "outcome": "VALIDATED",
        "reason": "ZERO_VAT_DISAMBIGUATED",
    }
    assert [line.line_index for line in record.line_items] == [0, 1]


def test_cypher_query_uses_unified_ontology() -> None:
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(
        _invoice(), rule_decision=_decision(), source_sha256=SOURCE_SHA
    )

    query, parameters = Neo4jGraphStore.build_upsert(record)

    assert "MERGE (t:Tenant {tenant_id: $tenant_id})" in query
    assert "MERGE (i:Invoice {invoice_id: $invoice_id})" in query
    assert "MERGE (s:Supplier {supplier_id: $supplier_id})" in query
    assert "MERGE (s)-[:ISSUED]->(i)" in query
    assert "MERGE (t)-[:OWNS]->(i)" in query
    assert "MERGE (i)-[:EVALUATED_BY]->(r)" in query
    assert "RuleOutcome" in query
    assert "MERGE (i)-[:HAS_LINE]->(l)" in query
    assert "MERGE (l:InvoiceLine {line_id: line.line_id})" in query
    assert "Product" not in query
    assert parameters["source_sha256"] == SOURCE_SHA
    assert parameters["tenant_id"] == "COMPANY-1"
    assert parameters["rule_id"] == "RULE-TVA-ZERO-001"
    assert parameters["rule_outcome"] == "VALIDATED"
    assert len(parameters["line_ids"]) == 2


def test_upsert_cypher_contract_is_exact() -> None:
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(
        _invoice(), rule_decision=_decision(), source_sha256=SOURCE_SHA
    )

    query, parameters = Neo4jGraphStore.build_upsert(record)

    assert " ".join(query.split()) == (
        "MERGE (t:Tenant {tenant_id: $tenant_id}) "
        "SET t.contract_version = $contract_version "
        "MERGE (i:Invoice {invoice_id: $invoice_id}) "
        "SET i += $invoice_properties "
        "SET i.contract_version = $contract_version "
        "MERGE (t)-[:OWNS]->(i) "
        "WITH i "
        "MERGE (s:Supplier {supplier_id: $supplier_id}) "
        "SET s += $supplier_properties "
        "MERGE (s)-[:ISSUED]->(i) "
        "WITH i "
        "MERGE (r:RuleOutcome {rule_id: $rule_id, outcome: $rule_outcome, "
        "reason: $rule_reason}) "
        "MERGE (i)-[:EVALUATED_BY]->(r) "
        "WITH i "
        "OPTIONAL MATCH (i)-[:HAS_LINE]->(stale:InvoiceLine) "
        "WHERE NOT stale.line_id IN $line_ids "
        "DETACH DELETE stale "
        "WITH DISTINCT i "
        "UNWIND $line_items AS line "
        "MERGE (l:InvoiceLine {line_id: line.line_id}) "
        "SET l.line_index = line.line_index, "
        "l.description = line.description, "
        "l.quantity = line.quantity, "
        "l.unit_price = line.unit_price, "
        "l.total_amount = line.total_amount, "
        "l.tax_rate = line.tax_rate "
        "MERGE (i)-[:HAS_LINE]->(l)"
    )
    assert parameters == {
        "tenant_id": "COMPANY-1",
        "contract_version": "1.0.1",
        "invoice_id": record.invoice_id,
        "source_sha256": SOURCE_SHA,
        "invoice_properties": {
            "invoice_id": record.invoice_id,
            "source_sha256": SOURCE_SHA,
            "document_number": "F-2026-001",
            "issue_date": "2026-10-01",
            "gross_amount": "120.00",
            "net_amount": "100.00",
            "tax_amount": "20.00",
            "currency": "EUR",
            "direction": "PURCHASE",
            "validation_status": "VALIDATED",
            "contract_version": "1.0.1",
        },
        "supplier_id": record.supplier_id,
        "supplier_properties": {
            "name": "FOURNISSEUR ALPHA",
            "siren": "401381363",
            "siret": "40138136300019",
            "country": "FR",
        },
        "rule_id": "RULE-TVA-ZERO-001",
        "rule_outcome": "VALIDATED",
        "rule_reason": "ZERO_VAT_DISAMBIGUATED",
        "line_ids": [line.line_id for line in record.line_items],
        "line_items": [
            {
                "line_id": record.line_items[0].line_id,
                "line_index": 0,
                "description": "Peinture marine",
                "quantity": "2",
                "unit_price": "30.00",
                "total_amount": "60.00",
                "tax_rate": "20",
            },
            {
                "line_id": record.line_items[1].line_id,
                "line_index": 1,
                "description": "Pinceau plat",
                "quantity": "4",
                "unit_price": "10.00",
                "total_amount": "40.00",
                "tax_rate": "20",
            },
        ],
    }


def test_upsert_cypher_covers_each_ontology_entity() -> None:
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(
        _invoice(), rule_decision=_decision(), source_sha256=SOURCE_SHA
    )

    query, _ = Neo4jGraphStore.build_upsert(record)
    normalized = " ".join(query.split())

    assert "MERGE (t:Tenant {tenant_id: $tenant_id})" in normalized
    assert "SET t.contract_version = $contract_version" in normalized
    assert "MERGE (i:Invoice {invoice_id: $invoice_id})" in normalized
    assert "SET i += $invoice_properties" in normalized
    assert "MERGE (t)-[:OWNS]->(i)" in normalized
    assert "MERGE (s:Supplier {supplier_id: $supplier_id})" in normalized
    assert "SET s += $supplier_properties" in normalized
    assert "MERGE (s)-[:ISSUED]->(i)" in normalized
    assert (
        "MERGE (r:RuleOutcome {rule_id: $rule_id, outcome: $rule_outcome, "
        "reason: $rule_reason})"
    ) in normalized
    assert "MERGE (i)-[:EVALUATED_BY]->(r)" in normalized
    assert "MERGE (l:InvoiceLine {line_id: line.line_id})" in normalized
    assert "MERGE (i)-[:HAS_LINE]->(l)" in normalized


def test_upsert_cypher_omits_absent_supplier_and_rule() -> None:
    record = InvoiceGraphRecord(
        tenant_id="TENANT-1",
        supplier_id=None,
        supplier_properties={},
        invoice_id="INVOICE-1",
        source_sha256=SOURCE_SHA,
        invoice_properties={"invoice_id": "INVOICE-1"},
        rule_outcome=None,
        line_items=(),
    )

    query, parameters = Neo4jGraphStore.build_upsert(record)
    normalized = " ".join(query.split())

    assert "Supplier" not in normalized
    assert "RuleOutcome" not in normalized
    assert "MERGE (t:Tenant {tenant_id: $tenant_id})" in normalized
    assert "MERGE (i:Invoice {invoice_id: $invoice_id})" in normalized
    assert "UNWIND $line_items AS line" in normalized
    assert parameters["supplier_id"] is None
    assert parameters["supplier_properties"] == {}
    assert parameters["rule_id"] is None
    assert parameters["rule_outcome"] is None
    assert parameters["rule_reason"] is None
    assert parameters["line_ids"] == []
    assert parameters["line_items"] == []


def test_upsert_invoice_passes_exact_query_and_parameters_to_session_run() -> None:
    driver = RecordingDriver()
    store = Neo4jGraphStore(driver, database="neo4j")
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(
        _invoice(), rule_decision=_decision(), source_sha256=SOURCE_SHA
    )
    expected_query, expected_parameters = Neo4jGraphStore.build_upsert(record)

    store.upsert_invoice(record)

    assert driver.runs[0][0] == NEO4J_LEGACY_CONSTRAINT_DROPS[0]
    assert [run[0] for run in driver.runs[1:4]] == list(
        Neo4jGraphStore.constraint_statements()
    )
    statement, parameters, database = driver.runs[4]
    assert statement == expected_query
    assert parameters == expected_parameters
    assert database == "neo4j"
    assert [result.consume_calls for result in driver.results] == [1, 1, 1, 1, 1]
    assert all(session.closed for session in driver.sessions)
    assert all(session.exited_with == (None, None, None) for session in driver.sessions)


def test_upsert_invoice_propagates_transaction_error() -> None:
    driver = RecordingDriver(fail_on=_is_upsert_statement)
    store = Neo4jGraphStore(driver, database="neo4j")
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(_invoice(), source_sha256=SOURCE_SHA)

    with pytest.raises(RuntimeError, match="transaction failed"):
        store.upsert_invoice(record)

    session = driver.sessions[-1]
    assert session.closed is True
    assert session.exited_with is not None
    assert session.exited_with[0] is RuntimeError
    assert isinstance(session.exited_with[1], RuntimeError)
    assert [result.consume_calls for result in driver.results] == [1, 1, 1, 1]
    assert driver.runs[-1][0] == store.build_upsert(record)[0]


def test_persistence_manager_reports_graph_transaction_failure() -> None:
    vector = FakeVectorStore()
    manager = PersistenceManager(
        graph_store=Neo4jGraphStore(RecordingDriver(fail_on=_is_upsert_statement)),
        vector_store=vector,
        embedder=FakeEmbedder(),
    )

    result = manager.persist_invoice(_invoice(), source_sha256=SOURCE_SHA)

    assert result.graph_status == "FAILED"
    assert result.vector_status == "INDEXED"
    assert any("transaction failed" in error for error in result.errors)
    assert len(vector.upserts) == 1


def test_constraint_failure_is_not_cached_and_is_retried() -> None:
    def fail_first_constraint(statement: str) -> bool:
        return "dsh_tenant_tenant_id" in statement

    driver = RecordingDriver(fail_on=fail_first_constraint)
    store = Neo4jGraphStore(driver)
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(_invoice(), source_sha256=SOURCE_SHA)

    with pytest.raises(RuntimeError, match="transaction failed"):
        store.upsert_invoice(record)
    assert store._constraints_ensured is False

    driver.fail_on = None
    store.upsert_invoice(record)

    assert store._constraints_ensured is True
    assert driver.runs[2][0] == NEO4J_LEGACY_CONSTRAINT_DROPS[0]
    assert [run[0] for run in driver.runs[3:6]] == list(
        Neo4jGraphStore.constraint_statements()
    )
    assert driver.runs[6][0] == store.build_upsert(record)[0]


def test_verify_connectivity_never_raises() -> None:
    class HealthyDriver:
        def verify_connectivity(self) -> None:
            return None

    class UnreachableDriver:
        def verify_connectivity(self) -> None:
            raise OSError("connection refused")

    assert Neo4jGraphStore(HealthyDriver()).verify_connectivity() is True
    assert Neo4jGraphStore(UnreachableDriver()).verify_connectivity() is False
    assert Neo4jGraphStore(object()).verify_connectivity() is True


def test_constraint_statements_are_exact() -> None:
    assert Neo4jGraphStore.constraint_statements() == (
        "CREATE CONSTRAINT dsh_tenant_tenant_id IF NOT EXISTS "
        "FOR (t:Tenant) REQUIRE t.tenant_id IS UNIQUE",
        "CREATE CONSTRAINT dsh_supplier_supplier_id IF NOT EXISTS "
        "FOR (s:Supplier) REQUIRE s.supplier_id IS UNIQUE",
        "CREATE CONSTRAINT dsh_invoice_invoice_id IF NOT EXISTS "
        "FOR (i:Invoice) REQUIRE i.invoice_id IS UNIQUE",
    )


def test_duplicate_descriptions_produce_distinct_invoice_lines() -> None:
    payload = _invoice().model_dump(mode="json")
    payload["lines"][1]["description"] = payload["lines"][0]["description"]
    invoice = InvoiceSchema.model_validate(payload)
    manager = PersistenceManager(graph_store=None, vector_store=None)

    record = manager.build_graph_record(
        invoice, rule_decision=_decision(), source_sha256=SOURCE_SHA
    )
    query, parameters = Neo4jGraphStore.build_upsert(record)

    assert len(record.line_items) == 2
    assert record.line_items[0].line_id != record.line_items[1].line_id
    assert len(set(parameters["line_ids"])) == 2
    assert [line["description"] for line in parameters["line_items"]] == [
        "Peinture marine",
        "Peinture marine",
    ]
    assert parameters["line_ids"] == [
        record.line_items[0].line_id,
        record.line_items[1].line_id,
    ]
    assert "Product" not in query


def test_constraint_statements_cover_unique_keys() -> None:
    statements = Neo4jGraphStore.constraint_statements()

    assert len(statements) == 3
    assert any(
        "Tenant" in statement and "tenant_id IS UNIQUE" in statement
        for statement in statements
    )
    assert any(
        "Supplier" in statement and "supplier_id IS UNIQUE" in statement
        for statement in statements
    )
    assert any(
        "Invoice" in statement and "invoice_id IS UNIQUE" in statement
        for statement in statements
    )


def test_upsert_ensures_constraints_once() -> None:
    class FakeSession:
        def __init__(self, runs: list[tuple[str, Any]]) -> None:
            self.runs = runs

        def __enter__(self) -> "FakeSession":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def run(self, statement: str, parameters: Any = None) -> Any:
            self.runs.append((statement, parameters))

            class Result:
                def consume(self) -> None:
                    return None

            return Result()

    class FakeDriver:
        def __init__(self) -> None:
            self.runs: list[tuple[str, Any]] = []

        def session(self, database: Any = None) -> FakeSession:
            return FakeSession(self.runs)

    driver = FakeDriver()
    store = Neo4jGraphStore(driver)
    manager = PersistenceManager(graph_store=None, vector_store=None)
    record = manager.build_graph_record(_invoice(), source_sha256=SOURCE_SHA)

    store.upsert_invoice(record)
    store.upsert_invoice(record)

    constraint_runs = [run for run in driver.runs if "CREATE CONSTRAINT" in run[0]]
    upsert_runs = [run for run in driver.runs if "MERGE (t:Tenant" in run[0]]
    assert len(constraint_runs) == 3
    assert len(upsert_runs) == 2
    assert upsert_runs[0][1]["source_sha256"] == SOURCE_SHA


def test_deterministic_embedding_shape() -> None:
    vector = deterministic_embedding("facture test", dimension=16)

    assert len(vector) == 16
    assert all(-1.0 <= value <= 1.0 for value in vector)
    assert vector == deterministic_embedding("facture test", dimension=16)


@pytest.mark.skipif(
    os.getenv("NEO4J_IT") != "1",
    reason="Nécessite Neo4j actif (opt-in)",
)
def test_neo4j_integration_is_conditional() -> None:
    """Integration: persist and read back the unified invoice graph."""

    store = Neo4jGraphStore.from_env()
    assert store is not None, "Neo4jGraphStore.from_env() returned no driver"
    if not store.verify_connectivity():
        pytest.skip("Neo4j unreachable via NEO4J_URI (NEO4J_IT=1)")

    unique = uuid4().hex
    source_sha256 = unique * 2
    tenant_id = f"test_it_{unique}"
    rule_id = f"RULE-TEST-{unique}"
    decision = replace(_decision(), rule_id=rule_id)
    payload = _invoice(
        company_id=tenant_id,
        invoice_number=f"TEST-NEO4J-{unique}",
    ).model_dump(mode="json")
    payload["lines"][1]["description"] = payload["lines"][0]["description"]
    invoice = InvoiceSchema.model_validate(payload)
    manager = PersistenceManager(
        graph_store=store,
        vector_store=None,
        embedder=FakeEmbedder(),
    )
    record = None
    try:
        record = manager.build_graph_record(
            invoice,
            rule_decision=decision,
            source_sha256=source_sha256,
            tenant_id=tenant_id,
        )
        result = manager.persist_invoice(
            invoice,
            rule_decision=decision,
            source_sha256=source_sha256,
            tenant_id=tenant_id,
        )

        assert result.graph_status == "PERSISTED"
        assert result.vector_status == "UNAVAILABLE"
        assert result.errors == ("qdrant: client unavailable",)

        with store.driver.session(database=store.database) as session:
            row = session.run(
                """
                MATCH (t:Tenant {tenant_id: $tenant_id})-[:OWNS]->(i:Invoice {source_sha256: $source_sha256})
                MATCH (s:Supplier {supplier_id: $supplier_id})-[:ISSUED]->(i)
                MATCH (i)-[:HAS_LINE]->(line:InvoiceLine)
                MATCH (i)-[:EVALUATED_BY]->(r:RuleOutcome)
                RETURN count(DISTINCT line) AS line_count,
                       collect(DISTINCT line.line_id) AS line_ids,
                       i.invoice_id AS invoice_id,
                       i.contract_version AS contract_version,
                       i.validation_status AS validation_status,
                       r.rule_id AS rule_id,
                       r.outcome AS outcome
                """,
                tenant_id=record.tenant_id,
                supplier_id=record.supplier_id,
                source_sha256=record.source_sha256,
            ).single()

        assert row is not None
        assert row["line_count"] == 2
        assert len(set(row["line_ids"])) == 2
        assert row["invoice_id"] == record.invoice_id
        assert row["contract_version"] == "1.0.1"
        assert row["validation_status"] == "VALIDATED"
        assert row["rule_id"] == rule_id
        assert row["outcome"] == "VALIDATED"
    finally:
        try:
            with store.driver.session(database=store.database) as session:
                session.run(
                    """
                    MATCH (i:Invoice {source_sha256: $source_sha256})
                    MATCH (i)-[:HAS_LINE]->(line:InvoiceLine)
                    DETACH DELETE line
                    """,
                    source_sha256=source_sha256,
                ).consume()
                session.run(
                    "MATCH (i:Invoice {source_sha256: $source_sha256}) "
                    "DETACH DELETE i",
                    source_sha256=source_sha256,
                ).consume()
                session.run(
                    "MATCH (r:RuleOutcome {rule_id: $rule_id}) DETACH DELETE r",
                    rule_id=rule_id,
                ).consume()
                if record is not None and record.supplier_id is not None:
                    session.run(
                        "MATCH (s:Supplier {supplier_id: $supplier_id}) "
                        "DETACH DELETE s",
                        supplier_id=record.supplier_id,
                    ).consume()
                session.run(
                    "MATCH (t:Tenant {tenant_id: $tenant_id}) DETACH DELETE t",
                    tenant_id=tenant_id,
                ).consume()
        finally:
            store.close()
