"""
KORTEX Workflow Step Evaluator & Compensation Runtime.

Responsible for step execution, exponential backoff retries, capability dispatching,
approval checkpointing, and LIFO compensation stack rollback execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID

from kortex.engines.workflow.approval import MemoryApprovalManager
from kortex.engines.workflow.exceptions import WorkflowExecutionError, WorkflowValidationError
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalState,
    CompensationAction,
    ExecutionResult,
    RetryPolicy,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowResult,
    WorkflowState,
    WorkflowStep,
)
from kortex.engines.workflow.state_machine import WorkflowStateMachine

logger = logging.getLogger("kortex.engines.workflow.evaluator")


class StepEvaluator:
    """Evaluates and executes individual workflow steps, retries, and compensation flows."""

    def __init__(self, approval_manager: MemoryApprovalManager) -> None:
        self._approval_manager = approval_manager

    @property
    def approval_manager(self) -> MemoryApprovalManager:
        """Return the approval manager instance."""
        return self._approval_manager

    async def execute_step(
        self,
        instance: WorkflowInstance,
        step: WorkflowStep,
        capability_resolver: Optional[Callable[[str], Any]] = None,
    ) -> ExecutionResult:
        """Execute a single workflow step with retry policies and compensation registration.

        Args:
            instance: Active WorkflowInstance.
            step: WorkflowStep to execute.
            capability_resolver: Optional callable resolving capability handlers by name.

        Returns:
            ExecutionResult descriptor.
        """
        start_time = time.time()
        policy = step.retry_policy or RetryPolicy()
        attempts = 0
        last_error: Optional[Exception] = None

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
                if step.capability_name and capability_resolver:
                    handler = capability_resolver(step.capability_name)
                    if handler:
                        if asyncio.iscoroutinefunction(handler):
                            output = await handler(**step.parameters)
                        else:
                            output = handler(**step.parameters)
                    else:
                        output = f"Executed capability {step.capability_name}"
                else:
                    output = f"Step {step.id} executed successfully"

                # Store output in step context
                instance.context.step_outputs[step.id] = output

                # Register compensation action if specified
                if step.compensation_action:
                    instance.compensation_stack.append(step.compensation_action)
                    logger.debug("Registered compensation action '%s' for step '%s'", step.compensation_action.name, step.id)

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
        capability_resolver: Optional[Callable[[str], Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute registered compensation actions in LIFO (reverse) order upon workflow failure.

        Args:
            instance: Active WorkflowInstance.
            capability_resolver: Optional capability resolver callback.

        Returns:
            List of compensation execution result records.
        """
        results: List[Dict[str, Any]] = []
        logger.info("Executing LIFO compensation stack for workflow instance '%s'", instance.id)

        while instance.compensation_stack:
            action: CompensationAction = instance.compensation_stack.pop()
            try:
                logger.info("Executing compensation action '%s'", action.name)
                if action.capability_name and capability_resolver:
                    handler = capability_resolver(action.capability_name)
                    if handler:
                        if asyncio.iscoroutinefunction(handler):
                            await handler(**action.parameters)
                        else:
                            handler(**action.parameters)
                results.append({"action_id": action.id, "name": action.name, "status": "COMPENSATED"})
            except Exception as e:
                logger.error("Failed compensation action '%s': %s", action.name, e)
                results.append({"action_id": action.id, "name": action.name, "status": "FAILED", "error": str(e)})

        return results
