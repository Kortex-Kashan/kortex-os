"""
Unit tests for KORTEX Workflow Mapping Resolution
(Milestone F3 — Workflow Data Mapping & Expression Foundation).

Covers `mapping.resolve_path`/`resolve_reference`/`resolve_value`/`resolve_mapping` and
`expression.evaluate_expression` — the pure resolver and evaluator, exercised directly against a
`WorkflowRuntimeContext`, with no graph, no persistence, and no execution involved.
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowExpressionError, WorkflowMappingValidationError
from kortex.engines.workflow.expression import evaluate_expression
from kortex.engines.workflow.mapping import resolve_mapping, resolve_path, resolve_reference, resolve_value
from kortex.engines.workflow.models import (
    MAX_TRAVERSAL_DEPTH,
    WorkflowExpression,
    WorkflowExpressionValue,
    WorkflowLiteralValue,
    WorkflowMapping,
    WorkflowOperator,
    WorkflowReference,
    WorkflowReferenceValue,
    WorkflowRuntimeContext,
)

# -- resolve_path ------------------------------------------------------------------------------


def test_resolve_path_empty_returns_data_itself() -> None:
    assert resolve_path({"a": 1}, []) == (True, {"a": 1})


def test_resolve_path_simple_key() -> None:
    assert resolve_path({"a": 1}, ["a"]) == (True, 1)


def test_resolve_path_nested_object() -> None:
    data = {"body": {"customer": {"email": "a@b.com"}}}
    assert resolve_path(data, ["body", "customer", "email"]) == (True, "a@b.com")


def test_resolve_path_array_index() -> None:
    assert resolve_path({"items": ["x", "y", "z"]}, ["items", 1]) == (True, "y")


def test_resolve_path_negative_array_index() -> None:
    assert resolve_path({"items": ["x", "y", "z"]}, ["items", -1]) == (True, "z")


def test_resolve_path_mixed_object_and_array() -> None:
    data = {"items": [{"name": "first"}, {"name": "second"}]}
    assert resolve_path(data, ["items", 1, "name"]) == (True, "second")


def test_resolve_path_missing_key_returns_not_found() -> None:
    assert resolve_path({"a": 1}, ["b"]) == (False, None)


def test_resolve_path_missing_array_index_returns_not_found() -> None:
    assert resolve_path({"items": ["x"]}, ["items", 5]) == (False, None)


def test_resolve_path_traversal_into_non_container_returns_not_found() -> None:
    assert resolve_path({"a": 1}, ["a", "b"]) == (False, None)


def test_resolve_path_at_max_traversal_depth_is_accepted() -> None:
    path: list[str | int] = ["a"] * MAX_TRAVERSAL_DEPTH
    data: dict = {}
    cursor = data
    for i, key in enumerate(path):
        if i == len(path) - 1:
            cursor[key] = "leaf"
        else:
            cursor[key] = {}
            cursor = cursor[key]
    assert resolve_path(data, path) == (True, "leaf")


def test_resolve_path_beyond_max_traversal_depth_is_rejected() -> None:
    path: list[str | int] = ["a"] * (MAX_TRAVERSAL_DEPTH + 1)
    with pytest.raises(WorkflowMappingValidationError, match="exceeds the maximum"):
        resolve_path({}, path)


# -- null / missing / empty semantics -----------------------------------------------------------


def test_explicit_null_is_found_true_value_none() -> None:
    assert resolve_path({"a": None}, ["a"]) == (True, None)


def test_empty_string_is_found_true() -> None:
    assert resolve_path({"a": ""}, ["a"]) == (True, "")


def test_empty_object_is_found_true() -> None:
    assert resolve_path({"a": {}}, ["a"]) == (True, {})


def test_empty_array_is_found_true() -> None:
    assert resolve_path({"a": []}, ["a"]) == (True, [])


def test_missing_never_equals_explicit_null() -> None:
    missing = resolve_path({}, ["a"])
    explicit_null = resolve_path({"a": None}, ["a"])
    assert missing == (False, None)
    assert explicit_null == (True, None)
    assert missing != explicit_null


# -- resolve_reference ---------------------------------------------------------------------------


def test_resolve_reference_missing_node_returns_not_found() -> None:
    ctx = WorkflowRuntimeContext(node_outputs={})
    ref = WorkflowReference(source_node_id="DoesNotExist")
    assert resolve_reference(ref, ctx) == (False, None)


def test_resolve_reference_with_port_and_path() -> None:
    ctx = WorkflowRuntimeContext(node_outputs={"GetCustomer": {"body": {"email": "a@b.com"}}})
    ref = WorkflowReference(source_node_id="GetCustomer", source_port="body", path=["email"])
    assert resolve_reference(ref, ctx) == (True, "a@b.com")


def test_resolve_reference_without_port_reads_raw_output_directly() -> None:
    ctx = WorkflowRuntimeContext(node_outputs={"A": {"status": "ok"}})
    ref = WorkflowReference(source_node_id="A", path=["status"])
    assert resolve_reference(ref, ctx) == (True, "ok")


# -- resolve_value / expressions -------------------------------------------------------------------


def test_resolve_value_literal_always_found() -> None:
    ctx = WorkflowRuntimeContext()
    assert resolve_value(WorkflowLiteralValue(value="x"), ctx) == (True, "x")
    assert resolve_value(WorkflowLiteralValue(value=None), ctx) == (True, None)


@pytest.mark.parametrize(
    ("operator", "operands", "expected"),
    [
        (WorkflowOperator.CONCAT, ["a", "b", "c"], "abc"),
        (WorkflowOperator.CONCAT, ["x", 1], "x1"),
        (WorkflowOperator.SUM, [1, 2, 3], 6.0),
        (WorkflowOperator.SUM, [1.5, 2.5], 4.0),
        (WorkflowOperator.SUBTRACT, [10, 3, 2], 5.0),
        (WorkflowOperator.LENGTH, ["hello"], 5),
        (WorkflowOperator.LENGTH, [[1, 2, 3]], 3),
        (WorkflowOperator.LENGTH, [{"a": 1, "b": 2}], 2),
    ],
)
def test_every_operator_produces_the_expected_result(
    operator: WorkflowOperator, operands: list, expected: object
) -> None:
    assert evaluate_expression(operator, operands) == expected


def test_operator_results_are_deterministic() -> None:
    assert evaluate_expression(WorkflowOperator.SUM, [1, 2, 3]) == evaluate_expression(WorkflowOperator.SUM, [1, 2, 3])


def test_concat_requires_at_least_one_operand() -> None:
    with pytest.raises(WorkflowExpressionError, match="at least one operand"):
        evaluate_expression(WorkflowOperator.CONCAT, [])


def test_sum_rejects_non_numeric_operand() -> None:
    with pytest.raises(WorkflowExpressionError, match="numeric"):
        evaluate_expression(WorkflowOperator.SUM, [1, "not-a-number"])


def test_sum_rejects_boolean_operand() -> None:
    """bool is a Python int subclass — must not silently be accepted as numeric."""
    with pytest.raises(WorkflowExpressionError, match="numeric"):
        evaluate_expression(WorkflowOperator.SUM, [True])


def test_subtract_requires_at_least_one_operand() -> None:
    with pytest.raises(WorkflowExpressionError, match="at least one operand"):
        evaluate_expression(WorkflowOperator.SUBTRACT, [])


def test_length_requires_exactly_one_operand() -> None:
    with pytest.raises(WorkflowExpressionError, match="exactly one operand"):
        evaluate_expression(WorkflowOperator.LENGTH, ["a", "b"])


def test_length_rejects_unsupported_operand_type() -> None:
    with pytest.raises(WorkflowExpressionError, match="string, array, or object"):
        evaluate_expression(WorkflowOperator.LENGTH, [42])


def test_nested_expression_resolves_end_to_end() -> None:
    ctx = WorkflowRuntimeContext(node_outputs={"A": {"first": "Jane", "last": "Doe"}})
    expr = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.CONCAT,
            operands=[
                WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", path=["first"])),
                WorkflowLiteralValue(value=" "),
                WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", path=["last"])),
            ],
        )
    )
    assert resolve_value(expr, ctx) == (True, "Jane Doe")


def test_expression_with_unresolvable_operand_does_not_resolve() -> None:
    """A missing operand makes the whole expression unresolvable — never silently defaulted or
    partially computed."""
    ctx = WorkflowRuntimeContext(node_outputs={})
    expr = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.CONCAT,
            operands=[
                WorkflowLiteralValue(value="x"),
                WorkflowReferenceValue(reference=WorkflowReference(source_node_id="DoesNotExist")),
            ],
        )
    )
    assert resolve_value(expr, ctx) == (False, None)


# -- resolve_mapping -------------------------------------------------------------------------------


def test_resolve_mapping_splits_resolved_and_missing() -> None:
    ctx = WorkflowRuntimeContext(node_outputs={"A": {"email": "a@b.com"}})
    mapping = WorkflowMapping(
        values={
            "recipient": WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", path=["email"])),
            "subject": WorkflowLiteralValue(value="Hello"),
            "phone": WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", path=["phone"])),
        }
    )
    resolved, missing = resolve_mapping(mapping, ctx)
    assert resolved == {"recipient": "a@b.com", "subject": "Hello"}
    assert missing == ["phone"]


def test_resolve_mapping_empty_mapping() -> None:
    assert resolve_mapping(WorkflowMapping(), WorkflowRuntimeContext()) == ({}, [])
