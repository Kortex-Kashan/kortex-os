"""Unit tests for MacroAdapter (Milestone 5).

Target: 100% pass rate, >=90% line coverage for adapters/macro_adapter.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox
from kortex.engines.document.adapters.macro_adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    MacroAdapter,
)
from kortex.engines.document.models import (
    AdapterCapability,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)


def test_metadata_completeness_and_validity() -> None:
    """MacroAdapter exposes complete, valid AdapterMetadata advertising MACROS."""
    adapter = MacroAdapter()
    meta = adapter.metadata

    assert meta.adapter_id == ADAPTER_ID
    assert meta.version == ADAPTER_VERSION
    assert meta.display_name
    assert meta.vendor
    assert meta.author
    assert meta.license
    assert meta.description
    assert AdapterCapability.MACROS in meta.supported_capabilities
    assert meta.supports_macros is True


def test_adapter_id_and_supported_capabilities_properties() -> None:
    """BaseDocumentAdapter's derived properties reflect the macro adapter's metadata."""
    adapter = MacroAdapter()

    assert adapter.adapter_id == ADAPTER_ID
    assert adapter.supports_capability(AdapterCapability.MACROS) is True
    assert adapter.supports_capability(AdapterCapability.GENERATE) is False


@pytest.mark.asyncio
async def test_execute_is_deterministic_across_repeated_calls() -> None:
    """Identical operation_type/context/options produce byte-identical output every time."""
    adapter = MacroAdapter()
    context = BindingContext(context_id="ctx-macro-1", data={"field_a": 1, "field_b": 2})

    first = await adapter.execute(DocumentOperationType.GENERATE, context, {"rule": "normalize"})
    second = await adapter.execute(DocumentOperationType.GENERATE, context, {"rule": "normalize"})

    assert first == second
    assert isinstance(first, bytes)


@pytest.mark.asyncio
async def test_execute_differs_by_context_data() -> None:
    """Different BindingContext data produces different deterministic output."""
    adapter = MacroAdapter()
    context_a = BindingContext(context_id="ctx-macro-2", data={"amount": 10})
    context_b = BindingContext(context_id="ctx-macro-2", data={"amount": 20})

    bytes_a = await adapter.execute(DocumentOperationType.GENERATE, context_a, {})
    bytes_b = await adapter.execute(DocumentOperationType.GENERATE, context_b, {})

    assert bytes_a != bytes_b


def test_validate_schema_always_accepts() -> None:
    """A technology-independent reference adapter imposes no schema constraints."""
    adapter = MacroAdapter()
    schema = TemplateSchema(
        template_id="any.schema.v1",
        name="Any Schema",
        namespace="kortex.test.any",
        version="1.0.0",
        description="Arbitrary schema for validate_schema acceptance test",
    )

    assert adapter.validate_schema(schema) is True


@pytest.mark.asyncio
async def test_registers_cleanly_into_real_registry() -> None:
    """MacroAdapter satisfies DocumentAdapterRegistry's registration contract."""
    registry = DocumentAdapterRegistry()
    adapter = MacroAdapter()

    registered = registry.register_adapter(adapter)

    assert registered is adapter
    fetched = registry.get_adapter_by_id(ADAPTER_ID)
    assert fetched is adapter
    by_capability = registry.get_adapter(AdapterCapability.MACROS)
    assert by_capability.adapter_id == ADAPTER_ID


@pytest.mark.asyncio
async def test_sandboxed_execution_is_deterministic_across_two_real_runs() -> None:
    """Two identical executions through the REAL AdapterSandbox produce byte-identical output.

    Reproduces the exact determinism stress-test previously applied to DummyDocumentAdapter
    (see the Milestone 4 P0 remediation): AdapterSandbox.run_adapter_in_sandbox() injects a
    freshly generated, randomly named temporary_workspace path into `options` on every call.
    """
    registry = DocumentAdapterRegistry()
    adapter = MacroAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-macro-determinism", data={"field": "value"})
    caller_options = {"rule": "normalize", "format": "table"}

    result_1 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.MACROS,
        context=context,
        options=dict(caller_options),
    )
    result_2 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.MACROS,
        context=context,
        options=dict(caller_options),
    )

    assert result_1.output_bytes == result_2.output_bytes
    assert result_1.workspace_path is not None
    assert result_2.workspace_path is not None
    assert result_1.workspace_path != result_2.workspace_path
    assert result_1.output_bytes is not None
    assert b"kortex_sandbox_" not in result_1.output_bytes
    assert b"temporary_workspace" not in result_1.output_bytes
    assert b"sandbox_permissions" not in result_1.output_bytes


@pytest.mark.asyncio
async def test_sandboxed_execution_deterministic_across_five_repeated_runs() -> None:
    """Five repeated real-sandbox executions with identical input all produce the same bytes."""
    registry = DocumentAdapterRegistry()
    adapter = MacroAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-macro-stress", data={"amount": 42})

    outputs = []
    for _ in range(5):
        result = await sandbox.run_adapter_in_sandbox(
            adapter=adapter,
            operation_type=DocumentOperationType.GENERATE,
            capability=AdapterCapability.MACROS,
            context=context,
            options={"rule": "normalize"},
        )
        outputs.append(result.output_bytes)

    assert len(set(outputs)) == 1
