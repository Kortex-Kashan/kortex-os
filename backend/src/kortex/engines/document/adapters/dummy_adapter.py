"""Dummy reference Document Adapter for the KORTEX OS Document Engine.

This module implements DummyDocumentAdapter, the technology-independent reference
adapter deliverable of Milestone 4 (Document Adapter Architecture). It exists so that
a freshly booted DocumentEngine always has at least one usable, deterministic adapter
available for GENERATE/PREVIEW operations, and so DocumentAdapterLoader has a concrete
BaseDocumentAdapter subclass to discover.
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

ADAPTER_ID = "kortex.document.dummy.v1"
ADAPTER_VERSION = "1.0.0"

# Keys AdapterSandbox injects into `options` per execution (e.g. a freshly generated
# temporary workspace path, config-derived permission list). These are sandbox-internal
# execution metadata, not semantic caller input, and must never affect adapter output.
_SANDBOX_EPHEMERAL_OPTION_KEYS = frozenset({"temporary_workspace", "sandbox_permissions"})


class DummyDocumentAdapter(BaseDocumentAdapter):
    """Deterministic, technology-independent reference adapter.

    Guarantees:
    1. Determinism: identical operation_type/binding_context/options produce identical
       output bytes. No timestamps, randomness, network, filesystem I/O, subprocess,
       eval(), exec(), or LLM/API dependency.
    2. Compatibility: validate_schema() always accepts, since this adapter renders no
       real technology-specific output and imposes no schema constraints of its own.
    """

    def __init__(self) -> None:
        self._metadata = AdapterMetadata(
            adapter_id=ADAPTER_ID,
            display_name="Dummy Reference Adapter",
            vendor="KORTEX OS",
            author="KORTEX Core Team",
            version=ADAPTER_VERSION,
            license="MIT",
            description=(
                "Deterministic, technology-independent reference adapter used for testing "
                "and to ensure the Document Engine always has a usable adapter out of the box."
            ),
            supported_capabilities=[AdapterCapability.GENERATE, AdapterCapability.PREVIEW],
            supported_operations=[
                DocumentOperationType.GENERATE,
                DocumentOperationType.PREVIEW,
            ],
            supports_preview=True,
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
            Deterministic UTF-8 encoded JSON bytes describing the mock operation.
        """
        op_value = (
            operation_type.value if isinstance(operation_type, DocumentOperationType) else str(operation_type)
        )
        semantic_options = {
            key: value
            for key, value in options.items()
            if key not in _SANDBOX_EPHEMERAL_OPTION_KEYS
        }
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


__all__ = ["ADAPTER_ID", "ADAPTER_VERSION", "DummyDocumentAdapter"]
