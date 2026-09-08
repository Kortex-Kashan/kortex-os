"""
KORTEX Workflow Expression Evaluator (Milestone F3 — Workflow Data Mapping & Expression Foundation).

A pure, deterministic evaluator for the closed `WorkflowOperator` set (`workflow/models.py`).
Mirrors the Document Engine's `InvariantOperator`/`compute_invariant_target` precedent
(`engines/document/ontology.py`) exactly: every operator is a fixed, reviewed, named function over
already-resolved plain Python values — never a parsed grammar, never `eval`/`exec`, never an
extensible plugin or callable-injection mechanism.

Hard architectural boundary (F3 discovery §13, ratified as an invariant): expression evaluation
computes/transforms already-resolved data. It never invokes a capability, never constructs a
`CapabilityRequest`, never calls the Kernel, never touches `SecurityEngine`/`SecretStore`, and never
performs filesystem, network, subprocess, or environment access. This module has no import of any
kind on those systems — enforced both by inspection here and by
`test_workflow_mapping_architecture.py`'s static guard.

This module never resolves a `WorkflowReference` itself and never sees an unresolved operand —
`mapping.py`'s resolver always resolves every operand to a plain value *before* calling
`evaluate_expression`, so the functions below are pure `(operator, list[Any]) -> Any` transforms
with no knowledge of where their operands came from.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.workflow.exceptions import WorkflowExpressionError
from kortex.engines.workflow.models import MAX_EXPRESSION_OPERANDS, WorkflowOperator

_NUMERIC_TYPES = (int, float)


def evaluate_expression(operator: WorkflowOperator, resolved_operands: list[Any]) -> Any:
    """Evaluate one `WorkflowOperator` over already-resolved plain-value operands.

    Args:
        operator: The closed, enum-declared operation to apply.
        resolved_operands: Plain Python values — never a `WorkflowValue`/`WorkflowReference`/
            `WorkflowExpression` object. The caller (`mapping.py`) is responsible for resolving
            every operand first; this function never performs resolution itself.

    Returns:
        The computed result.

    Raises:
        WorkflowExpressionError: The operand count or type is wrong for `operator`, or `operator`
            is not one of the closed set (defensive — Pydantic's own enum validation already
            prevents an invalid `WorkflowOperator` value from existing in the first place).
    """
    if len(resolved_operands) > MAX_EXPRESSION_OPERANDS:
        raise WorkflowExpressionError(
            f"Expression operator '{operator.value}' received {len(resolved_operands)} operands, "
            f"exceeding the maximum of {MAX_EXPRESSION_OPERANDS}."
        )

    if operator == WorkflowOperator.CONCAT:
        return _evaluate_concat(resolved_operands)
    if operator == WorkflowOperator.SUM:
        return _evaluate_sum(resolved_operands)
    if operator == WorkflowOperator.SUBTRACT:
        return _evaluate_subtract(resolved_operands)
    if operator == WorkflowOperator.LENGTH:
        return _evaluate_length(resolved_operands)

    raise WorkflowExpressionError(f"Unsupported expression operator: '{operator}'.")


def _evaluate_concat(operands: list[Any]) -> str:
    if not operands:
        raise WorkflowExpressionError("CONCAT requires at least one operand.")
    return "".join(str(operand) for operand in operands)


def _require_numeric_operands(operator: WorkflowOperator, operands: list[Any]) -> list[float]:
    if not operands:
        raise WorkflowExpressionError(f"{operator.value} requires at least one operand.")
    numeric: list[float] = []
    for index, operand in enumerate(operands):
        if isinstance(operand, bool) or not isinstance(operand, _NUMERIC_TYPES):
            raise WorkflowExpressionError(
                f"{operator.value} operand at index {index} must be numeric (int or float); "
                f"got '{type(operand).__name__}'."
            )
        numeric.append(float(operand))
    return numeric


def _evaluate_sum(operands: list[Any]) -> float:
    numeric = _require_numeric_operands(WorkflowOperator.SUM, operands)
    return float(sum(numeric))


def _evaluate_subtract(operands: list[Any]) -> float:
    numeric = _require_numeric_operands(WorkflowOperator.SUBTRACT, operands)
    return float(numeric[0] - sum(numeric[1:]))


def _evaluate_length(operands: list[Any]) -> int:
    if len(operands) != 1:
        raise WorkflowExpressionError(f"LENGTH requires exactly one operand; got {len(operands)}.")
    operand = operands[0]
    if not isinstance(operand, (str, list, dict)):
        raise WorkflowExpressionError(
            f"LENGTH operand must be a string, array, or object; got '{type(operand).__name__}'."
        )
    return len(operand)
