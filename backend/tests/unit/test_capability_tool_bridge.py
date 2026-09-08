"""
KORTEX OS — Milestone F5 Test Suite
Capability -> AI ToolDefinition Bridge: the generic `CapabilityDescriptor` -> `ToolDefinition`
converter (`kortex.api.capability_tool_bridge`).
"""

from __future__ import annotations

import pytest

from kortex.api.capability_tool_bridge import (
    CapabilityToolBridgeError,
    generate_tool_definition_from_capability,
)
from kortex.engines.ai.tools import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolDefinition
from kortex.engines.connector.reference_actions import WEBHOOK_SEND_ACTION, WEBHOOK_STATUS_ACTION
from kortex.engines.registry.engine import CapabilityDescriptor

_REAL_SCHEMA = {
    "type": "object",
    "properties": {"profile_id": {"type": "string"}, "url": {"type": "string"}},
    "required": ["profile_id", "url"],
}


def _make_descriptor(**overrides: object) -> CapabilityDescriptor:
    fields: dict[str, object] = {
        "name": "kortex.connector.notification.webhook.status",
        "description": "Check webhook delivery status.",
        "provider": "connector",
        "parameters_schema": _REAL_SCHEMA,
        "is_read_only": True,
    }
    fields.update(overrides)
    return CapabilityDescriptor(**fields)  # type: ignore[arg-type]


def test_generates_deterministic_dot_free_tool_name() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor())
    assert tool.name == "kortex_connector_notification_webhook_status"
    assert "." not in tool.name


def test_reuses_parameters_schema_verbatim() -> None:
    # Pydantic validation copies the dict on assignment, so object identity isn't preserved --
    # content equality is what "reused verbatim, never re-typed" actually guarantees here.
    descriptor = _make_descriptor()
    tool = generate_tool_definition_from_capability(descriptor)
    assert tool.parameters_schema == descriptor.parameters_schema


def test_preserves_description_verbatim() -> None:
    descriptor = _make_descriptor(description="A very specific description.")
    tool = generate_tool_definition_from_capability(descriptor)
    assert tool.description == "A very specific description."


def test_canonical_capability_relationship_is_explicit() -> None:
    descriptor = _make_descriptor()
    tool = generate_tool_definition_from_capability(descriptor)
    assert tool.canonical_capability == descriptor.name


def test_read_only_capability_becomes_non_mutation_tool() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor(is_read_only=True))
    assert tool.is_mutation is False


def test_non_read_only_capability_becomes_mutation_tool() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor(is_read_only=False))
    assert tool.is_mutation is True


def test_mutation_classification_never_derived_from_name() -> None:
    """A capability named to *sound* safe ('...get...') but explicitly marked mutating must still
    produce a mutation tool -- and vice versa -- proving the derivation reads only `is_read_only`,
    never the name string."""
    misleading_read_name = _make_descriptor(name="kortex.connector.notification.webhook.get_all", is_read_only=False)
    assert generate_tool_definition_from_capability(misleading_read_name).is_mutation is True

    misleading_write_name = _make_descriptor(name="kortex.connector.notification.webhook.delete", is_read_only=True)
    assert generate_tool_definition_from_capability(misleading_write_name).is_mutation is False


def test_default_timeout_matches_tool_default() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor())
    assert tool.timeout_seconds == DEFAULT_TOOL_TIMEOUT_SECONDS


def test_custom_timeout_is_honored() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor(), timeout_seconds=5.0)
    assert tool.timeout_seconds == 5.0


def test_empty_parameters_schema_raises_rather_than_fabricating() -> None:
    """D10: the generator must fail clearly for a capability with no real schema information --
    matching the ~140 pre-F5 production capabilities that never populated `parameters_schema`
    (F1/F3 discovery) -- never emit a `ToolDefinition` with an empty, meaningless schema."""
    with pytest.raises(CapabilityToolBridgeError, match="no declared parameters_schema"):
        generate_tool_definition_from_capability(_make_descriptor(parameters_schema={}))


def test_generated_tool_is_a_real_valid_tool_definition() -> None:
    tool = generate_tool_definition_from_capability(_make_descriptor())
    assert isinstance(tool, ToolDefinition)
    # Round-trips through ToolDefinition's own validation (name pattern, required fields) cleanly.
    ToolDefinition.model_validate(tool.model_dump())


@pytest.mark.parametrize("action_descriptor", [WEBHOOK_STATUS_ACTION, WEBHOOK_SEND_ACTION])
def test_generation_from_the_real_reference_action_descriptors_shape(action_descriptor: object) -> None:
    """Confirms the generator works against the actual shape F5's reference integration produces
    (full parameters_schema including the prepended profile_id property), not just a hand-built
    test descriptor."""
    from kortex.engines.connector.actions import _build_full_parameters_schema

    descriptor = CapabilityDescriptor(
        name=action_descriptor.capability_name,  # type: ignore[attr-defined]
        description=action_descriptor.description,  # type: ignore[attr-defined]
        provider="connector",
        parameters_schema=_build_full_parameters_schema(action_descriptor),  # type: ignore[arg-type]
        returns_schema=action_descriptor.returns_schema,  # type: ignore[attr-defined]
        is_read_only=action_descriptor.is_read_only,  # type: ignore[attr-defined]
        is_idempotent=action_descriptor.is_idempotent,  # type: ignore[attr-defined]
    )
    tool = generate_tool_definition_from_capability(descriptor)
    assert tool.parameters_schema["required"][0] == "profile_id"
    assert tool.canonical_capability == action_descriptor.capability_name  # type: ignore[attr-defined]
