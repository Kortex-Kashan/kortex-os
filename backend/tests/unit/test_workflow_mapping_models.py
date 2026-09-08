"""
Unit tests for KORTEX Workflow Mapping Domain Models
(Milestone F3 — Workflow Data Mapping & Expression Foundation).

Covers construction, the discriminated `WorkflowValue` union (exactly one of literal/reference/
expression), nested expression composition, and serialization/deserialization round-tripping.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from kortex.engines.workflow.models import (
    WorkflowExpression,
    WorkflowExpressionValue,
    WorkflowLiteralValue,
    WorkflowMapping,
    WorkflowOperator,
    WorkflowReference,
    WorkflowReferenceValue,
    WorkflowRuntimeContext,
    WorkflowValue,
)

_VALUE_ADAPTER: TypeAdapter[WorkflowValue] = TypeAdapter(WorkflowValue)


def test_workflow_reference_construction_and_defaults() -> None:
    ref = WorkflowReference(source_node_id="GetCustomer")
    assert ref.source_port is None
    assert ref.path == []


def test_workflow_reference_requires_source_node_id() -> None:
    with pytest.raises(ValidationError):
        WorkflowReference()  # type: ignore[call-arg]


def test_workflow_reference_path_supports_string_and_int_segments() -> None:
    ref = WorkflowReference(source_node_id="A", path=["body", "items", 0, "name"])
    assert ref.path == ["body", "items", 0, "name"]


def test_workflow_literal_value_kind_and_default() -> None:
    literal = WorkflowLiteralValue(value="hello@example.com")
    assert literal.kind == "literal"
    assert literal.value == "hello@example.com"

    default_literal = WorkflowLiteralValue()
    assert default_literal.value is None


def test_workflow_reference_value_kind() -> None:
    rv = WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A"))
    assert rv.kind == "reference"
    assert rv.reference.source_node_id == "A"


def test_workflow_expression_value_kind() -> None:
    ev = WorkflowExpressionValue(
        expression=WorkflowExpression(operator=WorkflowOperator.CONCAT, operands=[WorkflowLiteralValue(value="a")])
    )
    assert ev.kind == "expression"
    assert ev.expression.operator == WorkflowOperator.CONCAT


def test_workflow_value_is_a_true_discriminated_union() -> None:
    """Exactly one variant is ever representable — never zero, never two sources active
    simultaneously, by construction (a tagged union of three distinct model classes), not by a
    validator checking optional fields on one shared model."""
    literal = _VALUE_ADAPTER.validate_python({"kind": "literal", "value": 42})
    assert isinstance(literal, WorkflowLiteralValue)

    reference = _VALUE_ADAPTER.validate_python({"kind": "reference", "reference": {"source_node_id": "A"}})
    assert isinstance(reference, WorkflowReferenceValue)

    expression = _VALUE_ADAPTER.validate_python(
        {"kind": "expression", "expression": {"operator": "CONCAT", "operands": []}}
    )
    assert isinstance(expression, WorkflowExpressionValue)


def test_workflow_value_rejects_unrecognized_kind() -> None:
    with pytest.raises(ValidationError):
        _VALUE_ADAPTER.validate_python({"kind": "not-a-real-kind", "value": 1})


def test_nested_expression_composition() -> None:
    """An expression's operands may themselves be expressions, recursively."""
    inner = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.SUM, operands=[WorkflowLiteralValue(value=2), WorkflowLiteralValue(value=3)]
        )
    )
    outer = WorkflowExpression(
        operator=WorkflowOperator.CONCAT, operands=[WorkflowLiteralValue(value="total: "), inner]
    )
    assert isinstance(outer.operands[1], WorkflowExpressionValue)
    assert outer.operands[1].expression.operator == WorkflowOperator.SUM


def test_workflow_mapping_construction() -> None:
    mapping = WorkflowMapping(
        values={
            "recipient": WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A")),
            "subject": WorkflowLiteralValue(value="Hello"),
        }
    )
    assert set(mapping.values.keys()) == {"recipient", "subject"}


def test_workflow_mapping_defaults_to_empty() -> None:
    assert WorkflowMapping().values == {}


def test_workflow_runtime_context_defaults() -> None:
    assert WorkflowRuntimeContext().node_outputs == {}


def test_workflow_reference_serialization_round_trip() -> None:
    ref = WorkflowReference(source_node_id="A", source_port="result", path=["items", 0, "name"])
    restored = WorkflowReference.model_validate_json(ref.model_dump_json())
    assert restored == ref


def test_workflow_value_union_serialization_round_trip() -> None:
    for value in (
        WorkflowLiteralValue(value={"nested": [1, 2, 3]}),
        WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", path=["x"])),
        WorkflowExpressionValue(
            expression=WorkflowExpression(operator=WorkflowOperator.LENGTH, operands=[WorkflowLiteralValue(value="hi")])
        ),
    ):
        dumped = _VALUE_ADAPTER.dump_json(value)
        restored = _VALUE_ADAPTER.validate_json(dumped)
        assert restored == value
        assert type(restored) is type(value)


def test_nested_expression_serialization_round_trip_is_deterministic() -> None:
    inner = WorkflowExpressionValue(
        expression=WorkflowExpression(
            operator=WorkflowOperator.SUBTRACT, operands=[WorkflowLiteralValue(value=10), WorkflowLiteralValue(value=4)]
        )
    )
    outer = WorkflowExpressionValue(
        expression=WorkflowExpression(operator=WorkflowOperator.SUM, operands=[WorkflowLiteralValue(value=1), inner])
    )
    first = outer.model_dump_json()
    second = outer.model_dump_json()
    assert first == second
    assert WorkflowExpressionValue.model_validate_json(first) == outer


def test_workflow_mapping_serialization_round_trip() -> None:
    mapping = WorkflowMapping(
        values={
            "recipient": WorkflowReferenceValue(reference=WorkflowReference(source_node_id="A", source_port="body")),
            "subject": WorkflowLiteralValue(value="Hello"),
            "combined": WorkflowExpressionValue(
                expression=WorkflowExpression(
                    operator=WorkflowOperator.CONCAT,
                    operands=[WorkflowLiteralValue(value="a"), WorkflowLiteralValue(value="b")],
                )
            ),
        }
    )
    restored = WorkflowMapping.model_validate_json(mapping.model_dump_json())
    assert restored == mapping


# -- AI / visual-builder compatibility contract ---------------------------------------------------
#
# Neither an AI Workflow Builder nor a visual canvas is implemented anywhere in this milestone.
# These tests instead prove the *contract* future surfaces would target: a plain Python dict shaped
# exactly like what an LLM's structured-output mode (or a canvas's own serialized UI state) would
# naturally produce validates through this exact model with no special-casing — one canonical
# representation for every authoring surface (F3 architecture discovery §18/§20).


def test_ai_generated_shaped_mapping_validates_through_the_same_model() -> None:
    """A hand-built dict shaped exactly like structured LLM tool-call output — plain JSON types,
    the discriminator tag present, nothing KORTEX-specific about its construction."""
    ai_generated = {
        "values": {
            "recipient": {
                "kind": "reference",
                "reference": {"source_node_id": "get_customer", "source_port": "body", "path": ["email"]},
            },
            "subject": {"kind": "literal", "value": "Your order has shipped"},
            "greeting": {
                "kind": "expression",
                "expression": {
                    "operator": "CONCAT",
                    "operands": [
                        {"kind": "literal", "value": "Hello, "},
                        {
                            "kind": "reference",
                            "reference": {"source_node_id": "get_customer", "path": ["first_name"]},
                        },
                    ],
                },
            },
        }
    }
    mapping = WorkflowMapping.model_validate(ai_generated)
    assert set(mapping.values.keys()) == {"recipient", "subject", "greeting"}
    assert isinstance(mapping.values["subject"], WorkflowLiteralValue)
    assert isinstance(mapping.values["recipient"], WorkflowReferenceValue)
    assert isinstance(mapping.values["greeting"], WorkflowExpressionValue)


def test_visual_builder_shaped_mapping_validates_through_the_same_model() -> None:
    """A hand-built dict shaped exactly like what a canvas's drag-a-wire-between-ports interaction
    would serialize — a plain reference with an explicit port, no expression at all, which is the
    common case a canvas author would construct by pointing and clicking rather than typing."""
    canvas_generated = {
        "values": {
            "to": {
                "kind": "reference",
                "reference": {"source_node_id": "node_3", "source_port": "result", "path": []},
            },
        }
    }
    mapping = WorkflowMapping.model_validate(canvas_generated)
    assert isinstance(mapping.values["to"], WorkflowReferenceValue)
    assert mapping.values["to"].reference.source_node_id == "node_3"
    assert mapping.values["to"].reference.source_port == "result"
