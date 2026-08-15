"""Multi-Stage Adapter Pipeline Orchestrator for KORTEX OS Document Engine.

This module implements AdapterPipelineExecutor, which is responsible for validating and
executing multi-stage Document Adapter Pipelines in a deterministic, sandboxed, and controlled sequence,
in accordance with Section 10 and Section 14 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.document.adapter_registry import DocumentAdapterRegistry
from kortex.engines.document.exceptions import (
    AdapterNotFoundError,
    DocumentOperationError,
)
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterPipelineDefinition,
    BindingContext,
    DocumentOperationType,
    OperationRequest,
    OperationResult,
    PipelineExecutionMode,
    PipelineStage,
)

if TYPE_CHECKING:
    from kortex.engines.document.operation_profile import DocumentOperationProfileManager
    from kortex.engines.document.recovery import DocumentRecoveryManager


class StageExecutionResult(BaseModel):
    """Execution result for an individual pipeline stage."""

    model_config = ConfigDict(frozen=True)

    stage_id: str
    adapter_id: str
    capability: AdapterCapability
    is_success: bool
    is_skipped: bool = False
    output_bytes: bytes | None = None
    execution_time_ms: float = 0.0
    error_message: str | None = None


class PipelineExecutionResult(BaseModel):
    """Aggregate execution result for a complete Adapter Pipeline."""

    model_config = ConfigDict(frozen=True)

    pipeline_id: str
    profile_id: str
    is_success: bool
    final_output_bytes: bytes | None = None
    total_execution_time_ms: float = 0.0
    stage_results: list[StageExecutionResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def evaluate_declarative_condition(condition: str | None, context: BindingContext) -> bool:
    """Evaluate a declarative condition string against BindingContext without code execution.

    Supports simple boolean strings ('true'/'false'), key lookups ('key'),
    negations ('!key'), and equality comparisons ('key == value' or 'key != value').

    Args:
        condition: Declarative condition string or None.
        context: BindingContext containing data and computed_fields.

    Returns:
        True if condition is satisfied or None; False otherwise.
    """
    if not condition or not condition.strip():
        return True

    cond = condition.strip()

    if cond.lower() in ("true", "1", "enabled"):
        return True
    if cond.lower() in ("false", "0", "disabled"):
        return False

    # Equality operator
    if "==" in cond:
        key, expected = cond.split("==", 1)
        key_str = key.strip()
        expected_str = expected.strip().strip("'\"").lower()
        actual_val = _resolve_context_value(key_str, context)
        if actual_val is None:
            return False
        return str(actual_val).lower() == expected_str

    # Inequality operator
    if "!=" in cond:
        key, expected = cond.split("!=", 1)
        key_str = key.strip()
        expected_str = expected.strip().strip("'\"").lower()
        actual_val = _resolve_context_value(key_str, context)
        if actual_val is None:
            return True
        return str(actual_val).lower() != expected_str

    # Negation
    if cond.startswith("!"):
        key_str = cond[1:].strip()
        val = _resolve_context_value(key_str, context)
        return not bool(val)

    # Key lookup
    val = _resolve_context_value(cond, context)
    return bool(val)


def _resolve_context_value(key: str, context: BindingContext) -> Any:
    """Helper to look up a key in computed_fields, data, or metadata."""
    if context.computed_fields and key in context.computed_fields:
        return context.computed_fields[key]
    if context.data and key in context.data:
        return context.data[key]
    if context.metadata and key in context.metadata:
        return context.metadata[key]
    return None


class AdapterPipelineExecutor:
    """Orchestrator for executing multi-stage Document Adapter Pipelines.

    Guarantees:
    1. Determinism: Stages execute in exact declared sequence.
    2. Immutability: Input definitions, requests, and contexts are never mutated.
    3. Safety: Declarative stage condition evaluation; zero eval/exec or arbitrary code execution.
    4. Fault Isolation: Stage errors in optional stages are isolated; errors in required stages safely stop execution.
    5. Recovery: Automatic checkpointing, structured failure recording, exponential backoff retries, and rollback.
    """

    def __init__(
        self,
        registry: DocumentAdapterRegistry | None = None,
        sandbox: Any | None = None,
        profile_manager: "DocumentOperationProfileManager | None" = None,
        recovery_manager: "DocumentRecoveryManager | None" = None,
    ) -> None:
        """Initialize AdapterPipelineExecutor with an optional DocumentAdapterRegistry and sandbox.

        Args:
            registry: DocumentAdapterRegistry instance. Defaults to new registry if None.
            sandbox: Optional IAdapterSandbox or AdapterSandbox instance.
            profile_manager: Optional DocumentOperationProfileManager used by execute_pipeline()
                              to resolve a profile_id to its real, registered adapter_pipeline.
                              When None, execute_pipeline() preserves its legacy single-stage
                              shim behavior (treating profile_id as an adapter_id).
            recovery_manager: Optional DocumentRecoveryManager instance used for checkpointing,
                              failure telemetry, retry backoff, and rollback on pipeline failure.
        """
        self._registry = registry if registry is not None else DocumentAdapterRegistry()
        self._sandbox = sandbox
        self._profile_manager = profile_manager
        self._recovery_manager = recovery_manager

    @property
    def registry(self) -> DocumentAdapterRegistry:
        """Return the underlying DocumentAdapterRegistry."""
        return self._registry

    @property
    def sandbox(self) -> Any | None:
        """Return the underlying sandbox if configured."""
        return self._sandbox

    @property
    def profile_manager(self) -> "DocumentOperationProfileManager | None":
        """Return the configured DocumentOperationProfileManager, or None if unset."""
        return self._profile_manager

    @property
    def recovery_manager(self) -> "DocumentRecoveryManager | None":
        """Return the configured DocumentRecoveryManager, or None if unset."""
        return self._recovery_manager

    def validate_pipeline_definition(self, definition: AdapterPipelineDefinition) -> None:
        """Validate an AdapterPipelineDefinition before execution.

        Args:
            definition: Pipeline definition object.

        Raises:
            DocumentOperationError: If pipeline ID is empty, stages are empty, or duplicate stage IDs exist.
            AdapterNotFoundError: If a stage references an unregistered adapter.
        """
        if definition is None:
            raise DocumentOperationError("Pipeline definition cannot be None.")

        if not definition.pipeline_id or not definition.pipeline_id.strip():
            raise DocumentOperationError("Missing pipeline_id in pipeline definition.")

        if not definition.stages:
            raise DocumentOperationError(
                f"Pipeline definition '{definition.pipeline_id}' contains no execution stages."
            )

        seen_stages: set[str] = set()

        for stage in definition.stages:
            if not stage.stage_id or not stage.stage_id.strip():
                raise DocumentOperationError("Pipeline stage missing stage_id.")

            stage_id = stage.stage_id.strip()
            if stage_id in seen_stages:
                raise DocumentOperationError(
                    f"Duplicate stage ID '{stage_id}' in pipeline '{definition.pipeline_id}'."
                )
            seen_stages.add(stage_id)

            if not stage.adapter_id or not stage.adapter_id.strip():
                raise DocumentOperationError(
                    f"Stage '{stage_id}' in pipeline '{definition.pipeline_id}' missing adapter_id."
                )

            # Verify adapter exists in registry
            adapter = self._registry.get_adapter_by_id(stage.adapter_id)

            # Verify adapter advertises required capability
            if not adapter.supports_capability(stage.required_capability):
                raise DocumentOperationError(
                    f"Adapter '{stage.adapter_id}' in stage '{stage_id}' does not support required capability '{stage.required_capability.value}'."
                )

    async def execute_pipeline_definition(
        self,
        definition: AdapterPipelineDefinition,
        context: BindingContext,
        initial_input: bytes | None = None,
        request_id: str | None = None,
    ) -> PipelineExecutionResult:
        """Execute a validated AdapterPipelineDefinition against context data.

        Args:
            definition: AdapterPipelineDefinition object.
            context: BindingContext data payload.
            initial_input: Optional initial binary payload.
            request_id: Optional operation request ID for recovery telemetry and checkpointing.

        Returns:
            PipelineExecutionResult containing stage results and aggregate outcome.
        """
        self.validate_pipeline_definition(definition)

        req_id = request_id or context.context_id or definition.pipeline_id
        start_time = time.perf_counter()
        stage_results: list[StageExecutionResult] = []
        errors: list[str] = []
        current_payload: bytes = initial_input if initial_input is not None else b""
        pipeline_success = True

        for stage in definition.stages:
            stage_start = time.perf_counter()

            # Evaluate declarative execution condition
            if stage.execution_condition and not evaluate_declarative_condition(
                stage.execution_condition, context
            ):
                stage_time_ms = (time.perf_counter() - stage_start) * 1000.0
                stage_results.append(
                    StageExecutionResult(
                        stage_id=stage.stage_id,
                        adapter_id=stage.adapter_id,
                        capability=stage.required_capability,
                        is_success=True,
                        is_skipped=True,
                        output_bytes=current_payload,
                        execution_time_ms=stage_time_ms,
                    )
                )
                continue

            max_retries = 3
            backoff_factor = 1.5
            stage_attempt = 0
            stage_succeeded = False

            while not stage_succeeded:
                stage_attempt += 1
                attempt_start = time.perf_counter()
                try:
                    adapter = self._registry.get_adapter_by_id(stage.adapter_id)
                    options = dict(stage.stage_options)
                    options["input_bytes"] = current_payload

                    # Map capability to operation type
                    op_type = (
                        DocumentOperationType(stage.required_capability.value)
                        if stage.required_capability.value in DocumentOperationType.__members__
                        else DocumentOperationType.GENERATE
                    )

                    if self._sandbox is not None:
                        output_bytes = await self._sandbox.execute_sandboxed(
                            adapter_id=stage.adapter_id,
                            operation_type=stage.required_capability.value,
                            context=context,
                            options=options,
                        )
                    else:
                        output_bytes = await adapter.execute(
                            operation_type=op_type,
                            binding_context=context,
                            options=options,
                        )

                    if output_bytes is not None:
                        current_payload = output_bytes

                    stage_time_ms = (time.perf_counter() - attempt_start) * 1000.0
                    stage_results.append(
                        StageExecutionResult(
                            stage_id=stage.stage_id,
                            adapter_id=stage.adapter_id,
                            capability=stage.required_capability,
                            is_success=True,
                            is_skipped=False,
                            output_bytes=current_payload,
                            execution_time_ms=stage_time_ms,
                        )
                    )
                    stage_succeeded = True

                    # Record successful stage checkpoint
                    if self._recovery_manager is not None:
                        await self._recovery_manager.checkpoint(
                            request_id=req_id,
                            stage_id=stage.stage_id,
                            state_data=current_payload,
                        )

                except Exception as err:
                    stage_time_ms = (time.perf_counter() - attempt_start) * 1000.0
                    err_msg = f"Stage '{stage.stage_id}' execution failed: {err}"

                    # Record failure metadata in recovery manager
                    if self._recovery_manager is not None:
                        await self._recovery_manager.record_failure(
                            request_id=req_id,
                            stage_id=stage.stage_id,
                            adapter_id=stage.adapter_id,
                            error_code=type(err).__name__,
                            stack_trace_snippet=str(err),
                        )

                    # Determine retry eligibility. Recovery is opt-in: without a configured
                    # recovery_manager, a required-stage failure must behave exactly as it
                    # did pre-Milestone-6 — exactly one attempt, no retry, no backoff, no
                    # checkpoint, no failure telemetry, no rollback.
                    can_retry = False
                    if not stage.is_optional and self._recovery_manager is not None:
                        can_retry = await self._recovery_manager.retry_stage(
                            request_id=req_id,
                            stage_id=stage.stage_id,
                            max_retries=max_retries,
                            backoff_factor=backoff_factor,
                        )

                    if can_retry:
                        backoff_delay = 0.001
                        if self._recovery_manager is not None and hasattr(
                            self._recovery_manager, "calculate_backoff"
                        ):
                            backoff_delay = self._recovery_manager.calculate_backoff(
                                attempt=stage_attempt,
                                backoff_factor=backoff_factor,
                            )
                        await asyncio.sleep(backoff_delay)
                        continue

                    # Retries exhausted or non-retryable failure
                    stage_results.append(
                        StageExecutionResult(
                            stage_id=stage.stage_id,
                            adapter_id=stage.adapter_id,
                            capability=stage.required_capability,
                            is_success=False,
                            is_skipped=False,
                            output_bytes=None,
                            execution_time_ms=stage_time_ms,
                            error_message=err_msg,
                        )
                    )

                    if stage.is_optional:
                        errors.append(f"[Optional Stage Warning] {err_msg}")
                        break
                    else:
                        errors.append(err_msg)
                        pipeline_success = False
                        if self._recovery_manager is not None:
                            await self._recovery_manager.rollback(request_id=req_id)
                        break

            if not pipeline_success:
                break

        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        return PipelineExecutionResult(
            pipeline_id=definition.pipeline_id,
            profile_id=definition.profile_id,
            is_success=pipeline_success,
            final_output_bytes=current_payload if pipeline_success else None,
            total_execution_time_ms=total_time_ms,
            stage_results=stage_results,
            errors=errors,
        )

    async def execute_pipeline(
        self, profile_id: str, request: OperationRequest
    ) -> OperationResult:
        """Facade method executing pipeline for a request (IAdapterPipelineExecutor protocol).

        When a DocumentOperationProfileManager is configured, resolves the real registered
        profile for profile_id and executes its actual adapter_pipeline. When no profile
        manager is configured (or the profile cannot be resolved, or has no adapter_pipeline),
        falls back to the legacy single-stage behavior of treating profile_id as an adapter_id
        directly — preserved for backward compatibility with existing callers.

        Args:
            profile_id: Document Operation Profile identifier.
            request: OperationRequest payload.

        Returns:
            OperationResult payload.
        """
        start_time = time.perf_counter()

        if request is None or not request.request_id:
            raise DocumentOperationError("Invalid OperationRequest: request_id missing.")

        context = request.binding_context or BindingContext(context_id=f"ctx-{request.request_id}")

        pipeline_def: AdapterPipelineDefinition | None = None
        if self._profile_manager is not None:
            try:
                profile = await self._profile_manager.get_profile(
                    profile_id, tenant_id=context.tenant_id
                )
                pipeline_def = profile.adapter_pipeline
            except Exception:
                pipeline_def = None

        if pipeline_def is None:
            # Legacy single-stage fallback: treat profile_id as an adapter_id directly.
            try:
                adapter = self._registry.get_adapter_by_id(profile_id)
                cap = (
                    adapter.supported_capabilities[0]
                    if adapter.supported_capabilities
                    else AdapterCapability.GENERATE
                )
                pipeline_def = AdapterPipelineDefinition(
                    pipeline_id=f"pipeline-{profile_id}",
                    profile_id=profile_id,
                    stages=[
                        PipelineStage(
                            stage_id=f"stage-1-{profile_id}",
                            adapter_id=adapter.adapter_id,
                            required_capability=cap,
                        )
                    ],
                )
            except AdapterNotFoundError:
                pipeline_def = AdapterPipelineDefinition(
                    pipeline_id=f"pipeline-{profile_id}",
                    profile_id=profile_id,
                    stages=[],
                )

        try:
            res = await self.execute_pipeline_definition(
                pipeline_def, context, request_id=request.request_id
            )
            exec_ms = (time.perf_counter() - start_time) * 1000.0

            return OperationResult(
                request_id=request.request_id,
                status="COMPLETED" if res.is_success else "FAILED",
                output_bytes=res.final_output_bytes,
                execution_time_ms=exec_ms,
                errors=res.errors,
            )
        except Exception as err:
            exec_ms = (time.perf_counter() - start_time) * 1000.0
            return OperationResult(
                request_id=request.request_id,
                status="FAILED",
                output_bytes=None,
                execution_time_ms=exec_ms,
                errors=[str(err)],
            )


__all__ = [
    "AdapterPipelineExecutor",
    "PipelineExecutionResult",
    "StageExecutionResult",
    "evaluate_declarative_condition",
]
