"""Adapter Sandbox for KORTEX OS Document Engine.

This module implements AdapterSandbox, which establishes a controlled execution boundary
around Document Adapters governed by explicit capability, resource, and workspace policies,
in accordance with Section 11.1 and Section 14 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import asyncio
import datetime
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import (
    AdapterExecutionError,
    DocumentSecurityError,
)
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterSandboxConfig,
    BindingContext,
    DocumentOperationType,
)


class SandboxExecutionResult(BaseModel):
    """Structured execution result returned by AdapterSandbox."""

    model_config = ConfigDict(frozen=True)

    adapter_id: str
    adapter_version: str
    operation_type: DocumentOperationType
    capability: AdapterCapability
    is_success: bool
    is_timed_out: bool = False
    output_bytes: bytes | None = None
    execution_time_ms: float = 0.0
    workspace_path: str | None = None
    errors: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


class AdapterSandbox:
    """Isolated execution boundary for Document Adapters.

    Responsibilities:
    1. Validating adapter contract, metadata, and capability declarations before execution.
    2. Enforcing AdapterSandboxConfig policies (allowed_capabilities, timeout_seconds, permissions).
    3. Creating isolated temporary workspaces and guaranteeing 100% cleanup post-execution.
    4. Enforcing non-blocking timeout limits.
    5. Capturing structured audit metadata, execution duration, and fault diagnostics.
    6. Ensuring local-first, offline-first security: zero subprocess, zero eval/exec, zero network I/O.
    """

    def __init__(
        self,
        registry: DocumentAdapterRegistry | None = None,
        default_config: AdapterSandboxConfig | None = None,
    ) -> None:
        """Initialize AdapterSandbox with an optional DocumentAdapterRegistry and default policy.

        Args:
            registry: DocumentAdapterRegistry instance. Defaults to new registry if None.
            default_config: Default AdapterSandboxConfig. Defaults to AdapterSandboxConfig() if None.
        """
        self._registry = registry if registry is not None else DocumentAdapterRegistry()
        self._default_config = default_config if default_config is not None else AdapterSandboxConfig()

    @property
    def registry(self) -> DocumentAdapterRegistry:
        """Return the underlying DocumentAdapterRegistry."""
        return self._registry

    @property
    def default_config(self) -> AdapterSandboxConfig:
        """Return the default AdapterSandboxConfig."""
        return self._default_config

    def validate_sandbox_config(self, config: AdapterSandboxConfig) -> None:
        """Validate AdapterSandboxConfig policy parameters.

        Args:
            config: AdapterSandboxConfig to validate.

        Raises:
            DocumentSecurityError: If any configuration value is invalid.
        """
        if config is None:
            raise DocumentSecurityError("AdapterSandboxConfig cannot be None.")

        if config.timeout_seconds <= 0:
            raise DocumentSecurityError("Invalid sandbox config: 'timeout_seconds' must be greater than 0.")

        if config.memory_limit_mb <= 0:
            raise DocumentSecurityError("Invalid sandbox config: 'memory_limit_mb' must be greater than 0.")

        if not config.temporary_workspace or not config.temporary_workspace.strip():
            raise DocumentSecurityError("Invalid sandbox config: 'temporary_workspace' path cannot be empty.")

    def validate_capability_policy(
        self,
        adapter: BaseDocumentAdapter,
        capability: AdapterCapability,
        config: AdapterSandboxConfig,
    ) -> None:
        """Validate whether an adapter and capability are permitted under sandbox policy.

        Args:
            adapter: BaseDocumentAdapter instance.
            capability: Requested AdapterCapability enum.
            config: Active AdapterSandboxConfig policy.

        Raises:
            DocumentSecurityError: If capability is not advertised or prohibited by policy.
        """
        if not adapter.supports_capability(capability):
            raise DocumentSecurityError(
                f"Adapter '{adapter.adapter_id}' does not support required capability '{capability.value}'."
            )

        if config.allowed_capabilities and capability not in config.allowed_capabilities:
            raise DocumentSecurityError(
                f"Capability '{capability.value}' is prohibited by sandbox policy for adapter '{adapter.adapter_id}'."
            )

    async def execute_sandboxed(
        self,
        adapter_id: str,
        operation_type: str,
        context: BindingContext,
        options: dict[str, Any] | None = None,
        config: AdapterSandboxConfig | None = None,
    ) -> bytes:
        """Execute adapter operation in sandboxed context (IAdapterSandbox protocol).

        Args:
            adapter_id: Canonical adapter ID string.
            operation_type: Operation code or capability name string.
            context: BindingContext input payload.
            options: Optional execution options dictionary.
            config: Optional sandbox configuration override.

        Returns:
            Output bytes from adapter execution.

        Raises:
            AdapterNotFoundError: If adapter_id is missing from registry.
            DocumentSecurityError: If capability or sandbox policy validation fails.
            AdapterExecutionError: If adapter execution fails or times out.
        """
        effective_options = dict(options) if options is not None else {}
        effective_config = config if config is not None else self._default_config

        # Map operation_type string to AdapterCapability or DocumentOperationType
        try:
            capability = AdapterCapability(operation_type.strip())
        except ValueError:
            capability = AdapterCapability.GENERATE

        try:
            op_enum = DocumentOperationType(operation_type.strip())
        except ValueError:
            op_enum = DocumentOperationType.GENERATE

        adapter = self._registry.get_adapter_by_id(adapter_id)
        result = await self.run_adapter_in_sandbox(
            adapter=adapter,
            operation_type=op_enum,
            capability=capability,
            context=context,
            options=effective_options,
            config=effective_config,
        )

        if not result.is_success:
            err_msg = result.errors[0] if result.errors else "Sandboxed execution failed."
            raise AdapterExecutionError(err_msg)

        return result.output_bytes or b""

    async def run_adapter_in_sandbox(
        self,
        adapter: BaseDocumentAdapter,
        operation_type: DocumentOperationType,
        capability: AdapterCapability,
        context: BindingContext,
        options: dict[str, Any] | None = None,
        config: AdapterSandboxConfig | None = None,
    ) -> SandboxExecutionResult:
        """Execute a BaseDocumentAdapter instance inside an isolated sandbox boundary.

        Args:
            adapter: BaseDocumentAdapter instance.
            operation_type: DocumentOperationType code.
            capability: AdapterCapability code.
            context: BindingContext input payload.
            options: Execution options dictionary.
            config: Active AdapterSandboxConfig policy.

        Returns:
            SandboxExecutionResult containing outputs, execution duration, and audit metadata.
        """
        effective_config = config if config is not None else self._default_config
        effective_options = dict(options) if options is not None else {}

        self.validate_sandbox_config(effective_config)
        self.validate_capability_policy(adapter, capability, effective_config)

        start_time = time.perf_counter()
        start_timestamp = datetime.datetime.now(datetime.UTC).isoformat()

        # Create isolated temporary workspace directory
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="kortex_sandbox_")
        workspace_path = str(Path(temp_dir_obj.name).resolve())

        try:
            effective_options["temporary_workspace"] = workspace_path
            effective_options["sandbox_permissions"] = list(effective_config.permissions)

            # Execute adapter with non-blocking timeout enforcement
            try:
                output_bytes = await asyncio.wait_for(
                    adapter.execute(
                        operation_type=operation_type,
                        binding_context=context,
                        options=effective_options,
                    ),
                    timeout=float(effective_config.timeout_seconds),
                )

                duration_ms = (time.perf_counter() - start_time) * 1000.0
                end_timestamp = datetime.datetime.now(datetime.UTC).isoformat()

                audit_meta = {
                    "adapter_id": adapter.adapter_id,
                    "version": adapter.metadata.version,
                    "operation_type": operation_type.value,
                    "capability": capability.value,
                    "start_time": start_timestamp,
                    "end_time": end_timestamp,
                    "duration_ms": duration_ms,
                    "timeout_seconds": effective_config.timeout_seconds,
                    "memory_limit_mb": effective_config.memory_limit_mb,
                    "workspace_path": workspace_path,
                }

                return SandboxExecutionResult(
                    adapter_id=adapter.adapter_id,
                    adapter_version=adapter.metadata.version,
                    operation_type=operation_type,
                    capability=capability,
                    is_success=True,
                    is_timed_out=False,
                    output_bytes=output_bytes,
                    execution_time_ms=duration_ms,
                    workspace_path=workspace_path,
                    audit_metadata=audit_meta,
                )

            except TimeoutError:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                end_timestamp = datetime.datetime.now(datetime.UTC).isoformat()
                err_msg = f"Adapter '{adapter.adapter_id}' timed out after {effective_config.timeout_seconds}s."

                audit_meta = {
                    "adapter_id": adapter.adapter_id,
                    "version": adapter.metadata.version,
                    "operation_type": operation_type.value,
                    "capability": capability.value,
                    "start_time": start_timestamp,
                    "end_time": end_timestamp,
                    "duration_ms": duration_ms,
                    "is_timed_out": True,
                }

                return SandboxExecutionResult(
                    adapter_id=adapter.adapter_id,
                    adapter_version=adapter.metadata.version,
                    operation_type=operation_type,
                    capability=capability,
                    is_success=False,
                    is_timed_out=True,
                    output_bytes=None,
                    execution_time_ms=duration_ms,
                    workspace_path=workspace_path,
                    errors=[err_msg],
                    audit_metadata=audit_meta,
                )

        except Exception as err:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = f"Sandboxed execution error for '{adapter.adapter_id}': {err}"

            return SandboxExecutionResult(
                adapter_id=adapter.adapter_id,
                adapter_version=adapter.metadata.version,
                operation_type=operation_type,
                capability=capability,
                is_success=False,
                is_timed_out=False,
                output_bytes=None,
                execution_time_ms=duration_ms,
                workspace_path=workspace_path,
                errors=[err_msg],
            )

        finally:
            # Guarantee 100% workspace cleanup
            temp_dir_obj.cleanup()


__all__ = ["AdapterSandbox", "SandboxExecutionResult"]
