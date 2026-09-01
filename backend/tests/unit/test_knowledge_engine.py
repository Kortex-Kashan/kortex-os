"""Unit tests for the `KnowledgeEngine` facade (`engine.py`).

Constructs a real `Kernel()` + real `StorageEngine`, calling `initialize()`/
`start()` directly (bypassing `BootEngine`'s full topological boot
sequence, which `test_knowledge_engine_integration.py` exercises
end-to-end) -- matching this codebase's established preference for real
components over mocks (`Kernel()` and `StorageEngine` construction/
`initialize()` are both cheap, local, SQLite-backed operations, no
different in cost from the `_build_data_store` fixture every other
Knowledge Engine test file already uses).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.db import DatabaseEngineManager
from kortex.core.exceptions import EngineStateError
from kortex.core.kernel import Kernel
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.exceptions import KnowledgeSourceNotFoundError
from kortex.engines.knowledge.models import (
    KnowledgeNode,
    KnowledgePack,
    KnowledgeQuery,
    KnowledgeRelationship,
    KnowledgeRelationshipType,
    KnowledgeTrustState,
)
from kortex.engines.storage.engine import StorageEngine

_REFERENCE_SOURCE_ID = "kortex.knowledge.source.reference"


async def _build_ready_engine(tmp_path: Path) -> Tuple[Kernel, StorageEngine, KnowledgeEngine]:
    """`Kernel.boot()` (not a manual `initialize()`/`start()` call) is
    required here: it is the only thing that connects the database and
    calls `create_all_tables()` before any engine's `initialize()` runs
    (mirrors `test_storage_integration.py`'s own exact pattern). `BootEngine`
    topologically sorts by each engine's own `dependencies` property, so
    registering both engines before one `boot()` call correctly initializes
    Storage before Knowledge (`KnowledgeEngine.dependencies == ["storage",
    "registry"]`)."""
    kernel = Kernel()
    # `Kernel()` always defaults to a real, shared `./kortex_local.db` file
    # (`kortex.core.db.DatabaseEngineManager.__init__`'s own hardcoded
    # default) when no override is given -- a pre-existing test-isolation
    # gap unrelated to this closure work (confirmed: it is not tmp_path
    # or test-scoped in any way). Overriding the private `_db_manager`
    # before `boot()` (the same poke-private-state pattern already used by
    # `test_knowledge_persistence.py`'s `manager._data_store = ...`) keeps
    # this test file's assertions isolated per test.
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path}/kernel.db")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    knowledge_engine = KnowledgeEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(knowledge_engine)
    await kernel.boot()
    return kernel, storage_engine, knowledge_engine


def _pack(data: bytes, asset_id: str = "pack-1", tenant_id: str = "tenant-a") -> KnowledgePack:
    import hashlib

    return KnowledgePack(
        asset_id=asset_id,
        tenant_id=tenant_id,
        manifest={"name": "hr-ontology"},
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        mime_type="application/x-kortex-knowledge",
        storage_key=f"packs/{asset_id}.kortex-knowledge",
    )


# -- Lifecycle / capability registration ----------------------------------------


@pytest.mark.asyncio
async def test_initialize_wires_managers_and_registers_capabilities(tmp_path: Path) -> None:
    kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)

    assert knowledge_engine.state.value == "RUNNING"
    for cap_name in [
        "kortex.knowledge.query.search",
        "kortex.knowledge.graph.traverse",
        "kortex.knowledge.graph.list",
        "kortex.knowledge.pack.load",
        "kortex.knowledge.source.index",
    ]:
        descriptor = kernel.get_capability(cap_name)
        assert descriptor.provider == "knowledge"
    # M7.5-W1: `kortex.knowledge.graph.list` was already registered with the
    # Kernel at initialize() time but was missing from `_REGISTERED_CAPABILITIES`
    # (a self-reporting bug, not a dispatch gap) -- fixed alongside the tenant-
    # isolation work since this method was already being touched.
    assert set(knowledge_engine.capabilities()) == {
        "kortex.knowledge.query.search",
        "kortex.knowledge.graph.traverse",
        "kortex.knowledge.graph.list",
        "kortex.knowledge.pack.load",
        "kortex.knowledge.source.index",
    }


@pytest.mark.asyncio
async def test_stop_transitions_to_stopped_and_is_idempotent(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    await knowledge_engine.stop()
    assert knowledge_engine.state.value == "STOPPED"
    await knowledge_engine.stop()  # must be a safe no-op
    assert knowledge_engine.state.value == "STOPPED"


@pytest.mark.asyncio
async def test_initialize_without_storage_engine_fails_closed() -> None:
    """Mirrors Storage/Security Engine's own "boot fails closed" pattern:
    if `initialize()` raises for any reason (here: no "storage" engine was
    ever registered with the Kernel), the engine must transition to FAILED
    and re-raise -- never silently swallow the failure or leave itself in
    an ambiguous READY/RUNNING-looking state."""
    kernel = Kernel()
    engine = KnowledgeEngine()
    with pytest.raises(Exception):
        await engine.initialize(kernel)
    assert engine.state == EngineState.FAILED

    # A FAILED engine must still reject operations, not silently proceed.
    query = KnowledgeQuery(query_id="q1", tenant_id="tenant-a", query_text="anything")
    with pytest.raises(EngineStateError):
        await engine.search(query)


@pytest.mark.asyncio
async def test_operations_before_initialize_raise_engine_state_error() -> None:
    engine = KnowledgeEngine()
    query = KnowledgeQuery(query_id="q1", tenant_id="tenant-a", query_text="anything")
    with pytest.raises(EngineStateError):
        await engine.search(query)


@pytest.mark.asyncio
async def test_health_status_version_diagnostics(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    health = knowledge_engine.health()
    assert health["engine"] == "knowledge"
    assert health["healthy"] is True
    assert health["lineage_manager_implemented"] is True
    assert health["pack_manager_implemented"] is True
    assert knowledge_engine.status() == "RUNNING"
    assert isinstance(knowledge_engine.version(), str)
    diagnostics = knowledge_engine.diagnostics()
    assert diagnostics["capabilities"] == knowledge_engine.capabilities()
    assert _REFERENCE_SOURCE_ID in diagnostics["registered_source_providers"]


# -- index_source ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_source_persists_records_via_lineage_manager(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    created = await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")

    assert len(created) == 2
    assert all(r.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE for r in created)

    # Prove persistence actually happened through the facade's own lineage
    # manager, not merely returned raw from ingest().
    assert knowledge_engine._lineage_manager is not None
    for record in created:
        current = await knowledge_engine._lineage_manager.get_current(record.record_id, "tenant-a")
        assert current is not None
        assert current.version_id == record.version_id


@pytest.mark.asyncio
async def test_index_source_unknown_source_raises(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    with pytest.raises(KnowledgeSourceNotFoundError):
        await knowledge_engine.index_source("kortex.knowledge.source.nonexistent", "tenant-a")


@pytest.mark.asyncio
async def test_index_source_never_produces_confirmed_trust_state(tmp_path: Path) -> None:
    """Adversarial: proves the security invariant documented in `engine.py`'s
    module docstring in practice, not merely by code inspection -- this
    facade path can never mint HUMAN_CONFIRMED/HUMAN_CORRECTED knowledge."""
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    created = await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")
    assert all(
        r.trust_state not in (KnowledgeTrustState.HUMAN_CONFIRMED, KnowledgeTrustState.HUMAN_CORRECTED)
        for r in created
    )


@pytest.mark.asyncio
async def test_index_source_emits_knowledge_node_indexed_event(tmp_path: Path) -> None:
    kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    received: List[Any] = []
    kernel.subscribe_event("knowledge.node.indexed", lambda event: received.append(event))

    await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")

    assert len(received) == 1
    assert received[0].topic == "knowledge.node.indexed"
    assert received[0].payload["tenant_id"] == "tenant-a"
    assert received[0].payload["record_count"] == 2


# -- load_pack --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_pack_delegates_to_pack_manager(tmp_path: Path) -> None:
    kernel, storage_engine, knowledge_engine = await _build_ready_engine(tmp_path)
    data = b"pack-payload"
    pack = _pack(data)
    await storage_engine.object.put_object(pack.bucket_name, pack.storage_key, data)

    loaded = await knowledge_engine.load_pack(pack)
    assert loaded.asset_id == "pack-1"
    assert knowledge_engine.metrics()["packs_loaded"] == 1


@pytest.mark.asyncio
async def test_load_pack_emits_knowledge_pack_loaded_event(tmp_path: Path) -> None:
    kernel, storage_engine, knowledge_engine = await _build_ready_engine(tmp_path)
    received: List[Any] = []
    kernel.subscribe_event("knowledge.pack.loaded", lambda event: received.append(event))

    data = b"pack-event-payload"
    pack = _pack(data, asset_id="pack-evt")
    await storage_engine.object.put_object(pack.bucket_name, pack.storage_key, data)
    await knowledge_engine.load_pack(pack)

    assert len(received) == 1
    assert received[0].payload["asset_id"] == "pack-evt"


# -- search / query_knowledge -----------------------------------------------------


@pytest.mark.asyncio
async def test_search_finds_indexed_source_evidence_when_requested(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")

    query = KnowledgeQuery(
        query_id="q1",
        tenant_id="tenant-a",
        query_text="fact",
        trust_states=[KnowledgeTrustState.SOURCE_EVIDENCE],
    )
    result = await knowledge_engine.search(query)
    assert len(result.matching_records) == 2
    assert knowledge_engine.metrics()["queries_executed"] == 1


@pytest.mark.asyncio
async def test_query_knowledge_is_identical_to_search(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")

    query = KnowledgeQuery(
        query_id="q1",
        tenant_id="tenant-a",
        query_text="fact",
        trust_states=[KnowledgeTrustState.SOURCE_EVIDENCE],
    )
    via_search = await knowledge_engine.search(query)
    via_query_knowledge = await knowledge_engine.query_knowledge(query)
    assert [r.record_id for r in via_search.matching_records] == [
        r.record_id for r in via_query_knowledge.matching_records
    ]


@pytest.mark.asyncio
async def test_search_respects_tenant_isolation(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    await knowledge_engine.index_source(_REFERENCE_SOURCE_ID, "tenant-a")

    query = KnowledgeQuery(
        query_id="q1",
        tenant_id="tenant-b",
        query_text="fact",
        trust_states=[KnowledgeTrustState.SOURCE_EVIDENCE],
    )
    result = await knowledge_engine.search(query)
    assert result.matching_records == []


# -- traverse_graph (additive facade method) --------------------------------------


@pytest.mark.asyncio
async def test_traverse_graph_delegates_to_graph(tmp_path: Path) -> None:
    _kernel, _storage, knowledge_engine = await _build_ready_engine(tmp_path)
    node_a = KnowledgeNode(node_id="a", tenant_id="tenant-a", entity_type="thing", label="A")
    node_b = KnowledgeNode(node_id="b", tenant_id="tenant-a", entity_type="thing", label="B")
    knowledge_engine.graph.add_node(node_a)
    knowledge_engine.graph.add_node(node_b)
    knowledge_engine.graph.add_relationship(
        KnowledgeRelationship(
            relationship_id="r1",
            tenant_id="tenant-a",
            source_node_id="a",
            target_node_id="b",
            relationship_type=KnowledgeRelationshipType.RELATES_TO,
        )
    )

    neighbors = await knowledge_engine.traverse_graph("a", "tenant-a", max_hops=1)
    assert [n.node_id for n in neighbors] == ["b"]
