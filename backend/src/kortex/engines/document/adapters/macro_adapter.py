"""Reference Macro Adapter for the KORTEX OS Document Engine.

This module implements MacroAdapter, the Milestone 5 reference adapter proving that a
MACROS-capability pipeline stage flows correctly through the existing, unmodified
DocumentAdapterRegistry / AdapterSandbox / AdapterPipelineExecutor machinery, in
accordance with Section 5.3 (Macro Adapter Architecture) of the Document Engine
Implementation Specification (Version 3.0.0).

This is deliberately NOT a macro scripting/expression engine. Per Section 3 ("Core Macro
Execution" is explicitly out of scope for the engine core) and this milestone's own
authorization, MacroAdapter performs no rule evaluation, no expression parsing, and no
dynamic code execution of any kind — it is a pure, deterministic reference/stub, exactly
mirroring DummyDocumentAdapter's role for GENERATE/PREVIEW. Real macro rule-application
logic, if ever required, would live in a future, separately-authorized adapter.
"""

from __future__ import annotations

import json
from typing import Any

from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)

ADAPTER_ID = "kortex.document.macro.v1"
ADAPTER_VERSION = "1.0.0"

# Keys AdapterSandbox injects into `options` per execution (e.g. a freshly generated
# temporary workspace path, config-derived permission list). These are sandbox-internal
# execution metadata, not semantic caller input, and must never affect adapter output
# (see the identical, previously-fixed determinism defect in DummyDocumentAdapter).
_SANDBOX_EPHEMERAL_OPTION_KEYS = frozenset({"temporary_workspace", "sandbox_permissions"})


class MacroAdapter(BaseDocumentAdapter):
    """Deterministic, technology-independent reference Macro Adapter.

    Guarantees (identical bar to DummyDocumentAdapter):
    1. Determinism: identical operation_type/binding_context/options produce identical
       output bytes. No timestamps, randomness, network, filesystem I/O, subprocess,
       eval(), exec(), or LLM/API dependency.
    2. No macro scripting/expression engine of any kind — this adapter exists solely to
       prove the MACROS capability integrates correctly with the pipeline/sandbox, not to
       execute real macro rules.
    3. Compatibility: validate_schema() always accepts, since this adapter renders no
       real technology-specific output and imposes no schema constraints of its own.
    """

    def __init__(self) -> None:
        self._metadata = AdapterMetadata(
            adapter_id=ADAPTER_ID,
            display_name="Reference Macro Adapter",
            vendor="KORTEX OS",
            author="KORTEX Core Team",
            version=ADAPTER_VERSION,
            license="MIT",
            description=(
                "Deterministic, technology-independent reference Macro Adapter used to prove "
                "MACROS-capability pipeline stage integration. Performs no real macro rule "
                "execution — no scripting, no expression evaluation, no dynamic code execution."
            ),
            supported_capabilities=[AdapterCapability.MACROS],
            supported_operations=[DocumentOperationType.GENERATE],
            supports_macros=True,
        )

    @property
    def metadata(self) -> AdapterMetadata:
        """Return this adapter's immutable metadata."""
        return self._metadata

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        """Deterministically derive output bytes from the operation type and binding context.

        Args:
            operation_type: Document operation code being mock-executed.
            binding_context: Bound context data (only its declared, serializable fields
                              are used — no external state is consulted).
            options: Per-call options dict. Sandbox-injected ephemeral execution metadata
                     (e.g. a freshly generated temporary workspace path) is excluded from
                     the output; only semantic caller-provided options are used.

        Returns:
            Deterministic UTF-8 encoded JSON bytes describing the mock macro operation.
        """
        op_value = operation_type.value if isinstance(operation_type, DocumentOperationType) else str(operation_type)
        semantic_options = {key: value for key, value in options.items() if key not in _SANDBOX_EPHEMERAL_OPTION_KEYS}
        payload = {
            "adapter_id": self.adapter_id,
            "operation_type": op_value,
            "context_id": binding_context.context_id,
            "data": binding_context.data,
            "computed_fields": binding_context.computed_fields,
            "options": semantic_options,
        }
        return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        """Always accept, since this reference adapter renders no real technology output."""
        return True


__all__ = ["ADAPTER_ID", "ADAPTER_VERSION", "MacroAdapter"]
