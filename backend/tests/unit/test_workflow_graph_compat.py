"""
Unit tests for KORTEX Workflow Graph Compatibility Adapter (Milestone F2 — Workflow Graph Model).

Covers `steps_to_graph`/`graph_to_steps`: deterministic conversion, lossless round-tripping for the
linear case, and explicit rejection — never silent flattening — for a branching graph.
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowGraphConversionError
from kortex.engines.workflow.graph_compat import graph_to_steps, steps_to_graph
from kortex.engines.workflow.models import (
    CompensationAction,
    RetryPolicy,
    WorkflowGraph,
    WorkflowGraphEdge,
    WorkflowGraphNode,
    WorkflowStep,
)


def test_steps_to_graph_rejects_empty_list() -> None:
    with pytest.raises(WorkflowGraphConversionError, match="empty step list"):
        steps_to_graph([])


def test_steps_to_graph_single_step() -> None:
    steps = [WorkflowStep(id="s1", name="Only Step", capability_name="kortex.storage.data.session")]
    graph = steps_to_graph(steps)
    assert graph.entry_node_id == "s1"
    assert [n.node_id for n in graph.nodes] == ["s1"]
    assert graph.edges == []


def test_steps_to_graph_multiple_steps_produces_a_linear_chain() -> None:
    steps = [
        WorkflowStep(id="s1", name="One", capability_name="kortex.a.b.c"),
        WorkflowStep(id="s2", name="Two", capability_name="kortex.d.e.f"),
        WorkflowStep(id="s3", name="Three", capability_name="kortex.g.h.i"),
    ]
    graph = steps_to_graph(steps)
    assert graph.entry_node_id == "s1"
    assert [n.node_id for n in graph.nodes] == ["s1", "s2", "s3"]
    assert [(e.source_node_id, e.target_node_id) for e in graph.edges] == [("s1", "s2"), ("s2", "s3")]


def test_steps_to_graph_is_deterministic() -> None:
    steps = [
        WorkflowStep(id="s1", name="One", capability_name="kortex.a.b.c"),
        WorkflowStep(id="s2", name="Two", capability_name="kortex.d.e.f"),
    ]
    first = steps_to_graph(steps)
    second = steps_to_graph(steps)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_steps_to_graph_preserves_capability_reference_as_a_string() -> None:
    steps = [WorkflowStep(id="s1", name="One", capability_name="kortex.workflow.instance.start")]
    graph = steps_to_graph(steps)
    assert graph.nodes[0].capability_name == "kortex.workflow.instance.start"


def test_linear_graph_to_steps_round_trip_single_step() -> None:
    steps = [WorkflowStep(id="s1", name="Only Step", capability_name="kortex.storage.data.session")]
    assert graph_to_steps(steps_to_graph(steps)) == steps


def test_linear_graph_to_steps_round_trip_multiple_steps() -> None:
    steps = [
        WorkflowStep(id="s1", name="One", capability_name="kortex.a.b.c", parameters={"x": 1}),
        WorkflowStep(id="s2", name="Two", capability_name="kortex.d.e.f", parameters={"y": 2}),
        WorkflowStep(id="s3", name="Three", capability_name="kortex.g.h.i"),
    ]
    assert graph_to_steps(steps_to_graph(steps)) == steps


def test_round_trip_preserves_approval_retry_and_compensation_fields() -> None:
    steps = [
        WorkflowStep(
            id="s1",
            name="Approve Payout",
            capability_name="kortex.finance.invoice.create",
            parameters={"amount": 100},
            is_approval_step=True,
            required_approval_role="finance_approver",
            retry_policy=RetryPolicy(max_attempts=5, backoff_factor=1.5),
            compensation_action=CompensationAction(name="Reverse payout", capability_name="kortex.finance.invoice.get"),
            on_failure_continue=True,
        ),
    ]
    assert graph_to_steps(steps_to_graph(steps)) == steps


def test_graph_to_steps_rejects_branching_graph_explicitly() -> None:
    """A node with more than one outgoing edge has no faithful sequential representation — this
    must fail explicitly, never silently pick one branch or discard an edge."""
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[
            WorkflowGraphNode(node_id="a", node_type="capability"),
            WorkflowGraphNode(node_id="b", node_type="capability"),
            WorkflowGraphNode(node_id="c", node_type="capability"),
        ],
        edges=[
            WorkflowGraphEdge(edge_id="e1", source_node_id="a", target_node_id="b"),
            WorkflowGraphEdge(edge_id="e2", source_node_id="a", target_node_id="c"),
        ],
    )
    with pytest.raises(WorkflowGraphConversionError, match="branching or joining"):
        graph_to_steps(graph)


def test_graph_to_steps_rejects_joining_graph_explicitly() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[
            WorkflowGraphNode(node_id="a", node_type="capability"),
            WorkflowGraphNode(node_id="b", node_type="capability"),
            WorkflowGraphNode(node_id="c", node_type="capability"),
            WorkflowGraphNode(node_id="d", node_type="capability"),
        ],
        edges=[
            WorkflowGraphEdge(edge_id="e1", source_node_id="a", target_node_id="b"),
            WorkflowGraphEdge(edge_id="e2", source_node_id="a", target_node_id="c"),
            WorkflowGraphEdge(edge_id="e3", source_node_id="b", target_node_id="d"),
            WorkflowGraphEdge(edge_id="e4", source_node_id="c", target_node_id="d"),
        ],
    )
    with pytest.raises(WorkflowGraphConversionError, match="branching or joining"):
        graph_to_steps(graph)


def test_graph_to_steps_rejects_disconnected_graph_explicitly() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[
            WorkflowGraphNode(node_id="a", node_type="capability"),
            WorkflowGraphNode(node_id="orphan", node_type="capability"),
        ],
        edges=[],
    )
    with pytest.raises(WorkflowGraphConversionError, match="disconnected"):
        graph_to_steps(graph)


def test_graph_to_steps_rejects_cyclic_graph_via_underlying_validation() -> None:
    """graph_to_steps validates structurally first, so a cyclic graph fails there rather than in
    the conversion logic itself."""
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[
            WorkflowGraphNode(node_id="a", node_type="capability"),
            WorkflowGraphNode(node_id="b", node_type="capability"),
        ],
        edges=[
            WorkflowGraphEdge(edge_id="e1", source_node_id="a", target_node_id="b"),
            WorkflowGraphEdge(edge_id="e2", source_node_id="b", target_node_id="a"),
        ],
    )
    with pytest.raises(Exception, match="cycle"):
        graph_to_steps(graph)
