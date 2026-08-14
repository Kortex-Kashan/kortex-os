"""Unit tests for DummyDocumentAdapter (Milestone 4).

Target: 100% pass rate, >=90% line coverage for adapters/dummy_adapter.py.
"""

from __future__ import annotations

import pytest

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox
from kortex.engines.document.adapters.dummy_adapter import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    DummyDocumentAdapter,
)
from kortex.engines.document.models import (
    AdapterCapability,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)


def test_metadata_completeness_and_validity() -> None:
    """DummyDocumentAdapter exposes complete, valid AdapterMetadata."""
    adapter = DummyDocumentAdapter()
    meta = adapter.metadata

    assert meta.adapter_id == ADAPTER_ID
    assert meta.version == ADAPTER_VERSION
    assert meta.display_name
    assert meta.vendor
    assert meta.author
    assert meta.license
    assert meta.description
    assert AdapterCapability.GENERATE in meta.supported_capabilities
    assert AdapterCapability.PREVIEW in meta.supported_capabilities
    assert meta.supports_preview is True


def test_adapter_id_and_supported_capabilities_properties() -> None:
    """BaseDocumentAdapter's derived properties reflect the dummy adapter's metadata."""
    adapter = DummyDocumentAdapter()

    assert adapter.adapter_id == ADAPTER_ID
    assert adapter.supports_capability(AdapterCapability.GENERATE) is True
    assert adapter.supports_capability(AdapterCapability.PREVIEW) is True
    assert adapter.supports_capability(AdapterCapability.OCR) is False


@pytest.mark.asyncio
async def test_execute_is_deterministic_across_repeated_calls() -> None:
    """Identical operation_type/context/options produce byte-identical output every time."""
    adapter = DummyDocumentAdapter()
    context = BindingContext(
        context_id="ctx-dummy-1",
        data={"invoice_number": "INV-001", "total_amount": 100},
    )

    first = await adapter.execute(DocumentOperationType.GENERATE, context, {"page": 1})
    second = await adapter.execute(DocumentOperationType.GENERATE, context, {"page": 1})

    assert first == second
    assert isinstance(first, bytes)


@pytest.mark.asyncio
async def test_execute_differs_by_operation_type() -> None:
    """Different operation_type values produce different deterministic output."""
    adapter = DummyDocumentAdapter()
    context = BindingContext(context_id="ctx-dummy-2", data={"a": 1})

    generate_bytes = await adapter.execute(DocumentOperationType.GENERATE, context, {})
    preview_bytes = await adapter.execute(DocumentOperationType.PREVIEW, context, {})

    assert generate_bytes != preview_bytes


@pytest.mark.asyncio
async def test_execute_differs_by_context_data() -> None:
    """Different BindingContext data produces different deterministic output."""
    adapter = DummyDocumentAdapter()
    context_a = BindingContext(context_id="ctx-dummy-3", data={"amount": 10})
    context_b = BindingContext(context_id="ctx-dummy-3", data={"amount": 20})

    bytes_a = await adapter.execute(DocumentOperationType.GENERATE, context_a, {})
    bytes_b = await adapter.execute(DocumentOperationType.GENERATE, context_b, {})

    assert bytes_a != bytes_b


@pytest.mark.asyncio
async def test_execute_supports_all_supported_operations() -> None:
    """execute() succeeds for every operation type the adapter advertises support for."""
    adapter = DummyDocumentAdapter()
    context = BindingContext(context_id="ctx-dummy-4")

    for op in adapter.metadata.supported_operations:
        output = await adapter.execute(op, context, {})
        assert isinstance(output, bytes)
        assert len(output) > 0


def test_validate_schema_always_accepts() -> None:
    """A technology-independent reference adapter imposes no schema constraints."""
    adapter = DummyDocumentAdapter()
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
    """DummyDocumentAdapter satisfies DocumentAdapterRegistry's registration contract."""
    registry = DocumentAdapterRegistry()
    adapter = DummyDocumentAdapter()

    registered = registry.register_adapter(adapter)

    assert registered is adapter
    fetched = registry.get_adapter_by_id(ADAPTER_ID)
    assert fetched is adapter
    by_capability = registry.get_adapter(AdapterCapability.GENERATE)
    assert by_capability.adapter_id == ADAPTER_ID


@pytest.mark.asyncio
async def test_executes_through_real_adapter_sandbox() -> None:
    """DummyDocumentAdapter runs successfully through the real AdapterSandbox, end-to-end."""
    registry = DocumentAdapterRegistry()
    adapter = DummyDocumentAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-dummy-sandbox", data={"field": "value"})
    result = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        options={},
    )

    assert result.output_bytes is not None
    assert len(result.output_bytes) > 0


@pytest.mark.asyncio
async def test_sandboxed_execution_is_deterministic_across_two_real_runs() -> None:
    """Two identical executions through the REAL AdapterSandbox produce byte-identical output.

    AdapterSandbox.run_adapter_in_sandbox() injects a freshly generated, randomly named
    temporary_workspace path (and a sandbox_permissions list) into `options` on every call.
    This test proves that ephemeral, sandbox-generated value does not leak into the adapter's
    output — it would fail against the pre-fix implementation, which serialized the entire
    `options` dict verbatim (including the random temporary_workspace path).
    """
    registry = DocumentAdapterRegistry()
    adapter = DummyDocumentAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-determinism-check", data={"field": "value"})
    caller_options = {"page": 1, "format": "dummy"}

    result_1 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        options=dict(caller_options),
    )
    result_2 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        options=dict(caller_options),
    )

    assert result_1.output_bytes == result_2.output_bytes

    # The randomly generated sandbox workspace path must not leak into the output at all.
    assert result_1.workspace_path is not None
    assert result_2.workspace_path is not None
    assert result_1.workspace_path != result_2.workspace_path
    assert result_1.output_bytes is not None
    assert result_1.workspace_path.encode("utf-8") not in result_1.output_bytes
    assert result_2.workspace_path.encode("utf-8") not in result_1.output_bytes
    assert b"kortex_sandbox_" not in result_1.output_bytes
    assert b"temporary_workspace" not in result_1.output_bytes
    assert b"sandbox_permissions" not in result_1.output_bytes


@pytest.mark.asyncio
async def test_sandboxed_execution_deterministic_across_five_repeated_runs() -> None:
    """Five repeated real-sandbox executions with identical input all produce the same bytes."""
    registry = DocumentAdapterRegistry()
    adapter = DummyDocumentAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-determinism-stress", data={"amount": 42})

    outputs = []
    for _ in range(5):
        result = await sandbox.run_adapter_in_sandbox(
            adapter=adapter,
            operation_type=DocumentOperationType.PREVIEW,
            capability=AdapterCapability.PREVIEW,
            context=context,
            options={"page_number": 1},
        )
        outputs.append(result.output_bytes)

    assert len(set(outputs)) == 1


@pytest.mark.asyncio
async def test_sandboxed_execution_still_reflects_semantic_option_changes() -> None:
    """A genuine change to a caller-provided semantic option still changes the output.

    This proves the fix filters only sandbox-ephemeral keys, not all options — semantic
    caller input must still deterministically influence the result.
    """
    registry = DocumentAdapterRegistry()
    adapter = DummyDocumentAdapter()
    registry.register_adapter(adapter)
    sandbox = AdapterSandbox(registry=registry)

    context = BindingContext(context_id="ctx-semantic-options", data={"field": "value"})

    result_page_1 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        options={"page": 1, "format": "dummy"},
    )
    result_page_2 = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        options={"page": 2, "format": "dummy"},
    )

    assert result_page_1.output_bytes != result_page_2.output_bytes
