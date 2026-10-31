"""Dual persistence for validated ``InvoiceSchema`` records.

Neo4j stores the unified accounting graph
``Tenant -> Invoice -> InvoiceLine`` with ``Supplier`` and ``RuleOutcome``;
Qdrant stores a semantic vector plus the versioned invoice payload for
similarity search and RAG. Both backends are optional: a missing driver/server
degrades to an explicit status instead of breaking the LangGraph commit node.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from schemas.invoice_schema import InvoiceDirection, InvoiceSchema
from schemas.vector_payload_schema import CONTRACT_VERSION, InvoiceVectorPayloadV1

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_AUTO = object()
DEFAULT_COLLECTION = "invoices"
DEFAULT_COMPANY_ID = "DSH_CORE"
DEFAULT_COMPANY_NAME = "DSH-CORE"
DEFAULT_EMBEDDING_DIM = 768
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text:latest"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _file_sha256(path: str | Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


NEO4J_UNIQUE_CONSTRAINTS = (
    "CREATE CONSTRAINT dsh_tenant_tenant_id IF NOT EXISTS "
    "FOR (t:Tenant) REQUIRE t.tenant_id IS UNIQUE",
    "CREATE CONSTRAINT dsh_supplier_supplier_id IF NOT EXISTS "
    "FOR (s:Supplier) REQUIRE s.supplier_id IS UNIQUE",
    "CREATE CONSTRAINT dsh_invoice_invoice_id IF NOT EXISTS "
    "FOR (i:Invoice) REQUIRE i.invoice_id IS UNIQUE",
)
# v1.0.2: the global source_sha256 uniqueness leaked data across tenants.
NEO4J_LEGACY_CONSTRAINT_DROPS = (
    "DROP CONSTRAINT dsh_invoice_source_sha256 IF EXISTS",
)

_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@dataclass(frozen=True)
class InvoiceLineRecord:
    line_id: str
    line_index: int
    description: str
    quantity: str
    unit_price: str
    total_amount: str
    tax_rate: str


@dataclass(frozen=True)
class InvoiceGraphRecord:
    tenant_id: str
    supplier_id: str | None
    supplier_properties: Mapping[str, Any]
    invoice_id: str
    source_sha256: str
    invoice_properties: Mapping[str, Any]
    rule_outcome: Mapping[str, Any] | None = None
    line_items: tuple[InvoiceLineRecord, ...] = ()


class GraphStore(Protocol):
    def upsert_invoice(self, record: InvoiceGraphRecord) -> None: ...


class VectorStore(Protocol):
    def ensure_collection(self, vector_size: int) -> None: ...

    def upsert(
        self, point_id: str, vector: Sequence[float], payload: Mapping[str, Any]
    ) -> None: ...


@dataclass(frozen=True)
class PersistenceResult:
    invoice_id: str
    graph_status: str
    vector_status: str
    point_id: str | None = None
    embedding_source: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "graph_status": self.graph_status,
            "vector_status": self.vector_status,
            "point_id": self.point_id,
            "embedding_source": self.embedding_source,
            "errors": list(self.errors),
        }


def deterministic_embedding(text: str, dimension: int = DEFAULT_EMBEDDING_DIM) -> list[float]:
    """Offline embedding fallback, deterministic and dependency-free."""

    seed = hashlib.sha256(text.encode("utf-8")).digest()
    chunks: list[bytes] = []
    counter = 0
    while len(chunks) * 32 < dimension:
        chunks.append(
            hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        )
        counter += 1
    raw = b"".join(chunks)[:dimension]
    return [((byte / 255.0) * 2.0) - 1.0 for byte in raw]


class OllamaEmbedder:
    """Local Ollama embedder with a deterministic offline fallback."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        url: str = "http://localhost:11434/api/embeddings",
        timeout: float = 2.0,
        fallback_dimension: int = DEFAULT_EMBEDDING_DIM,
        fallback: bool = True,
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout
        self.fallback_dimension = fallback_dimension
        self.fallback = fallback
        self.last_source: str | None = None

    def __call__(self, text: str) -> list[float]:
        try:
            import requests

            response = requests.post(
                self.url,
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            response.raise_for_status()
            embedding = response.json().get("embedding")
            if not embedding:
                raise ValueError("empty embedding")
            self.last_source = "ollama"
            return [float(value) for value in embedding]
        except Exception:
            if not self.fallback:
                raise
            self.last_source = "hash_fallback"
            return deterministic_embedding(text, self.fallback_dimension)


class Neo4jGraphStore:
    """Neo4j writer for the unified DSH-Core accounting ontology."""

    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self.driver = driver
        self.database = database
        self._constraints_ensured = False

    @classmethod
    def from_env(cls) -> "Neo4jGraphStore | None":
        _load_dotenv()
        try:
            from neo4j import GraphDatabase
        except Exception:
            return None
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "neo4j"))
        password = os.getenv("NEO4J_PASSWORD", "")
        try:
            driver = GraphDatabase.driver(
                uri,
                auth=(user, password),
                connection_timeout=2.0,
            )
        except Exception:
            return None
        return cls(driver, database=os.getenv("NEO4J_DATABASE") or None)

    @staticmethod
    def constraint_statements() -> tuple[str, ...]:
        return NEO4J_UNIQUE_CONSTRAINTS

    def verify_connectivity(self) -> bool:
        """Probe the Neo4j server and report reachability without raising.

        Construction stays lazy so offline unit tests never touch the network.
        Integration tests can opt in and use this probe to skip cleanly when
        the local server is not running. A test double without
        ``verify_connectivity`` is treated as reachable.
        """

        verify = getattr(self.driver, "verify_connectivity", None)
        if not callable(verify):
            return True
        try:
            verify()
        except Exception:
            return False
        return True

    def ensure_constraints(self) -> None:
        if self._constraints_ensured:
            return
        with self.driver.session(database=self.database) as session:
            for statement in (
                *NEO4J_LEGACY_CONSTRAINT_DROPS,
                *NEO4J_UNIQUE_CONSTRAINTS,
            ):
                session.run(statement).consume()
        self._constraints_ensured = True

    @staticmethod
    def build_upsert(
        record: InvoiceGraphRecord,
    ) -> tuple[str, dict[str, Any]]:
        supplier_clause = ""
        if record.supplier_id is not None:
            supplier_clause = (
                "MERGE (s:Supplier {supplier_id: $supplier_id})\n"
                "        SET s += $supplier_properties\n"
                "        MERGE (s)-[:ISSUED]->(i)\n"
                "        WITH i"
            )
        rule = record.rule_outcome or {}
        rule_clause = ""
        if rule:
            rule_clause = (
                "MERGE (r:RuleOutcome {rule_id: $rule_id, "
                "outcome: $rule_outcome, reason: $rule_reason})\n"
                "        MERGE (i)-[:EVALUATED_BY]->(r)\n"
                "        WITH i"
            )
        query = f"""
        MERGE (t:Tenant {{tenant_id: $tenant_id}})
        SET t.contract_version = $contract_version
        MERGE (i:Invoice {{invoice_id: $invoice_id}})
        SET i += $invoice_properties
        SET i.contract_version = $contract_version
        MERGE (t)-[:OWNS]->(i)
        WITH i
        {supplier_clause}
        {rule_clause}
        OPTIONAL MATCH (i)-[:HAS_LINE]->(stale:InvoiceLine)
        WHERE NOT stale.line_id IN $line_ids
        DETACH DELETE stale
        WITH DISTINCT i
        UNWIND $line_items AS line
        MERGE (l:InvoiceLine {{line_id: line.line_id}})
        SET l.line_index = line.line_index,
            l.description = line.description,
            l.quantity = line.quantity,
            l.unit_price = line.unit_price,
            l.total_amount = line.total_amount,
            l.tax_rate = line.tax_rate
        MERGE (i)-[:HAS_LINE]->(l)
        """
        parameters = {
            "tenant_id": record.tenant_id,
            "contract_version": CONTRACT_VERSION,
            "invoice_id": record.invoice_id,
            "source_sha256": record.source_sha256,
            "invoice_properties": dict(record.invoice_properties),
            "supplier_id": record.supplier_id,
            "supplier_properties": dict(record.supplier_properties),
            "rule_id": rule.get("rule_id"),
            "rule_outcome": rule.get("outcome"),
            "rule_reason": rule.get("reason"),
            "line_ids": [line.line_id for line in record.line_items],
            "line_items": [
                {
                    "line_id": line.line_id,
                    "line_index": line.line_index,
                    "description": line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "total_amount": line.total_amount,
                    "tax_rate": line.tax_rate,
                }
                for line in record.line_items
            ],
        }
        return query, parameters

    def upsert_invoice(self, record: InvoiceGraphRecord) -> None:
        self.ensure_constraints()
        query, parameters = self.build_upsert(record)
        with self.driver.session(database=self.database) as session:
            session.run(query, parameters).consume()

    def close(self) -> None:
        self.driver.close()


class QdrantVectorStore:
    """Qdrant writer with lazy collection creation."""

    def __init__(self, client: Any, collection_name: str = DEFAULT_COLLECTION) -> None:
        self.client = client
        self.collection_name = collection_name

    @classmethod
    def from_env(
        cls, collection_name: str = DEFAULT_COLLECTION
    ) -> "QdrantVectorStore | None":
        try:
            from qdrant_client import QdrantClient
        except ImportError:
            return None
        url = os.getenv("QDRANT_URL", "http://localhost:6333")
        try:
            client = QdrantClient(url=url, timeout=1.0)
        except Exception:
            return None
        return cls(client, collection_name)

    def ensure_collection(self, vector_size: int) -> None:
        from qdrant_client.models import Distance, VectorParams

        existing = {
            collection.name for collection in self.client.get_collections().collections
        }
        if self.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=vector_size, distance=Distance.COSINE
                ),
            )
            return
        info = self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        existing_size = getattr(vectors, "size", None)
        if existing_size is None and isinstance(vectors, dict):
            existing_size = next(
                (getattr(value, "size", None) for value in vectors.values()), None
            )
        if existing_size is not None and existing_size != vector_size:
            raise ValueError(
                f"Qdrant collection {self.collection_name!r} expects "
                f"{existing_size} dimensions, got {vector_size}"
            )

    def upsert(
        self, point_id: str, vector: Sequence[float], payload: Mapping[str, Any]
    ) -> None:
        from qdrant_client.models import PointStruct

        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=point_id,
                    vector=[float(value) for value in vector],
                    payload=dict(payload),
                )
            ],
        )

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


class PersistenceManager:
    """Coordinates the Neo4j and Qdrant writes for validated invoices."""

    def __init__(
        self,
        *,
        graph_store: GraphStore | None | object = _AUTO,
        vector_store: VectorStore | None | object = _AUTO,
        embedder: Any = None,
        collection_name: str = DEFAULT_COLLECTION,
        company_id: str = DEFAULT_COMPANY_ID,
        company_name: str = DEFAULT_COMPANY_NAME,
        tenant_id: str | None = None,
        embedding_dimension: int = DEFAULT_EMBEDDING_DIM,
    ) -> None:
        self.graph_store = (
            Neo4jGraphStore.from_env() if graph_store is _AUTO else graph_store
        )
        self.vector_store = (
            QdrantVectorStore.from_env(collection_name)
            if vector_store is _AUTO
            else vector_store
        )
        self.embedder = embedder or OllamaEmbedder(
            fallback_dimension=embedding_dimension
        )
        self.collection_name = collection_name
        self.company_id = company_id
        self.company_name = company_name
        self.tenant_id = tenant_id
        self.embedding_dimension = embedding_dimension

    @classmethod
    def from_env(cls) -> "PersistenceManager":
        return cls()

    def persist_invoice(
        self,
        invoice: InvoiceSchema,
        *,
        rule_decision: Any = None,
        source_file: str | None = None,
        source_sha256: str | None = None,
        tenant_id: str | None = None,
    ) -> PersistenceResult:
        errors: list[str] = []
        try:
            resolved_tenant = self._resolve_tenant_id(invoice, tenant_id)
        except ValueError as exc:
            return PersistenceResult(
                invoice_id=self.build_invoice_id(invoice),
                graph_status="FAILED",
                vector_status="FAILED",
                errors=(f"tenant: {exc}",),
            )

        resolved_sha: str | None = None
        sha_error: str | None = None
        try:
            resolved_sha = self._resolve_source_sha256(
                invoice, source_file=source_file, explicit=source_sha256
            )
        except ValueError as exc:
            sha_error = str(exc)
        if resolved_sha is None and sha_error is None:
            sha_error = (
                "source_sha256 unavailable "
                "(provide source_sha256, invoice metadata or a readable source_file)"
            )

        invoice_id = self.build_invoice_id(
            invoice, tenant_id=resolved_tenant, source_sha256=resolved_sha
        )
        point_id = self.build_point_id(
            invoice, tenant_id=resolved_tenant, source_sha256=resolved_sha
        )

        if self.graph_store is None and self.vector_store is None:
            return PersistenceResult(
                invoice_id=invoice_id,
                graph_status="UNAVAILABLE",
                vector_status="UNAVAILABLE",
                errors=("neo4j: driver unavailable", "qdrant: client unavailable"),
            )

        record: InvoiceGraphRecord | None = None
        if self.graph_store is None:
            graph_status = "UNAVAILABLE"
            errors.append("neo4j: driver unavailable")
        elif resolved_sha is None:
            graph_status = "FAILED"
            errors.append(f"neo4j: {sha_error}")
        else:
            record = self.build_graph_record(
                invoice,
                rule_decision=rule_decision,
                source_file=source_file,
                source_sha256=resolved_sha,
                tenant_id=resolved_tenant,
            )
            try:
                self.graph_store.upsert_invoice(record)
                graph_status = "PERSISTED"
            except Exception as exc:
                graph_status = "FAILED"
                errors.append(f"neo4j: {exc}")

        if self.vector_store is None:
            return PersistenceResult(
                invoice_id=invoice_id,
                graph_status=graph_status,
                vector_status="UNAVAILABLE",
                errors=tuple(errors) + ("qdrant: client unavailable",),
            )

        if resolved_sha is None:
            return PersistenceResult(
                invoice_id=invoice_id,
                graph_status=graph_status,
                vector_status="FAILED",
                errors=tuple(errors) + (f"qdrant: {sha_error}",),
            )

        if record is None:
            record = self.build_graph_record(
                invoice,
                rule_decision=rule_decision,
                source_file=source_file,
                source_sha256=resolved_sha,
                tenant_id=resolved_tenant,
            )

        text = self.build_vector_text(invoice)
        try:
            vector = list(self.embedder(text))
            if not vector:
                raise ValueError("empty embedding")
        except Exception as exc:
            errors.append(f"embedding: {exc}")
            return PersistenceResult(
                invoice_id=invoice_id,
                graph_status=graph_status,
                vector_status="FAILED",
                errors=tuple(errors),
            )

        try:
            self.vector_store.ensure_collection(len(vector))
            self.vector_store.upsert(
                point_id,
                vector,
                self.build_vector_payload(
                    invoice,
                    record,
                    rule_decision=rule_decision,
                    source_sha256=resolved_sha,
                    tenant_id=resolved_tenant,
                    point_id=point_id,
                ),
            )
            vector_status = "INDEXED"
        except Exception as exc:
            vector_status = "FAILED"
            errors.append(f"qdrant: {exc}")

        return PersistenceResult(
            invoice_id=invoice_id,
            graph_status=graph_status,
            vector_status=vector_status,
            point_id=point_id,
            embedding_source=getattr(self.embedder, "last_source", None),
            errors=tuple(errors),
        )

    def persist_invoices(
        self,
        invoices: Sequence[InvoiceSchema],
        *,
        rule_decision: Any = None,
        source_file: str | None = None,
        source_sha256: str | None = None,
        tenant_id: str | None = None,
    ) -> list[PersistenceResult]:
        return [
            self.persist_invoice(
                invoice,
                rule_decision=rule_decision,
                source_file=source_file,
                source_sha256=source_sha256,
                tenant_id=tenant_id,
            )
            for invoice in invoices
        ]

    def build_graph_record(
        self,
        invoice: InvoiceSchema,
        *,
        rule_decision: Any = None,
        source_file: str | None = None,
        source_sha256: str | None = None,
        tenant_id: str | None = None,
    ) -> InvoiceGraphRecord:
        resolved_sha = source_sha256 or self._resolve_source_sha256(
            invoice, source_file=source_file
        )
        if resolved_sha is None:
            raise ValueError(
                "source_sha256 unavailable "
                "(provide source_sha256, invoice metadata or a readable source_file)"
            )
        resolved_tenant = self._resolve_tenant_id(invoice, tenant_id)
        invoice_id = self.build_invoice_id(
            invoice, tenant_id=resolved_tenant, source_sha256=resolved_sha
        )
        line_items = self.build_line_records(
            invoice, source_sha256=resolved_sha, tenant_id=resolved_tenant
        )
        rule = self._rule_outcome(rule_decision)

        supplier_id: str | None = None
        supplier_properties: dict[str, Any] = {}
        supplier = invoice.supplier
        if supplier is not None:
            supplier_id = self._party_id(
                resolved_tenant, "Supplier", supplier.name, supplier
            )
            siret = getattr(supplier, "siret", None)
            supplier_properties = {
                "name": supplier.name,
                "siren": siret[:9] if siret and len(siret) == 14 else None,
                "siret": siret,
                "vat_number": getattr(supplier, "vat_number", None),
                "country": invoice.metadata.get("supplier_country"),
            }

        invoice_properties = {
            "invoice_id": invoice_id,
            "source_sha256": resolved_sha,
            "document_number": invoice.reference.invoice_number,
            "issue_date": (
                invoice.reference.issue_date.isoformat()
                if invoice.reference.issue_date
                else None
            ),
            "gross_amount": self._decimal_text(invoice.amount.gross_amount),
            "net_amount": self._decimal_text(invoice.amount.net_amount),
            "tax_amount": self._decimal_text(invoice.amount.tax_amount),
            "currency": invoice.amount.currency,
            "direction": invoice.direction.value,
            "validation_status": invoice.validation_status.value,
            "contract_version": CONTRACT_VERSION,
        }
        return InvoiceGraphRecord(
            tenant_id=resolved_tenant,
            supplier_id=supplier_id,
            supplier_properties={
                key: value
                for key, value in supplier_properties.items()
                if value is not None
            },
            invoice_id=invoice_id,
            source_sha256=resolved_sha,
            invoice_properties={
                key: value
                for key, value in invoice_properties.items()
                if value is not None
            },
            rule_outcome=rule,
            line_items=line_items,
        )

    def build_line_records(
        self,
        invoice: InvoiceSchema,
        *,
        source_sha256: str,
        tenant_id: str,
    ) -> tuple[InvoiceLineRecord, ...]:
        return tuple(
            InvoiceLineRecord(
                line_id=self.build_line_id(tenant_id, source_sha256, index),
                line_index=index,
                description=line.description,
                quantity=self._decimal_text(line.quantite) or "0",
                unit_price=self._decimal_text(line.prix_unitaire_ht) or "0",
                total_amount=self._decimal_text(line.total_ht) or "0",
                tax_rate=self._decimal_text(line.taux_tva) or "0",
            )
            for index, line in enumerate(invoice.lines)
        )

    def build_line_id(
        self, tenant_id: str, source_sha256: str, line_index: int
    ) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"{tenant_id}:{source_sha256}:{line_index}",
            )
        )

    def build_invoice_id(
        self,
        invoice: InvoiceSchema,
        *,
        tenant_id: str | None = None,
        source_sha256: str | None = None,
    ) -> str:
        if tenant_id is not None and source_sha256 is not None:
            return str(uuid5(NAMESPACE_URL, f"{tenant_id}:{source_sha256}"))
        # Informational fallback when the source hash is unknown (nothing is written).
        if invoice.document_id is not None:
            return str(invoice.document_id)
        party = (
            invoice.customer
            if invoice.direction is InvoiceDirection.SALE
            else invoice.supplier
        )
        seed = "|".join(
            [
                invoice.company_id or self.company_id,
                party.name if party is not None else "TIERS_INCONNU",
                invoice.reference.invoice_number or "SANS_NUMERO",
                (
                    invoice.reference.issue_date.isoformat()
                    if invoice.reference.issue_date
                    else "SANS_DATE"
                ),
                self._decimal_text(invoice.amount.gross_amount) or "0",
                invoice.amount.currency,
            ]
        )
        return str(uuid5(NAMESPACE_URL, f"invoice:{seed}"))

    def build_point_id(
        self,
        invoice: InvoiceSchema,
        *,
        tenant_id: str | None = None,
        source_sha256: str | None = None,
    ) -> str:
        invoice_id = self.build_invoice_id(
            invoice, tenant_id=tenant_id, source_sha256=source_sha256
        )
        return str(uuid5(NAMESPACE_URL, f"qdrant:{invoice_id}"))

    def build_vector_text(self, invoice: InvoiceSchema) -> str:
        supplier = invoice.supplier.name if invoice.supplier else ""
        customer = invoice.customer.name if invoice.customer else ""
        lines = ", ".join(
            f"{line.description} ({line.quantite} x {line.prix_unitaire_ht})"
            for line in invoice.lines
        )
        return (
            f"Facture {invoice.reference.invoice_number or 'SANS_NUMERO'} "
            f"du {invoice.reference.issue_date or 'SANS_DATE'}. "
            f"Fournisseur : {supplier or 'INCONNU'}. "
            f"Client : {customer or 'INCONNU'}. "
            f"HT : {self._decimal_text(invoice.amount.net_amount) or '0'} "
            f"{invoice.amount.currency}, "
            f"TVA : {self._decimal_text(invoice.amount.tax_amount) or '0'}, "
            f"TTC : {self._decimal_text(invoice.amount.gross_amount)}. "
            f"Catégorie : {invoice.category or 'INCONNUE'}. "
            f"Lignes : {lines or 'aucune'}."
        )

    def build_vector_payload(
        self,
        invoice: InvoiceSchema,
        record: InvoiceGraphRecord,
        *,
        rule_decision: Any = None,
        source_sha256: str | None = None,
        tenant_id: str | None = None,
        point_id: str | None = None,
    ) -> dict[str, Any]:
        rule = self._rule_payload(rule_decision)
        resolved_sha = source_sha256 or self._resolve_source_sha256(invoice)
        if resolved_sha is None:
            raise ValueError(
                "source_sha256 unavailable "
                "(provide source_sha256, invoice metadata or a readable source_file)"
            )
        resolved_tenant = self._resolve_tenant_id(invoice, tenant_id)
        supplier_id = record.supplier_id
        if supplier_id is None and invoice.supplier is not None:
            supplier_id = self._party_id(
                resolved_tenant,
                "Supplier",
                invoice.supplier.name,
                invoice.supplier,
            )
        payload = InvoiceVectorPayloadV1(
            contract_version=CONTRACT_VERSION,
            tenant_id=resolved_tenant,
            source_sha256=resolved_sha,
            doc_type=invoice.document_type.value,
            invoice_id=record.invoice_id,
            point_id=point_id,
            company_id=invoice.company_id or self.company_id,
            supplier_id=supplier_id,
            party_name=record.supplier_properties.get("name"),
            supplier_name=(
                invoice.supplier.name if invoice.supplier is not None else None
            ),
            customer_name=(
                invoice.customer.name if invoice.customer is not None else None
            ),
            invoice_number=invoice.reference.invoice_number,
            issue_date=(
                invoice.reference.issue_date.isoformat()
                if invoice.reference.issue_date
                else None
            ),
            net_amount=self._decimal_text(invoice.amount.net_amount),
            tax_amount=self._decimal_text(invoice.amount.tax_amount),
            gross_amount=self._decimal_text(invoice.amount.gross_amount),
            currency=invoice.amount.currency,
            direction=invoice.direction.value,
            document_type=invoice.document_type.value,
            validation_status=invoice.validation_status.value,
            category=invoice.category,
            rule=rule,
            text=self.build_vector_text(invoice),
        )
        return payload.model_dump(mode="json")

    def _resolve_tenant_id(
        self, invoice: InvoiceSchema, explicit: str | None = None
    ) -> str:
        if explicit is not None:
            candidate = explicit
        elif self.tenant_id is not None:
            candidate = self.tenant_id
        else:
            candidate = (
                os.getenv("DSH_TENANT_ID")
                or invoice.company_id
                or self.company_id
            )
        if not isinstance(candidate, str) or not _TENANT_ID_PATTERN.fullmatch(
            candidate
        ):
            raise ValueError("tenant_id must match [A-Za-z0-9._:-]{1,64}")
        return candidate

    def _resolve_source_sha256(
        self,
        invoice: InvoiceSchema,
        *,
        source_file: str | None = None,
        explicit: str | None = None,
    ) -> str | None:
        candidate = explicit
        if candidate is None:
            metadata_value = invoice.metadata.get("source_sha256")
            if isinstance(metadata_value, str):
                candidate = metadata_value
        if candidate is not None:
            normalised = candidate.strip().lower()
            if not _SHA256_PATTERN.fullmatch(normalised):
                raise ValueError(
                    "source_sha256 must be 64 lowercase hex characters"
                )
            return normalised
        for path in (source_file, invoice.source_file):
            if path:
                digest = _file_sha256(path)
                if digest is not None:
                    return digest
        return None

    @staticmethod
    def _rule_outcome(rule_decision: Any) -> dict[str, Any] | None:
        if rule_decision is None:
            return None
        rule_id = getattr(rule_decision, "rule_id", None)
        if not rule_id:
            return None
        return {
            "rule_id": rule_id,
            "outcome": getattr(rule_decision, "status", None) or "UNKNOWN",
            "reason": getattr(rule_decision, "reason", None) or "",
        }

    @staticmethod
    def _rule_payload(rule_decision: Any) -> dict[str, Any]:
        if rule_decision is None:
            return {}
        return {
            "rule_id": getattr(rule_decision, "rule_id", None),
            "rule_version": getattr(rule_decision, "rule_version", None),
            "status": getattr(rule_decision, "status", None),
            "vat_regime": getattr(rule_decision, "vat_regime", None),
            "include_in_vat_balance": getattr(
                rule_decision, "include_in_vat_balance", None
            ),
            "accounting_treatment": getattr(
                rule_decision, "accounting_treatment", None
            ),
        }

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    def _party_id(
        self,
        tenant_id: str,
        party_label: str,
        party_name: str,
        party: Any,
    ) -> str:
        seed = "|".join(
            [
                tenant_id,
                party_label,
                party_name,
                getattr(party, "siret", None) or "",
                getattr(party, "vat_number", None) or "",
            ]
        )
        return str(uuid5(NAMESPACE_URL, f"party:{seed}"))

    def close(self) -> None:
        for store in (self.graph_store, self.vector_store):
            close = getattr(store, "close", None)
            if callable(close):
                close()


__all__ = [
    "DEFAULT_COLLECTION",
    "InvoiceGraphRecord",
    "InvoiceLineRecord",
    "Neo4jGraphStore",
    "OllamaEmbedder",
    "PersistenceManager",
    "PersistenceResult",
    "QdrantVectorStore",
    "deterministic_embedding",
]
