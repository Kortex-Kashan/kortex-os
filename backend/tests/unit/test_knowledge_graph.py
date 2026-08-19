"""Unit tests for the Knowledge Engine Directed Knowledge Graph (Milestone M2).

Verifies `KnowledgeGraph` satisfies `IKnowledgeGraph`, enforces tenant
isolation, rejects duplicate/invalid/cyclic data, and traverses
deterministically, per the Milestone M2 architectural decisions recorded in
`graph.py`.
"""

from __future__ import annotations

import time

import pytest

from kortex.engines.knowledge.exceptions import (
    KnowledgeDuplicateNodeError,
    KnowledgeDuplicateRelationshipError,
    KnowledgeGraphCycleError,
    KnowledgeNodeNotFoundError,
)
from kortex.engines.knowledge.graph import KnowledgeGraph
from kortex.engines.knowledge.interfaces import IKnowledgeGraph
from kortex.engines.knowledge.models import (
    KnowledgeNode,
    KnowledgeRelationship,
    KnowledgeRelationshipType,
)


def _node(node_id: str, tenant_id: str = "tenant-a") -> KnowledgeNode:
    return KnowledgeNode(node_id=node_id, tenant_id=tenant_id, entity_type="document", label=node_id)


def _rel(
    relationship_id: str,
    source_node_id: str,
    target_node_id: str,
    relationship_type: KnowledgeRelationshipType = KnowledgeRelationshipType.DERIVED_FROM,
    tenant_id: str = "tenant-a",
) -> KnowledgeRelationship:
    return KnowledgeRelationship(
        relationship_id=relationship_id,
        tenant_id=tenant_id,
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        relationship_type=relationship_type,
    )


# -- Contract conformance -------------------------------------------------------


def test_knowledge_graph_satisfies_iknowledgegraph() -> None:
    assert isinstance(KnowledgeGraph(), IKnowledgeGraph)


# -- add_node --------------------------------------------------------------------


def test_add_node_valid() -> None:
    graph = KnowledgeGraph()
    node = _node("n1")
    assert graph.add_node(node) == node


def test_add_node_rejects_duplicate_within_same_tenant() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    with pytest.raises(KnowledgeDuplicateNodeError):
        graph.add_node(_node("n1"))


def test_add_node_same_id_allowed_across_different_tenants() -> None:
    """Tenant isolation: identical node_id across tenants must not collide."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1", tenant_id="tenant-a"))
    graph.add_node(_node("n1", tenant_id="tenant-b"))  # must not raise


# -- add_relationship --------------------------------------------------------------


def test_add_relationship_valid() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    rel = _rel("r1", "n1", "n2")
    assert graph.add_relationship(rel) == rel


def test_add_relationship_rejects_duplicate_relationship_id() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    graph.add_relationship(_rel("r1", "n1", "n2"))
    with pytest.raises(KnowledgeDuplicateRelationshipError):
        graph.add_relationship(_rel("r1", "n1", "n2"))


def test_add_relationship_rejects_missing_source_node() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n2"))
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.add_relationship(_rel("r1", "n1", "n2"))


def test_add_relationship_rejects_missing_target_node() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.add_relationship(_rel("r1", "n1", "n2"))


def test_add_relationship_rejects_cross_tenant_endpoint_reference() -> None:
    """A relationship declared under tenant-a referencing a node that only
    exists under tenant-b must fail exactly like a nonexistent node —
    tenant isolation, not merely a missing-data coincidence."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1", tenant_id="tenant-a"))
    graph.add_node(_node("n2", tenant_id="tenant-b"))
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.add_relationship(_rel("r1", "n1", "n2", tenant_id="tenant-a"))


def test_add_relationship_rejects_self_loop_for_any_relationship_type() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    with pytest.raises(KnowledgeGraphCycleError):
        graph.add_relationship(_rel("r1", "n1", "n1", relationship_type=KnowledgeRelationshipType.RELATES_TO))


# -- Cycle detection (scoped per relationship_type) --------------------------------


def test_add_relationship_rejects_direct_two_node_hierarchical_cycle() -> None:
    """The minimal possible cycle case: a direct A->B then B->A pair for
    the same hierarchical type. Tested separately from the 3-node chain
    case below, since a subtle bug could plausibly affect only one of the
    two shapes."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.SUPERSEDES))
    with pytest.raises(KnowledgeGraphCycleError):
        graph.add_relationship(_rel("r2", "n2", "n1", relationship_type=KnowledgeRelationshipType.SUPERSEDES))


def test_add_relationship_rejects_hierarchical_cycle() -> None:
    """SUPERSEDES is hierarchical — a cycle among SUPERSEDES edges is a
    genuine data error and must be rejected."""
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.SUPERSEDES))
    graph.add_relationship(_rel("r2", "n2", "n3", relationship_type=KnowledgeRelationshipType.SUPERSEDES))
    with pytest.raises(KnowledgeGraphCycleError):
        graph.add_relationship(_rel("r3", "n3", "n1", relationship_type=KnowledgeRelationshipType.SUPERSEDES))


def test_add_relationship_allows_non_hierarchical_cycle() -> None:
    """RELATES_TO is not hierarchical — mutual/cyclic references are valid
    and must NOT be rejected. This proves the per-type cycle-detection
    scoping decision is real, testable behavior."""
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.RELATES_TO))
    graph.add_relationship(_rel("r2", "n2", "n3", relationship_type=KnowledgeRelationshipType.RELATES_TO))
    # Must not raise — closes a RELATES_TO cycle, which is legitimate.
    graph.add_relationship(_rel("r3", "n3", "n1", relationship_type=KnowledgeRelationshipType.RELATES_TO))


def test_add_relationship_allows_same_pair_with_different_non_hierarchical_types() -> None:
    """Cycle detection is scoped per type: a SUPERSEDES chain and an
    unrelated RELATES_TO edge between the same nodes must not interfere
    with each other."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.SUPERSEDES))
    graph.add_relationship(_rel("r2", "n2", "n1", relationship_type=KnowledgeRelationshipType.RELATES_TO))


# -- find_neighbors ------------------------------------------------------------


def test_find_neighbors_returns_outgoing_neighbors_only() -> None:
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2"))
    graph.add_relationship(_rel("r2", "n3", "n1"))  # incoming to n1 — must not appear
    neighbors = graph.find_neighbors("n1", "tenant-a")
    assert [n.node_id for n in neighbors] == ["n2"]


def test_find_neighbors_deduplicates_multi_edge_targets() -> None:
    """Two distinct relationships to the same target must still surface
    that target only once — a neighbor is a distinct node, not an edge
    count. Consistent with `traverse`'s visited-set deduplication."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.REFERENCES))
    graph.add_relationship(_rel("r2", "n1", "n2", relationship_type=KnowledgeRelationshipType.RELATES_TO))
    neighbors = graph.find_neighbors("n1", "tenant-a")
    assert [n.node_id for n in neighbors] == ["n2"]


def test_find_neighbors_returns_empty_for_isolated_node() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    assert graph.find_neighbors("n1", "tenant-a") == []


def test_find_neighbors_raises_for_missing_node() -> None:
    graph = KnowledgeGraph()
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.find_neighbors("does-not-exist", "tenant-a")


def test_find_neighbors_enforces_tenant_isolation() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1", tenant_id="tenant-a"))
    graph.add_node(_node("n2", tenant_id="tenant-a"))
    graph.add_node(_node("n1", tenant_id="tenant-b"))
    graph.add_relationship(_rel("r1", "n1", "n2", tenant_id="tenant-a"))
    assert graph.find_neighbors("n1", "tenant-b") == []


# -- traverse --------------------------------------------------------------------


def test_traverse_returns_correct_multi_hop_chain() -> None:
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3", "n4"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2"))
    graph.add_relationship(_rel("r2", "n2", "n3"))
    graph.add_relationship(_rel("r3", "n3", "n4"))

    assert [n.node_id for n in graph.traverse("n1", "tenant-a", max_hops=1)] == ["n2"]
    assert [n.node_id for n in graph.traverse("n1", "tenant-a", max_hops=2)] == ["n2", "n3"]
    assert [n.node_id for n in graph.traverse("n1", "tenant-a", max_hops=3)] == ["n2", "n3", "n4"]


def test_traverse_max_hops_zero_returns_empty_list() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    graph.add_node(_node("n2"))
    graph.add_relationship(_rel("r1", "n1", "n2"))
    assert graph.traverse("n1", "tenant-a", max_hops=0) == []


def test_traverse_rejects_negative_max_hops() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    with pytest.raises(ValueError):
        graph.traverse("n1", "tenant-a", max_hops=-1)


def test_traverse_raises_for_missing_node() -> None:
    graph = KnowledgeGraph()
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.traverse("does-not-exist", "tenant-a", max_hops=2)


def test_traverse_enforces_tenant_isolation() -> None:
    graph = KnowledgeGraph()
    graph.add_node(_node("n1", tenant_id="tenant-a"))
    graph.add_node(_node("n2", tenant_id="tenant-a"))
    graph.add_node(_node("n1", tenant_id="tenant-b"))
    graph.add_relationship(_rel("r1", "n1", "n2", tenant_id="tenant-a"))
    assert graph.traverse("n1", "tenant-b", max_hops=5) == []


def test_traverse_is_cycle_safe_and_does_not_duplicate_nodes() -> None:
    """RELATES_TO cycles are legal (see cycle-detection tests above), so
    traverse must not infinite-loop or return duplicate nodes when one
    exists."""
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2", relationship_type=KnowledgeRelationshipType.RELATES_TO))
    graph.add_relationship(_rel("r2", "n2", "n3", relationship_type=KnowledgeRelationshipType.RELATES_TO))
    graph.add_relationship(_rel("r3", "n3", "n1", relationship_type=KnowledgeRelationshipType.RELATES_TO))

    result = graph.traverse("n1", "tenant-a", max_hops=10)
    result_ids = [n.node_id for n in result]
    assert len(result_ids) == len(set(result_ids))
    assert set(result_ids) == {"n2", "n3"}


def test_traverse_does_not_cross_into_a_disconnected_component() -> None:
    """Two entirely separate components (no edge between them) must never
    leak into each other's traversal results — this is a distinct contract
    from tenant isolation (both components are the same tenant here) and
    from single-isolated-node handling (both components have internal
    structure of their own)."""
    graph = KnowledgeGraph()
    for nid in ("a1", "a2", "b1", "b2"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("ra", "a1", "a2"))
    graph.add_relationship(_rel("rb", "b1", "b2"))

    result_ids = {n.node_id for n in graph.traverse("a1", "tenant-a", max_hops=10)}
    assert result_ids == {"a2"}
    assert "b1" not in result_ids and "b2" not in result_ids


def test_find_neighbors_and_traverse_reject_empty_string_node_id() -> None:
    """`find_neighbors`/`traverse` take raw `str` primitives at the call
    boundary (unlike `add_node`/`add_relationship`, which take
    Pydantic-validated models) — an empty-string `node_id` can never match
    a stored node (model validation forbids empty `node_id` on `KnowledgeNode`
    itself), so it must fail exactly like any other nonexistent node, not
    crash or silently return an empty result for the wrong reason."""
    graph = KnowledgeGraph()
    graph.add_node(_node("n1"))
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.find_neighbors("", "tenant-a")
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.traverse("", "tenant-a", max_hops=1)
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.find_neighbors("n1", "")
    with pytest.raises(KnowledgeNodeNotFoundError):
        graph.traverse("n1", "", max_hops=1)


def test_traverse_is_deterministic_across_repeated_calls() -> None:
    graph = KnowledgeGraph()
    for nid in ("n1", "n2", "n3"):
        graph.add_node(_node(nid))
    graph.add_relationship(_rel("r1", "n1", "n2"))
    graph.add_relationship(_rel("r2", "n1", "n3"))

    first = [n.node_id for n in graph.traverse("n1", "tenant-a", max_hops=1)]
    second = [n.node_id for n in graph.traverse("n1", "tenant-a", max_hops=1)]
    assert first == second


# -- Documented characteristic: caller-side mutation after insertion --------------


def test_mutating_a_node_after_insertion_desynchronizes_internal_index() -> None:
    """Proves the documented characteristic in `graph.py`'s module
    docstring is real and understood, not an undiscovered bug: mutating a
    `KnowledgeNode` after `add_node` desynchronizes the graph's internal
    index from the object's own `node_id`, because `KnowledgeNode` is
    mutable by Milestone M1's own design and the graph stores it by
    reference. Callers must not mutate an object after handing it to the
    graph."""
    graph = KnowledgeGraph()
    node = _node("n1")
    graph.add_node(node)
    node.node_id = "n2"  # caller-side mutation after insertion
    # The graph's index still uses the original key ("tenant-a", "n1"),
    # so a lookup under the original id still succeeds even though the
    # object itself now reports a different node_id.
    neighbors = graph.find_neighbors("n1", "tenant-a")
    assert neighbors == []  # still resolvable — but the stored object is desynchronized:
    stored = graph._nodes[("tenant-a", "n1")]
    assert stored.node_id == "n2"  # object mutated in place; index key unchanged


# -- Performance (spec §16: <=50ms for 3-hop traversal) ----------------------------


def test_traverse_three_hop_meets_performance_budget() -> None:
    """In-memory 3-hop traversal must complete well within the spec §16
    50ms budget. `ICacheStore`-backed caching is explicitly out of scope
    for Milestone M2 (Milestone M7) — this proves the uncached in-memory
    implementation alone already satisfies the target."""
    graph = KnowledgeGraph()
    chain_length = 500
    for i in range(chain_length):
        graph.add_node(_node(f"n{i}"))
    for i in range(chain_length - 1):
        graph.add_relationship(_rel(f"r{i}", f"n{i}", f"n{i + 1}"))

    start = time.perf_counter()
    result = graph.traverse("n0", "tenant-a", max_hops=3)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert [n.node_id for n in result] == ["n1", "n2", "n3"]
    assert elapsed_ms <= 50.0
