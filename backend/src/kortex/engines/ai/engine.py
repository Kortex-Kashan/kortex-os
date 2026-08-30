"""KORTEX OS AI Orchestration Engine — Core Facade (`AIOrchestrationEngine`).

Governed by the ratified Milestone 8 specification:
docs/architecture/ai_engine_m8_facade_and_integration_spec.md

This module implements `AIOrchestrationEngine`, extending `BaseEngine` and conforming
to `IEngineDiagnostics` and `IAIOrchestrationEngine`. It serves as the single public entry
point orchestrating ProviderRegistry, ModelRouter, AIMemoryManager, ContextComposer,
AIToolInvoker, AgentOrchestrator, and AIDiagnostics.

Invariants:
- Pure Facade: Contains zero business logic, zero routing math, zero prompt parsing,
  zero SQL, and zero loop detection.
- Decoupled from Kernel: Interacts with Kernel strictly via `IKernelBridge`.
- Decoupled from Security: All authorization decisions are delegated to Security Engine.
- Context Single-Point Rule: Composes single-turn generation context in the facade,
  and delegates multi-step agent step composition to `EngineAgentContextPort`.
- Non-blocking Event Publishing: Event bus degradation never fails AI generation turns.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.ai.agent import (
    AgentExecutionResult,
    AgentOrchestrator,
    AgentStatus,
    AgentStep,
    AgentTask,
    IAgentContextPort,
    IApprovalPolicy,
    ILLMExecutionPort,
    PersistedAgentTaskRecord,
    ResumeToken,
)
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.events import (
    AIBaseEvent,
)
from kortex.engines.ai.exceptions import (
    AIGovernanceQuotaExceededError,
    AIProviderTimeoutError,
    ConversationStoreError,
    NoRoutableProviderError,
)
from kortex.engines.ai.governance import (
    AIGovernanceManager,
    AIGovernancePolicy,
    AITenantQuota,
    ContentSafetyGuardrail,
    ToolGovernanceEvaluator,
)
from kortex.engines.ai.interfaces import (
    IEngineDiagnostics,
    IKernelBridge,
    ToolAuthorizer,
)
from kortex.engines.ai.memory import (
    AIMemoryManager,
    InMemoryConversationStore,
    require_identifier,
    sanitize_context_content,
)
from kortex.engines.ai.models import (
    AIModelSummary,
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
    TokenUsage,
)
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.resilience import ProviderFallbackChain, RetryPolicy
from kortex.engines.ai.router import ModelRouter, RoutingContext
from kortex.engines.ai.telemetry import AITelemetryEmitter
from kortex.engines.ai.throttling import TenantConcurrencyThrottler
from kortex.engines.ai.tools import (
    AIToolInvoker,
    InMemoryToolExecutionPort,
    IToolExecutionPort,
    ToolCall,
    ToolExecutionStatus,
    ToolRegistry,
    ToolResult,
    scrub_secrets_from_text,
)

logger = logging.getLogger("kortex.engines.ai")

DEFAULT_GENERATION_TIMEOUT_SECONDS: Final[float] = 60.0


# ---------------------------------------------------------------------------
# Production Port Adapters
# ---------------------------------------------------------------------------

# A single attempt per candidate: fallback breadth (trying the next eligible
# provider) is this helper's own concern. Per-provider retry/backoff/circuit
# state is owned by whatever `ResilientAIProvider` the registry already holds
# for that provider (see bootstrap.py) — attempting more than once per
# candidate here would double that policy for already-wrapped providers and
# introduce unwanted retry latency for raw/unwrapped ones.
_FALLBACK_ATTEMPT_POLICY: Final[RetryPolicy] = RetryPolicy(max_attempts=1)


async def _generate_with_fallback(
    router: ModelRouter,
    registry: ProviderRegistry,
    request: LLMRequest,
    context: dict[str, Any],
    telemetry: object | None = None,
) -> LLMResponse:
    """Enumerate every eligible provider and attempt generation with automatic failover.

    Satisfies the M9 architecture spec's Systematic Failure Recovery Matrix
    (Attack 6, row 1: "Primary LLM Unreachable/Crash... route to secondary
    local/cloud candidate"), which `ModelRouter.select_model` alone cannot
    provide since it returns only the single best-ranked candidate.

    Shared by `RouterLLMExecutionPort.generate_step` (agent reasoning steps)
    and `AIOrchestrationEngine.generate_response` (direct generation) so
    fallback behavior is identical and defined in exactly one place.
    """
    candidates = await router.select_candidates(request, context)
    if not candidates:
        raise NoRoutableProviderError("No routable AI provider matched the routing constraints.")
    providers = [registry.get(metadata.provider_id) for metadata in candidates]
    chain = ProviderFallbackChain(
        providers=providers,
        retry_policy=_FALLBACK_ATTEMPT_POLICY,
        telemetry=telemetry,
    )
    return await chain.generate_text(request)


class RouterLLMExecutionPort(ILLMExecutionPort):
    """Production adapter for `ILLMExecutionPort` using `ModelRouter` and `ProviderRegistry`."""

    def __init__(
        self,
        router: ModelRouter,
        registry: ProviderRegistry,
        default_routing_context: RoutingContext | None = None,
        telemetry: object | None = None,
    ) -> None:
        self._router = router
        self._registry = registry
        self._default_context = default_routing_context or RoutingContext(allow_cloud=False)
        self._telemetry = telemetry

    async def generate_step(self, request: LLMRequest) -> LLMResponse:
        """Route to eligible providers and execute a single reasoning step, with failover."""
        context_dict = self._default_context.model_dump()
        return await _generate_with_fallback(
            self._router, self._registry, request, context_dict, telemetry=self._telemetry
        )


class EngineAgentContextPort(IAgentContextPort):
    """Production adapter for `IAgentContextPort` using `ContextComposer` and `AIMemoryManager`."""

    def __init__(
        self,
        composer: ContextComposer,
        memory_manager: AIMemoryManager | None = None,
        max_step_history_window: int = 10,
        max_step_result_chars: int = 2000,
    ) -> None:
        self._composer = composer
        self._memory_manager = memory_manager
        self._max_step_history_window = max(1, max_step_history_window)
        self._max_step_result_chars = max(100, max_step_result_chars)

    async def build_step_context(
        self,
        task: AgentTask,
        steps: list[AgentStep],
    ) -> LLMRequest:
        """Assemble an `LLMRequest` for the next reasoning step with prompt, RAG, and history."""
        # 1. Slide window over the most recent steps
        windowed_steps = (
            steps[-self._max_step_history_window:]
            if len(steps) > self._max_step_history_window
            else steps
        )

        history_lines: list[str] = []
        if windowed_steps:
            history_lines.append("\nExecution History:")
            for s in windowed_steps:
                history_lines.append(f"Step {s.step_number}:")
                if s.thought:
                    sanitized_thought = sanitize_context_content(
                        scrub_secrets_from_text(s.thought)
                    )
                    history_lines.append(f"  Thought: {sanitized_thought}")
                for tc in s.tool_calls:
                    tc_args_str = json.dumps(tc.arguments, default=str)
                    sanitized_args = sanitize_context_content(
                        scrub_secrets_from_text(tc_args_str)
                    )
                    history_lines.append(f"  Tool Call: {tc.tool_name}({sanitized_args})")
                for tr in s.tool_results:
                    raw_out = str(tr.output) if tr.output is not None else "null"
                    if len(raw_out) > self._max_step_result_chars:
                        raw_out = (
                            raw_out[: self._max_step_result_chars]
                            + f" [TRUNCATED at {self._max_step_result_chars} chars]"
                        )
                    scrubbed_out = scrub_secrets_from_text(raw_out)
                    sanitized_out = sanitize_context_content(scrubbed_out)
                    history_lines.append(
                        f"  Tool Result: status={tr.status.value}, output={sanitized_out}"
                    )
                if s.response_text:
                    sanitized_resp = sanitize_context_content(
                        scrub_secrets_from_text(s.response_text)
                    )
                    history_lines.append(f"  Response: {sanitized_resp}")

        history_block = "\n".join(history_lines)
        full_prompt = (
            f"Goal: {task.goal}\n{history_block}" if history_block else f"Goal: {task.goal}"
        )

        raw_request = LLMRequest(
            request_id=f"req-{uuid.uuid4().hex}",
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            conversation_id=task.conversation_id,
            prompt=full_prompt,
            system_instruction=task.system_instruction,
        )

        # ContextComposer handles RAG retrieval and safe marker injection
        return await self._composer.compose(raw_request)


class KernelSecurityApprovalPolicy(IApprovalPolicy):
    """Production adapter for `IApprovalPolicy` delegating policy to Security Engine or mutations check."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        security_authorizer: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._security_authorizer = security_authorizer

    async def requires_approval(
        self,
        task: AgentTask,
        proposed_calls: list[ToolCall],
    ) -> bool:
        """Evaluate if proposed tool calls require human approval."""
        if not task.require_human_approval_for_mutations:
            return False

        if self._tool_registry is not None:
            for call in proposed_calls:
                try:
                    tool_def = self._tool_registry.get_tool(call.tool_name)
                    if tool_def.is_mutation:
                        return True
                except Exception:
                    # Unknown tool default: assume mutation for safety
                    return True
        return False


class KernelToolExecutionPort(IToolExecutionPort):
    """Production adapter for `IToolExecutionPort` dispatching to `IKernelBridge`.

    M6.2-2: previously never supplied a `session_token` to
    `IKernelBridge.invoke_capability`, so every AI tool call against an
    authenticated capability failed closed with `AuthenticationError` —
    silently misclassified downstream as a generic `EXECUTION_ERROR`
    (`AuthenticationError` is not a subclass of this package's own
    `ToolAuthorizationError`). `ai_identity`, when supplied, resolves the
    AI system principal's own session token for the target tenant and
    attaches it to every capability invocation this port makes — the sole
    boundary crossing from the AI engine into the Kernel, and therefore the
    correct single place to inject a static system identity (there is
    nothing further upstream — `AgentTask`/`ToolCall`/`AIToolInvoker` never
    carried a per-call caller identity to begin with; the AI has exactly
    one identity, not a passthrough of someone else's).
    """

    def __init__(
        self,
        kernel_bridge: IKernelBridge,
        ai_identity: object | None = None,
    ) -> None:
        self._kernel_bridge = kernel_bridge
        self._ai_identity = ai_identity

    async def execute_tool(
        self,
        tenant_id: str,
        capability_name: str,
        arguments: dict[str, object],
        authorizer: ToolAuthorizer | None = None,
        correlation_id: str | None = None,
    ) -> object:
        """Execute capability handler through Kernel enforcement boundary."""
        require_identifier(tenant_id, "tenant_id")
        if authorizer is not None:
            is_allowed = await authorizer(capability_name, arguments)
            if not is_allowed:
                from kortex.engines.ai.exceptions import ToolAuthorizationError
                raise ToolAuthorizationError(f"Authorization denied for capability '{capability_name}'.")

        session_token = None
        if self._ai_identity is not None:
            session_token = await self._ai_identity.get_session_token(tenant_id)

        return await self._kernel_bridge.invoke_capability(
            name=capability_name,
            arguments=arguments,
            tenant_id=tenant_id,
            request_id=correlation_id,
            session_token=session_token,
        )


# ---------------------------------------------------------------------------
# Engine Facade
# ---------------------------------------------------------------------------


class AIOrchestrationEngine(BaseEngine, IEngineDiagnostics):
    """Core runtime facade and orchestrator for KORTEX AI Orchestration Engine."""

    def __init__(
        self,
        provider_registry: ProviderRegistry | None = None,
        model_router: ModelRouter | None = None,
        memory_manager: AIMemoryManager | None = None,
        context_composer: ContextComposer | None = None,
        tool_invoker: AIToolInvoker | None = None,
        tool_registry: ToolRegistry | None = None,
        agent_orchestrator: AgentOrchestrator | None = None,
        diagnostics: AIDiagnostics | None = None,
        telemetry: AITelemetryEmitter | None = None,
        throttler: TenantConcurrencyThrottler | None = None,
        governance_manager: AIGovernanceManager | None = None,
        default_generation_timeout_seconds: float = DEFAULT_GENERATION_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize AIOrchestrationEngine with optional component injections.

        If components are omitted, sensible default subsystem instances are created.
        """
        super().__init__()
        self._default_generation_timeout_seconds = default_generation_timeout_seconds
        self._throttler = throttler if throttler is not None else TenantConcurrencyThrottler()
        self._provider_registry = (
            provider_registry if provider_registry is not None else ProviderRegistry()
        )
        self._model_router = (
            model_router if model_router is not None else ModelRouter(registry=self._provider_registry)
        )
        self._memory_manager = (
            memory_manager
            if memory_manager is not None
            else AIMemoryManager(store=InMemoryConversationStore())
        )
        self._tool_registry = (
            tool_registry if tool_registry is not None else ToolRegistry()
        )
        self._tool_invoker = (
            tool_invoker
            if tool_invoker is not None
            else AIToolInvoker(
                registry=self._tool_registry,
                execution_port=InMemoryToolExecutionPort(),
            )
        )
        self._context_composer = (
            context_composer
            if context_composer is not None
            else ContextComposer(
                memory=self._memory_manager,
                pipeline=PromptPipeline(),
            )
        )
        self._diagnostics = (
            diagnostics
            if diagnostics is not None
            else AIDiagnostics(
                provider_registry=self._provider_registry,
                model_router=self._model_router,
                memory_manager=self._memory_manager,
                tool_registry=self._tool_registry,
            )
        )
        self._telemetry = (
            telemetry
            if telemetry is not None
            else AITelemetryEmitter(diagnostics=self._diagnostics)
        )
        self._governance_manager = (
            governance_manager
            if governance_manager is not None
            else AIGovernanceManager(tool_registry=self._tool_registry)
        )

        # Wire AgentOrchestrator with production adapters
        if agent_orchestrator is not None:
            self._agent_orchestrator = agent_orchestrator
        else:
            llm_port = RouterLLMExecutionPort(
                router=self._model_router,
                registry=self._provider_registry,
                telemetry=self._telemetry,
            )
            ctx_port = EngineAgentContextPort(
                composer=self._context_composer,
                memory_manager=self._memory_manager,
            )
            approval_policy = self._governance_manager.create_approval_policy()
            self._agent_orchestrator = AgentOrchestrator(
                tool_invoker=self._tool_invoker,
                llm_port=llm_port,
                context_port=ctx_port,
                approval_policy=approval_policy,
                telemetry=self._telemetry,
            )

        self._kernel: IKernelBridge | None = None


    @property
    def name(self) -> str:
        """Unique engine identifier string."""
        return "ai"

    @property
    def dependencies(self) -> list[str]:
        """Prerequisite foundation engines for Kernel boot sequence."""
        return ["configuration", "registry", "event", "storage"]

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Access the provider registry subsystem."""
        return self._provider_registry

    @property
    def model_router(self) -> ModelRouter:
        """Access the model router subsystem."""
        return self._model_router

    @property
    def memory_manager(self) -> AIMemoryManager:
        """Access the conversation memory manager subsystem."""
        return self._memory_manager

    @property
    def tool_registry(self) -> ToolRegistry:
        """Access the tool registry subsystem."""
        return self._tool_registry

    @property
    def tool_invoker(self) -> AIToolInvoker:
        """Access the tool invoker subsystem."""
        return self._tool_invoker

    @property
    def context_composer(self) -> ContextComposer:
        """Access the context composer subsystem."""
        return self._context_composer

    @property
    def agent_orchestrator(self) -> AgentOrchestrator:
        """Access the agent orchestrator subsystem."""
        return self._agent_orchestrator

    @property
    def throttler(self) -> TenantConcurrencyThrottler:
        """Access the tenant concurrency throttler subsystem."""
        return self._throttler

    @property
    def diagnostics_collector(self) -> AIDiagnostics:
        """Access the internal diagnostics collector."""
        return self._diagnostics

    @property
    def telemetry(self) -> AITelemetryEmitter:
        """Access the telemetry subsystem."""
        return self._telemetry

    @property
    def governance_manager(self) -> AIGovernanceManager:
        """Access the AI governance, guardrails, and quota subsystem."""
        return self._governance_manager

    # -- BaseEngine Lifecycle Implementations ---------------------------------

    async def initialize(self, kernel: IKernelBridge) -> None:  # type: ignore[override]
        """Initialize engine resources and register canonical capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX AI Orchestration Engine...")

        try:
            self._kernel = kernel
            if self._telemetry._kernel_bridge is None:
                self._telemetry._kernel_bridge = kernel

            # M6.2-4: react to durable approval decisions for AI-originated
            # tickets so an approved/rejected mutation actually resumes or
            # cancels the paused agent task that proposed it.
            if hasattr(kernel, "subscribe_event"):
                kernel.subscribe_event(
                    topic="workflow.approval.decided",
                    handler=self._on_approval_decided,
                    subscriber_name=self.name,
                )

            # Register canonical capabilities with the Kernel Registry
            kernel.register_capability(
                name="kortex.ai.response.generate",
                description="Generate an LLM response with context composition and model routing",
                provider=self.name,
                handler=self.generate_response,
                required_permissions=["ai:generate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.orchestrate",
                description="Orchestrate a bounded multi-step agent reasoning workflow",
                provider=self.name,
                handler=self.orchestrate_agent,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.resume",
                description="Resume a paused agent reasoning workflow with verified token",
                provider=self.name,
                handler=self.resume_agent,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.tool.invoke",
                description="Invoke an authorized AI tool capability",
                provider=self.name,
                handler=self.invoke_tool,
                required_permissions=["ai:execute"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.provider.register",
                description="Register an AI provider with the engine registry",
                provider=self.name,
                handler=self.register_provider,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.provider.list",
                description="List metadata of all registered AI providers",
                provider=self.name,
                handler=self.list_providers,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.model.list",
                description="List models declared across all registered AI providers",
                provider=self.name,
                handler=self.list_models,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.cancel",
                description="Cancel an active or paused agent reasoning task",
                provider=self.name,
                handler=self.cancel_agent_task,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.status",
                description="Retrieve the persisted status of an agent reasoning task",
                provider=self.name,
                handler=self.get_agent_task,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.list",
                description="List agent reasoning tasks for a tenant, optionally filtered by status",
                provider=self.name,
                handler=self.list_agent_tasks,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )

            # AI Governance Capabilities (M5.5)
            kernel.register_capability(
                name="kortex.ai.governance.policy.evaluate",
                description="Evaluate prompts and proposed tool calls against tenant governance policy",
                provider=self.name,
                handler=self.evaluate_governance_policy,
                required_permissions=["ai:governance"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.policy.upsert",
                description="Create or update tenant AI governance and guardrail policy",
                provider=self.name,
                handler=self.upsert_governance_policy,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.governance.policy.get",
                description="Retrieve active AI governance policy for a tenant",
                provider=self.name,
                handler=self.get_governance_policy,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.quota.get",
                description="Retrieve token consumption quota and usage for a tenant",
                provider=self.name,
                handler=self.get_tenant_quota,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.quota.update",
                description="Update tenant token budget limits and concurrency limits",
                provider=self.name,
                handler=self.update_tenant_quota,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.governance.audit.query",
                description="Query immutable AI reasoning decision records",
                provider=self.name,
                handler=self.query_decision_records,
                required_permissions=["audit:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.guardrail.check",
                description="Evaluate text against prompt injection, safety patterns, and PII guardrails",
                provider=self.name,
                handler=self.check_content_guardrail,
                required_permissions=["ai:generate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.approval.create",
                description="Create a durable human approval request for an AI action",
                provider=self.name,
                handler=self.create_governance_approval,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )

            self._set_state(EngineState.READY)

            self.logger.info("AI Orchestration Engine initialized successfully.")
        except Exception as exc:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize AI Orchestration Engine: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        """Transition engine state to RUNNING."""
        self.ensure_state(EngineState.READY, EngineState.STOPPED)
        self._set_state(EngineState.RUNNING)
        self.logger.info("AI Orchestration Engine is RUNNING.")

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information (BaseEngine async contract)."""
        return self._diagnostics.health()

    async def stop(self) -> None:
        """Gracefully shut down active tasks and release resources."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("AI Orchestration Engine stopped.")

    # -- Diagnostics Delegation (IEngineDiagnostics Protocol) ----------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and subsystem checks."""
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and state metrics."""
        return self._diagnostics.metrics()

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics."""
        return self._diagnostics.diagnostics()

    def status(self) -> str:
        """Return current engine state name string."""
        return self._state.value

    def version(self) -> str:
        """Return engine semantic version string."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return canonical capability strings declared by the engine."""
        return self._diagnostics.capabilities()

    # -- Facade Capability Handlers ------------------------------------------

    async def generate_response(
        self,
        request: LLMRequest,
        routing_context: RoutingContext | None = None,
        timeout_seconds: float | None = None,
        principal: Any = None,
    ) -> LLMResponse:
        """Generate an AI text response with context composition, routing, history tracking, and global timeout.

        `principal` (M6.1-1): the Kernel dispatcher injects its own
        verified identity into any handler parameter literally named
        `principal` (`core/dispatch.py`'s `_invoke_handler`), regardless of
        this parameter's declared type — typed here as `Any`, not
        `SecurityPrincipal`, because `kortex.engines.security` is a hard,
        AST-enforced forbidden import for this module (see
        `test_ai_engine.py::test_m8_files_quarantine_forbidden_imports`).
        Only `.tenant_id` is read, duck-typed, never imported.

        Before this fix, tenant scope for governance, quota, persistence,
        and audit came entirely from the caller-constructed
        `request.tenant_id` field, with nothing cross-checking it against
        the authenticated caller's real tenant — the same class of gap
        M6.0-3 closed on 12 Workflow Engine handlers. When a verified
        `principal` is present, its `tenant_id` is authoritative: the
        request is corrected to it before anything below reads
        `request.tenant_id`, so every existing line of this method (already
        governance/quota/persistence/audit-tested) is unaffected by this
        fix without further changes.
        """
        effective_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self._default_generation_timeout_seconds
        )
        start_time = time.perf_counter()
        require_identifier(request.tenant_id, "tenant_id")
        require_identifier(request.conversation_id, "conversation_id")

        if principal is not None:
            principal_tenant_id = getattr(principal, "tenant_id", None)
            require_identifier(principal_tenant_id, "principal.tenant_id")
            if principal_tenant_id != request.tenant_id:
                request = request.model_copy(update={"tenant_id": principal_tenant_id})

        async with self._throttler.acquire_generation_slot(request.tenant_id):
            # 1. Emit generation started event
            await self._telemetry.emit_generation_started(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                request_id=request.request_id,
            )

            async def _execute_generation() -> LLMResponse:
                # M5-A3: AI governance now actually executes on this, the
                # real generation path — previously these checks existed
                # only as isolated, unit-tested components reachable via
                # separate, manually-invoked `kortex.ai.governance.*`
                # capabilities that `generate_response` never called,
                # meaning tenant policy/guardrails/quotas had zero effect on
                # real requests. `evaluate_prompt_guardrails` raises
                # `AIPolicyViolationError` itself on a failed check.
                policy = await self._governance_manager.get_policy(request.tenant_id)
                await self._governance_manager.evaluate_prompt_guardrails(request)

                # Cheap pre-flight rejection of a tenant already over budget
                # — avoids spending a provider call before the authoritative
                # post-call debit below (which uses the real token count,
                # per the M5-A5 hardening of `check_and_record_consumption`)
                # would reject it anyway.
                quota_manager = self._governance_manager.quota_manager
                pre_quota = await quota_manager.get_or_create_quota(request.tenant_id)
                today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
                already_consumed = (
                    pre_quota.daily_tokens_consumed if pre_quota.last_reset_date == today else 0
                )
                if already_consumed >= policy.max_daily_budget_tokens:
                    raise AIGovernanceQuotaExceededError(
                        request.tenant_id,
                        f"Daily token budget of {policy.max_daily_budget_tokens} already exhausted.",
                    )

                # 2. Single-point Context Composition (RAG + Prompt Template)
                enriched_request = await self._context_composer.compose(request)

                # 3-4. Model Routing, Provider Resolution & Execution (with failover)
                effective_context = routing_context or RoutingContext(allow_cloud=False)
                response = await _generate_with_fallback(
                    self._model_router,
                    self._provider_registry,
                    enriched_request,
                    effective_context.model_dump(),
                    telemetry=self._telemetry,
                )

                # 5. History Recording. A failure here must not discard an
                # already-successful generation: the M9 architecture spec's
                # Systematic Failure Recovery Matrix requires returning the
                # generation with a degraded flag and an emitted system
                # alert, rather than dropping the turn.
                try:
                    await self._memory_manager.append_history(
                        tenant_id=request.tenant_id,
                        conversation_id=request.conversation_id,
                        request=request,
                        response=response,
                    )
                except ConversationStoreError as exc:
                    self.logger.critical(
                        "Conversation history write failed after successful generation "
                        "for request '%s': %s",
                        request.request_id,
                        exc,
                    )
                    await self._telemetry.emit_storage_write_failed(
                        tenant_id=request.tenant_id,
                        conversation_id=request.conversation_id,
                        request_id=request.request_id,
                        error_category=type(exc).__name__,
                        user_id=request.user_id,
                    )
                    response = response.model_copy(update={"degraded": True})

                # M5-A3: authoritative, atomic quota debit from the REAL
                # token usage the provider reported (never pre-flight-only
                # — see `check_and_record_consumption`'s docstring on why a
                # provider failure must not leave quota debited with
                # nothing to show for it), and immutable decision-audit
                # logging, on every completed generation — degraded or not.
                usage = TokenUsage.from_dict(response.token_usage)
                try:
                    await quota_manager.check_and_record_consumption(request.tenant_id, usage, policy)
                except AIGovernanceQuotaExceededError:
                    # This generation already happened and is still
                    # returned below — discarding a completed response
                    # here wastes the resource without preventing anything.
                    # Recording the overage means the pre-flight check
                    # above rejects the tenant's *next* request before
                    # another generation is attempted.
                    self.logger.warning(
                        "Tenant '%s' exceeded its daily AI token budget as of request '%s'.",
                        request.tenant_id,
                        request.request_id,
                    )

                await self._governance_manager.log_decision(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    prompt_text=request.prompt,
                    output_text=response.text_content,
                    request_id=request.request_id,
                    token_usage=usage,
                    latency_ms=response.execution_time_ms,
                    # M6.1-2: read whatever the serving provider self-reported
                    # on its own response (None for providers that don't set
                    # these, unchanged from prior behavior).
                    provider_id=response.provider_id,
                    model_name=response.model_name,
                )

                return response

            try:
                if effective_timeout is not None and effective_timeout > 0:
                    response = await asyncio.wait_for(
                        _execute_generation(),
                        timeout=effective_timeout,
                    )
                else:
                    response = await _execute_generation()

                # 6. Record Diagnostics & Emit completion event
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                await self._telemetry.emit_generation_completed(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    latency_ms=latency_ms,
                    token_usage=response.token_usage,
                )

                return response

            except TimeoutError as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                timeout_err = AIProviderTimeoutError(
                    f"Global AI generation timeout exceeded ({effective_timeout}s)."
                )
                await self._telemetry.emit_generation_failed(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    latency_ms=latency_ms,
                    error_category="AIProviderTimeoutError",
                )
                self.logger.warning("AI generation timed out after %.2fs", effective_timeout)
                raise timeout_err from exc

            except Exception as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                await self._telemetry.emit_generation_failed(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    conversation_id=request.conversation_id,
                    request_id=request.request_id,
                    latency_ms=latency_ms,
                    error_category=type(exc).__name__,
                )
                self.logger.warning("AI generation failed: %s", exc)
                raise

    async def orchestrate_agent(
        self,
        task: AgentTask,
        authorizer: ToolAuthorizer | None = None,
        principal: Any = None,
    ) -> AgentExecutionResult:
        """Orchestrate a bounded multi-step agent reasoning workflow.

        `principal` (M6.2-2): same fix as `generate_response` (M6.1-1) and
        for the identical reason, now with materially higher stakes --
        `AgentOrchestrator` eventually reaches `KernelToolExecutionPort`,
        which (as of M6.2-1) authenticates as a REAL AI system principal
        scoped to `task.tenant_id`. Before this fix, a caller-spoofed
        `task.tenant_id` was inert (every tool call failed authentication
        regardless); after M6.2-1 it would have let a caller in tenant B
        cause the AI to genuinely act against tenant A's resources merely
        by constructing an `AgentTask(tenant_id="tenant_a", ...)`. Typed
        `Any`, not `SecurityPrincipal`, for the same AST-quarantine reason
        as `generate_response`.
        """
        start_time = time.perf_counter()
        require_identifier(task.tenant_id, "tenant_id")
        require_identifier(task.task_id, "task_id")

        if principal is not None:
            principal_tenant_id = getattr(principal, "tenant_id", None)
            require_identifier(principal_tenant_id, "principal.tenant_id")
            if principal_tenant_id != task.tenant_id:
                task = task.model_copy(update={"tenant_id": principal_tenant_id})

        async with self._throttler.acquire_agent_slot(task.tenant_id):
            try:
                result = await self._agent_orchestrator.run_task(task, authorizer=authorizer)
                latency_ms = (time.perf_counter() - start_time) * 1000.0

                await self._telemetry.emit_agent_completed(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    user_id=getattr(task, "user_id", "user-task"),
                    total_steps=result.total_steps,
                    latency_ms=latency_ms,
                    status=result.status.value,
                )
                return result

            except Exception as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                await self._telemetry.emit_agent_failed(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    user_id=getattr(task, "user_id", "user-task"),
                    total_steps=0,
                    latency_ms=latency_ms,
                    error_category=type(exc).__name__,
                )
                self.logger.warning("Agent orchestration failed: %s", exc)
                raise

    async def resume_agent(
        self,
        task: AgentTask,
        resume_token: ResumeToken,
        approved_tool_calls: list[ToolCall],
        authorizer: ToolAuthorizer | None = None,
        principal: Any = None,
    ) -> AgentExecutionResult:
        """Resume a paused agent workflow with a verified ResumeToken.

        `principal` (M6.2-2): same tenant-correction fix as `orchestrate_agent`.
        """
        start_time = time.perf_counter()
        require_identifier(task.tenant_id, "tenant_id")
        require_identifier(task.task_id, "task_id")

        if principal is not None:
            principal_tenant_id = getattr(principal, "tenant_id", None)
            require_identifier(principal_tenant_id, "principal.tenant_id")
            if principal_tenant_id != task.tenant_id:
                task = task.model_copy(update={"tenant_id": principal_tenant_id})

        async with self._throttler.acquire_agent_slot(task.tenant_id):
            try:
                result = await self._agent_orchestrator.resume_task(
                    task=task,
                    resume_token=resume_token,
                    approved_tool_calls=approved_tool_calls,
                    authorizer=authorizer,
                )
                latency_ms = (time.perf_counter() - start_time) * 1000.0

                await self._telemetry.emit_agent_completed(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    user_id=getattr(task, "user_id", "user-task"),
                    total_steps=result.total_steps,
                    latency_ms=latency_ms,
                    status=result.status.value,
                )
                return result

            except Exception as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                await self._telemetry.emit_agent_failed(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    user_id=getattr(task, "user_id", "user-task"),
                    total_steps=0,
                    latency_ms=latency_ms,
                    error_category=type(exc).__name__,
                )
                self.logger.warning("Agent resume failed: %s", exc)
                raise

    async def cancel_agent_task(self, task_id: str, tenant_id: str) -> bool:
        """Cancel an active or paused agent task across local and durable task stores."""
        return await self._agent_orchestrator.cancel_task(task_id, tenant_id)

    async def get_agent_task(
        self, task_id: str, tenant_id: str
    ) -> PersistedAgentTaskRecord | None:
        """Retrieve a persisted agent task record by identity."""
        return await self._agent_orchestrator.get_task(task_id, tenant_id)

    async def list_agent_tasks(
        self,
        tenant_id: str,
        status: AgentStatus | str | None = None,
        limit: int = 50,
    ) -> list[PersistedAgentTaskRecord]:
        """List persisted agent task records for a tenant, optionally filtered by status.

        `status` accepts a raw string in addition to `AgentStatus` so this
        method is safe to invoke as a Kernel capability handler, where
        parameters cross a JSON-shaped boundary and arrive as plain `str`.
        An unrecognized value raises `ValueError` (from `AgentStatus`
        itself) rather than reaching the store, where a raw string would
        otherwise fail differently on each backend: the in-memory store's
        `StrEnum` equality would silently "work" while the SQL-backed
        store's `status.value` access would raise `AttributeError`.
        """
        normalized_status = AgentStatus(status) if isinstance(status, str) else status
        return await self._agent_orchestrator.list_tasks(tenant_id, normalized_status, limit)

    async def invoke_tool(
        self,
        tenant_id: str,
        tool_call: ToolCall,
        authorizer: ToolAuthorizer | None = None,
        principal: Any = None,
    ) -> ToolResult:
        """Invoke an authorized tool capability through the tool invoker subsystem.

        `principal` (M6.2-2): same tenant-correction fix as `generate_response`/
        `orchestrate_agent` -- a caller-supplied `tenant_id` is never
        authoritative once a verified principal is available.
        """
        start_time = time.perf_counter()

        if principal is not None:
            principal_tenant_id = getattr(principal, "tenant_id", None)
            require_identifier(principal_tenant_id, "principal.tenant_id")
            tenant_id = principal_tenant_id

        # M5-A3: tenant tool governance (blocklist/allowlist) is enforced
        # here, unconditionally, at the actual point of execution — not
        # merely when tools are offered to the model, and not contingent on
        # whether `authorizer` happens to also be supplied. Previously
        # nothing on this path consulted `AIGovernancePolicy` at all; a
        # tenant's `blocked_tools` list had zero effect on what a live tool
        # call could actually do.
        policy = await self._governance_manager.get_policy(tenant_id)
        governance_evaluator = ToolGovernanceEvaluator(self._tool_registry)
        is_allowed, violations, _ = governance_evaluator.evaluate_tool_calls([tool_call], policy)
        if not is_allowed:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            reason = "; ".join(violations)
            await self._telemetry.emit_tool_denied(
                tenant_id=tenant_id,
                tool_name=tool_call.tool_name,
                request_id=tool_call.call_id,
                reason=reason,
                latency_ms=latency_ms,
            )
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                status=ToolExecutionStatus.DENIED,
                error_message=reason,
                execution_time_ms=latency_ms,
            )

        await self._telemetry.emit_tool_invoked(
            tenant_id=tenant_id,
            tool_name=tool_call.tool_name,
            request_id=tool_call.call_id,
        )
        try:
            result = await self._tool_invoker.invoke_tool(
                tenant_id=tenant_id,
                tool_call=tool_call,
                authorizer=authorizer,
            )
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            if result.status.value == "SUCCESS":
                self._diagnostics.record_tool_invocation(
                    status="SUCCESS",
                    latency_ms=latency_ms,
                )
            elif result.status.value == "DENIED":
                await self._telemetry.emit_tool_denied(
                    tenant_id=tenant_id,
                    tool_name=tool_call.tool_name,
                    request_id=tool_call.call_id,
                    reason=result.error_message or "Denied",
                    latency_ms=latency_ms,
                )
            else:
                await self._telemetry.emit_tool_failed(
                    tenant_id=tenant_id,
                    tool_name=tool_call.tool_name,
                    request_id=tool_call.call_id,
                    error_category=result.status.value,
                    latency_ms=latency_ms,
                    is_timeout=(result.status.value == "TIMEOUT"),
                )

            return result

        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            await self._telemetry.emit_tool_failed(
                tenant_id=tenant_id,
                tool_name=tool_call.tool_name,
                request_id=tool_call.call_id,
                error_category=type(exc).__name__,
                latency_ms=latency_ms,
            )
            self.logger.warning("Tool invocation failed: %s", exc)
            raise

    # -- AI Governance Capability Handlers (M5.5) ----------------------------

    async def evaluate_governance_policy(
        self,
        tenant_id: str,
        prompt: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate prompt guardrails and proposed tool calls against tenant policy."""
        require_identifier(tenant_id, "tenant_id")
        policy = await self._governance_manager.get_policy(tenant_id)

        prompt_passed = True
        prompt_violations: list[str] = []
        if prompt:
            res = ContentSafetyGuardrail.evaluate_text(prompt, policy)
            prompt_passed = res.passed
            prompt_violations = res.violations

        tools_passed = True
        tool_violations: list[str] = []
        requires_approval = False
        if tool_calls:
            calls = [ToolCall.model_validate(c) for c in tool_calls]
            evaluator = ToolGovernanceEvaluator(self._tool_registry)
            tools_passed, tool_violations, requires_approval = evaluator.evaluate_tool_calls(calls, policy)

        all_passed = prompt_passed and tools_passed
        all_violations = prompt_violations + tool_violations
        return {
            "passed": all_passed,
            "violations": all_violations,
            "requires_human_approval": requires_approval,
            "tenant_id": tenant_id,
        }

    async def upsert_governance_policy(
        self,
        policy: dict[str, Any] | AIGovernancePolicy,
    ) -> dict[str, Any]:
        """Create or update a tenant AI governance policy."""
        pol = AIGovernancePolicy.model_validate(policy) if isinstance(policy, dict) else policy
        saved = await self._governance_manager.set_policy(pol)
        return saved.model_dump(mode="json")

    async def get_governance_policy(
        self,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Retrieve the governance policy for a tenant."""
        require_identifier(tenant_id, "tenant_id")
        policy = await self._governance_manager.get_policy(tenant_id)
        return policy.model_dump(mode="json")

    async def get_tenant_quota(
        self,
        tenant_id: str,
    ) -> dict[str, Any]:
        """Retrieve token consumption quota for a tenant."""
        require_identifier(tenant_id, "tenant_id")
        quota = await self._governance_manager.quota_manager.get_or_create_quota(tenant_id)
        return quota.model_dump(mode="json")

    async def update_tenant_quota(
        self,
        quota: dict[str, Any] | AITenantQuota,
    ) -> dict[str, Any]:
        """Update token consumption quota and concurrency limits for a tenant."""
        q = AITenantQuota.model_validate(quota) if isinstance(quota, dict) else quota
        if self._governance_manager._store is not None:
            await self._governance_manager._store.save_quota(q)
        else:
            self._governance_manager.quota_manager._memory_quotas[q.tenant_id] = q
        return q.model_dump(mode="json")


    async def query_decision_records(
        self,
        tenant_id: str,
        limit: int = 50,
        offset: int = 0,
        user_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query immutable AI decision audit records."""
        require_identifier(tenant_id, "tenant_id")
        if self._governance_manager._store is not None:
            records = await self._governance_manager._store.query_decision_records(
                tenant_id=tenant_id,
                limit=limit,
                offset=offset,
                user_id=user_id,
                task_id=task_id,
            )
            return [r.model_dump(mode="json") for r in records]
        return []

    async def check_content_guardrail(
        self,
        text: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """Check and sanitize content against prompt injection, safety patterns, and PII."""
        policy = None
        if tenant_id:
            policy = await self._governance_manager.get_policy(tenant_id)
        res = ContentSafetyGuardrail.evaluate_text(text, policy)
        return res.model_dump(mode="json")

    async def create_governance_approval(
        self,
        tenant_id: str,
        task_id: str,
        goal: str,
        proposed_calls: list[dict[str, Any]],
        required_role: str = "ai_approver",
    ) -> dict[str, Any]:
        """Create a durable human approval request for an AI action."""
        require_identifier(tenant_id, "tenant_id")
        require_identifier(task_id, "task_id")
        approval_id = str(uuid.uuid4())
        if self._governance_manager._approval_manager is not None:
            # M6.2-3: `instance_id` is a `WorkflowInstance.id` (a UUID) --
            # this AI-created ticket has no workflow instance, so it must
            # never be `task_id` (an arbitrary string). See the identical
            # fix and full rationale in `governance.py`'s
            # `DurableAIApprovalPolicy.requires_approval`.
            await self._governance_manager._approval_manager.create_request(
                instance_id=None,
                step_id=task_id,
                required_role=required_role,
                tenant_id=tenant_id,
                context={
                    "action": "ai_tool_invocation",
                    "task_id": task_id,
                    "goal": goal,
                    "proposed_calls": proposed_calls,
                },
                correlation_id=task_id,
            )
        return {
            "approval_id": approval_id,
            "task_id": task_id,
            "tenant_id": tenant_id,
            "status": "WAITING_APPROVAL",
            "required_role": required_role,
        }

    def register_provider(self, provider: BaseAIProvider) -> None:

        """Register an AI provider in the provider registry."""
        self._provider_registry.register(provider)

    def list_providers(self) -> list[AIProviderMetadata]:
        """List metadata of all registered AI providers."""
        return self._provider_registry.list_providers()

    def list_models(self) -> list[AIModelSummary]:
        """List every model declared across all registered providers.

        A pure flatten of `list_providers()`'s own `supported_models` field
        (see `AIModelSummary`'s docstring) — zero routing/selection logic,
        unlike `ModelRouter`, which this does not call or duplicate."""
        return [
            AIModelSummary(
                model_id=model_id,
                provider_id=provider.provider_id,
                provider_display_name=provider.display_name,
            )
            for provider in self._provider_registry.list_providers()
            for model_id in provider.supported_models
        ]

    # -- Durable Approval Decision Resume (M6.2-4) ---------------------------

    @staticmethod
    def _action_fingerprint(tool_calls: list[ToolCall]) -> str:
        """Recompute the same fingerprint `DurableAIApprovalPolicy.requires_approval`
        (governance.py) stamps onto an AI-originated approval ticket at
        creation time -- must stay byte-for-byte identical to that formula
        (scrubbed args, `sort_keys=True`) or a legitimate approval would
        spuriously fail re-verification here."""
        calls_summary = [
            {"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))}
            for c in tool_calls
        ]
        return hashlib.sha256(json.dumps(calls_summary, sort_keys=True).encode("utf-8")).hexdigest()

    async def _on_approval_decided(self, event: Any) -> None:
        """React to a durable approval decision for an AI-originated ticket (M6.2-4).

        Subscribed to the generic `workflow.approval.decided` event
        (published unconditionally by `WorkflowEngine.decide_approval_request`
        regardless of whether the ticket is linked to a workflow instance —
        an AI-originated ticket never is). This keeps the Workflow Engine
        entirely unaware of the AI engine's existence: it publishes one
        plain domain event, and this handler is simply one of potentially
        several subscribers. Filters on the ticket's own
        `context_snapshot["action"] == "ai_tool_invocation"` marker so
        every other (human/workflow-instance) decision is ignored.

        Fails closed: any ambiguity (task not found, wrong status, missing
        or mismatched action fingerprint) results in the paused task being
        left alone or cancelled — never resumed on uncertain grounds.
        """
        try:
            payload = getattr(event, "payload", None)
            if not isinstance(payload, dict):
                return
            context_snapshot = payload.get("context_snapshot")
            if not isinstance(context_snapshot, dict) or context_snapshot.get("action") != "ai_tool_invocation":
                return

            task_id = context_snapshot.get("task_id")
            tenant_id = payload.get("tenant_id")
            decision = payload.get("decision")
            if not task_id or not tenant_id:
                return

            record = await self._agent_orchestrator.get_task(task_id, tenant_id)
            if record is None or record.status != AgentStatus.PAUSED_FOR_APPROVAL:
                # Already resumed/cancelled by a prior delivery of this
                # event, or the task never reached this pause state --
                # idempotent no-op either way.
                return

            if decision == "APPROVED":
                stored_fingerprint = payload.get("action_fingerprint")
                actual_fingerprint = self._action_fingerprint(record.pending_tool_calls)
                if stored_fingerprint and stored_fingerprint != actual_fingerprint:
                    self.logger.error(
                        "Refusing to resume agent task '%s': approved action fingerprint does not "
                        "match the task's current pending tool calls (approve-one/execute-another "
                        "attempt or stale approval).",
                        task_id,
                    )
                    await self._agent_orchestrator.cancel_task(task_id, tenant_id)
                    return
                await self._agent_orchestrator.resume_task(
                    task=record.task,
                    resume_token=record.resume_token,
                    approved_tool_calls=record.pending_tool_calls,
                )
            else:
                # REJECTED (or any other terminal, non-approved decision):
                # the paused task must never execute the calls it was
                # paused on.
                await self._agent_orchestrator.cancel_task(task_id, tenant_id)
        except Exception as exc:
            self.logger.error("Failed to process approval decision event for AI task: %s", exc, exc_info=True)

    # -- Internal Event Helper -----------------------------------------------

    async def _publish_event(self, event: AIBaseEvent) -> None:
        """Publish a system event via the Kernel Event Engine safely without throwing."""
        if self._kernel is None:
            return
        try:
            await self._kernel.publish_event(
                topic=event.event_type,
                payload=event.model_dump(),
                sender=self.name,
            )
        except Exception as exc:
            self.logger.warning("Failed to publish event %s: %s", event.event_type, exc)


__all__ = [
    "AIOrchestrationEngine",
    "EngineAgentContextPort",
    "KernelSecurityApprovalPolicy",
    "KernelToolExecutionPort",
    "RouterLLMExecutionPort",
]
