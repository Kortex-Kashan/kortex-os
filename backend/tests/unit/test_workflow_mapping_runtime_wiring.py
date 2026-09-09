"""
Unit tests for F3 Data Mapping runtime wiring into `StepEvaluator.execute_step`
(AI Workflow Builder milestone, discovery report §5/§10, master prompt §6).

Covers the 8 required regression cases:
1. literal parameters still work (no `mapping` set — unchanged behavior).
2. reference parameters resolve correctly.
3. expression parameters resolve correctly.
4. references can only target valid prior/topological outputs (structural — proven at
   `mapping_validation` level; this file proves the runtime-side complement: an unresolved
   reference at execution time fails explicitly rather than silently).
5. missing source output fails explicitly (never substituted with null/empty).
6. invalid mapping cannot reach capability dispatch (a spy dispatcher proves zero invocations).
7. the resolver's runtime data surface is exactly `instance.context.step_outputs` — nothing else.
8. existing workflows with no mapping behave exactly as before (regression).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from kortex.engines.workflow.approval import MemoryApprovalManager
from kortex.engines.workflow.evaluator import StepEvaluator
from kortex.engines.workflow.models import WorkflowInstance, WorkflowStep


def _evaluator() -> StepEvaluator:
    return StepEvaluator(MemoryApprovalManager())


class _SpyDispatcher:
    """Records every capability dispatch attempt so a test can assert dispatch never happened."""

    def __init__(self, result: Any = "ok") -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self._result = result

    async def __call__(self, capability_name: str, parameters: dict[str, Any], context: dict[str, Any]) -> Any:
        self.calls.append((capability_name, dict(parameters), dict(context)))
        return self._result


@pytest.mark.asyncio
async def test_literal_parameters_unaffected_by_mapping_wiring() -> None:
    """(1) A step with `mapping=None` dispatches its literal `parameters` unchanged — the
    pre-F3-wiring behavior exactly."""
    instance = WorkflowInstance(definition_id="def_1")
    step = WorkflowStep(id="s1", name="literal", capability_name="cap.echo", parameters={"x": 10})
    dispatcher = _SpyDispatcher(result=42)

    result = await _evaluator().execute_step(instance, step, capability_dispatcher=dispatcher)

    assert result.success is True
    assert result.output == 42
    assert dispatcher.calls == [("cap.echo", {"x": 10}, {})]


@pytest.mark.asyncio
async def test_reference_mapping_resolves_prior_step_output() -> None:
    """(2) A `reference` mapping field resolves against a prior step's real output and is merged
    into the dispatched parameters."""
    instance = WorkflowInstance(definition_id="def_1")
    step1 = WorkflowStep(id="s1", name="producer", capability_name="cap.produce", parameters={})
    dispatcher = _SpyDispatcher(result={"customer_name": "Acme Co"})
    res1 = await _evaluator().execute_step(instance, step1, capability_dispatcher=dispatcher)
    assert res1.success is True
    assert instance.context.step_outputs["s1"] == {"customer_name": "Acme Co"}

    step2 = WorkflowStep(
        id="s2",
        name="consumer",
        capability_name="cap.consume",
        parameters={"literal_field": "kept"},
        mapping={
            "values": {
                "greeting_target": {
                    "kind": "reference",
                    "reference": {"source_node_id": "s1", "source_port": None, "path": ["customer_name"]},
                }
            }
        },
    )
    dispatcher2 = _SpyDispatcher(result="ok")
    res2 = await _evaluator().execute_step(instance, step2, capability_dispatcher=dispatcher2)

    assert res2.success is True
    assert dispatcher2.calls[0][1] == {"literal_field": "kept", "greeting_target": "Acme Co"}


@pytest.mark.asyncio
async def test_expression_mapping_concat_resolves() -> None:
    """(3) An `expression` mapping field (CONCAT) evaluates deterministically over resolved
    literal/reference operands."""
    instance = WorkflowInstance(definition_id="def_1")
    instance.context.step_outputs["s1"] = {"customer_name": "Acme Co"}

    step = WorkflowStep(
        id="s2",
        name="consumer",
        capability_name="cap.consume",
        parameters={},
        mapping={
            "values": {
                "body": {
                    "kind": "expression",
                    "expression": {
                        "operator": "CONCAT",
                        "operands": [
                            {"kind": "literal", "value": "Invoice for "},
                            {
                                "kind": "reference",
                                "reference": {"source_node_id": "s1", "source_port": None, "path": ["customer_name"]},
                            },
                        ],
                    },
                }
            }
        },
    )
    dispatcher = _SpyDispatcher(result="ok")
    result = await _evaluator().execute_step(instance, step, capability_dispatcher=dispatcher)

    assert result.success is True
    assert dispatcher.calls[0][1] == {"body": "Invoice for Acme Co"}


@pytest.mark.asyncio
async def test_missing_source_output_fails_explicitly_never_substituted() -> None:
    """(4)/(5) A reference to a node that has not (yet) executed fails the step explicitly —
    never silently substituted with null/empty — and the capability is never dispatched."""
    instance = WorkflowInstance(definition_id="def_1")  # "s1" never ran; no step_outputs entry
    step = WorkflowStep(
        id="s2",
        name="consumer",
        capability_name="cap.consume",
        parameters={},
        mapping={
            "values": {
                "field": {
                    "kind": "reference",
                    "reference": {"source_node_id": "s1", "source_port": None, "path": ["anything"]},
                }
            }
        },
        retry_policy={"max_attempts": 1},
    )
    dispatcher = _SpyDispatcher(result="unreachable")

    result = await _evaluator().execute_step(instance, step, capability_dispatcher=dispatcher)

    assert result.success is False
    assert "field" in result.error
    assert dispatcher.calls == []  # capability never invoked
    assert "s2" not in instance.context.step_outputs or instance.context.step_outputs.get("s2") != "unreachable"


@pytest.mark.asyncio
async def test_structurally_invalid_mapping_never_reaches_dispatch() -> None:
    """(6) A malformed `mapping` shape (fails `WorkflowMapping.model_validate`) fails the step
    before any capability dispatch attempt."""
    instance = WorkflowInstance(definition_id="def_1")
    step = WorkflowStep(
        id="s1",
        name="bad",
        capability_name="cap.consume",
        parameters={},
        mapping={"values": {"field": {"kind": "not-a-real-kind"}}},
        retry_policy={"max_attempts": 1},
    )
    dispatcher = _SpyDispatcher(result="unreachable")

    result = await _evaluator().execute_step(instance, step, capability_dispatcher=dispatcher)

    assert result.success is False
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_resolver_runtime_surface_is_exactly_step_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """(7) The mapping resolver's only external data input is `instance.context.step_outputs` --
    proven by asserting the `WorkflowRuntimeContext` constructed for resolution carries exactly
    that dict's contents, nothing ambient/extra."""
    from kortex.engines.workflow import evaluator as evaluator_module

    captured: dict[str, Any] = {}
    original = evaluator_module.resolve_mapping

    def _spy_resolve_mapping(mapping: Any, context: Any) -> Any:
        captured["node_outputs"] = dict(context.node_outputs)
        return original(mapping, context)

    monkeypatch.setattr(evaluator_module, "resolve_mapping", _spy_resolve_mapping)

    instance = WorkflowInstance(definition_id="def_1")
    instance.context.step_outputs["s1"] = {"a": 1}
    instance.context.variables["unrelated"] = "must not leak into node_outputs"
    step = WorkflowStep(
        id="s2",
        name="consumer",
        capability_name="cap.consume",
        parameters={},
        mapping={"values": {"field": {"kind": "literal", "value": "x"}}},
    )
    dispatcher = _SpyDispatcher(result="ok")

    result = await evaluator_module.StepEvaluator(MemoryApprovalManager()).execute_step(
        instance, step, capability_dispatcher=dispatcher
    )

    assert result.success is True
    assert captured["node_outputs"] == {"s1": {"a": 1}}


class _FakeInvoiceOutput(BaseModel):
    customer_name: str
    amount: float


@pytest.mark.asyncio
async def test_pydantic_model_output_normalized_to_json_safe_before_storage() -> None:
    """A capability handler returning a Pydantic `BaseModel` instance directly (as
    `kortex.finance.invoice.get` does) is normalized to a plain dict before being stored into
    `step_outputs`, so a later step's `WorkflowReference` path traversal can actually reach its
    fields (`resolve_path` requires `isinstance(current, dict)`)."""
    instance = WorkflowInstance(definition_id="def_1")
    step1 = WorkflowStep(id="s1", name="producer", capability_name="cap.get_invoice", parameters={})
    dispatcher = _SpyDispatcher(result=_FakeInvoiceOutput(customer_name="Acme Co", amount=100.0))
    res1 = await _evaluator().execute_step(instance, step1, capability_dispatcher=dispatcher)
    assert res1.success is True
    assert instance.context.step_outputs["s1"] == {"customer_name": "Acme Co", "amount": 100.0}

    step2 = WorkflowStep(
        id="s2",
        name="consumer",
        capability_name="cap.consume",
        parameters={},
        mapping={
            "values": {
                "name": {
                    "kind": "reference",
                    "reference": {"source_node_id": "s1", "source_port": None, "path": ["customer_name"]},
                }
            }
        },
    )
    dispatcher2 = _SpyDispatcher(result="ok")
    res2 = await _evaluator().execute_step(instance, step2, capability_dispatcher=dispatcher2)
    assert res2.success is True
    assert dispatcher2.calls[0][1] == {"name": "Acme Co"}


@pytest.mark.asyncio
async def test_existing_workflow_with_no_mapping_field_behaves_identically() -> None:
    """(8) A `WorkflowStep` constructed without ever setting `mapping` (the universal case for
    every workflow persisted before this milestone) executes byte-for-byte as before: `mapping`
    defaults to `None`, and the mapping-resolution branch is skipped entirely."""
    instance = WorkflowInstance(definition_id="def_1")
    step = WorkflowStep(id="s1", name="legacy", capability_name="cap.echo", parameters={"a": 1, "b": 2})
    assert step.mapping is None
    dispatcher = _SpyDispatcher(result="legacy-ok")

    result = await _evaluator().execute_step(instance, step, capability_dispatcher=dispatcher)

    assert result.success is True
    assert result.output == "legacy-ok"
    assert dispatcher.calls == [("cap.echo", {"a": 1, "b": 2}, {})]
