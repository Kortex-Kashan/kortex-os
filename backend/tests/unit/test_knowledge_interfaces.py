"""Unit tests for Knowledge Engine interface/Protocol contracts (Milestone M1
— redesigned scope).

Verifies the Protocol contracts declared in `interfaces.py` are correctly
shaped as forward contracts. Dummy conforming classes exist solely to prove
each Protocol's method surface is satisfiable via structural
(`@runtime_checkable`) `isinstance` checks — they implement no real
behavior and are not milestone implementations of any kind.
"""

from __future__ import annotations

import inspect
from typing import List

from kortex.engines.knowledge.interfaces import (
    IEngineDiagnostics,
    IKnowledgeAnnotationManager,
    IKnowledgeEngine,
    IKnowledgeGraph,
    IKnowledgeRecordManager,
    IKnowledgeSearchEngine,
    IKnowledgeSourceProvider,
)
from kortex.engines.knowledge.models import KnowledgeRecord

ALL_M1_PROTOCOLS = (
    IKnowledgeGraph,
    IKnowledgeRecordManager,
    IKnowledgeAnnotationManager,
    IKnowledgeSourceProvider,
    IKnowledgeSearchEngine,
    IKnowledgeEngine,
    IEngineDiagnostics,
)

# -- Protocol declaration -------------------------------------------------------


def test_all_m1_interfaces_are_runtime_checkable_protocols() -> None:
    for protocol in ALL_M1_PROTOCOLS:
        assert getattr(protocol, "_is_runtime_protocol", False) is True


# -- IKnowledgeSourceProvider.ingest() return contract --------------------------


def test_source_provider_ingest_returns_knowledge_record_list_not_node_list() -> None:
    """Confirms the redesigned M1 contract: ingestion produces
    `SOURCE_EVIDENCE`-trust-state `KnowledgeRecord`s, not raw
    `KnowledgeNode`s, per the Chief Architect's explicit instruction."""
    signature = inspect.signature(IKnowledgeSourceProvider.ingest)
    assert signature.return_annotation == "List[KnowledgeRecord]"


def test_index_source_return_type_matches_ingest_return_type() -> None:
    """`IKnowledgeEngine.index_source()` wraps `IKnowledgeSourceProvider.ingest()`
    — their return types must not drift apart (M1 fast-follow reconciliation)."""
    ingest_return = inspect.signature(IKnowledgeSourceProvider.ingest).return_annotation
    index_source_return = inspect.signature(IKnowledgeEngine.index_source).return_annotation
    assert ingest_return == index_source_return == "List[KnowledgeRecord]"


# -- IKnowledgeRecordManager.promote() contract shape ---------------------------


def test_record_manager_promote_contract_includes_actor_type_and_trust_state() -> None:
    """`promote()` must express the domain operation (who is promoting, to
    what trust state) even though enforcement of the USER-only invariant is
    Milestone M6 behavior, not implemented here."""
    parameters = inspect.signature(IKnowledgeRecordManager.promote).parameters
    assert "actor_id" in parameters
    assert "actor_type" in parameters
    assert "new_trust_state" in parameters


# -- Structural conformance (dummy classes prove the contract is satisfiable) --


class _DummyGraph:
    def add_node(self, node):
        return node

    def add_relationship(self, relationship):
        return relationship

    def find_neighbors(self, node_id, tenant_id):
        return []

    def traverse(self, node_id, tenant_id, max_hops):
        return []


class _DummyRecordManager:
    async def create_record(self, record):
        return record

    async def get_current(self, record_id, tenant_id):
        return None

    async def get_lineage(self, record_id, tenant_id):
        return []

    async def supersede(self, record_id, tenant_id, new_version):
        return new_version

    async def promote(self, record_id, tenant_id, actor_id, actor_type, new_trust_state):
        raise NotImplementedError


class _DummyAnnotationManager:
    async def add_annotation(self, annotation):
        return annotation

    async def list_annotations(self, target_record_id, tenant_id):
        return []


class _DummySourceProvider:
    def source_id(self):
        return "dummy"

    async def ingest(self, tenant_id):
        return []


class _DummySearchEngine:
    async def search_text(self, query):
        raise NotImplementedError

    async def search_graph(self, query):
        raise NotImplementedError

    async def search_hybrid(self, query):
        raise NotImplementedError


class _DummyKnowledgeEngine:
    async def query_knowledge(self, query):
        raise NotImplementedError

    async def index_source(self, source_id, tenant_id):
        return []

    async def load_pack(self, pack):
        return pack

    async def search(self, query):
        raise NotImplementedError


class _DummyDiagnostics:
    def health(self):
        return {}

    def metrics(self):
        return {}

    def diagnostics(self):
        return {}

    def status(self):
        return "READY"

    def version(self):
        return "0.1.0"

    def capabilities(self):
        return []


def test_dummy_conforming_class_satisfies_iknowledgegraph() -> None:
    assert isinstance(_DummyGraph(), IKnowledgeGraph)


def test_dummy_conforming_class_satisfies_iknowledgerecordmanager() -> None:
    assert isinstance(_DummyRecordManager(), IKnowledgeRecordManager)


def test_dummy_conforming_class_satisfies_iknowledgeannotationmanager() -> None:
    assert isinstance(_DummyAnnotationManager(), IKnowledgeAnnotationManager)


def test_dummy_conforming_class_satisfies_iknowledgesourceprovider() -> None:
    assert isinstance(_DummySourceProvider(), IKnowledgeSourceProvider)


def test_dummy_conforming_class_satisfies_iknowledgesearchengine() -> None:
    assert isinstance(_DummySearchEngine(), IKnowledgeSearchEngine)


def test_dummy_conforming_class_satisfies_iknowledgeengine() -> None:
    assert isinstance(_DummyKnowledgeEngine(), IKnowledgeEngine)


def test_dummy_conforming_class_satisfies_ienginediagnostics() -> None:
    assert isinstance(_DummyDiagnostics(), IEngineDiagnostics)


def test_incomplete_class_does_not_satisfy_iknowledgerecordmanager() -> None:
    """Negative control — proves the structural check is real, not a no-op."""

    class _Incomplete:
        async def create_record(self, record):
            return record

    assert not isinstance(_Incomplete(), IKnowledgeRecordManager)
