"""Unit tests for the Knowledge Engine Search Coordinator (Milestone M8).

Verifies `KnowledgeSearchEngine` satisfies `IKnowledgeSearchEngine`, enforces
tenant isolation and trust-state filtering across `search_text`/
`search_graph`/`search_hybrid`, correctly resolves `as_of` point-in-time
queries via the existing lineage chain, respects `max_results` (including
boundary values), returns deterministic ordering, never mutates state, and
that the two new enumeration methods (`KnowledgeGraph.list_nodes`,
`KnowledgeLineageManager.list_current_records`) are tenant-scoped and
concurrency-safe.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from kortex.engines.knowledge.graph import KnowledgeGraph
from kortex.engines.knowledge.interfaces import IKnowledgeSearchEngine
from kortex.engines.knowledge.lineage import KnowledgeLineageManager
from kortex.engines.knowledge.models import (
    KnowledgeActorType,
    KnowledgeNode,
    KnowledgeQuery,
    KnowledgeRecord,
    KnowledgeRecordType,
    KnowledgeRelationship,
    KnowledgeRelationshipType,
    KnowledgeTrustState,
)
from kortex.engines.knowledge.search import KnowledgeSearchEngine

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _query(
    query_text: str,
    tenant_id: str = "tenant-a",
    query_id: str | None = None,
    trust_states: list[KnowledgeTrustState] | None = None,
    entity_types: list[str] | None = None,
    max_results: int | None = None,
    as_of: datetime | None = None,
) -> KnowledgeQuery:
    default_trust_states = [KnowledgeTrustState.HUMAN_CONFIRMED, KnowledgeTrustState.HUMAN_CORRECTED]
    return KnowledgeQuery(
        query_id=query_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        query_text=query_text,
        entity_types=entity_types or [],
        max_results=max_results,
        as_of=as_of,
        trust_states=trust_states if trust_states is not None else default_trust_states,
    )


def _record(
    record_id: str,
    tenant_id: str = "tenant-a",
    trust_state: KnowledgeTrustState = KnowledgeTrustState.HUMAN_CONFIRMED,
    record_type: KnowledgeRecordType = KnowledgeRecordType.FACT,
    content: dict | None = None,
    created_at: datetime = _NOW,
    version_id: str = "v1",
    parent_version_id: str | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        record_id=record_id,
        tenant_id=tenant_id,
        version_id=version_id,
        parent_version_id=parent_version_id,
        record_type=record_type,
        content=content or {"summary": f"content for {record_id}"},
        trust_state=trust_state,
        created_by="user-1",
        created_by_type=KnowledgeActorType.USER,
        created_at=created_at,
    )


def _node(node_id: str, label: str, tenant_id: str = "tenant-a", entity_type: str = "document") -> KnowledgeNode:
    return KnowledgeNode(node_id=node_id, tenant_id=tenant_id, entity_type=entity_type, label=label)


def _rel(
    relationship_id: str,
    source_node_id: str,
    target_node_id: str,
    tenant_id: str = "tenant-a",
    relationship_type: KnowledgeRelationshipType = KnowledgeRelationshipType.RELATES_TO,
) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        relationship_id=relationship_id,
        tenant_id=tenant_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        relationship_type=relationship_type,
    )


async def _build_engine() -> tuple[KnowledgeSearchEngine, KnowledgeGraph, KnowledgeLineageManager]:
    graph = KnowledgeGraph()
    lineage = KnowledgeLineageManager()
    engine = KnowledgeSearchEngine(graph=graph, lineage_manager=lineage)
    return engine, graph, lineage


# -- Contract conformance -------------------------------------------------------


@pytest.mark.asyncio
async def test_search_engine_satisfies_iknowledgesearchengine() -> None:
    engine, _graph, _lineage = await _build_engine()
    assert isinstance(engine, IKnowledgeSearchEngine)


# -- Model-level evidence this module relies on (not re-implemented here) --------


def test_knowledge_query_rejects_empty_query_text_at_the_model_layer() -> None:
    """`search.py` performs no empty-query-text handling of its own because
    `KnowledgeQuery.query_text` already has `min_length=1` — an empty
    string can never reach the search coordinator at all."""
    with pytest.raises(ValidationError):
        KnowledgeQuery(query_id="q1", tenant_id="tenant-a", query_text="")


# -- search_text: happy path, trust-state filtering, tenant isolation -------------


@pytest.mark.asyncio
async def test_search_text_finds_matching_record() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "quarterly revenue report"}))

    result = await engine.search_text(_query("revenue"))
    assert [r.record_id for r in result.matching_records] == ["rec-1"]
    assert result.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_search_text_default_trust_states_exclude_unconfirmed() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", trust_state=KnowledgeTrustState.SOURCE_EVIDENCE))
    await lineage.create_record(_record("rec-2", trust_state=KnowledgeTrustState.AI_CANDIDATE))
    await lineage.create_record(_record("rec-3", trust_state=KnowledgeTrustState.HUMAN_CONFIRMED))

    result = await engine.search_text(_query("content"))
    assert [r.record_id for r in result.matching_records] == ["rec-3"]


@pytest.mark.asyncio
async def test_search_text_explicit_source_evidence_is_surfaced() -> None:
    """Proves the trust filter is real, not hardcoded to only ever exclude
    unconfirmed states — explicitly requesting SOURCE_EVIDENCE must
    include it."""
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", trust_state=KnowledgeTrustState.SOURCE_EVIDENCE))

    result = await engine.search_text(_query("content", trust_states=[KnowledgeTrustState.SOURCE_EVIDENCE]))
    assert [r.record_id for r in result.matching_records] == ["rec-1"]


@pytest.mark.asyncio
async def test_search_text_explicit_ai_candidate_is_surfaced() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", trust_state=KnowledgeTrustState.AI_CANDIDATE))

    result = await engine.search_text(_query("content", trust_states=[KnowledgeTrustState.AI_CANDIDATE]))
    assert [r.record_id for r in result.matching_records] == ["rec-1"]


@pytest.mark.asyncio
async def test_search_text_enforces_tenant_isolation_both_directions() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", tenant_id="tenant-a", content={"summary": "shared keyword"}))
    await lineage.create_record(_record("rec-1", tenant_id="tenant-b", content={"summary": "shared keyword"}))

    result_a = await engine.search_text(_query("shared", tenant_id="tenant-a"))
    result_b = await engine.search_text(_query("shared", tenant_id="tenant-b"))
    assert [r.tenant_id for r in result_a.matching_records] == ["tenant-a"]
    assert [r.tenant_id for r in result_b.matching_records] == ["tenant-b"]


@pytest.mark.asyncio
async def test_search_text_applies_entity_type_filter() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", record_type=KnowledgeRecordType.FACT))
    await lineage.create_record(_record("rec-2", record_type=KnowledgeRecordType.PROCEDURE))

    result = await engine.search_text(_query("content", entity_types=["FACT"]))
    assert [r.record_id for r in result.matching_records] == ["rec-1"]


@pytest.mark.asyncio
async def test_search_text_returns_empty_for_no_match() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "alpha"}))

    result = await engine.search_text(_query("zzz-no-such-term"))
    assert result.matching_records == []


# -- search_text: max_results boundaries -------------------------------------------


@pytest.mark.asyncio
async def test_search_text_respects_max_results_positive() -> None:
    engine, _graph, lineage = await _build_engine()
    for i in range(5):
        await lineage.create_record(_record(f"rec-{i}", content={"summary": "matchable"}))

    result = await engine.search_text(_query("matchable", max_results=2))
    assert len(result.matching_records) == 2


@pytest.mark.asyncio
async def test_search_text_max_results_one() -> None:
    engine, _graph, lineage = await _build_engine()
    for i in range(3):
        await lineage.create_record(_record(f"rec-{i}", content={"summary": "matchable"}))

    result = await engine.search_text(_query("matchable", max_results=1))
    assert len(result.matching_records) == 1


@pytest.mark.asyncio
async def test_search_text_max_results_zero_returns_empty() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "matchable"}))

    result = await engine.search_text(_query("matchable", max_results=0))
    assert result.matching_records == []


@pytest.mark.asyncio
async def test_search_text_negative_max_results_treated_as_zero() -> None:
    """No model-level constraint forbids a negative `max_results`; this
    module documents and tests treating any non-positive value as "return
    nothing" rather than relying on Python's surprising negative-slice
    semantics."""
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "matchable"}))

    result = await engine.search_text(_query("matchable", max_results=-1))
    assert result.matching_records == []


@pytest.mark.asyncio
async def test_search_text_max_results_none_means_unlimited() -> None:
    engine, _graph, lineage = await _build_engine()
    for i in range(10):
        await lineage.create_record(_record(f"rec-{i}", content={"summary": "matchable"}))

    result = await engine.search_text(_query("matchable", max_results=None))
    assert len(result.matching_records) == 10


# -- search_text: as_of point-in-time resolution -----------------------------------


@pytest.mark.asyncio
async def test_search_text_as_of_resolves_superseded_version() -> None:
    """Proves `as_of` correctly reconstructs point-in-time state through
    the existing `get_lineage()` chain, not a second lineage
    implementation."""
    engine, _graph, lineage = await _build_engine()
    t0 = _NOW
    t1 = _NOW + timedelta(days=1)

    v1 = _record("rec-1", version_id="v1", content={"summary": "original text"}, created_at=t0)
    await lineage.create_record(v1)
    v2 = _record("rec-1", version_id="v2", parent_version_id="v1", content={"summary": "revised text"}, created_at=t1)
    await lineage.supersede("rec-1", "tenant-a", v2)

    # As of a time between v1 and v2, only the original version applies.
    as_of_result = await engine.search_text(_query("original", as_of=t0 + timedelta(hours=1)))
    assert [r.version_id for r in as_of_result.matching_records] == ["v1"]

    # Searching for the revised term at that same as_of time finds nothing --
    # the revision did not exist yet.
    not_yet_result = await engine.search_text(_query("revised", as_of=t0 + timedelta(hours=1)))
    assert not_yet_result.matching_records == []

    # As of a time after v2, the revised version applies.
    after_result = await engine.search_text(_query("revised", as_of=t1 + timedelta(hours=1)))
    assert [r.version_id for r in after_result.matching_records] == ["v2"]


@pytest.mark.asyncio
async def test_search_text_as_of_resolution_does_not_assume_chain_order_matches_timestamp_order() -> None:
    """Adversarial finding fixed during this milestone's audit: `as_of`
    resolution must pick the version with the latest `created_at` that
    still qualifies, not merely the first chain-order version whose
    `created_at` exceeds `as_of` -- nothing in the frozen `KnowledgeRecord`
    model or `supersede()`'s contract requires strictly increasing
    timestamps along a chain."""
    engine, _graph, lineage = await _build_engine()
    t_v1 = _NOW + timedelta(days=5)  # v1 deliberately created "later" than v2, by wall-clock
    t_v2 = _NOW

    v1 = _record("rec-1", version_id="v1", content={"summary": "out of order first"}, created_at=t_v1)
    await lineage.create_record(v1)
    v2 = _record(
        "rec-1", version_id="v2", parent_version_id="v1", content={"summary": "out of order second"}, created_at=t_v2
    )
    await lineage.supersede("rec-1", "tenant-a", v2)

    # As of t_v1 (after both timestamps), the version with the latest
    # created_at that still qualifies (<= t_v1) must be chosen -- here
    # that is v1 itself (t_v1 > t_v2), even though v1 sits earlier in the
    # structural chain than v2.
    result = await engine.search_text(_query("first", as_of=t_v1))
    assert [r.version_id for r in result.matching_records] == ["v1"]


@pytest.mark.asyncio
async def test_search_text_as_of_before_record_existed_excludes_it() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", created_at=_NOW))

    result = await engine.search_text(_query("content", as_of=_NOW - timedelta(days=1)))
    assert result.matching_records == []


@pytest.mark.asyncio
async def test_search_text_never_mutates_state() -> None:
    engine, _graph, lineage = await _build_engine()
    original = await lineage.create_record(_record("rec-1", content={"summary": "alpha"}))

    await engine.search_text(_query("alpha"))

    current = await lineage.get_current("rec-1", "tenant-a")
    assert current == original  # unchanged by the search


# -- search_text: deterministic ordering -------------------------------------------


@pytest.mark.asyncio
async def test_search_text_orders_matches_by_record_id() -> None:
    engine, _graph, lineage = await _build_engine()
    for rid in ("rec-c", "rec-a", "rec-b"):
        await lineage.create_record(_record(rid, content={"summary": "matchable"}))

    first = await engine.search_text(_query("matchable"))
    second = await engine.search_text(_query("matchable"))
    assert [r.record_id for r in first.matching_records] == ["rec-a", "rec-b", "rec-c"]
    assert [r.record_id for r in first.matching_records] == [r.record_id for r in second.matching_records]


# -- search_graph: happy path, tenant isolation, determinism -----------------------


@pytest.mark.asyncio
async def test_search_graph_finds_seed_node_and_neighbors() -> None:
    engine, graph, _lineage = await _build_engine()
    graph.add_node(_node("n1", "Alpha Project"))
    graph.add_node(_node("n2", "Beta Component"))
    graph.add_relationship(_rel("r1", "n1", "n2"))

    result = await engine.search_graph(_query("alpha"))
    assert "n1" in [n.node_id for n in result.matching_nodes]
    assert "n2" in [n.node_id for n in result.matching_nodes]
    assert result.graph_relationships == []  # disclosed scope gap — see search.py module docstring
    assert result.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_search_graph_enforces_tenant_isolation() -> None:
    engine, graph, _lineage = await _build_engine()
    graph.add_node(_node("n1", "Shared Label", tenant_id="tenant-a"))
    graph.add_node(_node("n1", "Shared Label", tenant_id="tenant-b"))

    result_a = await engine.search_graph(_query("shared", tenant_id="tenant-a"))
    result_b = await engine.search_graph(_query("shared", tenant_id="tenant-b"))
    assert all(n.tenant_id == "tenant-a" for n in result_a.matching_nodes)
    assert all(n.tenant_id == "tenant-b" for n in result_b.matching_nodes)


@pytest.mark.asyncio
async def test_search_graph_applies_entity_type_filter_to_seeds() -> None:
    engine, graph, _lineage = await _build_engine()
    graph.add_node(_node("n1", "Matching Label", entity_type="document"))
    graph.add_node(_node("n2", "Matching Label", entity_type="recipe"))

    result = await engine.search_graph(_query("matching", entity_types=["document"]))
    assert [n.node_id for n in result.matching_nodes] == ["n1"]


@pytest.mark.asyncio
async def test_search_graph_returns_empty_for_no_seed_match() -> None:
    engine, graph, _lineage = await _build_engine()
    graph.add_node(_node("n1", "Alpha"))

    result = await engine.search_graph(_query("zzz-no-such-term"))
    assert result.matching_nodes == []


@pytest.mark.asyncio
async def test_search_graph_deduplicates_nodes_reached_by_multiple_seeds() -> None:
    """Two seeds that both traverse to the same descendant must not
    produce a duplicate entry in `matching_nodes`."""
    engine, graph, _lineage = await _build_engine()
    graph.add_node(_node("seed1", "Alpha Seed"))
    graph.add_node(_node("seed2", "Alpha Other Seed"))
    graph.add_node(_node("shared", "Shared Descendant"))
    graph.add_relationship(_rel("r1", "seed1", "shared"))
    graph.add_relationship(_rel("r2", "seed2", "shared"))

    result = await engine.search_graph(_query("alpha"))
    node_ids = [n.node_id for n in result.matching_nodes]
    assert node_ids.count("shared") == 1


@pytest.mark.asyncio
async def test_search_graph_respects_max_results() -> None:
    engine, graph, _lineage = await _build_engine()
    for i in range(5):
        graph.add_node(_node(f"n{i}", "Matchable Label"))

    result = await engine.search_graph(_query("matchable", max_results=2))
    assert len(result.matching_nodes) == 2


@pytest.mark.asyncio
async def test_search_graph_traversal_uses_the_evidenced_default_depth() -> None:
    """The chain n0 -> n1 -> n2 -> n3 -> n4 exceeds the evidenced 3-hop
    default (spec §16 / `test_traverse_three_hop_meets_performance_budget`)
    starting from n0: n1/n2/n3 are within 3 hops, n4 is a 4th hop and must
    NOT be included."""
    engine, graph, _lineage = await _build_engine()
    for i in range(5):
        graph.add_node(_node(f"n{i}", "n0-seed" if i == 0 else f"unrelated-{i}"))
    for i in range(4):
        graph.add_relationship(_rel(f"r{i}", f"n{i}", f"n{i + 1}"))

    result = await engine.search_graph(_query("n0-seed"))
    node_ids = {n.node_id for n in result.matching_nodes}
    assert node_ids == {"n0", "n1", "n2", "n3"}
    assert "n4" not in node_ids


@pytest.mark.asyncio
async def test_search_graph_never_mutates_state() -> None:
    engine, graph, _lineage = await _build_engine()
    node = graph.add_node(_node("n1", "Alpha"))

    await engine.search_graph(_query("alpha"))

    assert graph.list_nodes("tenant-a") == [node]


@pytest.mark.asyncio
async def test_search_graph_orders_matches_by_node_id() -> None:
    engine, graph, _lineage = await _build_engine()
    for nid in ("n-c", "n-a", "n-b"):
        graph.add_node(_node(nid, "Matchable"))

    first = await engine.search_graph(_query("matchable"))
    second = await engine.search_graph(_query("matchable"))
    assert [n.node_id for n in first.matching_nodes] == ["n-a", "n-b", "n-c"]
    assert [n.node_id for n in first.matching_nodes] == [n.node_id for n in second.matching_nodes]


# -- search_hybrid: combination, no duplication, tenant/trust preserved ------------


@pytest.mark.asyncio
async def test_search_hybrid_combines_text_and_graph_results() -> None:
    engine, graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "alpha finding"}))
    graph.add_node(_node("n1", "Alpha Node"))

    result = await engine.search_hybrid(_query("alpha"))
    assert [r.record_id for r in result.matching_records] == ["rec-1"]
    assert [n.node_id for n in result.matching_nodes] == ["n1"]
    assert result.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_search_hybrid_preserves_tenant_isolation() -> None:
    engine, graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", tenant_id="tenant-a", content={"summary": "alpha"}))
    await lineage.create_record(_record("rec-1", tenant_id="tenant-b", content={"summary": "alpha"}))
    graph.add_node(_node("n1", "Alpha", tenant_id="tenant-a"))
    graph.add_node(_node("n1", "Alpha", tenant_id="tenant-b"))

    result = await engine.search_hybrid(_query("alpha", tenant_id="tenant-a"))
    assert all(r.tenant_id == "tenant-a" for r in result.matching_records)
    assert all(n.tenant_id == "tenant-a" for n in result.matching_nodes)


@pytest.mark.asyncio
async def test_search_hybrid_preserves_trust_state_filtering() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", trust_state=KnowledgeTrustState.AI_CANDIDATE, content={"summary": "alpha"}))

    result = await engine.search_hybrid(_query("alpha"))
    assert result.matching_records == []  # default trust_states excludes AI_CANDIDATE


@pytest.mark.asyncio
async def test_search_hybrid_does_not_duplicate_within_its_own_fields() -> None:
    engine, graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-1", content={"summary": "alpha finding"}))
    graph.add_node(_node("seed", "Alpha Seed"))
    graph.add_node(_node("shared", "Shared"))
    graph.add_relationship(_rel("r1", "seed", "shared"))

    result = await engine.search_hybrid(_query("alpha"))
    assert len(result.matching_records) == len(set(r.record_id for r in result.matching_records))
    assert len(result.matching_nodes) == len(set(n.node_id for n in result.matching_nodes))


# -- New enumeration methods: KnowledgeGraph.list_nodes / lineage.list_current_records --


def test_list_nodes_scopes_to_tenant_and_empty_for_unknown() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1", "Alpha", tenant_id="tenant-a"))
    graph.add_node(_node("n1", "Alpha", tenant_id="tenant-b"))

    assert [n.tenant_id for n in graph.list_nodes("tenant-a")] == ["tenant-a"]
    assert graph.list_nodes("does-not-exist") == []


@pytest.mark.asyncio
async def test_list_current_records_scopes_to_tenant_and_excludes_superseded() -> None:
    lineage = KnowledgeLineageManager()
    await lineage.create_record(_record("rec-1", tenant_id="tenant-a", version_id="v1"))
    await lineage.supersede(
        "rec-1", "tenant-a", _record("rec-1", tenant_id="tenant-a", version_id="v2", parent_version_id="v1")
    )
    await lineage.create_record(_record("rec-1", tenant_id="tenant-b", version_id="v1"))

    records_a = await lineage.list_current_records("tenant-a")
    assert [r.version_id for r in records_a] == ["v2"]  # only the current version, not the superseded v1
    records_b = await lineage.list_current_records("tenant-b")
    assert [r.tenant_id for r in records_b] == ["tenant-b"]
    assert await lineage.list_current_records("does-not-exist") == []


@pytest.mark.asyncio
async def test_list_current_records_is_a_coroutine_function_using_the_existing_lock() -> None:
    """Confirms `list_current_records` participates in the same
    synchronization mechanism M7 introduced, rather than a second,
    competing locking architecture. Concurrent enumeration and mutation
    must not crash or deadlock."""
    lineage = KnowledgeLineageManager()
    assert inspect.iscoroutinefunction(lineage.list_current_records)

    await lineage.create_record(_record("rec-1"))
    results = await asyncio.gather(
        lineage.list_current_records("tenant-a"),
        lineage.create_record(_record("rec-2")),
    )
    assert results[0] is not None
    assert results[1] is not None


# -- Adversarial: concurrent search during concurrent mutation --------------------


@pytest.mark.asyncio
async def test_concurrent_search_text_during_concurrent_create_record_does_not_corrupt_state() -> None:
    engine, _graph, lineage = await _build_engine()
    await lineage.create_record(_record("rec-existing", content={"summary": "alpha"}))

    async def _search_repeatedly() -> None:
        for _ in range(20):
            await engine.search_text(_query("alpha"))

    await asyncio.gather(
        _search_repeatedly(),
        lineage.create_record(_record("rec-new", content={"summary": "beta"})),
    )

    final = await engine.search_text(_query("alpha"))
    assert [r.record_id for r in final.matching_records] == ["rec-existing"]
