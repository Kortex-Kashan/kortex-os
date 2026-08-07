"""
KORTEX Workflow Engine Core Implementation.

Extends BaseEngine and implements IEngineDiagnostics. Serves as the sole runtime
execution engine for state machines and compiled business recipes across KORTEX OS.

Guiding Principle: Zero Business Logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import UUID

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import ResourceAlreadyExistsError, ResourceNotFoundError
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.approval import MemoryApprovalManager
from kortex.engines.workflow.evaluator import StepEvaluator
from kortex.engines.workflow.exceptions import (
    WorkflowApprovalError,
    WorkflowExecutionError,
    WorkflowStateError,
    WorkflowValidationError,
)
from kortex.engines.workflow.interfaces import IWorkflowExecutor
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalState,
    ExecutionResult,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowResult,
    WorkflowSettings,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)
from kortex.engines.workflow.state_machine import WorkflowStateMachine

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.workflow")


class WorkflowEngine(BaseEngine, IWorkflowExecutor):
    """KORTEX Workflow Engine execution runtime and state machine host."""

    def __init__(self, settings: Optional[WorkflowSettings] = None) -> None:
        """Initialize WorkflowEngine with settings.

        Args:
            settings: WorkflowSettings configuration model.
        """
        super().__init__()
        self._settings = settings or WorkflowSettings()
        self._definitions: Dict[str, WorkflowDefinition] = {}
        self._instances: Dict[UUID, WorkflowInstance] = {}
        self._approval_manager = MemoryApprovalManager()
        self._evaluator = StepEvaluator(self._approval_manager)
        self._kernel: Optional[Kernel] = None
        self._storage_engine: Optional[StorageEngine] = None
        self._metrics: Dict[str, Any] = {
            "workflows_started": 0,
            "workflows_completed": 0,
            "workflows_failed": 0,
            "workflows_cancelled": 0,
            "steps_executed": 0,
            "approvals_requested": 0,
            "compensations_executed": 0,
        }
        self._registered_capabilities: List[str] = [
            "kortex.workflow.instance.start",
            "kortex.workflow.instance.approve",
            "kortex.workflow.instance.cancel",
            "kortex.workflow.state.get",
        ]

    @property
    def name(self) -> str:
        """Unique engine identifier name."""
        return "workflow"

    @property
    def dependencies(self) -> List[str]:
        """Prerequisite foundation engines."""
        return ["configuration", "registry", "event", "storage"]

    @property
    def settings(self) -> WorkflowSettings:
        """Access engine configuration settings."""
        return self._settings

    @property
    def approval_manager(self) -> MemoryApprovalManager:
        """Access the approval manager subsystem."""
        return self._approval_manager

    # -- Lifecycle Implementation -------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize Workflow Engine resources and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Workflow Engine...")
        self._kernel = kernel

        try:
            # Resolve Storage Engine dependency via IoC Container
            self._storage_engine = kernel.container.resolve("engine.storage")

            # Register Kernel capabilities
            kernel.register_capability(
                name="kortex.workflow.instance.start",
                description="Start a workflow instance execution",
                provider=self.name,
                handler=self.start_workflow,
            )
            kernel.register_capability(
                name="kortex.workflow.instance.approve",
                description="Submit a decision for an approval step",
                provider=self.name,
                handler=self.submit_approval_decision,
            )
            kernel.register_capability(
                name="kortex.workflow.instance.cancel",
                description="Cancel an active workflow instance",
                provider=self.name,
                handler=self.cancel_workflow,
            )
            kernel.register_capability(
                name="kortex.workflow.state.get",
                description="Get current state of a workflow instance",
                provider=self.name,
                handler=self.get_instance,
            )

            self._set_state(EngineState.READY)
            self.logger.info("Workflow Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Workflow Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Workflow Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down engine tasks."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return

        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Workflow Engine...")
        self._set_state(EngineState.STOPPED)
        self.logger.info("Workflow Engine stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Return diagnostic health status."""
        return self.health()

    # -- Event Helper -------------------------------------------------------

    async def _publish_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Publish event via Kernel Event Engine if Kernel is attached."""
        if self._kernel:
            try:
                await self._kernel.publish_event(topic=topic, payload=payload, sender=self.name)
            except Exception as e:
                self.logger.warning("Failed to publish event '%s': %s", topic, e)

    # -- Definition Registration & Validation ------------------------------

    def register_definition(self, definition: WorkflowDefinition) -> str:
        """Register a WorkflowDefinition with the engine.

        Args:
            definition: WorkflowDefinition to register.

        Returns:
            Definition ID string.
        """
        if not definition.steps:
            raise WorkflowValidationError(f"Workflow definition '{definition.id}' must contain at least one step.")

        self._definitions[definition.id] = definition
        self.logger.info("Registered workflow definition: '%s' (v%s)", definition.name, definition.version)
        
        # Fire created & validated events
        asyncio.create_task(self._publish_event("workflow.created", {"definition_id": definition.id, "name": definition.name}))
        asyncio.create_task(self._publish_event("workflow.validated", {"definition_id": definition.id, "name": definition.name}))
        return definition.id

    def get_definition(self, definition_id: str) -> WorkflowDefinition:
        """Retrieve a registered WorkflowDefinition by ID."""
        if definition_id not in self._definitions:
            raise ResourceNotFoundError(f"Workflow definition '{definition_id}' not found.")
        return self._definitions[definition_id]

    def list_definitions(self) -> List[WorkflowDefinition]:
        """List all registered WorkflowDefinition objects."""
        return list(self._definitions.values())

    # -- Instance Persistence Helper via Storage Engine -----------------------

    async def _persist_instance_snapshot(self, instance: WorkflowInstance) -> None:
        """Persist a workflow instance state snapshot using Storage Engine object store."""
        if self._storage_engine:
            try:
                payload_bytes = instance.model_dump_json().encode("utf-8")
                key = f"snapshots/{instance.id}.json"
                await self._storage_engine.object.put_object(
                    bucket_name="workflows",
                    object_key=key,
                    data=payload_bytes,
                    mime_type="application/json",
                )
            except Exception as e:
                self.logger.warning("Failed to persist snapshot for workflow instance '%s': %s", instance.id, e)

    # -- Execution Orchestration --------------------------------------------

    async def start_workflow(
        self,
        definition_id: str,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowInstance:
        """Instantiate and start executing a registered workflow definition.

        Args:
            definition_id: ID of registered WorkflowDefinition.
            initial_context: Initial context variables dict.

        Returns:
            Instantiated WorkflowInstance object.
        """
        definition = self.get_definition(definition_id)

        # Create instance
        context = WorkflowContext(variables=initial_context or {})
        instance = WorkflowInstance(
            definition_id=definition.id,
            definition_version=definition.version,
            context=context,
            state=WorkflowState.CREATED,
            status=WorkflowStatus.PENDING,
        )

        self._instances[instance.id] = instance
        self._metrics["workflows_started"] += 1

        # Execute deterministic lifecycle transitions: CREATED -> VALIDATED -> READY -> RUNNING
        WorkflowStateMachine.transition(instance, WorkflowState.VALIDATED)
        WorkflowStateMachine.transition(instance, WorkflowState.READY)
        WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)

        await self._persist_instance_snapshot(instance)
        await self._publish_event("workflow.started", {"instance_id": str(instance.id), "definition_id": definition.id})

        # Run workflow steps asynchronously
        asyncio.create_task(self._run_instance_steps(instance, definition))
        return instance

    async def _run_instance_steps(self, instance: WorkflowInstance, definition: WorkflowDefinition) -> WorkflowResult:
        """Internal step execution loop."""
        start_time = time.time()
        step_results: List[ExecutionResult] = []

        def resolver(cap_name: str) -> Any:
            if self._kernel:
                try:
                    desc = self._kernel.get_capability(cap_name)
                    return desc.handler
                except Exception:
                    return None
            return None

        while instance.current_step_index < len(definition.steps):
            if instance.state not in (WorkflowState.RUNNING, WorkflowState.APPROVED):
                self.logger.info("Workflow '%s' execution loop paused in state %s", instance.id, instance.state.value)
                break

            step = definition.steps[instance.current_step_index]
            instance.current_step_id = step.id

            # If coming from APPROVED state, transition back to RUNNING
            if instance.state == WorkflowState.APPROVED:
                WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)

            res = await self._evaluator.execute_step(instance, step, capability_resolver=resolver)
            step_results.append(res)
            self._metrics["steps_executed"] += 1

            if not res.success and not step.on_failure_continue:
                self.logger.error("Step '%s' failed in workflow '%s'. Initiating failure & rollback.", step.id, instance.id)
                WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
                self._metrics["workflows_failed"] += 1

                # Execute LIFO compensation stack rollback
                comp_results = await self._evaluator.execute_compensation_stack(instance, capability_resolver=resolver)
                self._metrics["compensations_executed"] += len(comp_results)

                await self._persist_instance_snapshot(instance)
                await self._publish_event("workflow.failed", {"instance_id": str(instance.id), "error": res.error})
                await self._publish_event("workflow.rollback", {"instance_id": str(instance.id), "compensations": comp_results})
                
                duration = (time.time() - start_time) * 1000
                return WorkflowResult(
                    instance_id=instance.id,
                    definition_name=definition.name,
                    state=instance.state,
                    status=instance.status,
                    context=instance.context,
                    step_results=step_results,
                    duration_ms=duration,
                    error=res.error,
                )

            if instance.state == WorkflowState.WAITING:
                self.logger.info("Workflow '%s' paused at step '%s' waiting for approval.", instance.id, step.id)
                self._metrics["approvals_requested"] += 1
                await self._persist_instance_snapshot(instance)
                await self._publish_event("workflow.waiting", {"instance_id": str(instance.id), "step_id": step.id})
                
                duration = (time.time() - start_time) * 1000
                return WorkflowResult(
                    instance_id=instance.id,
                    definition_name=definition.name,
                    state=instance.state,
                    status=instance.status,
                    context=instance.context,
                    step_results=step_results,
                    duration_ms=duration,
                )

            instance.current_step_index += 1

        # Check for workflow completion
        if instance.current_step_index >= len(definition.steps) and instance.state == WorkflowState.RUNNING:
            WorkflowStateMachine.transition(instance, WorkflowState.COMPLETED)
            self._metrics["workflows_completed"] += 1
            await self._persist_instance_snapshot(instance)
            await self._publish_event("workflow.completed", {"instance_id": str(instance.id)})

        duration = (time.time() - start_time) * 1000
        return WorkflowResult(
            instance_id=instance.id,
            definition_name=definition.name,
            state=instance.state,
            status=instance.status,
            context=instance.context,
            step_results=step_results,
            duration_ms=duration,
        )

    # -- Pause, Resume, Cancel, Approval APIs -------------------------------

    async def pause_workflow(self, instance_id: UUID) -> WorkflowInstance:
        """Pause a running workflow instance."""
        instance = self.get_instance(instance_id)
        if instance.state != WorkflowState.RUNNING:
            raise WorkflowStateError(f"Cannot pause workflow in state '{instance.state.value}'.")

        instance.status = WorkflowStatus.PAUSED
        await self._persist_instance_snapshot(instance)
        await self._publish_event("workflow.paused", {"instance_id": str(instance.id)})
        return instance

    async def resume_workflow(self, instance_id: UUID) -> WorkflowInstance:
        """Resume a paused or approved workflow instance."""
        instance = self.get_instance(instance_id)
        if instance.state not in (WorkflowState.WAITING, WorkflowState.APPROVED, WorkflowState.RUNNING):
            raise WorkflowStateError(f"Cannot resume workflow in state '{instance.state.value}'.")

        if instance.state == WorkflowState.WAITING:
            WorkflowStateMachine.transition(instance, WorkflowState.APPROVED)

        instance.status = WorkflowStatus.RUNNING
        definition = self.get_definition(instance.definition_id)

        await self._persist_instance_snapshot(instance)
        await self._publish_event("workflow.resumed", {"instance_id": str(instance.id)})

        # Continue step execution
        asyncio.create_task(self._run_instance_steps(instance, definition))
        return instance

    async def cancel_workflow(self, instance_id: UUID, reason: str = "") -> WorkflowInstance:
        """Cancel a running or waiting workflow instance."""
        instance = self.get_instance(instance_id)
        WorkflowStateMachine.transition(instance, WorkflowState.CANCELLED)
        self._metrics["workflows_cancelled"] += 1

        await self._persist_instance_snapshot(instance)
        await self._publish_event("workflow.cancelled", {"instance_id": str(instance.id), "reason": reason})
        return instance

    async def submit_approval_decision(self, decision: ApprovalDecision) -> WorkflowInstance:
        """Submit an approval decision and resume workflow if approved."""
        request = await self._approval_manager.submit_decision(decision)
        instance = self.get_instance(request.instance_id)

        if decision.decision == ApprovalState.APPROVED:
            await self._publish_event("workflow.approved", {"instance_id": str(instance.id), "approver_id": decision.approver_id})
            # Advance step index past the approval step and resume
            instance.current_step_index += 1
            return await self.resume_workflow(instance.id)

        # Rejected decision
        WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
        self._metrics["workflows_failed"] += 1
        await self._persist_instance_snapshot(instance)
        await self._publish_event("workflow.failed", {"instance_id": str(instance.id), "reason": "Approval rejected"})
        return instance

    async def execute_workflow(
        self,
        definition: WorkflowDefinition,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> WorkflowResult:
        """Synchronously execute a workflow definition to completion."""
        def_id = self.register_definition(definition)
        instance = await self.start_workflow(def_id, initial_context)
        return await self._run_instance_steps(instance, definition)

    def get_instance(self, instance_id: UUID) -> WorkflowInstance:
        """Retrieve a WorkflowInstance by UUID."""
        if instance_id not in self._instances:
            raise ResourceNotFoundError(f"Workflow instance '{instance_id}' not found.")
        return self._instances[instance_id]

    def list_instances(self) -> List[WorkflowInstance]:
        """List all active WorkflowInstance objects."""
        return list(self._instances.values())

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health check report."""
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "active_instances": len(self._instances),
            "definitions_loaded": len(self._definitions),
        }

    def metrics(self) -> Dict[str, Any]:
        """Return runtime performance metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> Dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self.name,
            "version": self.version(),
            "state": self._state.value,
            "settings": self._settings.model_dump(),
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "1.0.0"

    def capabilities(self) -> List[str]:
        """Return list of registered capability strings."""
        return list(self._registered_capabilities)
