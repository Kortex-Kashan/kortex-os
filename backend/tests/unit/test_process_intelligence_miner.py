"""
Unit tests for ProcessMiner: Graph construction, bounding guarantees, and trace variants.
"""

from __future__ import annotations

import datetime

from kortex.engines.process_intelligence.miner import (
    MAX_EDGES_GUARANTEE,
    MAX_NODES_GUARANTEE,
    ProcessMiner,
)
from kortex.engines.process_intelligence.models import (
    NODE_END_SUCCESS,
    NODE_OTHER_STEPS,
    NODE_START,
    STATE_COMPLETED,
    RawInstanceTrace,
    RawStepExecution,
)


def _make_trace(
    instance_id: str,
    step_ids: list[str],
    state: str = STATE_COMPLETED,
    duration_per_step_ms: float = 100.0,
) -> RawInstanceTrace:
    t0 = datetime.datetime(2026, 8, 1, 10, 0, 0, tzinfo=datetime.UTC)
    steps: list[RawStepExecution] = []
    curr = t0
    for idx, s_id in enumerate(step_ids):
        started = curr
        completed = started + datetime.timedelta(milliseconds=duration_per_step_ms)
        steps.append(
            RawStepExecution(
                id=f"step_run_{instance_id}_{idx}",
                instance_id=instance_id,
                step_id=s_id,
                status="COMPLETED",
                started_at=started,
                completed_at=completed,
                attempt=1,
            )
        )
        curr = completed + datetime.timedelta(milliseconds=10.0)

    return RawInstanceTrace(
        instance_id=instance_id,
        definition_id="test_def",
        definition_version="1.0.0",
        state=state,
        status="COMPLETED",
        created_at=t0,
        updated_at=curr,
        steps=steps,
    )


def test_miner_basic_graph() -> None:
    miner = ProcessMiner()
    trace1 = _make_trace("inst_1", ["step_a", "step_b", "step_c"])
    trace2 = _make_trace("inst_2", ["step_a", "step_b", "step_c"])

    graph = miner.build_directly_follows_graph(
        definition_id="test_def",
        traces=[trace1, trace2],
        total_matching=2,
        window_clamped=False,
        version_analyzed="1.0.0",
        available_versions=["1.0.0"],
    )

    assert graph.definition_id == "test_def"
    node_ids = {n.id for n in graph.nodes}
    assert node_ids == {NODE_START, "step_a", "step_b", "step_c", NODE_END_SUCCESS}

    edges = {(e.source, e.target): e for e in graph.edges}
    assert (NODE_START, "step_a") in edges
    assert edges[(NODE_START, "step_a")].transition_count == 2
    assert edges[(NODE_START, "step_a")].transition_probability == 1.0

    assert ("step_a", "step_b") in edges
    assert edges[("step_a", "step_b")].transition_count == 2
    assert edges[("step_a", "step_b")].transition_probability == 1.0

    assert ("step_b", "step_c") in edges
    assert edges[("step_c", NODE_END_SUCCESS)].transition_count == 2

    assert len(graph.nodes) <= MAX_NODES_GUARANTEE
    assert len(graph.edges) <= MAX_EDGES_GUARANTEE
    assert not graph.metadata.nodes_collapsed
    assert not graph.metadata.edges_pruned


def test_miner_probability_normalization() -> None:
    miner = ProcessMiner()
    # Branching: step_a -> step_b (3 times), step_a -> step_c (1 time)
    t1 = _make_trace("inst_1", ["step_a", "step_b"])
    t2 = _make_trace("inst_2", ["step_a", "step_b"])
    t3 = _make_trace("inst_3", ["step_a", "step_b"])
    t4 = _make_trace("inst_4", ["step_a", "step_c"])

    graph = miner.build_directly_follows_graph(
        definition_id="branch_def",
        traces=[t1, t2, t3, t4],
        total_matching=4,
        window_clamped=False,
        version_analyzed="1.0.0",
        available_versions=["1.0.0"],
    )

    edges = {(e.source, e.target): e for e in graph.edges}
    assert edges[("step_a", "step_b")].transition_probability == 0.75
    assert edges[("step_a", "step_c")].transition_probability == 0.25
    assert (
        edges[("step_a", "step_b")].transition_probability + edges[("step_a", "step_c")].transition_probability
    ) == 1.0


def test_miner_guarantees_max_100_nodes_under_extreme_load() -> None:
    miner = ProcessMiner()
    # Generate 150 distinct steps in traces
    traces: list[RawInstanceTrace] = []
    for i in range(150):
        s_id = f"step_{i:03d}"
        traces.append(_make_trace(f"inst_{i}", [s_id]))

    graph = miner.build_directly_follows_graph(
        definition_id="huge_def",
        traces=traces,
        total_matching=150,
        window_clamped=False,
        version_analyzed="1.0.0",
        available_versions=["1.0.0"],
    )

    # Invariant: Must not exceed 100 nodes
    assert len(graph.nodes) <= MAX_NODES_GUARANTEE
    assert graph.metadata.nodes_collapsed is True
    assert graph.metadata.collapsed_node_count == 150 - 95
    node_ids = {n.id for n in graph.nodes}
    assert NODE_OTHER_STEPS in node_ids


def test_miner_guarantees_max_500_edges() -> None:
    miner = ProcessMiner()
    # Create complete mesh among 30 nodes (30*29 = 870 possible edges)
    traces: list[RawInstanceTrace] = []
    nodes = [f"n_{i}" for i in range(30)]
    for i in range(len(nodes)):
        for j in range(len(nodes)):
            if i != j:
                traces.append(_make_trace(f"inst_{i}_{j}", [nodes[i], nodes[j]]))

    graph = miner.build_directly_follows_graph(
        definition_id="dense_def",
        traces=traces,
        total_matching=len(traces),
        window_clamped=False,
        version_analyzed="1.0.0",
        available_versions=["1.0.0"],
    )

    # Invariant: Must not exceed 500 edges
    assert len(graph.edges) <= MAX_EDGES_GUARANTEE
    assert graph.metadata.edges_pruned is True
    assert graph.metadata.pruned_edge_count > 0


def test_miner_variant_extraction() -> None:
    miner = ProcessMiner()
    t1 = _make_trace("i1", ["A", "B", "C"])
    t2 = _make_trace("i2", ["A", "B", "C"])
    t3 = _make_trace("i3", ["A", "D"])

    result = miner.extract_trace_variants(
        definition_id="var_def",
        traces=[t1, t2, t3],
        limit=20,
        total_matching=3,
        window_clamped=False,
        version_analyzed="1.0.0",
        available_versions=["1.0.0"],
    )

    assert result.definition_id == "var_def"
    assert result.total_variants_discovered == 2
    assert len(result.returned_variants) == 2

    top = result.returned_variants[0]
    assert top.steps == ["A", "B", "C"]
    assert top.frequency == 2
    assert top.percentage == 66.67

    second = result.returned_variants[1]
    assert second.steps == ["A", "D"]
    assert second.frequency == 1
    assert second.percentage == 33.33


def test_miner_empty_traces() -> None:
    miner = ProcessMiner()
    graph = miner.build_directly_follows_graph(
        definition_id="empty_def",
        traces=[],
        total_matching=0,
        window_clamped=False,
        version_analyzed=None,
        available_versions=[],
    )
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.metadata.sample_size == 0

    variants = miner.extract_trace_variants(
        definition_id="empty_def",
        traces=[],
        limit=20,
        total_matching=0,
        window_clamped=False,
        version_analyzed=None,
        available_versions=[],
    )
    assert variants.total_variants_discovered == 0
    assert variants.returned_variants == []
