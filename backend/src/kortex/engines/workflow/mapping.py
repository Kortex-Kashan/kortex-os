"""
KORTEX Workflow Mapping Resolution (Milestone F3 — Workflow Data Mapping & Expression Foundation).

A pure resolver for `WorkflowValue`/`WorkflowReference`/`WorkflowMapping` (`workflow/models.py`),
built directly on the Document Engine's `resolve_dotted_path` precedent
(`engines/document/template_binder.py`): every lookup returns `(found: bool, value: Any)` rather
than raising on a missing path — "missing" and "explicitly null" are never conflated (Chief
Architect decision D6), and a caller always gets an explicit, inspectable answer rather than a
silent default.

Hard architectural boundary (F3 discovery §13, ratified as an invariant, enforced structurally by
this module's own import list and re-verified by `test_workflow_mapping_architecture.py`): this
resolver receives all the data it needs as an explicit `WorkflowRuntimeContext` argument and reads
nothing else. It never accesses ambient state, the Kernel, `SecurityEngine`, `SecretStore`, the
filesystem, the network, or a subprocess. It cannot invoke a capability because it holds no
reference to anything capable of dispatching one. This is what makes it safe to call before any
approval, authorization, or tenant-scoping decision has been made about the workflow it belongs to
— it computes, it never acts.

Not wired into the production executor by this milestone — see `workflow/engine.py`'s
`StepEvaluator.execute_step`, unmodified, for the seam a future milestone would use.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.workflow.exceptions import WorkflowMappingValidationError
from kortex.engines.workflow.expression import evaluate_expression
from kortex.engines.workflow.models import (
    MAX_TRAVERSAL_DEPTH,
    WorkflowExpression,
    WorkflowExpressionValue,
    WorkflowLiteralValue,
    WorkflowMapping,
    WorkflowReference,
    WorkflowReferenceValue,
    WorkflowRuntimeContext,
    WorkflowValue,
)


def resolve_path(data: Any, path: list[str | int]) -> tuple[bool, Any]:
    """Safely resolve a `path` of string keys (dict access) and/or integer indices (list access,
    negative indices included, exactly like ordinary Python indexing) beneath `data`.

    Returns `(True, value)` the moment every segment resolves — including when `value` is `None`,
    an empty string, an empty dict, or an empty list, all of which are legitimate resolved data,
    never conflated with "missing" (decision D6). Returns `(False, None)` the instant any segment
    cannot be resolved (a dict key absent, a list index out of range, or a non-container value
    where a key/index lookup was attempted) — never raises for this, and never guesses further.

    Raises:
        WorkflowMappingValidationError: `len(path) > MAX_TRAVERSAL_DEPTH` — a defensive ceiling
            independent of any earlier structural validation, so this function is safe to call
            directly without relying on a caller having validated the reference first.
    """
    if len(path) > MAX_TRAVERSAL_DEPTH:
        raise WorkflowMappingValidationError(
            f"Path traversal of depth {len(path)} exceeds the maximum of {MAX_TRAVERSAL_DEPTH}."
        )

    current: Any = data
    for segment in path:
        if isinstance(segment, str):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            else:
                return (False, None)
        else:
            if isinstance(current, list) and -len(current) <= segment < len(current):
                current = current[segment]
            else:
                return (False, None)
    return (True, current)


def resolve_reference(reference: WorkflowReference, context: WorkflowRuntimeContext) -> tuple[bool, Any]:
    """Resolve a `WorkflowReference` against `context.node_outputs`.

    `source_port`, when set, is treated as the first traversal segment beneath the source node's
    raw output, with `reference.path` continuing from there (see `WorkflowReference`'s own
    docstring for why — F2's ports carry no independent schema/keying of their own today).

    Returns `(False, None)` — never raises — when `reference.source_node_id` has not (yet)
    produced an output in `context`; this is the "referenced node did not execute" case, identical
    in shape to any other missing path segment.
    """
    if reference.source_node_id not in context.node_outputs:
        return (False, None)

    raw_output = context.node_outputs[reference.source_node_id]
    full_path: list[str | int] = []
    if reference.source_port is not None:
        full_path.append(reference.source_port)
    full_path.extend(reference.path)

    return resolve_path(raw_output, full_path)


def resolve_value(value: WorkflowValue, context: WorkflowRuntimeContext) -> tuple[bool, Any]:
    """Resolve one `WorkflowValue` — literal, reference, or expression — against `context`.

    A literal always resolves (`True`, its own value), including when that value is `None`. A
    reference delegates to `resolve_reference`. An expression first resolves every operand
    (recursively, so a nested expression's own operands are resolved the same way); if *any*
    operand does not resolve (`found=False`), the whole expression does not resolve either —
    `(False, None)` propagates rather than silently substituting a default or a partial result,
    since an operator needs every operand to compute a meaningful answer. Only once every operand
    resolves does `expression.py`'s `evaluate_expression` ever run.
    """
    if isinstance(value, WorkflowLiteralValue):
        return (True, value.value)
    if isinstance(value, WorkflowReferenceValue):
        return resolve_reference(value.reference, context)
    if isinstance(value, WorkflowExpressionValue):
        return _resolve_expression(value.expression, context)
    raise WorkflowMappingValidationError(f"Unrecognized WorkflowValue variant: {type(value).__name__!r}.")


def _resolve_expression(expression: WorkflowExpression, context: WorkflowRuntimeContext) -> tuple[bool, Any]:
    resolved_operands: list[Any] = []
    for operand in expression.operands:
        found, operand_value = resolve_value(operand, context)
        if not found:
            return (False, None)
        resolved_operands.append(operand_value)
    return (True, evaluate_expression(expression.operator, resolved_operands))


def resolve_mapping(mapping: WorkflowMapping, context: WorkflowRuntimeContext) -> tuple[dict[str, Any], list[str]]:
    """Resolve every field in `mapping` against `context`.

    Returns `(resolved, missing_field_names)` — mirroring `TemplateBinder.BindingResult`'s own
    `resolved_values`/`unresolved_placeholders` split exactly. Never raises for an individually
    unresolvable field: whether a missing field is fatal is a decision for whichever future caller
    actually needs the resolved data (an execution integration point), not for this pure resolver.
    """
    resolved: dict[str, Any] = {}
    missing: list[str] = []
    for field_name, value in mapping.values.items():
        found, resolved_value = resolve_value(value, context)
        if found:
            resolved[field_name] = resolved_value
        else:
            missing.append(field_name)
    return resolved, missing
