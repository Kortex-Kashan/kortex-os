"""
Unit tests for KORTEX Workflow Graph Structural Validation (Milestone F2 — Workflow Graph Model).

Covers every invariant `graph_validation.validate_graph` enforces, plus the non-fatal
`find_unreachable_nodes` reachability check.
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowGraphValidationError
from kortex.engines.workflow.graph_validation import find_unreachable_nodes, validate_graph
from kortex.engines.workflow.models import WorkflowGraph, WorkflowGraphEdge, WorkflowGraphNode


def _node(node_id: str, **kwargs: object) -> WorkflowGraphNode:
    return WorkflowGraphNode(node_id=node_id, node_type="capability", **kwargs)  # type: ignore[arg-type]


def _edge(edge_id: str, source: str, target: str, **kwargs: object) -> WorkflowGraphEdge:
    return WorkflowGraphEdge(edge_id=edge_id, source_node_id=source, target_node_id=target, **kwargs)  # type: ignore[arg-type]


def test_valid_linear_dag_passes() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_edge("e1", "a", "b"), _edge("e2", "b", "c")],
    )
    validate_graph(graph)  # must not raise


def test_valid_branching_dag_passes() -> None:
    """Multiple valid branches out of one node are structurally allowed — F2 forbids cycles, not
    branching (branching is exactly what a future execution milestone needs the graph shape for)."""
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_edge("e1", "a", "b"), _edge("e2", "a", "c")],
    )
    validate_graph(graph)  # must not raise


def test_valid_joining_dag_passes() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c"), _node("d")],
        edges=[_edge("e1", "a", "b"), _edge("e2", "a", "c"), _edge("e3", "b", "d"), _edge("e4", "c", "d")],
    )
    validate_graph(graph)  # must not raise


def test_duplicate_node_id_rejected() -> None:
    graph = WorkflowGraph(entry_node_id="a", nodes=[_node("a"), _node("a")], edges=[])
    with pytest.raises(WorkflowGraphValidationError, match="Duplicate node ID"):
        validate_graph(graph)


def test_duplicate_edge_id_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_edge("e1", "a", "b"), _edge("e1", "b", "c")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="Duplicate edge ID"):
        validate_graph(graph)


def test_missing_entry_node_rejected() -> None:
    graph = WorkflowGraph(entry_node_id="does-not-exist", nodes=[_node("a")], edges=[])
    with pytest.raises(WorkflowGraphValidationError, match="entry_node_id"):
        validate_graph(graph)


def test_missing_source_node_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a")],
        edges=[_edge("e1", "does-not-exist", "a")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="source node"):
        validate_graph(graph)


def test_missing_target_node_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a")],
        edges=[_edge("e1", "a", "does-not-exist")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="target node"):
        validate_graph(graph)


def test_invalid_source_port_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a", output_ports=["out"]), _node("b")],
        edges=[_edge("e1", "a", "b", source_port="not-declared")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="source_port"):
        validate_graph(graph)


def test_invalid_target_port_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b", input_ports=["in"])],
        edges=[_edge("e1", "a", "b", target_port="not-declared")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="target_port"):
        validate_graph(graph)


def test_valid_declared_ports_pass() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a", output_ports=["out"]), _node("b", input_ports=["in"])],
        edges=[_edge("e1", "a", "b", source_port="out", target_port="in")],
    )
    validate_graph(graph)  # must not raise


def test_unported_edge_never_requires_a_port() -> None:
    """An edge that leaves source_port/target_port as None is never required to reference a
    declared port on either node, regardless of what ports those nodes declare (or don't)."""
    graph = WorkflowGraph(entry_node_id="a", nodes=[_node("a"), _node("b")], edges=[_edge("e1", "a", "b")])
    validate_graph(graph)  # must not raise


def test_self_loop_rejected() -> None:
    graph = WorkflowGraph(entry_node_id="a", nodes=[_node("a")], edges=[_edge("e1", "a", "a")])
    with pytest.raises(WorkflowGraphValidationError, match="self-loop"):
        validate_graph(graph)


def test_multi_node_cycle_rejected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_edge("e1", "a", "b"), _edge("e2", "b", "c"), _edge("e3", "c", "a")],
    )
    with pytest.raises(WorkflowGraphValidationError, match="cycle"):
        validate_graph(graph)


def test_capability_reference_is_a_plain_string_not_resolved() -> None:
    """A node's capability_name is validated only as a string reference — validate_graph never
    contacts the Registry, never raises for an unknown/nonexistent capability name, and never
    requires one to be present at all."""
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a", capability_name="kortex.this.capability.does_not_exist")],
        edges=[],
    )
    validate_graph(graph)  # must not raise — no Registry lookup occurs here

    graph_no_capability = WorkflowGraph(entry_node_id="a", nodes=[_node("a")], edges=[])
    validate_graph(graph_no_capability)  # must not raise — capability_name is optional


def test_find_unreachable_nodes_reports_disconnected_node_without_raising() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("orphan")],
        edges=[_edge("e1", "a", "b")],
    )
    validate_graph(graph)  # a disconnected node is not a validation failure
    assert find_unreachable_nodes(graph) == ["orphan"]


def test_find_unreachable_nodes_returns_empty_when_all_connected() -> None:
    graph = WorkflowGraph(
        entry_node_id="a",
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[_edge("e1", "a", "b"), _edge("e2", "a", "c")],
    )
    assert find_unreachable_nodes(graph) == []
