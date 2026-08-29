"""
KORTEX Workflow Engine Core Implementation.

Extends BaseEngine and implements IEngineDiagnostics. Serves as the sole runtime
execution engine for state machines and compiled business recipes across KORTEX OS.

Guiding Principle: Zero Business Logic.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ResourceNotFoundError
from kortex.engines.security.models import SecurityPrincipal, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.approval import (
    ApprovalProvider,
    DurableApprovalManager,
    MemoryApprovalManager,
)
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
)
from kortex.engines.workflow.persistence import (
    WorkflowStore,
)
from kortex.engines.workflow.state_machine import WorkflowStateMachine

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.workflow")


class WorkflowEngine(BaseEngine, IWorkflowExecutor):
    """KORTEX Workflow Engine execution runtime and state machine host."""

    def __init__(self, settings: WorkflowSettings | None = None) -> None:
        """Initialize WorkflowEngine with settings.

        Args:
            settings: WorkflowSettings configuration model.
        """
        super().__init__()
        self._settings = settings or WorkflowSettings()
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._instances: dict[UUID, WorkflowInstance] = {}
        self._approval_manager: ApprovalProvider | MemoryApprovalManager = MemoryApprovalManager()
        self._evaluator = StepEvaluator(self._approval_manager)
        self._kernel: Kernel | None = None
        self._storage_engine: StorageEngine | None = None
        self._workflow_store: WorkflowStore | None = None
        self._recovery_lock = asyncio.Lock()
        self._running_tasks: dict[UUID, asyncio.Task[Any]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._metrics: dict[str, Any] = {
            "workflows_started": 0,
            "workflows_completed": 0,
            "workflows_failed": 0,
            "workflows_cancelled": 0,
            "steps_executed": 0,
            "approvals_requested": 0,
            "compensations_executed": 0,
        }
        self._registered_capabilities: list[str] = [
            "kortex.workflow.instance.start",
            "kortex.workflow.instance.list",
            "kortex.workflow.instance.get",
            "kortex.workflow.instance.resume",
            "kortex.workflow.instance.cancel",
            "kortex.workflow.state.get",
            "kortex.workflow.instance.approve",
            "kortex.workflow.definition.list",
            "kortex.workflow.approval.create",
            "kortex.workflow.approval.list",
            "kortex.workflow.approval.get",
            "kortex.workflow.approval.decide",
            "kortex.workflow.approval.delegate",
        ]

    @property
    def name(self) -> str:
        """Unique engine identifier name."""
        return "workflow"

    @property
    def dependencies(self) -> list[str]:
        """Prerequisite foundation engines."""
        return ["configuration", "registry", "event", "storage"]

    @property
    def settings(self) -> WorkflowSettings:
        """Access engine configuration settings."""
        return self._settings

    @property
    def approval_manager(self) -> ApprovalProvider | MemoryApprovalManager:
        """Access the approval manager subsystem."""
        return self._approval_manager

    def set_approval_manager(self, manager: ApprovalProvider | MemoryApprovalManager) -> None:
        """Explicitly inject or configure the approval manager (e.g. for testing)."""
        self._approval_manager = manager
        self._evaluator = StepEvaluator(self._approval_manager)

    @property
    def workflow_store(self) -> WorkflowStore | None:
        """Access the persistent workflow store."""
        return self._workflow_store

    def set_workflow_store(self, store: WorkflowStore) -> None:
        """Explicitly inject or configure the WorkflowStore (e.g. for testing)."""
        self._workflow_store = store

    # -- Lifecycle Implementation -------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize Workflow Engine resources and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Workflow Engine...")
        self._kernel = kernel

        try:
            # Resolve Storage Engine dependency via IoC Container
            self._storage_engine = kernel.container.resolve("engine.storage")
            if self._storage_engine and hasattr(self._storage_engine, "data"):
                self._workflow_store = WorkflowStore(self._storage_engine.data)
                self.logger.info("WorkflowEngine wired to StorageEngine.data relational store.")

                sec_engine = None
                try:
                    sec_engine = kernel.get_engine("security")
                except Exception as err:
                    self.logger.debug("Security engine lookup in initialize: %s", err)

                event_engine = None
                try:
                    event_engine = kernel.get_engine("event")
                except Exception as err:
                    self.logger.debug("Event engine lookup in initialize: %s", err)

                outbox_store = getattr(kernel, "outbox", None)

                self._approval_manager = DurableApprovalManager(
                    data_store=self._storage_engine.data,
                    security_engine=sec_engine,
                    outbox_store=outbox_store,
                    event_engine=event_engine,
                )
                self._evaluator = StepEvaluator(self._approval_manager)
                self.logger.info("WorkflowEngine wired to DurableApprovalManager backed by StorageEngine.data.")

            # Register Kernel capabilities
            kernel.register_capability(
                name="kortex.workflow.instance.start",
                description="Start a workflow instance execution",
                provider=self.name,
                handler=self.start_workflow,
                required_permissions=["workflow:start"],
            )
            kernel.register_capability(
                name="kortex.workflow.instance.list",
                description="List workflow instances for tenant",
                provider=self.name,
                handler=self.list_instances_durable,
                required_permissions=["workflow:read"],
            )
            kernel.register_capability(
                name="kortex.workflow.instance.get",
                description="Get workflow instance by ID",
                provider=self.name,
                handler=self.get_instance_durable,
                required_permissions=["workflow:read"],
            )
            kernel.register_capability(
                name="kortex.workflow.instance.resume",
                description="Resume a paused or waiting workflow instance",
                provider=self.name,
                handler=self.resume_workflow,
                required_permissions=["workflow:start"],
            )
            kernel.register_capability(
                name="kortex.workflow.instance.cancel",
                description="Cancel an active workflow instance",
                provider=self.name,
                handler=self.cancel_workflow,
                required_permissions=["workflow:cancel"],
            )
            kernel.register_capability(
                name="kortex.workflow.instance.approve",
                description="Submit a decision for an approval step",
                provider=self.name,
                handler=self.submit_approval_decision,
                required_permissions=["workflow:approve"],
            )
            kernel.register_capability(
                name="kortex.workflow.approval.create",
                description="Create an approval request ticket",
                provider=self.name,
                handler=self.create_approval_request,
                required_permissions=["approval:write"],
            )
            kernel.register_capability(
                name="kortex.workflow.approval.list",
                description="List approval request tickets for tenant",
                provider=self.name,
                handler=self.list_approval_requests,
                required_permissions=["approval:read"],
            )
            kernel.register_capability(
                name="kortex.workflow.approval.get",
                description="Get approval request ticket by ID",
                provider=self.name,
                handler=self.get_approval_request,
                required_permissions=["approval:read"],
            )
            kernel.register_capability(
                name="kortex.workflow.approval.decide",
                description="Submit a decision for an approval ticket",
                provider=self.name,
                handler=self.decide_approval_request,
                required_permissions=["approval:write"],
            )
            kernel.register_capability(
                name="kortex.workflow.approval.delegate",
                description="Delegate an approval role to another principal",
                provider=self.name,
                handler=self.delegate_approval_role,
                required_permissions=["approval:write"],
            )
            kernel.register_capability(
                name="kortex.workflow.state.get",
                description="Get current state of a workflow instance (legacy alias)",
                provider=self.name,
                handler=self.get_instance_durable,
                required_permissions=["workflow:read"],
            )
            kernel.register_capability(
                name="kortex.workflow.definition.list",
                description="List registered workflow definitions",
                provider=self.name,
                handler=self.list_definitions,
                required_permissions=["workflow:read"],
            )

            self._set_state(EngineState.READY)
            self.logger.info("Workflow Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Workflow Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services and perform state recovery hydration."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Workflow Engine is RUNNING.")

        # Startup hydration and recovery daemon
        try:
            await self.hydrate_and_recover()
        except Exception as e:
            self.logger.error("Error during workflow startup hydration: %s", e, exc_info=True)

    async def stop(self) -> None:
        """Gracefully shut down engine tasks and cancel active execution jobs."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return

        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Workflow Engine...")

        # Cancel any active running background execution tasks cleanly
        for task in list(self._running_tasks.values()):
            if not task.done():
                task.cancel()
        self._running_tasks.clear()

        self._set_state(EngineState.STOPPED)
        self.logger.info("Workflow Engine stopped cleanly.")

    # -- Hydration & Restart Recovery ---------------------------------------

    async def hydrate_and_recover(self, tenant_id: str | None = None) -> list[WorkflowInstance]:
        """Scan persistent storage for unfinalized workflow instances and recover them.

        - WAITING approval workflows remain waiting and have approval requests restored.
        - RUNNING, APPROVED, and READY executions are deterministically resumed from current_step_index.
        - Terminal states (COMPLETED, FAILED, CANCELLED) are NEVER resurrected.
        """
        if self._workflow_store is None:
            return []

        recovered: list[WorkflowInstance] = []
        async with self._recovery_lock:
            unfinalized = await self._workflow_store.get_unfinalized_instances(tenant_id=tenant_id)
            self.logger.info("Discovered %d unfinalized workflow instance(s) during recovery scan.", len(unfinalized))

            for instance in unfinalized:
                # Cache instance in memory
                self._instances[instance.id] = instance

                # Ensure definition is available
                definition: WorkflowDefinition | None = self._definitions.get(instance.definition_id)
                if definition is None:
                    try:
                        definition = await self._workflow_store.get_definition(
                            instance.definition_id, tenant_id=instance.tenant_id
                        )
                        if definition is not None:
                            self._definitions[definition.id] = definition
                    except Exception as e:
                        self.logger.warning(
                            "Could not load definition '%s' for instance '%s': %s",
                            instance.definition_id,
                            instance.id,
                            e,
                        )

                if definition is None:
                    self.logger.error(
                        "Cannot recover workflow instance '%s': definition '%s' not found. Transitioning to FAILED.",
                        instance.id,
                        instance.definition_id,
                    )
                    WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
                    instance.status = WorkflowStatus.FAILED
                    try:
                        await self._workflow_store.update_instance(instance)
                    except Exception as e:
                        self.logger.error("Failed to persist FAILED state for missing definition: %s", e)
                    continue

                if instance.state == WorkflowState.WAITING:
                    # Workflow is waiting for human approval or external decision.
                    if instance.current_step_id:
                        step = next((s for s in definition.steps if s.id == instance.current_step_id), None)
                        if step and step.is_approval_step:
                            role = step.required_approval_role or "APPROVAL_ROLE"
                            existing_req: ApprovalRequest | None = None
                            if hasattr(self._approval_manager, "get_request_by_step"):
                                try:
                                    existing_req = await self._approval_manager.get_request_by_step(
                                        instance.id, step.id, tenant_id=instance.tenant_id
                                    )
                                except TypeError:
                                    existing_req = await self._approval_manager.get_request_by_step(
                                        instance.id, step.id
                                    )

                            if existing_req is not None:
                                if existing_req.state == ApprovalState.APPROVED:
                                    self.logger.info(
                                        "Recovered workflow '%s' with already-APPROVED ticket '%s'. Resuming.",
                                        instance.id,
                                        existing_req.id,
                                    )
                                    instance.current_step_index += 1
                                    if self._workflow_store:
                                        await self._workflow_store.update_instance(instance)
                                    task = asyncio.create_task(self._run_instance_steps(instance, definition))
                                    self._running_tasks[instance.id] = task
                                    recovered.append(instance)
                                    continue
                                elif existing_req.state in (ApprovalState.REJECTED, ApprovalState.EXPIRED):
                                    self.logger.info(
                                        "Recovered workflow '%s' with %s ticket '%s'. Transitioning to FAILED.",
                                        instance.id,
                                        existing_req.state.value,
                                        existing_req.id,
                                    )
                                    WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
                                    instance.status = WorkflowStatus.FAILED
                                    if self._workflow_store:
                                        await self._workflow_store.update_instance(instance)
                                    if instance.compensation_stack:
                                        await self._evaluator.execute_compensation_stack(
                                            instance=instance,
                                            capability_dispatcher=self._dispatch_capability,
                                        )
                                    recovered.append(instance)
                                    continue
                                elif existing_req.state == ApprovalState.PENDING:
                                    self.logger.info(
                                        "Hydrated workflow '%s' in WAITING state with pending approval ticket '%s'.",
                                        instance.id,
                                        existing_req.id,
                                    )
                                    recovered.append(instance)
                                    continue
                            else:
                                # Create missing ticket
                                await self._approval_manager.create_request(
                                    instance_id=instance.id,
                                    step_id=step.id,
                                    required_role=role,
                                    tenant_id=instance.tenant_id,
                                )
                    self.logger.info(
                        "Hydrated workflow '%s' in WAITING state at step '%s'.",
                        instance.id,
                        instance.current_step_id,
                    )
                    recovered.append(instance)

                elif instance.state in (WorkflowState.RUNNING, WorkflowState.APPROVED, WorkflowState.READY):
                    # Interrupted mid-execution: resume deterministically from current_step_index
                    self.logger.info(
                        "Recovered interrupted workflow '%s' in state '%s'. Resuming from step index %d ('%s').",
                        instance.id,
                        instance.state.value,
                        instance.current_step_index,
                        instance.current_step_id,
                    )
                    task = asyncio.create_task(self._run_instance_steps(instance, definition))
                    self._running_tasks[instance.id] = task
                    recovered.append(instance)

        return recovered

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health status."""
        return self.health()

    # -- Event Helper -------------------------------------------------------

    async def _publish_event(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish event via Kernel Event Engine if Kernel is attached."""
        if self._kernel:
            try:
                await self._kernel.publish_event(topic=topic, payload=payload, sender=self.name)
            except Exception as e:
                self.logger.warning("Failed to publish event '%s': %s", topic, e)

    # -- Definition Registration & Validation ------------------------------

    # -- Definition Registration & Validation ------------------------------

    def register_definition(self, definition: WorkflowDefinition, tenant_id: str = "default") -> str:
        """Register a WorkflowDefinition with the engine.

        Args:
            definition: WorkflowDefinition to register.
            tenant_id: Tenant owning this definition.

        Returns:
            Definition ID string.
        """
        if not definition.steps:
            raise WorkflowValidationError(f"Workflow definition '{definition.id}' must contain at least one step.")

        if definition.tenant_id == "default" and tenant_id != "default":
            definition.tenant_id = tenant_id

        self._definitions[definition.id] = definition
        self.logger.info("Registered workflow definition: '%s' (v%s)", definition.name, definition.version)

        # Fire created & validated events
        t1 = asyncio.create_task(
            self._publish_event("workflow.created", {"definition_id": definition.id, "name": definition.name})
        )
        t2 = asyncio.create_task(
            self._publish_event("workflow.validated", {"definition_id": definition.id, "name": definition.name})
        )
        self._background_tasks.add(t1)
        self._background_tasks.add(t2)
        t1.add_done_callback(self._background_tasks.discard)
        t2.add_done_callback(self._background_tasks.discard)
        return definition.id

    async def register_definition_async(self, definition: WorkflowDefinition, tenant_id: str = "default") -> str:
        """Register and persist a WorkflowDefinition asynchronously."""
        if not definition.steps:
            raise WorkflowValidationError(f"Workflow definition '{definition.id}' must contain at least one step.")

        if definition.tenant_id == "default" and tenant_id != "default":
            definition.tenant_id = tenant_id

        self._definitions[definition.id] = definition
        self.logger.info("Registered workflow definition (async): '%s' (v%s)", definition.name, definition.version)

        if self._workflow_store:
            await self._workflow_store.save_definition(definition, tenant_id=definition.tenant_id)

        await self._publish_event("workflow.created", {"definition_id": definition.id, "name": definition.name})
        await self._publish_event("workflow.validated", {"definition_id": definition.id, "name": definition.name})
        return definition.id

    def get_definition(self, definition_id: str) -> WorkflowDefinition:
        """Retrieve a registered WorkflowDefinition by ID from memory."""
        if definition_id not in self._definitions:
            raise ResourceNotFoundError(f"Workflow definition '{definition_id}' not found.")
        return self._definitions[definition_id]

    async def get_definition_async(
        self, definition_id: str, tenant_id: str | None = None
    ) -> WorkflowDefinition:
        """Retrieve a WorkflowDefinition by ID, reading through to persistent store if necessary."""
        if definition_id in self._definitions:
            return self._definitions[definition_id]

        if self._workflow_store:
            loaded = await self._workflow_store.get_definition(definition_id, tenant_id=tenant_id)
            if loaded:
                self._definitions[loaded.id] = loaded
                return loaded

        raise ResourceNotFoundError(f"Workflow definition '{definition_id}' not found.")

    def list_definitions(self) -> list[WorkflowDefinition]:
        """List all registered WorkflowDefinition objects in memory."""
        return list(self._definitions.values())

    async def list_definitions_durable(self, tenant_id: str | None = None) -> list[WorkflowDefinition]:
        """List all registered WorkflowDefinition objects from persistent store or memory."""
        if self._workflow_store:
            persisted = await self._workflow_store.list_definitions(tenant_id=tenant_id)
            # Merge with in-memory definitions
            known_ids = {d.id for d in persisted}
            for d in self._definitions.values():
                if d.id not in known_ids and (tenant_id is None or d.tenant_id == tenant_id):
                    persisted.append(d)
            return persisted
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
        initial_context: dict[str, Any] | None = None,
        session_token: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> WorkflowInstance:
        """Instantiate, persist, and start executing a registered workflow definition.

        Args:
            definition_id: ID of registered WorkflowDefinition.
            initial_context: Initial context variables dict.
            session_token: Optional opaque session token blob (matching
                `TokenPayload`'s own field shape), carried through to every
                step's capability dispatch via `Kernel.invoke_capability()`.
            tenant_id: Optional tenant identifier. If omitted, extracted from
                session_token or defaults to "default".

        Returns:
            Instantiated WorkflowInstance object.
        """
        # Resolve tenant ID
        tid = tenant_id
        if not tid and session_token and isinstance(session_token, dict):
            tid = session_token.get("tenant_id")
        tid = tid or "default"

        definition = await self.get_definition_async(definition_id, tenant_id=tid)

        # Create instance domain model
        context = WorkflowContext(variables=initial_context or {}, session_token=session_token)
        instance = WorkflowInstance(
            definition_id=definition.id,
            definition_version=definition.version,
            tenant_id=tid,
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

        # Durable relational persistence before asynchronous execution begins
        if self._workflow_store:
            await self._workflow_store.save_instance(instance, definition=definition, tenant_id=tid)

        await self._persist_instance_snapshot(instance)
        await self._publish_event(
            "workflow.started",
            {"instance_id": str(instance.id), "definition_id": definition.id, "tenant_id": tid},
        )

        # Run workflow steps asynchronously
        task = asyncio.create_task(self._run_instance_steps(instance, definition))
        self._running_tasks[instance.id] = task
        return instance

    async def _run_instance_steps(
        self, instance: WorkflowInstance, definition: WorkflowDefinition
    ) -> WorkflowResult:
        """Internal step execution loop with durable step runs and state updates."""
        start_time = time.time()
        step_results: list[ExecutionResult] = []

        async def dispatch(capability_name: str, parameters: dict[str, Any], context: dict[str, Any]) -> object:
            """Enforced capability dispatch for this workflow run.

            Routes every capability invocation through `Kernel.invoke_capability()`
            — the Kernel-mediated authentication+authorization boundary —
            rather than resolving and calling a raw handler directly.
            """
            if not self._kernel:
                raise WorkflowExecutionError(
                    f"Cannot invoke capability '{capability_name}': Workflow Engine has no Kernel reference."
                )
            session_token = None
            if instance.context.session_token:
                token_fields = dict(instance.context.session_token)
                raw_signature = token_fields.get("signature")
                if isinstance(raw_signature, str):
                    token_fields["signature"] = bytes.fromhex(raw_signature)
                session_token = TokenPayload(**token_fields)
            request = CapabilityRequest(
                capability_name=capability_name,
                session_token=session_token,
                parameters=parameters,
                context=context,
            )
            return await self._kernel.invoke_capability(request)

        while instance.current_step_index < len(definition.steps):
            if instance.state not in (WorkflowState.RUNNING, WorkflowState.APPROVED, WorkflowState.READY):
                self.logger.info("Workflow '%s' execution loop paused in state %s", instance.id, instance.state.value)
                break

            step = definition.steps[instance.current_step_index]
            instance.current_step_id = step.id

            # If coming from APPROVED or READY state, transition to RUNNING
            if instance.state in (WorkflowState.APPROVED, WorkflowState.READY):
                WorkflowStateMachine.transition(instance, WorkflowState.RUNNING)
                if self._workflow_store:
                    await self._workflow_store.update_instance(instance)

            # Record step run start in persistent ledger
            run_id: str | None = None
            if self._workflow_store:
                run_id = await self._workflow_store.record_step_run_start(instance.id, step.id, attempt=1)

            res = await self._evaluator.execute_step(instance, step, capability_dispatcher=dispatch)
            step_results.append(res)
            self._metrics["steps_executed"] += 1

            if not res.success and not step.on_failure_continue:
                self.logger.error(
                    "Step '%s' failed in workflow '%s'. Initiating failure & rollback.",
                    step.id,
                    instance.id,
                )
                WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
                self._metrics["workflows_failed"] += 1

                # Execute LIFO compensation stack rollback
                comp_results = await self._evaluator.execute_compensation_stack(
                    instance, capability_dispatcher=dispatch
                )
                self._metrics["compensations_executed"] += len(comp_results)

                if self._workflow_store:
                    await self._workflow_store.record_step_complete_and_update_state(
                        run_id=run_id,
                        instance=instance,
                        output=res.output,
                        error=res.error,
                    )

                await self._persist_instance_snapshot(instance)
                await self._publish_event(
                    "workflow.failed",
                    {"instance_id": str(instance.id), "error": res.error, "tenant_id": instance.tenant_id},
                )
                await self._publish_event(
                    "workflow.rollback",
                    {"instance_id": str(instance.id), "compensations": comp_results, "tenant_id": instance.tenant_id},
                )

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

            if instance.state.value == WorkflowState.WAITING.value:
                self.logger.info("Workflow '%s' paused at step '%s' waiting for approval.", instance.id, step.id)
                self._metrics["approvals_requested"] += 1
                if self._workflow_store:
                    await self._workflow_store.record_step_complete_and_update_state(
                        run_id=run_id,
                        instance=instance,
                        output=res.output,
                        error=res.error,
                    )
                await self._persist_instance_snapshot(instance)
                await self._publish_event(
                    "workflow.waiting",
                    {"instance_id": str(instance.id), "step_id": step.id, "tenant_id": instance.tenant_id},
                )

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
            if self._workflow_store:
                await self._workflow_store.record_step_complete_and_advance_instance(
                    run_id=run_id,
                    instance=instance,
                    output=res.output,
                    error=res.error,
                )

        # Check for workflow completion
        if instance.current_step_index >= len(definition.steps) and instance.state == WorkflowState.RUNNING:
            WorkflowStateMachine.transition(instance, WorkflowState.COMPLETED)
            self._metrics["workflows_completed"] += 1
            if self._workflow_store:
                await self._workflow_store.update_instance(instance)
            await self._persist_instance_snapshot(instance)
            await self._publish_event(
                "workflow.completed",
                {"instance_id": str(instance.id), "tenant_id": instance.tenant_id},
            )

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

    async def pause_workflow(self, instance_id: UUID | str, tenant_id: str | None = None) -> WorkflowInstance:
        """Pause a running workflow instance."""
        target_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        instance = await self.get_instance_durable(target_id, tenant_id=tenant_id)
        if instance.state != WorkflowState.RUNNING:
            raise WorkflowStateError(f"Cannot pause workflow in state '{instance.state.value}'.")

        instance.status = WorkflowStatus.PAUSED
        if self._workflow_store:
            await self._workflow_store.update_instance(instance)

        await self._persist_instance_snapshot(instance)
        await self._publish_event(
            "workflow.paused",
            {"instance_id": str(instance.id), "tenant_id": instance.tenant_id},
        )
        return instance

    async def resume_workflow(self, instance_id: UUID | str, tenant_id: str | None = None) -> WorkflowInstance:
        """Resume a paused or approved workflow instance. Terminal states are never resumed."""
        target_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        instance = await self.get_instance_durable(target_id, tenant_id=tenant_id)

        if instance.state not in (WorkflowState.WAITING, WorkflowState.APPROVED, WorkflowState.RUNNING):
            raise WorkflowStateError(f"Cannot resume workflow in state '{instance.state.value}'.")

        if instance.state == WorkflowState.WAITING:
            WorkflowStateMachine.transition(instance, WorkflowState.APPROVED)

        instance.status = WorkflowStatus.RUNNING
        definition = await self.get_definition_async(instance.definition_id, tenant_id=instance.tenant_id)

        if self._workflow_store:
            await self._workflow_store.update_instance(instance)

        await self._persist_instance_snapshot(instance)
        await self._publish_event(
            "workflow.resumed",
            {"instance_id": str(instance.id), "tenant_id": instance.tenant_id},
        )

        # Continue step execution
        task = asyncio.create_task(self._run_instance_steps(instance, definition))
        self._running_tasks[instance.id] = task
        return instance

    async def cancel_workflow(
        self, instance_id: UUID | str, reason: str = "", tenant_id: str | None = None
    ) -> WorkflowInstance:
        """Cancel a running or waiting workflow instance. Terminal states cannot be cancelled."""
        target_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        instance = await self.get_instance_durable(target_id, tenant_id=tenant_id)

        if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED):
            raise WorkflowStateError(
                f"Cannot cancel workflow instance '{instance.id}' already in terminal state '{instance.state.value}'."
            )

        # Cancel running task if active
        active_task = self._running_tasks.pop(instance.id, None)
        if active_task and not active_task.done():
            active_task.cancel()

        WorkflowStateMachine.transition(instance, WorkflowState.CANCELLED)
        self._metrics["workflows_cancelled"] += 1

        if self._workflow_store:
            await self._workflow_store.update_instance(instance)

        await self._persist_instance_snapshot(instance)
        await self._publish_event(
            "workflow.cancelled",
            {"instance_id": str(instance.id), "reason": reason, "tenant_id": instance.tenant_id},
        )
        return instance

    async def _dispatch_capability(
        self,
        capability_name: str,
        parameters: dict[str, Any],
        context: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Helper to invoke a capability through the Kernel capability dispatch boundary."""
        if not self._kernel:
            return None
        session_token: TokenPayload | None = None
        if "session_token" in context and isinstance(context["session_token"], dict):
            token_fields = dict(context["session_token"])
            raw_signature = token_fields.get("signature")
            if isinstance(raw_signature, str):
                token_fields["signature"] = bytes.fromhex(raw_signature)
            session_token = TokenPayload(**token_fields)
        request = CapabilityRequest(
            capability_name=capability_name,
            session_token=session_token,
            parameters=parameters,
            context=context,
        )
        return await self._kernel.invoke_capability(request)

    async def _advance_workflow_after_approval(
        self, ticket: ApprovalRequest, decision: ApprovalDecision, tenant_id: str | None = None
    ) -> WorkflowInstance:
        """Advance or fail a workflow instance following an approval decision (Article 8 authority)."""
        if not ticket.instance_id:
            raise WorkflowApprovalError(f"Approval ticket '{ticket.id}' is not linked to a workflow instance.")

        instance = await self.get_instance_durable(ticket.instance_id, tenant_id=tenant_id)

        if decision.decision == ApprovalState.APPROVED:
            await self._publish_event(
                "workflow.approved",
                {
                    "instance_id": str(instance.id),
                    "approver_id": decision.approver_id,
                    "tenant_id": instance.tenant_id,
                },
            )
            # Advance step index past the approval step and persist
            instance.current_step_index += 1
            if self._workflow_store:
                await self._workflow_store.update_instance(instance)
            return await self.resume_workflow(instance.id, tenant_id=tenant_id)

        # Rejected decision
        WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
        self._metrics["workflows_failed"] += 1
        if self._workflow_store:
            await self._workflow_store.update_instance(instance)
        await self._persist_instance_snapshot(instance)
        await self._publish_event(
            "workflow.failed",
            {"instance_id": str(instance.id), "reason": "Approval rejected", "tenant_id": instance.tenant_id},
        )
        if instance.compensation_stack:
            await self._evaluator.execute_compensation_stack(
                instance=instance,
                capability_dispatcher=self._dispatch_capability,
            )
        return instance

    async def submit_approval_decision(
        self, decision: ApprovalDecision, tenant_id: str | None = None
    ) -> WorkflowInstance:
        """Submit an approval decision and resume workflow if approved (legacy capability handler)."""
        tid = tenant_id or decision.tenant_id or "default"
        ticket = await self._approval_manager.submit_decision(decision, tenant_id=tid)
        return await self._advance_workflow_after_approval(ticket, decision, tenant_id=tid)

    async def create_approval_request(
        self,
        required_role: str,
        instance_id: str | UUID | None = None,
        step_id: str | None = None,
        timeout_seconds: int | None = None,
        context_snapshot: dict[str, Any] | None = None,
        signature_required: bool = False,
        tenant_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Create an approval request ticket (kortex.workflow.approval.create capability)."""
        tid = tenant_id or "default"
        req = await self._approval_manager.create_request(
            instance_id=instance_id,
            step_id=step_id,
            required_role=required_role,
            tenant_id=tid,
            timeout_seconds=timeout_seconds,
            context_snapshot=context_snapshot,
            signature_required=signature_required,
        )
        return {
            "id": str(req.id),
            "tenant_id": req.tenant_id,
            "instance_id": str(req.instance_id) if req.instance_id else None,
            "step_id": req.step_id,
            "required_role": req.required_role,
            "state": req.state.value if hasattr(req.state, "value") else str(req.state),
            "timeout_at": req.timeout_at.isoformat() if req.timeout_at else None,
            "signature_required": req.signature_required,
        }

    async def list_approval_requests(
        self,
        tenant_id: str | None = None,
        role_filter: str | None = None,
        state_filter: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        """List approval request tickets for tenant (kortex.workflow.approval.list capability)."""
        tid = tenant_id or "default"
        requests: list[ApprovalRequest]
        if hasattr(self._approval_manager, "list_requests"):
            requests = await self._approval_manager.list_requests(
                tenant_id=tid, role_filter=role_filter, state_filter=state_filter
            )
        elif hasattr(self._approval_manager, "list_pending_requests"):
            mgr = cast(Any, self._approval_manager)
            requests = await mgr.list_pending_requests(role_filter=role_filter)
        else:
            requests = []
        return [
            {
                "id": str(r.id),
                "tenant_id": r.tenant_id,
                "instance_id": str(r.instance_id) if r.instance_id else None,
                "step_id": r.step_id,
                "required_role": r.required_role,
                "state": r.state.value if hasattr(r.state, "value") else str(r.state),
                "timeout_at": r.timeout_at.isoformat() if r.timeout_at else None,
                "signature_required": r.signature_required,
            }
            for r in requests
        ]

    async def get_approval_request(
        self,
        request_id: str | UUID,
        tenant_id: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Get approval request ticket by ID (kortex.workflow.approval.get capability)."""
        tid = tenant_id or "default"
        if hasattr(self._approval_manager, "get_request"):
            req = await self._approval_manager.get_request(request_id, tenant_id=tid)
        else:
            req = None
        if req is None:
            raise ResourceNotFoundError(f"Approval request '{request_id}' not found for tenant '{tid}'.")
        return {
            "id": str(req.id),
            "tenant_id": req.tenant_id,
            "instance_id": str(req.instance_id) if req.instance_id else None,
            "step_id": req.step_id,
            "required_role": req.required_role,
            "state": req.state.value if hasattr(req.state, "value") else str(req.state),
            "timeout_at": req.timeout_at.isoformat() if req.timeout_at else None,
            "context_snapshot": req.context_snapshot,
            "signature_required": req.signature_required,
        }

    async def decide_approval_request(
        self,
        request_id: str | UUID,
        decision: str | ApprovalState,
        approver_id: str,
        reason: str | None = None,
        signature_hex: str | None = None,
        public_key_hex: str | None = None,
        decision_data: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        principal: SecurityPrincipal | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Submit a decision for an approval ticket (kortex.workflow.approval.decide capability)."""
        tid = tenant_id or "default"
        dec_state: ApprovalState
        if isinstance(decision, ApprovalState):
            dec_state = decision
        elif isinstance(decision, str) and decision in ApprovalState._value2member_map_:
            dec_state = ApprovalState(decision)
        else:
            raise WorkflowValidationError(f"Invalid approval decision state: '{decision}'.")

        # Resolve principal from session_token if not directly provided
        if principal is None and "session_token" in kwargs:
            raw_token = kwargs["session_token"]
            sec_eng = None
            if self._kernel:
                try:
                    sec_eng = self._kernel.get_engine("security")
                except Exception as err:
                    self.logger.debug("Security engine lookup in decide_approval_request: %s", err)
            if sec_eng is not None and hasattr(sec_eng, "authentication_manager"):
                try:
                    from kortex.engines.security.models import TokenPayload

                    if isinstance(raw_token, TokenPayload):
                        principal = await sec_eng.authentication_manager.verify_token(raw_token)
                    elif isinstance(raw_token, dict):
                        tok_dict = dict(raw_token)
                        raw_sig = tok_dict.get("signature")
                        if isinstance(raw_sig, str):
                            tok_dict["signature"] = bytes.fromhex(raw_sig)
                        tok = TokenPayload(**tok_dict)
                        principal = await sec_eng.authentication_manager.verify_token(tok)
                except Exception as err:
                    self.logger.debug("Could not verify session token in decide_approval_request: %s", err)

        decision_obj = ApprovalDecision(
            request_id=UUID(str(request_id)),
            tenant_id=tid,
            approver_id=approver_id,
            decision=dec_state,
            reason=reason,
            signature_hex=signature_hex,
            public_key_hex=public_key_hex,
            decision_data=decision_data or {},
        )
        updated_ticket = await self._approval_manager.submit_decision(
            decision=decision_obj,
            principal=principal,
            tenant_id=tid,
        )

        if updated_ticket.instance_id:
            await self._advance_workflow_after_approval(updated_ticket, decision_obj, tenant_id=tid)

        ticket_state_val = (
            updated_ticket.state.value
            if hasattr(updated_ticket.state, "value")
            else str(updated_ticket.state)
        )
        return {
            "id": str(updated_ticket.id),
            "state": ticket_state_val,
            "decision": dec_state.value if hasattr(dec_state, "value") else str(dec_state),
            "approver_id": approver_id,
            "tenant_id": tid,
        }

    async def delegate_approval_role(
        self,
        delegator_id: str,
        delegatee_id: str,
        role: str,
        valid_from: str | datetime.datetime,
        valid_until: str | datetime.datetime,
        tenant_id: str | None = None,
        principal: SecurityPrincipal | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Delegate an approval role to another principal (kortex.workflow.approval.delegate capability)."""
        tid = tenant_id or "default"
        dt_from = (
            datetime.datetime.fromisoformat(valid_from) if isinstance(valid_from, str) else valid_from
        )
        dt_until = (
            datetime.datetime.fromisoformat(valid_until) if isinstance(valid_until, str) else valid_until
        )

        # Resolve principal from session_token if not directly provided
        if principal is None and "session_token" in kwargs:
            raw_token = kwargs["session_token"]
            sec_eng = None
            if self._kernel:
                try:
                    sec_eng = self._kernel.get_engine("security")
                except Exception as err:
                    self.logger.debug("Security engine lookup in delegate_approval_role: %s", err)
            if sec_eng is not None and hasattr(sec_eng, "authentication_manager"):
                try:
                    from kortex.engines.security.models import TokenPayload

                    if isinstance(raw_token, TokenPayload):
                        principal = await sec_eng.authentication_manager.verify_token(raw_token)
                    elif isinstance(raw_token, dict):
                        tok_dict = dict(raw_token)
                        raw_sig = tok_dict.get("signature")
                        if isinstance(raw_sig, str):
                            tok_dict["signature"] = bytes.fromhex(raw_sig)
                        tok = TokenPayload(**tok_dict)
                        principal = await sec_eng.authentication_manager.verify_token(tok)
                except Exception as err:
                    self.logger.debug("Could not verify session token in delegate_approval_role: %s", err)

        if hasattr(self._approval_manager, "create_delegation"):
            delegation = await self._approval_manager.create_delegation(
                delegator_id=delegator_id,
                delegatee_id=delegatee_id,
                role=role,
                valid_from=dt_from,
                valid_until=dt_until,
                tenant_id=tid,
                principal=principal,
            )
            return {
                "id": str(delegation.id),
                "tenant_id": delegation.tenant_id,
                "delegator_id": delegation.delegator_id,
                "delegatee_id": delegation.delegatee_id,
                "role": delegation.role,
                "valid_from": delegation.valid_from.isoformat(),
                "valid_until": delegation.valid_until.isoformat(),
                "is_active": delegation.is_active,
            }
        raise WorkflowApprovalError("Active approval manager does not support role delegations.")

    async def sweep_expired_approvals(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        """Sweep expired pending approval requests and fail associated workflows with compensation."""
        if not hasattr(self._approval_manager, "sweep_expired_requests"):
            return []
        mgr = cast(Any, self._approval_manager)
        expired_tickets: list[ApprovalRequest] = await mgr.sweep_expired_requests(tenant_id=tenant_id)
        for ticket in expired_tickets:
            if ticket.instance_id:
                try:
                    instance = await self.get_instance_durable(ticket.instance_id, tenant_id=ticket.tenant_id)
                    if instance.state == WorkflowState.WAITING:
                        WorkflowStateMachine.transition(instance, WorkflowState.FAILED)
                        instance.status = WorkflowStatus.FAILED
                        self._metrics["workflows_failed"] += 1
                        if self._workflow_store:
                            await self._workflow_store.update_instance(instance)
                        await self._persist_instance_snapshot(instance)
                        await self._publish_event(
                            "workflow.failed",
                            {
                                "instance_id": str(instance.id),
                                "reason": "Approval timed out",
                                "tenant_id": instance.tenant_id,
                            },
                        )
                        if instance.compensation_stack:
                            await self._evaluator.execute_compensation_stack(
                                instance=instance,
                                capability_dispatcher=self._dispatch_capability,
                            )
                except Exception as e:
                    self.logger.error("Failed to fail expired workflow '%s': %s", ticket.instance_id, e)
        return expired_tickets

    async def execute_workflow(
        self,
        definition: WorkflowDefinition,
        initial_context: dict[str, Any] | None = None,
        tenant_id: str = "default",
    ) -> WorkflowResult:
        """Synchronously execute a workflow definition to completion."""
        def_id = self.register_definition(definition, tenant_id=tenant_id)
        instance = await self.start_workflow(def_id, initial_context, tenant_id=tenant_id)
        return await self._run_instance_steps(instance, definition)

    def get_instance(self, instance_id: UUID | str, tenant_id: str | None = None) -> WorkflowInstance:
        """Retrieve a WorkflowInstance from local memory cache, verifying tenant if provided."""
        target_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        if target_id not in self._instances:
            raise ResourceNotFoundError(f"Workflow instance '{target_id}' not found.")
        instance = self._instances[target_id]
        if tenant_id and instance.tenant_id != tenant_id:
            raise ResourceNotFoundError(f"Workflow instance '{target_id}' not found.")
        return instance

    async def get_instance_durable(
        self, instance_id: UUID | str, tenant_id: str | None = None
    ) -> WorkflowInstance:
        """Retrieve a WorkflowInstance with read-through to persistent store and tenant isolation."""
        target_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        if target_id in self._instances:
            cached_instance = self._instances[target_id]
            if tenant_id and cached_instance.tenant_id != tenant_id:
                raise ResourceNotFoundError(f"Workflow instance '{target_id}' not found.")
            return cached_instance

        if self._workflow_store:
            stored_instance = await self._workflow_store.get_instance(target_id, tenant_id=tenant_id)
            if stored_instance is not None:
                self._instances[stored_instance.id] = stored_instance
                return stored_instance

        raise ResourceNotFoundError(f"Workflow instance '{target_id}' not found.")

    def list_instances(self, tenant_id: str | None = None) -> list[WorkflowInstance]:
        """List all active WorkflowInstance objects in memory, optionally filtered by tenant."""
        if tenant_id:
            return [inst for inst in self._instances.values() if inst.tenant_id == tenant_id]
        return list(self._instances.values())

    async def list_instances_durable(
        self, tenant_id: str | None = None, state: str | None = None
    ) -> list[WorkflowInstance]:
        """List WorkflowInstance objects from durable storage matching tenant and state filters."""
        state_filter = WorkflowState(state) if state and state in WorkflowState._value2member_map_ else None
        if self._workflow_store:
            instances = await self._workflow_store.list_instances(tenant_id=tenant_id, state_filter=state_filter)
            for inst in instances:
                self._instances[inst.id] = inst
            return instances
        # Fallback to in-memory list
        filtered = list(self._instances.values())
        if tenant_id:
            filtered = [i for i in filtered if i.tenant_id == tenant_id]
        if state_filter:
            filtered = [i for i in filtered if i.state == state_filter]
        return filtered

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> dict[str, Any]:
        """Return diagnostic health check report."""
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "active_instances": len(self._instances),
            "definitions_loaded": len(self._definitions),
        }

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> dict[str, Any]:
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

    def capabilities(self) -> list[str]:
        """Return list of registered capability strings."""
        return list(self._registered_capabilities)
