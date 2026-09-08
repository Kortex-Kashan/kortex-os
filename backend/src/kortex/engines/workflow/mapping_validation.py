"""
KORTEX Workflow Mapping Structural & Graph-Aware Validation
(Milestone F3 — Workflow Data Mapping & Expression Foundation).

Enforces the structural invariants a `WorkflowMapping` (`workflow/models.py`) must satisfy before
any future consumer trusts it: resource-limit compliance (Chief-Architect-ratified decision D4),
and graph-aware reference validity (F3 discovery §15, decision D1/§6 of the implementation
mandate). Kept as its own module, mirroring `graph_validation.py`'s existing separation of "rules"
from "models" — this module contains no resolution/evaluation logic of its own (see `mapping.py`/
`expression.py` for that); it only decides whether a mapping is *well-formed*, never what it
*resolves to*.

Scope discipline (Milestone F3 firewall):
- No capability metadata is read from, or written onto, anything here. A `WorkflowReference`'s
  `source_port` is validated only as a string declared on the source node's own `output_ports` —
  never resolved against the Capability Registry, never assumed to carry a schema.
- No third-party graph or schema library — the ancestor check reuses `graph_validation.
  compute_ancestors` (itself a plain, dependency-free traversal); schema classification below is a
  handful of `isinstance` checks against an optional, caller-supplied JSON-Schema-shaped dict, never
  a JSON Schema validator library.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from kortex.engines.workflow.exceptions import WorkflowMappingValidationError, WorkflowReferenceError
from kortex.engines.workflow.graph_validation import compute_ancestors
from kortex.engines.workflow.models import (
    MAX_EXPRESSION_NESTING_DEPTH,
    MAX_EXPRESSION_OPERANDS,
    MAX_MAPPING_VALUES_PER_NODE,
    MAX_REFERENCE_PATH_DEPTH,
    WorkflowExpression,
    WorkflowExpressionValue,
    WorkflowGraph,
    WorkflowMapping,
    WorkflowReference,
    WorkflowReferenceValue,
)


def validate_mapping(graph: WorkflowGraph, consuming_node_id: str, mapping: WorkflowMapping) -> None:
    """Validate every field of `mapping` — resource limits, then every reference's structural and
    graph-aware validity — for a mapping that will supply input to `consuming_node_id`.

    Raises:
        WorkflowMappingValidationError: A resource limit is exceeded.
        WorkflowReferenceError: Any reference within `mapping` is structurally invalid or not a
            valid graph-aware target (§ below).
    """
    if len(mapping.values) > MAX_MAPPING_VALUES_PER_NODE:
        raise WorkflowMappingValidationError(
            f"Mapping for node '{consuming_node_id}' declares {len(mapping.values)} values, "
            f"exceeding the maximum of {MAX_MAPPING_VALUES_PER_NODE}."
        )
    for field_name, value in mapping.values.items():
        _validate_value(graph, consuming_node_id, value, field_name=field_name, depth=1)


def validate_reference(graph: WorkflowGraph, consuming_node_id: str, reference: WorkflowReference) -> None:
    """Validate one `WorkflowReference` in isolation — structural shape, then graph-aware validity.

    A reference is valid exactly when:
      1. `source_node_id` references an existing node in `graph`.
      2. `source_port`, when set, is declared in that node's own `output_ports`.
      3. `path` does not exceed `MAX_REFERENCE_PATH_DEPTH`.
      4. `source_node_id` is not `consuming_node_id` (no self-reference).
      5. `source_node_id` is a topological ancestor of `consuming_node_id` — this single check
         simultaneously rejects a sibling-branch reference, a descendant/future-node reference,
         and a disconnected-node reference, since none of those is ever in the ancestor set
         (F3 architecture discovery §15).

    Raises:
        WorkflowMappingValidationError: `path` exceeds the maximum depth.
        WorkflowReferenceError: Any other invalidity above.
    """
    if len(reference.path) > MAX_REFERENCE_PATH_DEPTH:
        raise WorkflowMappingValidationError(
            f"Reference to node '{reference.source_node_id}' has a path of length "
            f"{len(reference.path)}, exceeding the maximum of {MAX_REFERENCE_PATH_DEPTH}."
        )

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    if reference.source_node_id not in nodes_by_id:
        raise WorkflowReferenceError(
            f"Reference from node '{consuming_node_id}' targets nonexistent source node '{reference.source_node_id}'."
        )

    if reference.source_node_id == consuming_node_id:
        raise WorkflowReferenceError(f"Node '{consuming_node_id}' cannot reference its own output.")

    if reference.source_port is not None:
        source_node = nodes_by_id[reference.source_node_id]
        if reference.source_port not in source_node.output_ports:
            raise WorkflowReferenceError(
                f"Reference from node '{consuming_node_id}' names source_port "
                f"'{reference.source_port}', not declared in node '{reference.source_node_id}'.output_ports."
            )

    ancestors = compute_ancestors(graph, consuming_node_id)
    if reference.source_node_id not in ancestors:
        raise WorkflowReferenceError(
            f"Reference from node '{consuming_node_id}' targets node '{reference.source_node_id}', "
            f"which is not a topological ancestor of '{consuming_node_id}' — it is a sibling "
            f"branch, a descendant, or disconnected. A node may only reference an ancestor's output."
        )


def _validate_value(graph: WorkflowGraph, consuming_node_id: str, value: object, field_name: str, depth: int) -> None:
    if isinstance(value, WorkflowReferenceValue):
        validate_reference(graph, consuming_node_id, value.reference)
    elif isinstance(value, WorkflowExpressionValue):
        _validate_expression(graph, consuming_node_id, value.expression, field_name=field_name, depth=depth)
    # WorkflowLiteralValue: nothing to validate — a literal carries no reference or nested structure
    # this module is responsible for.


def _validate_expression(
    graph: WorkflowGraph, consuming_node_id: str, expression: WorkflowExpression, field_name: str, depth: int
) -> None:
    if depth > MAX_EXPRESSION_NESTING_DEPTH:
        raise WorkflowMappingValidationError(
            f"Expression for field '{field_name}' on node '{consuming_node_id}' nests "
            f"{depth} levels deep, exceeding the maximum of {MAX_EXPRESSION_NESTING_DEPTH}."
        )
    if len(expression.operands) > MAX_EXPRESSION_OPERANDS:
        raise WorkflowMappingValidationError(
            f"Expression for field '{field_name}' on node '{consuming_node_id}' declares "
            f"{len(expression.operands)} operands, exceeding the maximum of {MAX_EXPRESSION_OPERANDS}."
        )
    for operand in expression.operands:
        _validate_value(graph, consuming_node_id, operand, field_name=field_name, depth=depth + 1)


# ============================================================================
# Type / schema classification (F3 discovery §10, implementation mandate §10)
# ============================================================================


class SchemaValidationOutcome(str, Enum):
    """The three-way result of classifying a resolved value against an optional declared schema.

    Deliberately three-valued, never a boolean: `CapabilityDescriptor.returns_schema` is populated
    by none of the 140 production capabilities today (F3 architecture discovery §6) — treating an
    absent schema as `VALID` would be false certainty, and treating it as `INVALID` would reject
    perfectly good data for no reason. `UNKNOWN` names the honest state; nothing here ever
    fabricates a schema to avoid it.
    """

    VALID = "VALID"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


_JSON_SCHEMA_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def classify_against_schema(value: Any, schema: dict[str, Any] | None) -> SchemaValidationOutcome:
    """Classify `value` against an optional JSON-Schema-shaped `schema` dict's own `"type"` key.

    Never coerces `value` — this is a pure classification, never a transformation. Recognizes
    exactly the JSON Schema primitive type names KORTEX already uses elsewhere (capability
    `parameters_schema`, `TemplateSchema.schema_definition`, AI tool schemas) — no new type system.

    Returns:
        `UNKNOWN` if `schema` is `None`/empty, or declares no recognized `"type"`.
        `VALID`/`INVALID` otherwise, per the declared type.
    """
    if not schema:
        return SchemaValidationOutcome.UNKNOWN
    declared_type = schema.get("type")
    if not isinstance(declared_type, str):
        return SchemaValidationOutcome.UNKNOWN
    check = _JSON_SCHEMA_TYPE_CHECKS.get(declared_type)
    if check is None:
        return SchemaValidationOutcome.UNKNOWN
    return SchemaValidationOutcome.VALID if check(value) else SchemaValidationOutcome.INVALID
