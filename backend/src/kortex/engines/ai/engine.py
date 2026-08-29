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
    """Production adapter for `IToolExecutionPort` dispatching to `IKernelBridge`."""

    def __init__(self, kernel_bridge: IKernelBridge) -> None:
        self._kernel_bridge = kernel_bridge

    async def execute_tool(
        self,
        tenant_id: str,
        capability_name: str,
        arguments: dict[str, object],
        authorizer: ToolAuthorizer | None = None,
    ) -> object:
        """Execute capability handler through Kernel enforcement boundary."""
        require_identifier(tenant_id, "tenant_id")
        if authorizer is not None:
            is_allowed = await authorizer(capability_name, arguments)
            if not is_allowed:
                from kortex.engines.ai.exceptions import ToolAuthorizationError
                raise ToolAuthorizationError(f"Authorization denied for capability '{capability_name}'.")

        return await self._kernel_bridge.invoke_capability(
            name=capability_name,
            arguments=arguments,
            tenant_id=tenant_id,
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
    ) -> LLMResponse:
        """Generate an AI text response with context composition, routing, history tracking, and global timeout."""
        effective_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self._default_generation_timeout_seconds
        )
        start_time = time.perf_counter()
        require_identifier(request.tenant_id, "tenant_id")
        require_identifier(request.conversation_id, "conversation_id")

        async with self._throttler.acquire_generation_slot(request.tenant_id):
            # 1. Emit generation started event
            await self._telemetry.emit_generation_started(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                request_id=request.request_id,
            )

            async def _execute_generation() -> LLMResponse:
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
                    return response.model_copy(update={"degraded": True})

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
    ) -> AgentExecutionResult:
        """Orchestrate a bounded multi-step agent reasoning workflow."""
        start_time = time.perf_counter()
        require_identifier(task.tenant_id, "tenant_id")
        require_identifier(task.task_id, "task_id")

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
    ) -> AgentExecutionResult:
        """Resume a paused agent workflow with a verified ResumeToken."""
        start_time = time.perf_counter()
        require_identifier(task.tenant_id, "tenant_id")
        require_identifier(task.task_id, "task_id")

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
    ) -> ToolResult:
        """Invoke an authorized tool capability through the tool invoker subsystem."""
        start_time = time.perf_counter()
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
            await self._governance_manager._approval_manager.create_request(
                instance_id=task_id,
                step_id=task_id,
                required_role=required_role,
                tenant_id=tenant_id,
                context={
                    "action": "ai_tool_invocation",
                    "task_id": task_id,
                    "goal": goal,
                    "proposed_calls": proposed_calls,
                },
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
