"""
Unit tests for KORTEX Workflow Mapping Structural & Graph-Aware Validation
(Milestone F3 — Workflow Data Mapping & Expression Foundation).

Covers `mapping_validation.validate_reference`/`validate_mapping` (structural limits + graph-aware
ancestor-based reference validity) and `classify_against_schema` (the three-way schema outcome).
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowMappingValidationError, WorkflowReferenceError
from kortex.engines.workflow.mapping_validation import (
    SchemaValidationOutcome,
    classify_against_schema,
    validate_mapping,
    validate_reference,
)
from kortex.engines.workflow.models import (
    MAX_EXPRESSION_NESTING_DEPTH,
    MAX_EXPRESSION_OPERANDS,
    MAX_MAPPING_VALUES_PER_NODE,
    MAX_REFERENCE_PATH_DEPTH,
    WorkflowExpression,
    WorkflowExpressionValue,
    WorkflowGraph,
    WorkflowGraphEdge,
    WorkflowGraphNode,
    WorkflowLiteralValue,
    WorkflowMapping,
    WorkflowOperator,
    WorkflowReference,
    WorkflowReferenceValue,
)


def _node(node_id: str, **kwargs: object) -> WorkflowGraphNode:
    return WorkflowGraphNode(node_id=node_id, node_type="capability", **kwargs)  # type: ignore[arg-type]


def _edge(edge_id: str, source: str, target: str) -> WorkflowGraphEdge:
    return WorkflowGraphEdge(edge_id=edge_id, source_node_id=source, target_node_id=target)


def _linear_graph() -> WorkflowGraph:
    """A -> B -> C"""
    return WorkflowGraph(
        entry_node_id="A",
        nodes=[_node("A", output_ports=["result"]), _node("B", output_ports=["result"]), _node("C")],
        edges=[_edge("e1", "A", "B"), _edge("e2", "B", "C")],
    )


def _branching_graph() -> WorkflowGraph:
    """A -> B, A -> C (siblings)"""
    return WorkflowGraph(
        entry_node_id="A",
        nodes=[_node("A", output_ports=["result"]), _node("B"), _node("C")],
        edges=[_edge("e1", "A", "B"), _edge("e2", "A", "C")],
    )


# -- graph-aware reference validity ---------------------------------------------------------------


def test_ancestor_reference_is_accepted() -> None:
    graph = _linear_graph()
    validate_reference(graph, "C", WorkflowReference(source_node_id="A"))
    validate_reference(graph, "C", WorkflowReference(source_node_id="B"))


def test_sibling_branch_reference_is_rejected() -> None:
    graph = _branching_graph()
    with pytest.raises(WorkflowReferenceError, match="not a topological ancestor"):
        validate_reference(graph, "C", WorkflowReference(source_node_id="B"))


def test_descendant_future_node_reference_is_rejected() -> None:
    graph = _linear_graph()
    with pytest.raises(WorkflowReferenceError, match="not a topological ancestor"):
        validate_reference(graph, "A", WorkflowReference(source_node_id="C"))


def test_disconnected_node_reference_is_rejected() -> None:
    graph = WorkflowGraph(entry_node_id="A", nodes=[_node("A"), _node("orphan")], edges=[])
    with pytest.raises(WorkflowReferenceError, match="not a topological ancestor"):
        validate_reference(graph, "A", WorkflowReference(source_node_id="orphan"))


def test_self_reference_is_rejected() -> None:
    graph = _linear_graph()
    with pytest.raises(WorkflowReferenceError, match="cannot reference its own output"):
        validate_reference(graph, "A", WorkflowReference(source_node_id="A"))


def test_reference_to_nonexistent_node_is_rejected() -> None:
    graph = _linear_graph()
    with pytest.raises(WorkflowReferenceError, match="nonexistent source node"):
        validate_reference(graph, "C", WorkflowReference(source_node_id="does-not-exist"))


def test_reference_to_undeclared_port_is_rejected() -> None:
    graph = _linear_graph()
    with pytest.raises(WorkflowReferenceError, match="output_ports"):
        validate_reference(graph, "C", WorkflowReference(source_node_id="A", source_port="not-declared"))


def test_reference_with_no_port_never_requires_one() -> None:
    graph = _linear_graph()
    validate_reference(graph, "C", WorkflowReference(source_node_id="A", path=["anything"]))


# -- resource limits: path depth --------------------------------------------------------------------


def test_reference_path_at_max_depth_is_accepted() -> None:
    graph = _linear_graph()
    validate_reference(graph, "C", WorkflowReference(source_node_id="A", path=["x"] * MAX_REFERENCE_PATH_DEPTH))


def test_reference_path_beyond_max_depth_is_rejected() -> None:
    graph = _linear_graph()
    oversized_path = ["x"] * (MAX_REFERENCE_PATH_DEPTH + 1)
    with pytest.raises(WorkflowMappingValidationError, match="exceeding the maximum"):
        validate_reference(graph, "C", WorkflowReference(source_node_id="A", path=oversized_path))


# -- resource limits: mapping value count -------------------------------------------------------------


def test_mapping_at_max_value_count_is_accepted() -> None:
    graph = _linear_graph()
    values = {f"f{i}": WorkflowLiteralValue(value=i) for i in range(MAX_MAPPING_VALUES_PER_NODE)}
    validate_mapping(graph, "C", WorkflowMapping(values=values))


def test_mapping_beyond_max_value_count_is_rejected() -> None:
    graph = _linear_graph()
    mapping = WorkflowMapping(
        values={f"f{i}": WorkflowLiteralValue(value=i) for i in range(MAX_MAPPING_VALUES_PER_NODE + 1)}
    )
    with pytest.raises(WorkflowMappingValidationError, match="exceeding the maximum"):
        validate_mapping(graph, "C", mapping)


# -- resource limits: expression nesting depth ---------------------------------------------------------


def _nest_expression(depth: int) -> WorkflowExpressionValue:
    value: object = WorkflowLiteralValue(value=1)
    for _ in range(depth):
        value = WorkflowExpressionValue(expression=WorkflowExpression(operator=WorkflowOperator.SUM, operands=[value]))
    return value  # type: ignore[return-value]


def test_expression_at_max_nesting_depth_is_accepted() -> None:
    graph = _linear_graph()
    mapping = WorkflowMapping(values={"f": _nest_expression(MAX_EXPRESSION_NESTING_DEPTH)})
    validate_mapping(graph, "C", mapping)


def test_expression_beyond_max_nesting_depth_is_rejected() -> None:
    graph = _linear_graph()
    mapping = WorkflowMapping(values={"f": _nest_expression(MAX_EXPRESSION_NESTING_DEPTH + 1)})
    with pytest.raises(WorkflowMappingValidationError, match="nests"):
        validate_mapping(graph, "C", mapping)


# -- resource limits: expression operand count ---------------------------------------------------------


def test_expression_at_max_operand_count_is_accepted() -> None:
    graph = _linear_graph()
    expr = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.CONCAT, operands=[WorkflowLiteralValue(value="x")] * MAX_EXPRESSION_OPERANDS
        )
    )
    validate_mapping(graph, "C", WorkflowMapping(values={"f": expr}))


def test_expression_beyond_max_operand_count_is_rejected() -> None:
    graph = _linear_graph()
    expr = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.CONCAT, operands=[WorkflowLiteralValue(value="x")] * (MAX_EXPRESSION_OPERANDS + 1)
        )
    )
    with pytest.raises(WorkflowMappingValidationError, match="operands"):
        validate_mapping(graph, "C", WorkflowMapping(values={"f": expr}))


# -- validate_mapping validates nested references too ----------------------------------------------------


def test_validate_mapping_validates_references_nested_inside_expressions() -> None:
    graph = _branching_graph()
    bad_mapping = WorkflowMapping(
        values={
            "f": WorkflowExpressionValue(
                expression=WorkflowExpression(
                    operator=WorkflowOperator.CONCAT,
                    operands=[WorkflowReferenceValue(reference=WorkflowReference(source_node_id="B"))],
                )
            )
        }
    )
    with pytest.raises(WorkflowReferenceError, match="not a topological ancestor"):
        validate_mapping(graph, "C", bad_mapping)


def test_validate_mapping_accepts_a_fully_valid_mapping() -> None:
    graph = _linear_graph()
    mapping = WorkflowMapping(
        values={
            "recipient": WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", source_port="result")),
            "subject": WorkflowLiteralValue(value="Hello"),
        }
    )
    validate_mapping(graph, "C", mapping)


# -- schema classification -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        ("hi", None, SchemaValidationOutcome.UNKNOWN),
        ("hi", {}, SchemaValidationOutcome.UNKNOWN),
        ("hi", {"type": "unrecognized-type"}, SchemaValidationOutcome.UNKNOWN),
        ("hi", {"type": "string"}, SchemaValidationOutcome.VALID),
        (5, {"type": "string"}, SchemaValidationOutcome.INVALID),
        (5, {"type": "integer"}, SchemaValidationOutcome.VALID),
        (5.5, {"type": "integer"}, SchemaValidationOutcome.INVALID),
        (5.5, {"type": "number"}, SchemaValidationOutcome.VALID),
        (5, {"type": "number"}, SchemaValidationOutcome.VALID),
        (True, {"type": "integer"}, SchemaValidationOutcome.INVALID),  # bool must not pass as integer
        (True, {"type": "boolean"}, SchemaValidationOutcome.VALID),
        ({"a": 1}, {"type": "object"}, SchemaValidationOutcome.VALID),
        ([1, 2], {"type": "array"}, SchemaValidationOutcome.VALID),
        ([1, 2], {"type": "object"}, SchemaValidationOutcome.INVALID),
        (None, {"type": "null"}, SchemaValidationOutcome.VALID),
        (None, {"type": "string"}, SchemaValidationOutcome.INVALID),
    ],
)
def test_classify_against_schema(value: object, schema: dict | None, expected: SchemaValidationOutcome) -> None:
    assert classify_against_schema(value, schema) == expected


def test_classify_against_schema_never_fabricates_a_schema() -> None:
    """Absent schema is always UNKNOWN — never silently treated as VALID or INVALID."""
    assert classify_against_schema("anything at all", None) == SchemaValidationOutcome.UNKNOWN
    assert classify_against_schema({"deeply": {"nested": True}}, None) == SchemaValidationOutcome.UNKNOWN
