"""
Unit tests for KORTEX Workflow Expression Evaluator
(Milestone F3 — Workflow Data Mapping & Expression Foundation).

Exercises `expression.evaluate_expression` directly, against hand-built operand lists — the pure
operator layer in isolation, independent of reference resolution or a `WorkflowRuntimeContext`
(see `test_workflow_mapping_resolution.py` for the full resolve-then-evaluate pipeline).
"""

from __future__ import annotations

import pytest

from kortex.engines.workflow.exceptions import WorkflowExpressionError
from kortex.engines.workflow.expression import evaluate_expression
from kortex.engines.workflow.models import MAX_EXPRESSION_OPERANDS, WorkflowOperator


class TestConcat:
    def test_joins_strings(self) -> None:
        assert evaluate_expression(WorkflowOperator.CONCAT, ["a", "b", "c"]) == "abc"

    def test_coerces_non_string_operands(self) -> None:
        assert evaluate_expression(WorkflowOperator.CONCAT, ["count: ", 5]) == "count: 5"

    def test_single_operand(self) -> None:
        assert evaluate_expression(WorkflowOperator.CONCAT, ["only"]) == "only"

    def test_requires_at_least_one_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="at least one operand"):
            evaluate_expression(WorkflowOperator.CONCAT, [])


class TestSum:
    def test_sums_integers(self) -> None:
        assert evaluate_expression(WorkflowOperator.SUM, [1, 2, 3]) == 6.0

    def test_sums_floats(self) -> None:
        assert evaluate_expression(WorkflowOperator.SUM, [1.5, 2.25]) == 3.75

    def test_single_operand(self) -> None:
        assert evaluate_expression(WorkflowOperator.SUM, [7]) == 7.0

    def test_requires_at_least_one_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="at least one operand"):
            evaluate_expression(WorkflowOperator.SUM, [])

    def test_rejects_string_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="numeric"):
            evaluate_expression(WorkflowOperator.SUM, [1, "2"])

    def test_rejects_none_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="numeric"):
            evaluate_expression(WorkflowOperator.SUM, [1, None])

    def test_rejects_boolean_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="numeric"):
            evaluate_expression(WorkflowOperator.SUM, [1, False])

    def test_identifies_offending_operand_index_in_message(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="index 1"):
            evaluate_expression(WorkflowOperator.SUM, [1, "bad", 3])


class TestSubtract:
    def test_subtracts_remaining_from_first(self) -> None:
        assert evaluate_expression(WorkflowOperator.SUBTRACT, [10, 3, 2]) == 5.0

    def test_single_operand_returns_itself(self) -> None:
        assert evaluate_expression(WorkflowOperator.SUBTRACT, [10]) == 10.0

    def test_requires_at_least_one_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="at least one operand"):
            evaluate_expression(WorkflowOperator.SUBTRACT, [])

    def test_rejects_non_numeric_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="numeric"):
            evaluate_expression(WorkflowOperator.SUBTRACT, [10, "3"])


class TestLength:
    def test_string_length(self) -> None:
        assert evaluate_expression(WorkflowOperator.LENGTH, ["hello"]) == 5

    def test_list_length(self) -> None:
        assert evaluate_expression(WorkflowOperator.LENGTH, [[1, 2, 3, 4]]) == 4

    def test_dict_length_counts_keys(self) -> None:
        assert evaluate_expression(WorkflowOperator.LENGTH, [{"a": 1, "b": 2}]) == 2

    def test_empty_string_length_is_zero(self) -> None:
        assert evaluate_expression(WorkflowOperator.LENGTH, [""]) == 0

    def test_empty_list_length_is_zero(self) -> None:
        assert evaluate_expression(WorkflowOperator.LENGTH, [[]]) == 0

    def test_requires_exactly_one_operand_zero(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="exactly one operand"):
            evaluate_expression(WorkflowOperator.LENGTH, [])

    def test_requires_exactly_one_operand_two(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="exactly one operand"):
            evaluate_expression(WorkflowOperator.LENGTH, ["a", "b"])

    def test_rejects_numeric_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="string, array, or object"):
            evaluate_expression(WorkflowOperator.LENGTH, [42])

    def test_rejects_none_operand(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="string, array, or object"):
            evaluate_expression(WorkflowOperator.LENGTH, [None])


class TestDeterminism:
    def test_same_input_produces_identical_output_every_time(self) -> None:
        results = {evaluate_expression(WorkflowOperator.CONCAT, ["a", "b", "c"]) for _ in range(20)}
        assert results == {"abc"}

    def test_operator_never_mutates_its_operand_list(self) -> None:
        operands = ["a", "b"]
        original = list(operands)
        evaluate_expression(WorkflowOperator.CONCAT, operands)
        assert operands == original


class TestOperandCountDefense:
    """expression.py enforces MAX_EXPRESSION_OPERANDS itself, in addition to
    mapping_validation.py's static check — defense in depth, not reliance on a caller having
    validated first."""

    def test_at_max_operand_count_is_accepted(self) -> None:
        evaluate_expression(WorkflowOperator.CONCAT, ["x"] * MAX_EXPRESSION_OPERANDS)

    def test_beyond_max_operand_count_is_rejected(self) -> None:
        with pytest.raises(WorkflowExpressionError, match="exceeding the maximum"):
            evaluate_expression(WorkflowOperator.CONCAT, ["x"] * (MAX_EXPRESSION_OPERANDS + 1))
