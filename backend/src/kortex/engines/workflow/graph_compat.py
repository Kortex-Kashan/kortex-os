"""
KORTEX Workflow Graph Compatibility Adapter (Milestone F2 — Workflow Graph Model).

Deterministic, explicitly-scoped conversion between the legacy flat `list[WorkflowStep]`
representation `WorkflowDefinition.steps` has always used and the new canonical `WorkflowGraph`
structural representation (`workflow/models.py`). This is the mechanism that makes F2 additive
rather than a breaking change: every existing persisted `WorkflowDefinition` — which has `graph =
None` and always will until some later caller populates it — is still one `steps_to_graph()` call
away from a graph, and a linear graph is still one `graph_to_steps()` call away from the exact flat
shape the unmodified production executor (`engine.py`/`evaluator.py`/`state_machine.py`) already
runs today. Neither direction is wired into that executor by this milestone — it remains an
in-memory conversion utility, not a persistence or execution path.

Scope discipline:
- `graph_to_steps` supports only a *linear* graph — a single unbranching chain from the entry node
  to one terminal node, visiting every node exactly once. A branching or joining graph is explicitly
  rejected with `WorkflowGraphConversionError`; it is never silently flattened by an arbitrary
  traversal, and no edge is ever discarded to force a fit (Milestone F2's own contract, restated in
  `docs`: "Do not choose an arbitrary traversal and pretend it is equivalent").
- Fields not representable by `WorkflowGraphNode`'s minimal ratified shape (a step's human-readable
  `name`, its retry/compensation/approval configuration) are preserved, verbatim and JSON-safe,
  inside that node's own `metadata` dict under clearly namespaced keys — never inferred, never
  silently dropped. This is not "execution semantics leaking into the graph": the graph itself never
  reads or interprets these keys; they exist solely so `graph_to_steps` can reconstruct the original
  `WorkflowStep` exactly. `metadata` is documented as opaque graph-author data for precisely this
  kind of round-trip payload.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.workflow.exceptions import WorkflowGraphConversionError
from kortex.engines.workflow.graph_validation import validate_graph
from kortex.engines.workflow.models import (
    CompensationAction,
    RetryPolicy,
    WorkflowGraph,
    WorkflowGraphEdge,
    WorkflowGraphNode,
    WorkflowStep,
)

_STEP_METADATA_KEY = "kortex.workflow.step"
"""Namespaced metadata key holding a step's non-structural fields — see module docstring."""


def steps_to_graph(steps: list[WorkflowStep]) -> WorkflowGraph:
    """Deterministically project a flat `list[WorkflowStep]` into an equivalent linear `WorkflowGraph`.

    `node_id` is the step's own `id` — step identity is preserved verbatim, never re-minted — and
    edge ids are derived deterministically from the two node ids they connect, so calling this twice
    on the same input always produces byte-identical output (Milestone F2's determinism requirement).

    Raises:
        WorkflowGraphConversionError: `steps` is empty. `WorkflowGraph.entry_node_id` is a required
            reference to a real node — there is no valid graph representation of "no steps at all" —
            so this is an explicit rejection, not a silently-produced invalid graph.
    """
    if not steps:
        raise WorkflowGraphConversionError(
            "Cannot convert an empty step list to a WorkflowGraph: entry_node_id must reference a "
            "real node, and a graph with no nodes has none to reference."
        )

    nodes = [_step_to_node(step) for step in steps]
    edges = [
        WorkflowGraphEdge(
            edge_id=f"edge_{steps[i].id}_{steps[i + 1].id}",
            source_node_id=steps[i].id,
            target_node_id=steps[i + 1].id,
        )
        for i in range(len(steps) - 1)
    ]

    return WorkflowGraph(entry_node_id=steps[0].id, nodes=nodes, edges=edges)


def graph_to_steps(graph: WorkflowGraph) -> list[WorkflowStep]:
    """Deterministically project a `WorkflowGraph` back into a flat `list[WorkflowStep]`, ordered
    from `entry_node_id` to the chain's single terminal node.

    Only a *linear* graph can be represented this way. Raises `WorkflowGraphConversionError` — never
    silently flattens, never discards an edge, never picks an arbitrary traversal order — for any
    graph that branches, joins, is disconnected, or is otherwise not a single unbranching chain
    visiting every node exactly once.

    Validates `graph` structurally first (`graph_validation.validate_graph`) so this never traverses
    a graph with a dangling reference or a cycle.
    """
    validate_graph(graph)

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    out_degree: dict[str, int] = dict.fromkeys(nodes_by_id, 0)
    in_degree: dict[str, int] = dict.fromkeys(nodes_by_id, 0)
    next_node: dict[str, str] = {}
    for edge in graph.edges:
        out_degree[edge.source_node_id] += 1
        in_degree[edge.target_node_id] += 1
        next_node[edge.source_node_id] = edge.target_node_id

    if any(count > 1 for count in out_degree.values()) or any(count > 1 for count in in_degree.values()):
        raise WorkflowGraphConversionError(
            "Cannot convert a branching or joining WorkflowGraph to a flat step list: a node with "
            "more than one outgoing or incoming edge has no faithful sequential representation."
        )
    if in_degree.get(graph.entry_node_id, 0) != 0:
        raise WorkflowGraphConversionError(
            f"Cannot convert this WorkflowGraph to a flat step list: entry_node_id "
            f"'{graph.entry_node_id}' has an incoming edge, so it is not the chain's true start."
        )

    ordered_ids: list[str] = [graph.entry_node_id]
    current = graph.entry_node_id
    while current in next_node:
        current = next_node[current]
        if current in ordered_ids:
            # validate_graph() above already rejects cycles, so this is unreachable in practice —
            # kept as a defensive guard against ever traversing the same node twice.
            raise WorkflowGraphConversionError(
                f"Cannot convert this WorkflowGraph to a flat step list: node '{current}' would be visited twice."
            )
        ordered_ids.append(current)

    if len(ordered_ids) != len(graph.nodes):
        raise WorkflowGraphConversionError(
            "Cannot convert this WorkflowGraph to a flat step list: it is disconnected — the chain "
            f"from entry_node_id '{graph.entry_node_id}' reaches {len(ordered_ids)} of "
            f"{len(graph.nodes)} nodes."
        )

    return [_node_to_step(nodes_by_id[node_id]) for node_id in ordered_ids]


def _step_to_node(step: WorkflowStep) -> WorkflowGraphNode:
    step_metadata: dict[str, Any] = {
        "name": step.name,
        "is_approval_step": step.is_approval_step,
        "required_approval_role": step.required_approval_role,
        "retry_policy": step.retry_policy.model_dump(mode="json") if step.retry_policy is not None else None,
        "compensation_action": (
            step.compensation_action.model_dump(mode="json") if step.compensation_action is not None else None
        ),
        "on_failure_continue": step.on_failure_continue,
    }
    return WorkflowGraphNode(
        node_id=step.id,
        node_type="capability",
        capability_name=step.capability_name,
        config=dict(step.parameters),
        metadata={_STEP_METADATA_KEY: step_metadata},
    )


def _node_to_step(node: WorkflowGraphNode) -> WorkflowStep:
    step_metadata = node.metadata.get(_STEP_METADATA_KEY, {})
    retry_policy_data = step_metadata.get("retry_policy")
    compensation_action_data = step_metadata.get("compensation_action")
    return WorkflowStep(
        id=node.node_id,
        name=step_metadata.get("name", node.node_id),
        capability_name=node.capability_name,
        parameters=dict(node.config),
        is_approval_step=step_metadata.get("is_approval_step", False),
        required_approval_role=step_metadata.get("required_approval_role"),
        retry_policy=RetryPolicy(**retry_policy_data) if retry_policy_data is not None else None,
        compensation_action=(
            CompensationAction(**compensation_action_data) if compensation_action_data is not None else None
        ),
        on_failure_continue=step_metadata.get("on_failure_continue", False),
    )
