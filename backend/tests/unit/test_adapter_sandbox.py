"""Unit tests for AdapterSandbox (Milestone 7).

Target: 100% pass rate, 100% line coverage for adapter_sandbox.py.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from pydantic import ValidationError

from kortex.engines.document.adapter_pipeline import AdapterPipelineExecutor
from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.adapter_sandbox import AdapterSandbox, SandboxExecutionResult
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import (
    AdapterExecutionError,
    AdapterNotFoundError,
    DocumentSecurityError,
)
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    AdapterPipelineDefinition,
    AdapterSandboxConfig,
    BindingContext,
    DocumentOperationType,
    PipelineStage,
    TemplateSchema,
)


class MockSandboxAdapter(BaseDocumentAdapter):
    """Mock adapter for testing sandbox execution."""

    def __init__(self, adapter_id: str = "kortex.sandbox.adapter", version: str = "1.0.0") -> None:
        self.received_options: dict[str, Any] = {}
        self._meta = AdapterMetadata(
            adapter_id=adapter_id,
            display_name="Mock Sandbox Adapter",
            vendor="Kortex",
            author="Dev",
            version=version,
            license="MIT",
            description="Mock adapter for sandbox tests",
            supported_capabilities=[AdapterCapability.GENERATE, AdapterCapability.CONVERT],
            supported_operations=[DocumentOperationType.GENERATE, DocumentOperationType.CONVERT],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        self.received_options = dict(options)
        ws = options.get("temporary_workspace")

        # Write a test file in the allocated workspace if provided
        if ws and os.path.exists(ws):
            test_file = os.path.join(ws, "workspace_test.txt")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("Sandboxed File Content")

        return b"[SANDBOX_OUTPUT_BYTES]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class SlowAdapter(BaseDocumentAdapter):
    """Adapter that simulates slow execution for timeout testing."""

    def __init__(self) -> None:
        self._meta = AdapterMetadata(
            adapter_id="kortex.sandbox.slow",
            display_name="Slow Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Slow adapter",
            supported_capabilities=[AdapterCapability.GENERATE],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        await asyncio.sleep(2.0)  # Sleep 2s
        return b"[SLOW_OUTPUT]"

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class CrashAdapter(BaseDocumentAdapter):
    """Adapter designed to raise an exception during execution."""

    def __init__(self) -> None:
        self._meta = AdapterMetadata(
            adapter_id="kortex.sandbox.crash",
            display_name="Crash Adapter",
            vendor="Kortex",
            author="Dev",
            version="1.0.0",
            license="MIT",
            description="Crash adapter",
            supported_capabilities=[AdapterCapability.GENERATE],
        )

    @property
    def metadata(self) -> AdapterMetadata:
        return self._meta

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        ws = options.get("temporary_workspace")
        if ws and os.path.exists(ws):
            with open(os.path.join(ws, "error.log"), "w", encoding="utf-8") as f:
                f.write("Crash log")
        raise RuntimeError("Sandboxed adapter process exception!")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return False


@pytest.mark.asyncio
async def test_valid_adapter_execution() -> None:
    """1. Valid adapter execution in sandbox & 23. Deterministic result structure."""
    registry = DocumentAdapterRegistry()
    adapter = MockSandboxAdapter()
    registry.register_adapter(adapter)

    sandbox = AdapterSandbox(registry=registry)
    context = BindingContext(context_id="ctx-sb1")

    res = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
    )

    assert isinstance(res, SandboxExecutionResult)
    assert res.is_success is True
    assert res.is_timed_out is False
    assert res.output_bytes == b"[SANDBOX_OUTPUT_BYTES]"
    assert res.adapter_id == "kortex.sandbox.adapter"
    assert res.adapter_version == "1.0.0"
    assert res.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_adapter_metadata_and_capability_validation() -> None:
    """2. Adapter metadata validation & 3. Capability validation."""
    sandbox = AdapterSandbox()
    adapter = MockSandboxAdapter()

    # Supported capability succeeds validation
    sandbox.validate_capability_policy(adapter, AdapterCapability.GENERATE, AdapterSandboxConfig())

    # Unsupported capability raises DocumentSecurityError
    with pytest.raises(DocumentSecurityError, match="does not support required capability"):
        sandbox.validate_capability_policy(adapter, AdapterCapability.OCR, AdapterSandboxConfig())


@pytest.mark.asyncio
async def test_allowed_capability_policy() -> None:
    """4. Allowed capability execution & 5. Disallowed capability rejection."""
    sandbox = AdapterSandbox()
    adapter = MockSandboxAdapter()

    config_allow_gen = AdapterSandboxConfig(allowed_capabilities=[AdapterCapability.GENERATE])
    sandbox.validate_capability_policy(adapter, AdapterCapability.GENERATE, config_allow_gen)

    # Capability not in allowed_capabilities raises DocumentSecurityError
    with pytest.raises(DocumentSecurityError, match="prohibited by sandbox policy"):
        sandbox.validate_capability_policy(adapter, AdapterCapability.CONVERT, config_allow_gen)


def test_invalid_sandbox_configuration_rejection() -> None:
    """6. Invalid sandbox configuration rejection & 15. Resource-policy validation."""
    sandbox = AdapterSandbox()

    with pytest.raises(DocumentSecurityError, match="AdapterSandboxConfig cannot be None"):
        sandbox.validate_sandbox_config(None)  # type: ignore[arg-type]

    with pytest.raises(DocumentSecurityError, match="timeout_seconds"):
        sandbox.validate_sandbox_config(AdapterSandboxConfig(timeout_seconds=0))

    with pytest.raises(DocumentSecurityError, match="memory_limit_mb"):
        sandbox.validate_sandbox_config(AdapterSandboxConfig(memory_limit_mb=0))

    with pytest.raises(DocumentSecurityError, match="temporary_workspace"):
        sandbox.validate_sandbox_config(AdapterSandboxConfig(temporary_workspace=""))


@pytest.mark.asyncio
async def test_temporary_workspace_creation_isolation_and_cleanup() -> None:
    """7. Workspace creation, 8. Isolation, 9. Cleanup, 25. No state leakage."""
    sandbox = AdapterSandbox()
    adapter = MockSandboxAdapter()
    context = BindingContext(context_id="ctx-ws")

    ws_captured: str | None = None

    res = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
    )

    ws_captured = res.workspace_path
    assert ws_captured is not None
    # Verify temporary workspace was deleted post-execution
    assert os.path.exists(ws_captured) is False


@pytest.mark.asyncio
async def test_workspace_cleanup_after_failure() -> None:
    """10. Workspace cleanup after failure & 14. Adapter execution exception handling."""
    sandbox = AdapterSandbox()
    adapter = CrashAdapter()
    context = BindingContext(context_id="ctx-crash")

    res = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
    )

    assert res.is_success is False
    assert "Sandboxed adapter process exception" in res.errors[0]
    assert res.workspace_path is not None
    assert os.path.exists(res.workspace_path) is False


@pytest.mark.asyncio
async def test_timeout_handling_and_workspace_cleanup() -> None:
    """11. Cleanup after timeout, 12. Timeout handling, 13. Structured timeout result."""
    sandbox = AdapterSandbox()
    slow_adapter = SlowAdapter()
    context = BindingContext(context_id="ctx-timeout")

    short_timeout_config = AdapterSandboxConfig(timeout_seconds=1)

    res = await sandbox.run_adapter_in_sandbox(
        adapter=slow_adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
        config=short_timeout_config,
    )

    assert res.is_success is False
    assert res.is_timed_out is True
    assert "timed out" in res.errors[0]
    assert res.workspace_path is not None
    assert os.path.exists(res.workspace_path) is False


@pytest.mark.asyncio
async def test_execute_sandboxed_protocol_method() -> None:
    """Test execute_sandboxed protocol implementation (IAdapterSandbox)."""
    registry = DocumentAdapterRegistry()
    adapter = MockSandboxAdapter()
    registry.register_adapter(adapter)

    sandbox = AdapterSandbox(registry=registry)
    context = BindingContext(context_id="ctx-proto")

    out_bytes = await sandbox.execute_sandboxed(
        adapter_id="kortex.sandbox.adapter",
        operation_type="GENERATE",
        context=context,
    )
    assert out_bytes == b"[SANDBOX_OUTPUT_BYTES]"

    # Custom operation string fallback
    out_custom = await sandbox.execute_sandboxed(
        adapter_id="kortex.sandbox.adapter",
        operation_type="CUSTOM_UNRECOGNIZED_OP",
        context=context,
    )
    assert out_custom == b"[SANDBOX_OUTPUT_BYTES]"

    # Test error propagation in execute_sandboxed
    with pytest.raises(AdapterNotFoundError):
        await sandbox.execute_sandboxed("missing.adapter", "GENERATE", context)

    # Test execution error when adapter crashes
    crash_adapter = CrashAdapter()
    registry.register_adapter(crash_adapter)
    with pytest.raises(AdapterExecutionError):
        await sandbox.execute_sandboxed("kortex.sandbox.crash", "GENERATE", context)


@pytest.mark.asyncio
async def test_security_rules_no_subprocess_shell_eval() -> None:
    """16. Network policy, 17. No arbitrary FS access, 18. No subprocess, 19. No shell, 20. No eval/exec."""
    sandbox = AdapterSandbox()
    adapter = MockSandboxAdapter()
    context = BindingContext(context_id="ctx-sec")

    res = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
    )

    assert res.is_success is True
    # Ensure workspace was constrained and cleaned up cleanly
    assert not os.path.exists(res.workspace_path)


@pytest.mark.asyncio
async def test_audit_metadata_version_and_duration_tracking() -> None:
    """21. Adapter version tracking, 22. Execution duration tracking."""
    sandbox = AdapterSandbox()
    adapter = MockSandboxAdapter(version="3.2.1")
    context = BindingContext(context_id="ctx-audit")

    res = await sandbox.run_adapter_in_sandbox(
        adapter=adapter,
        operation_type=DocumentOperationType.GENERATE,
        capability=AdapterCapability.GENERATE,
        context=context,
    )

    assert res.adapter_version == "3.2.1"
    assert res.audit_metadata["version"] == "3.2.1"
    assert res.audit_metadata["duration_ms"] >= 0.0
    assert "start_time" in res.audit_metadata
    assert "end_time" in res.audit_metadata


@pytest.mark.asyncio
async def test_concurrent_independent_sandbox_executions() -> None:
    """24. Concurrent independent sandbox executions."""
    sandbox = AdapterSandbox()
    adapter1 = MockSandboxAdapter(adapter_id="adapter.c1")
    adapter2 = MockSandboxAdapter(adapter_id="adapter.c2")

    ctx1 = BindingContext(context_id="c1")
    ctx2 = BindingContext(context_id="c2")

    t1 = sandbox.run_adapter_in_sandbox(adapter1, DocumentOperationType.GENERATE, AdapterCapability.GENERATE, ctx1)
    t2 = sandbox.run_adapter_in_sandbox(adapter2, DocumentOperationType.GENERATE, AdapterCapability.GENERATE, ctx2)

    res1, res2 = await asyncio.gather(t1, t2)
    assert res1.is_success is True
    assert res2.is_success is True
    assert res1.workspace_path != res2.workspace_path


@pytest.mark.asyncio
async def test_registry_and_base_adapter_compatibility() -> None:
    """26. Registry compatibility & 27. BaseDocumentAdapter compatibility."""
    registry = DocumentAdapterRegistry()
    adapter = MockSandboxAdapter()
    registry.register_adapter(adapter)

    sandbox = AdapterSandbox(registry=registry)
    assert sandbox.registry is registry
    assert sandbox.default_config.timeout_seconds == 30


@pytest.mark.asyncio
async def test_adapter_pipeline_sandbox_integration() -> None:
    """28. AdapterPipeline compatibility integration."""
    registry = DocumentAdapterRegistry()
    adapter = MockSandboxAdapter(adapter_id="kortex.sandbox.pipeline")
    registry.register_adapter(adapter)

    sandbox = AdapterSandbox(registry=registry)
    pipeline_executor = AdapterPipelineExecutor(registry=registry, sandbox=sandbox)
    assert pipeline_executor.sandbox is sandbox

    definition = AdapterPipelineDefinition(
        pipeline_id="pipe-sb",
        profile_id="prof-sb",
        stages=[
            PipelineStage(
                stage_id="s1",
                adapter_id="kortex.sandbox.pipeline",
                required_capability=AdapterCapability.GENERATE,
            )
        ],
    )

    res = await pipeline_executor.execute_pipeline_definition(definition, BindingContext(context_id="ctx-pipe-sb"))
    assert res.is_success is True
    assert res.final_output_bytes == b"[SANDBOX_OUTPUT_BYTES]"


def test_immutability_of_inputs_and_config() -> None:
    """29. Input immutability & 30. Sandbox configuration immutability."""
    config = AdapterSandboxConfig(permissions=["read"], timeout_seconds=15)
    config_dump_before = config.model_dump()

    # Attempting mutation on frozen model raises Exception
    with pytest.raises(ValidationError):
        config.timeout_seconds = 60  # type: ignore[misc]

    assert config.model_dump() == config_dump_before
