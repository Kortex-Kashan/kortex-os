"""
KORTEX Capability -> AI Tool Definition Bridge (Milestone F5).

The canonical, generic conversion from a Kernel `CapabilityDescriptor` (the Capability Registry's
own authoritative record) to a `ToolDefinition` (the AI Engine's existing tool-schema contract,
`kortex.engines.ai.tools`). Before this module, every AI tool in the codebase was hand-authored
independently in `kernel_bootstrap.py`, with its schema re-typed by hand to mirror whatever a
target capability's handler happened to expect -- this module replaces that hand-authoring step
for any capability that already declares a real, non-empty `parameters_schema`, without touching
`ToolRegistry`, `ToolDefinition`, or the tool-invocation pipeline in any way.

Deliberately lives in `kortex.api` (the composition-root layer `kernel_bootstrap.py` itself
belongs to), not inside `kortex.engines.ai`: that package's own architecture guard
(`test_ai_model_router.py::test_ai_package_imports_no_forbidden_dependency`) enforces an allow-list
of imports for every file directly under `kortex/engines/ai/`, and `kortex.engines.registry` --
where `CapabilityDescriptor` lives -- is not on it. `kortex.api` carries no such restriction and is
already the one place that freely imports across engine boundaries (Security, Connector, AI, etc.)
to assemble the running Kernel, so this bridge belongs here architecturally, not as a workaround.

Scope discipline (Milestone F5 firewall):
- `CapabilityDescriptor` is the only source of truth this reads. `parameters_schema` is reused
  verbatim (the exact same JSON-Schema-shaped `dict` representation both models already share) --
  never re-typed, never re-derived, never duplicated into a second schema language.
- Produces a `ToolDefinition` only; never registers it. Registration into a `ToolRegistry` remains
  the caller's job (mirroring `kernel_bootstrap.py`'s existing `_register_tool_if_absent` pattern),
  keeping this module a pure, side-effect-free converter.
- Never fabricates a schema: a capability with no real `parameters_schema` (the common case for the
  ~140 pre-F5 capabilities that never populated this field) raises `CapabilityToolBridgeError`
  rather than emitting a `ToolDefinition` with an empty, meaningless schema.
- `is_mutation` is derived exclusively from the capability's own existing `is_read_only` field
  (`is_mutation = not is_read_only`) -- never inferred from the capability's name, matching the
  F1-established discipline that behavioral risk metadata lives on the descriptor, not in naming
  conventions.
- This module does not fix, and is not gated on, the separate, pre-existing gap where real LLM
  provider requests do not yet populate/send `tools` at all (see F5 discovery §10) -- a
  `ToolDefinition` produced here is fully valid and immediately usable by direct
  `AIToolInvoker`/`ToolRegistry` consumers (and any future AI Workflow Builder / node-authoring
  surface); it simply cannot yet be exercised by a live, non-scripted LLM tool call, for reasons
  entirely outside this bridge's or F5's scope.
"""

from __future__ import annotations

from kortex.engines.ai.tools import DEFAULT_TOOL_TIMEOUT_SECONDS, ToolDefinition
from kortex.engines.registry.engine import CapabilityDescriptor


class CapabilityToolBridgeError(Exception):
    """Raised when a `CapabilityDescriptor` cannot be safely converted into a `ToolDefinition`."""


def _tool_name_for_capability(capability_name: str) -> str:
    """Derive a deterministic, `ToolDefinition`-legal tool name from a capability name.

    `ToolDefinition.name` must match `^[a-zA-Z0-9_-]+$` (no dots), while every canonical Kernel
    capability name is dot-separated (`kortex.connector.notification.webhook.send`) -- dots are
    replaced with underscores, a lossless, deterministic, collision-free mapping given KORTEX's own
    capability-naming convention never uses underscores adjacent to dots.
    """
    return capability_name.replace(".", "_")


def generate_tool_definition_from_capability(
    descriptor: CapabilityDescriptor, *, timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> ToolDefinition:
    """Convert one `CapabilityDescriptor` into a `ToolDefinition`, reusing its `parameters_schema`
    and `is_read_only` classification verbatim.

    Raises:
        CapabilityToolBridgeError: `descriptor.parameters_schema` is empty/falsy -- there is
            nothing to safely generate a tool schema from, and this function never fabricates one.
    """
    if not descriptor.parameters_schema:
        raise CapabilityToolBridgeError(
            f"Capability '{descriptor.name}' has no declared parameters_schema; refusing to "
            f"generate a ToolDefinition with a fabricated or empty schema."
        )

    return ToolDefinition(
        name=_tool_name_for_capability(descriptor.name),
        description=descriptor.description,
        parameters_schema=descriptor.parameters_schema,
        canonical_capability=descriptor.name,
        is_mutation=not descriptor.is_read_only,
        timeout_seconds=timeout_seconds,
    )
