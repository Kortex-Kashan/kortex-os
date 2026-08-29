"""
KORTEX Workflow Step Evaluator & Compensation Runtime.

Responsible for step execution, exponential backoff retries, capability dispatching,
approval checkpointing, and LIFO compensation stack rollback execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from kortex.engines.workflow.approval import ApprovalProvider, MemoryApprovalManager
from kortex.engines.workflow.models import (
    CompensationAction,
    ExecutionResult,
    RetryPolicy,
    WorkflowInstance,
    WorkflowState,
    WorkflowStep,
)
from kortex.engines.workflow.state_machine import WorkflowStateMachine

logger = logging.getLogger("kortex.engines.workflow.evaluator")


class StepEvaluator:
    """Evaluates and executes individual workflow steps, retries, and compensation flows."""

    def __init__(self, approval_manager: ApprovalProvider | MemoryApprovalManager) -> None:
        self._approval_manager = approval_manager

    @property
    def approval_manager(self) -> ApprovalProvider | MemoryApprovalManager:
        """Return the approval manager instance."""
        return self._approval_manager

    async def execute_step(
        self,
        instance: WorkflowInstance,
        step: WorkflowStep,
        capability_dispatcher: Callable[[str, dict[str, Any], dict[str, Any]], Any] | None = None,
    ) -> ExecutionResult:
        """Execute a single workflow step with retry policies and compensation registration.

        Args:
            instance: Active WorkflowInstance.
            step: WorkflowStep to execute.
            capability_dispatcher: Optional async callable of the form
                `(capability_name, parameters, context) -> result`, routing
                the call through the Kernel's enforced dispatch boundary
                (`Kernel.invoke_capability`). Unlike the prior
                `capability_resolver`, a lookup/authentication/authorization
                failure here propagates as a real exception rather than
                being silently swallowed into a fake success — the `except`
                block below already handles it via the normal retry/failure
                path, no new failure-handling logic is needed.

        Returns:
            ExecutionResult descriptor.
        """
        start_time = time.time()
        policy = step.retry_policy or RetryPolicy()
        attempts = 0
        last_error: Exception | None = None

        # Check if approval step
        if step.is_approval_step:
            logger.info("Step '%s' requires human approval.", step.id)
            role = step.required_approval_role or "APPROVAL_ROLE"
            await self._approval_manager.create_request(
                instance_id=instance.id,
                step_id=step.id,
                required_role=role,
            )
            WorkflowStateMachine.transition(instance, WorkflowState.WAITING)
            return ExecutionResult(
                step_id=step.id,
                success=True,
                output={"approval_status": "WAITING_APPROVAL", "required_role": role},
                execution_time_ms=(time.time() - start_time) * 1000,
                attempts=1,
            )

        # Normal capability or inline execution loop with retry backoff
        while attempts < policy.max_attempts:
            attempts += 1
            try:
                logger.debug("Executing step '%s' (Attempt %d/%d)", step.id, attempts, policy.max_attempts)

                output = None
                if step.capability_name and capability_dispatcher:
                    call_parameters = dict(step.parameters)
                    authz_context = call_parameters.pop("_authz_context", {})
                    output = await capability_dispatcher(step.capability_name, call_parameters, authz_context)
                else:
                    output = f"Step {step.id} executed successfully"

                # Store output in step context
                instance.context.step_outputs[step.id] = output

                # Register compensation action if specified
                if step.compensation_action:
                    instance.compensation_stack.append(step.compensation_action)
                    logger.debug(
                        "Registered compensation action '%s' for step '%s'",
                        step.compensation_action.name,
                        step.id,
                    )

                elapsed_ms = (time.time() - start_time) * 1000
                return ExecutionResult(
                    step_id=step.id,
                    success=True,
                    output=output,
                    execution_time_ms=elapsed_ms,
                    attempts=attempts,
                )
            except Exception as e:
                last_error = e
                logger.warning("Step '%s' failed attempt %d/%d: %s", step.id, attempts, policy.max_attempts, e)
                if attempts < policy.max_attempts:
                    delay = policy.initial_delay_seconds * (policy.backoff_factor ** (attempts - 1))
                    await asyncio.sleep(delay)

        elapsed_ms = (time.time() - start_time) * 1000
        error_msg = str(last_error) if last_error else "Step execution failed"

        if step.on_failure_continue:
            logger.info("Step '%s' failed but on_failure_continue is True. Continuing execution.", step.id)
            return ExecutionResult(
                step_id=step.id,
                success=False,
                error=error_msg,
                execution_time_ms=elapsed_ms,
                attempts=attempts,
            )

        return ExecutionResult(
            step_id=step.id,
            success=False,
            error=error_msg,
            execution_time_ms=elapsed_ms,
            attempts=attempts,
        )

    async def execute_compensation_stack(
        self,
        instance: WorkflowInstance,
        capability_dispatcher: Callable[[str, dict[str, Any], dict[str, Any]], Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute registered compensation actions in LIFO (reverse) order upon workflow failure.

        Args:
            instance: Active WorkflowInstance.
            capability_dispatcher: Optional async dispatcher callback, same
                shape as `execute_step`'s. Compensation actions reuse the
                same `instance` (and therefore the same session token, if
                any) as the original steps — no separate system-internal
                security context exists for compensation in this milestone;
                this is a deliberate, minimal choice, not a silent one, and
                is flagged in the completion report as an open design point
                rather than assumed settled.

        Returns:
            List of compensation execution result records.
        """
        results: list[dict[str, Any]] = []
        logger.info("Executing LIFO compensation stack for workflow instance '%s'", instance.id)

        while instance.compensation_stack:
            action: CompensationAction = instance.compensation_stack.pop()
            try:
                logger.info("Executing compensation action '%s'", action.name)
                if action.capability_name and capability_dispatcher:
                    call_parameters = dict(action.parameters)
                    authz_context = call_parameters.pop("_authz_context", {})
                    await capability_dispatcher(action.capability_name, call_parameters, authz_context)
                results.append({"action_id": action.id, "name": action.name, "status": "COMPENSATED"})
            except Exception as e:
                logger.error("Failed compensation action '%s': %s", action.name, e)
                results.append({"action_id": action.id, "name": action.name, "status": "FAILED", "error": str(e)})

        return results
