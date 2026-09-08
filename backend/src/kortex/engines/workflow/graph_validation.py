"""
KORTEX Workflow Graph Structural Validation (Milestone F2 — Workflow Graph Model).

Enforces the structural invariants a `WorkflowGraph` (`workflow/models.py`) must satisfy before any
future consumer (an authoring capability, an AI workflow builder, a visual canvas, or a later
execution milestone) can trust it. Kept as a dedicated module, separate from the Pydantic domain
models themselves, mirroring this engine's existing separation of "rules" from "models"
(`state_machine.py` enforces `WorkflowInstance` lifecycle rules the same way, next to but apart
from `models.py`).

Scope discipline (Milestone F2 firewall):
- Structural validation only — no execution, no expression evaluation, no data-mapping semantics.
- No capability metadata is read from, or written onto, a node here. `WorkflowGraphNode.
  capability_name` is validated only as a *string reference* (present or absent); resolving what it
  means, whether it exists in the Registry, or whether the caller may invoke it, is Kernel/Registry/
  Security Engine business this module never touches and never duplicates (F1's own capability
  model already owns that authoritatively).
- No third-party graph library is used — every check below is a plain traversal over the graph's
  own `nodes`/`edges` lists, matching `KnowledgeGraph`'s (`engines/knowledge/graph.py`) precedent of
  a small, dependency-free, in-memory implementation for exactly this kind of structural check.

Cycle policy (Milestone F2, Chief-Architect-ratified): `WorkflowGraph` is acyclic. This is a
deliberate, temporary restriction, not a claim that KORTEX workflows can never loop — no executor
exists yet that could run a cycle safely, and no edge vocabulary exists yet to distinguish an
intentional loop-back from an authoring mistake. A future milestone may introduce an `edge_kind`
discriminator (e.g. a `LOOP` kind) and narrow this check to non-loop edges only; that is an
additive change to this module, not a rewrite of it. No such field is added preemptively here —
doing so with no consumer for it would add surface area this milestone has no use for.
"""

from __future__ import annotations

from kortex.engines.workflow.exceptions import WorkflowGraphValidationError
from kortex.engines.workflow.models import WorkflowGraph


def validate_graph(graph: WorkflowGraph) -> None:
    """Validate every F2 structural invariant, raising `WorkflowGraphValidationError` on the first
    violation found (fail-fast, matching this codebase's prevailing convention — e.g.
    `KnowledgeGraph.add_relationship` raises immediately per violation rather than aggregating).

    Checks, in order:
      1. Node IDs are unique.
      2. Edge IDs are unique.
      3. `entry_node_id` references an existing node.
      4. Every edge's `source_node_id` references an existing node.
      5. Every edge's `target_node_id` references an existing node.
      6. Every edge's `source_port`/`target_port`, when set, is declared on the referenced node's
         own `output_ports`/`input_ports` — an edge that leaves a port `None` is never required to
         reference one.
      7. No self-loop (`source_node_id == target_node_id`) and no multi-node directed cycle exists.

    Does not check reachability from the entry node — see `find_unreachable_nodes` below, which is
    deliberately informational rather than fatal (an authoring UI may legitimately hold a
    not-yet-connected node mid-edit; F2's own contract does not require rejecting that).
    """
    nodes_by_id = _validate_unique_node_ids(graph)
    _validate_unique_edge_ids(graph)
    _validate_entry_node(graph, nodes_by_id)
    _validate_edge_node_references(graph, nodes_by_id)
    _validate_edge_port_references(graph, nodes_by_id)
    _validate_acyclic(graph)


def find_unreachable_nodes(graph: WorkflowGraph) -> list[str]:
    """Return the `node_id` of every node not reachable from `entry_node_id` by following outgoing
    edges — informational only, never raised by `validate_graph`. A node may be legitimately
    disconnected mid-authoring; F2 does not treat that as a structural defect.

    Assumes `graph` already passed `validate_graph` (so `entry_node_id` is known to resolve to a
    real node) — callers should validate first.
    """
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source_node_id, []).append(edge.target_node_id)

    visited: set[str] = {graph.entry_node_id}
    frontier = [graph.entry_node_id]
    while frontier:
        current = frontier.pop()
        for neighbor in outgoing.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append(neighbor)

    return [node.node_id for node in graph.nodes if node.node_id not in visited]


def _validate_unique_node_ids(graph: WorkflowGraph) -> dict[str, object]:
    seen: dict[str, object] = {}
    for node in graph.nodes:
        if node.node_id in seen:
            raise WorkflowGraphValidationError(f"Duplicate node ID '{node.node_id}' in workflow graph.")
        seen[node.node_id] = node
    return seen


def _validate_unique_edge_ids(graph: WorkflowGraph) -> None:
    seen: set[str] = set()
    for edge in graph.edges:
        if edge.edge_id in seen:
            raise WorkflowGraphValidationError(f"Duplicate edge ID '{edge.edge_id}' in workflow graph.")
        seen.add(edge.edge_id)


def _validate_entry_node(graph: WorkflowGraph, nodes_by_id: dict[str, object]) -> None:
    if graph.entry_node_id not in nodes_by_id:
        raise WorkflowGraphValidationError(
            f"entry_node_id '{graph.entry_node_id}' does not reference any node in this graph."
        )


def _validate_edge_node_references(graph: WorkflowGraph, nodes_by_id: dict[str, object]) -> None:
    for edge in graph.edges:
        if edge.source_node_id not in nodes_by_id:
            raise WorkflowGraphValidationError(
                f"Edge '{edge.edge_id}' references nonexistent source node '{edge.source_node_id}'."
            )
        if edge.target_node_id not in nodes_by_id:
            raise WorkflowGraphValidationError(
                f"Edge '{edge.edge_id}' references nonexistent target node '{edge.target_node_id}'."
            )


def _validate_edge_port_references(graph: WorkflowGraph, nodes_by_id: dict[str, object]) -> None:
    for edge in graph.edges:
        if edge.source_port is not None:
            source_node = nodes_by_id[edge.source_node_id]
            if edge.source_port not in source_node.output_ports:  # type: ignore[attr-defined]
                raise WorkflowGraphValidationError(
                    f"Edge '{edge.edge_id}' references source_port '{edge.source_port}' not declared "
                    f"in node '{edge.source_node_id}'.output_ports."
                )
        if edge.target_port is not None:
            target_node = nodes_by_id[edge.target_node_id]
            if edge.target_port not in target_node.input_ports:  # type: ignore[attr-defined]
                raise WorkflowGraphValidationError(
                    f"Edge '{edge.edge_id}' references target_port '{edge.target_port}' not declared "
                    f"in node '{edge.target_node_id}'.input_ports."
                )


def _validate_acyclic(graph: WorkflowGraph) -> None:
    """Reject a self-loop or any multi-node directed cycle (Milestone F2 cycle policy — see module
    docstring). Iterative white/gray/black DFS: never revisits a fully-explored (black) node, and
    raises the moment a currently-in-progress (gray) node is reached again, which is exactly a
    back-edge — the definition of a cycle in a directed graph."""
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.source_node_id == edge.target_node_id:
            raise WorkflowGraphValidationError(
                f"Edge '{edge.edge_id}' is a self-loop on node '{edge.source_node_id}', which F2 "
                f"forbids (see graph_validation.py's cycle-policy docstring)."
            )
        outgoing.setdefault(edge.source_node_id, []).append(edge.target_node_id)

    white, gray, black = 0, 1, 2
    color: dict[str, int] = {node.node_id: white for node in graph.nodes}

    for start in color:
        if color[start] != white:
            continue
        # Iterative DFS with an explicit stack of (node, neighbor_iterator_index) frames, avoiding
        # Python recursion-depth limits on a large graph.
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = gray
        while stack:
            node_id, next_idx = stack[-1]
            neighbors = outgoing.get(node_id, [])
            if next_idx >= len(neighbors):
                color[node_id] = black
                stack.pop()
                continue
            stack[-1] = (node_id, next_idx + 1)
            neighbor = neighbors[next_idx]
            if color[neighbor] == gray:
                raise WorkflowGraphValidationError(
                    f"Workflow graph contains a directed cycle reaching back to node '{neighbor}' "
                    f"— F2 requires an acyclic graph (see graph_validation.py's cycle-policy docstring)."
                )
            if color[neighbor] == white:
                color[neighbor] = gray
                stack.append((neighbor, 0))
