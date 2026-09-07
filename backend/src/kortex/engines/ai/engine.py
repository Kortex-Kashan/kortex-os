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
from kortex.engines.ai.cloud_authorization import TenantCloudRoutingAuthority
from kortex.engines.ai.credentials import (
    CredentialResolutionError,
    TenantCredentialResolver,
    provider_secret_handle,
)
from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.events import (
    AIBaseEvent,
)
from kortex.engines.ai.exceptions import (
    AIEngineNotConfiguredError,
    AIGovernanceQuotaExceededError,
    AIProviderTimeoutError,
    CloudRoutingNotPermittedError,
    ConversationStoreError,
    NoRoutableProviderError,
    PermanentProviderError,
    ProviderNotFoundError,
    TenantQuotaExceededError,
    TransientProviderError,
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
    ConversationTurn,
    InMemoryConversationStore,
    require_identifier,
    sanitize_context_content,
)
from kortex.engines.ai.models import (
    AIModelSummary,
    AIProviderConfig,
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


def _principal_from(execution_context: Any) -> Any:
    """Resolve the dispatcher-verified principal for one invocation (Phase B).

    Identity reaches an AI capability handler through the dispatcher-injected
    `CapabilityExecutionContext` (`requires_execution_context=True`), the same
    mechanism Workflow Engine and Security Engine already use.

    Duck-typed on purpose: `kortex.engines.security` is an AST-enforced
    forbidden import for this module, so only `.principal`/`.tenant_id` are
    read and no security type is ever imported.

    Phase B mechanism change: M6.1-1/M6.2-2 delivered this identity through a
    handler parameter named `principal`, which `RegistryEngine.register_
    capability` auto-infers into `legacy_principal_bridge=True`. That worked,
    but the bridge is the deprecated channel — it hands over a bare principal
    with none of the execution context's request/correlation/session fields,
    and it is inferred from a parameter name rather than declared. These
    handlers now take `execution_context` instead, so the same registrations
    auto-infer `requires_execution_context=True` and no AI capability uses
    the bridge. The tenant-rebinding logic each handler already had is
    unchanged; only the channel it reads from is.
    """
    if execution_context is None:
        return None
    return getattr(execution_context, "principal", None)


def _provider_config_view(config: AIProviderConfig) -> dict[str, Any]:
    """Wire view of one provider configuration.

    Built field by field rather than via `model_dump()` so that adding a
    field to `AIProviderConfig` can never silently widen what crosses the
    capability boundary. `has_credential` answers the only question a UI
    actually has ("is this provider set up?") without exposing anything
    about the credential itself.

    `secret_handle` is deliberately **not** here (B4.1, Chief Architect
    decision). It is a `SecretStore` reference rather than secret material,
    and B1 included it so a tenant could reason about their own
    configuration — but B4 gives that handle a real consumer (AI Studio),
    and the standing requirement is that the frontend never *receives* a
    secret handle, not merely that it declines to display one. Omitting it
    from the response is the only version of that guarantee which cannot be
    undone by a later mapping change: `apps/desktop/src/features/ai-studio/
    types.ts` already documents the same reasoning for why the field is
    absent from its types rather than hidden in its UI. Nothing consumed
    the field — `has_credential` carries the whole signal a caller needs.
    """
    return {
        "tenant_id": config.tenant_id,
        "provider_id": config.provider_id,
        "enabled": config.enabled,
        "has_credential": config.secret_handle is not None,
        "default_model": config.default_model,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _authoritative_tenant_id(execution_context: Any, claimed_tenant_id: str) -> str:
    """Return the tenant a handler must act on, ignoring any caller claim.

    A verified principal's tenant always wins over `claimed_tenant_id`, so a
    caller cannot reach another tenant's governance policy, quota, agent
    task, or audit records by passing that tenant's id. When no verified
    identity is present (in-process/system callers and the existing unit
    tests), the supplied value stands — the dispatcher is what guarantees a
    principal exists for every externally reachable invocation.
    """
    principal = _principal_from(execution_context)
    if principal is None:
        return claimed_tenant_id
    verified = getattr(principal, "tenant_id", None)
    if not verified:
        return claimed_tenant_id
    return str(verified)


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
    """Production adapter for `ILLMExecutionPort` using `ModelRouter` and `ProviderRegistry`.

    This is the *only* place the agent/orchestration path decides placement,
    which is why B4.1's trusted cloud authorization — and B5.1's tenant
    provider/model preference — belong here and nowhere else.
    `ILLMExecutionPort.generate_step` takes an `LLMRequest` and nothing more
    — there is deliberately no routing-context parameter, so an agent step
    has no channel through which a caller could ask for cloud egress or name
    a provider. Before B4.1 that meant cloud was simply unreachable from
    this path (`self._default_context` was used unconditionally); B4.1 made
    cloud reachable when authorized; B5.1 makes an authorized tenant's own
    configured provider the one actually used, instead of ADR #001's
    local-first ranking silently outranking it whenever a healthy local
    provider happens to also be registered.
    """

    def __init__(
        self,
        router: ModelRouter,
        registry: ProviderRegistry,
        default_routing_context: RoutingContext | None = None,
        telemetry: object | None = None,
        cloud_authority: TenantCloudRoutingAuthority | None = None,
        credential_resolver: TenantCredentialResolver | None = None,
    ) -> None:
        self._router = router
        self._registry = registry
        self._default_context = default_routing_context or RoutingContext(allow_cloud=False)
        self._telemetry = telemetry
        self._cloud_authority = cloud_authority
        self._credential_resolver = credential_resolver

    async def generate_step(self, request: LLMRequest) -> LLMResponse:
        """Route to eligible providers and execute a single reasoning step, with failover.

        `allow_cloud` is decided here, per request, from `request.tenant_id`
        — which is server-stamped: `EngineAgentContextPort.
        build_step_context` copies it from the persisted `AgentTask`, whose
        tenant the dispatcher derived from the authenticated principal. No
        caller-supplied value reaches this decision.

        When a `cloud_authority` is wired it is **authoritative in both
        directions**: it can permit cloud egress that `default_routing_
        context` denies, and it denies egress that `default_routing_context`
        would have allowed. This is the point — `enable_cloud_models` is a
        process-wide flag that cannot express a per-tenant decision, so once
        a real authority exists the flag must stop being the answer on this
        path. With no authority wired, behavior is byte-identical to before
        B4.1, which is what keeps the in-memory/unit composition (no
        provider config store, hence no authoritative state to consult)
        working unchanged.

        `allow_cloud` and the pin are now two **independent** authority
        calls (B5 correction), not one: `is_cloud_permitted` answers "may
        this tenant reach cloud at all" (true whenever at least one
        qualifying configuration exists); `resolve_tenant_preference`
        answers the narrower "is there exactly one unambiguous provider to
        pin to" (`None` when zero *or more than one* configuration
        qualifies — see `cloud_authorization.py`). A tenant with two
        enabled cloud providers is still cloud-permitted via ordinary
        `_discover`-mode ranking/fallback; they simply are not pinned to
        either one, since pinning by an accident of database row order
        would not be a real preference.

        When a preference exists **and the request does not already name a
        model** (`request.model_id is None`), it is applied two ways:

        1. **`provider_id` is pinned to it.** This reuses `ModelRouter.
           _resolve_pinned` exactly as an externally-supplied pin would
           (B4's own `generate_response` path already does this for a
           caller-supplied pin) — no router change, no second routing path.
           A pin forecloses fallback to any other provider by design
           (`_resolve_pinned` returns exactly one candidate); that is the
           intended reading of "trusted tenant-level preference", not a
           defect — silently falling back to local on a cloud outage would
           contradict the tenant's own explicit choice. A tenant with *no*
           configured preference is completely unaffected: `preference is
           None` leaves `context`/`request` exactly as before B5.1, and
           `_discover`'s local-first ranking still governs.
        2. **`request.model_id` is set to the preference's `default_model`,
           when present — but only after `_validate_configured_default_
           model` confirms it against the provider's LIVE catalog** (B5
           correction). `ModelRouter._resolve_pinned`'s static `model_id
           not in metadata.supported_models` check alone proves only that
           this KORTEX build recognizes the model's name — not that this
           tenant's own credential can currently serve it (account tier,
           vendor-side deprecation, etc.). `_validate_configured_default_
           model` calls the pinned provider's own `discover_models`, the
           exact mechanism `kortex.ai.provider.test` already uses for this
           identical question, and raises `NoRoutableProviderError` — the
           same typed failure the static check already used — for every
           way that verification can fail, rather than proceeding with an
           unverified pin.

        The `request.model_id is None` guard on the pin itself (not just on
        setting it) exists for the same D1 doctrine in the other direction:
        a caller that already asserted a specific model is asserting
        something a tenant *provider* preference must not override — the
        pin is skipped entirely (and `resolve_tenant_preference` is not even
        called), `allow_cloud` still reflects authorization, and `_discover`
        mode finds whichever authorized provider actually serves the named
        model, exactly as before B5.1. `build_step_context` never sets
        `model_id` today, so this guard is presently defensive, not
        load-bearing, on the real agent path — but it keeps this port's
        behavior correct for any other caller.
        """
        context = self._default_context
        if self._cloud_authority is not None:
            permitted = await self._cloud_authority.is_cloud_permitted(request.tenant_id)
            context = context.model_copy(update={"allow_cloud": permitted})
            if request.model_id is None:
                preference = await self._cloud_authority.resolve_tenant_preference(request.tenant_id)
                if preference is not None:
                    if preference.default_model is not None:
                        await self._validate_configured_default_model(preference)
                        request = request.model_copy(update={"model_id": preference.default_model})
                    context = context.model_copy(update={"provider_id": preference.provider_id})
        return await _generate_with_fallback(
            self._router, self._registry, request, context.model_dump(), telemetry=self._telemetry
        )

    async def _validate_configured_default_model(self, preference: AIProviderConfig) -> None:
        """Verify `preference.default_model` against the provider's LIVE catalog before pinning to it (B5 correction).

        `preference` already comes from `TenantCloudRoutingAuthority.
        resolve_tenant_preference` — enabled, credentialed, policy-permitted
        — so everything checked here is specifically about the *model*, not
        the provider or the tenant's authorization to reach it.

        Deliberately reuses `discover_models`/`TenantCredentialResolver`
        rather than adding any new mechanism: this is the identical pair of
        calls `test_provider_connection` (`kortex.ai.provider.test`) already
        makes to answer the identical question for a human clicking "Test
        connection". No caching — the standing no-credential-caching rule
        this shares with `credentials.py` and `cloud_authorization.py` means
        this really does cost one extra vendor round trip per request that
        reaches a pinned provider with a configured default model.

        Raises:
            NoRoutableProviderError: The model cannot be positively
                confirmed as currently servable by this tenant's own
                credential — for *any* reason: the provider vanished from
                the registry between authorization and this call, no
                credential resolver is wired, the credential itself no
                longer resolves, the live discovery call itself fails
                (`PermanentProviderError`/`TransientProviderError`/
                `AIProviderTimeoutError`), or the model is genuinely absent
                from the live result. Every one of these answers "do not
                proceed with this pin" — never "fall through to try
                something else instead", which is exactly the silent
                unintended-provider selection this method exists to
                prevent. The caller (`generate_step`) has already decided
                pinning is otherwise appropriate; this method's only job is
                to say yes or raise, never to pick a different placement.
        """
        assert preference.default_model is not None  # only ever called when true, by generate_step

        try:
            provider = self._registry.get(preference.provider_id)
        except ProviderNotFoundError as exc:
            raise NoRoutableProviderError(
                f"Tenant's preferred provider '{preference.provider_id}' is not currently registered; "
                "cannot verify its configured default model against a live catalog."
            ) from exc

        if self._credential_resolver is None:
            raise NoRoutableProviderError(
                f"Cannot verify the configured default model for provider '{preference.provider_id}': "
                "no credential resolver is wired to reach its live catalog."
            )

        try:
            resolved = await self._credential_resolver.resolve(preference.tenant_id, preference.provider_id)
        except CredentialResolutionError as exc:
            raise NoRoutableProviderError(
                f"Tenant's credential for provider '{preference.provider_id}' could not be resolved "
                "while verifying its configured default model."
            ) from exc

        if resolved is None:
            raise NoRoutableProviderError(
                f"Provider '{preference.provider_id}' is no longer credentialed for this tenant; "
                "cannot verify its configured default model."
            )

        try:
            discovered = await provider.discover_models(resolved.plaintext)
        except (PermanentProviderError, TransientProviderError, AIProviderTimeoutError) as exc:
            raise NoRoutableProviderError(
                f"Could not reach the live model catalog for provider '{preference.provider_id}' to "
                f"verify the configured default model '{preference.default_model}': {exc}"
            ) from exc

        if not any(model.model_id == preference.default_model for model in discovered):
            raise NoRoutableProviderError(
                f"Provider '{preference.provider_id}' does not currently list "
                f"'{preference.default_model}' among the models this tenant's credential can serve; "
                "refusing to route to it rather than silently using a different model or provider."
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
            steps[-self._max_step_history_window :] if len(steps) > self._max_step_history_window else steps
        )

        history_lines: list[str] = []
        if windowed_steps:
            history_lines.append("\nExecution History:")
            for s in windowed_steps:
                history_lines.append(f"Step {s.step_number}:")
                if s.thought:
                    sanitized_thought = sanitize_context_content(scrub_secrets_from_text(s.thought))
                    history_lines.append(f"  Thought: {sanitized_thought}")
                for tc in s.tool_calls:
                    tc_args_str = json.dumps(tc.arguments, default=str)
                    sanitized_args = sanitize_context_content(scrub_secrets_from_text(tc_args_str))
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
                    history_lines.append(f"  Tool Result: status={tr.status.value}, output={sanitized_out}")
                if s.response_text:
                    sanitized_resp = sanitize_context_content(scrub_secrets_from_text(s.response_text))
                    history_lines.append(f"  Response: {sanitized_resp}")

        history_block = "\n".join(history_lines)
        full_prompt = f"Goal: {task.goal}\n{history_block}" if history_block else f"Goal: {task.goal}"

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
        self._ai_identity: Any = ai_identity

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
        provider_config_store: Any = None,
        secret_getter: Any = None,
        secret_putter: Any = None,
        cloud_routing_authority: TenantCloudRoutingAuthority | None = None,
    ) -> None:
        """Initialize AIOrchestrationEngine with optional component injections.

        If components are omitted, sensible default subsystem instances are created.

        `provider_config_store`/`secret_getter`/`secret_putter` (Phase B /
        B1d) are the tenant provider-configuration surface. All three are
        injected and typed `Any` rather than imported: the store is created
        by `bootstrap.py` from a `data_store`, and the two secret callables
        are `SecurityEngine.get_secret`/`put_secret` — and
        `kortex.engines.security` is an AST-forbidden import here. When any
        is absent the provider-configuration capabilities fail explicitly
        (`AIEngineNotConfiguredError`) rather than pretending to store a
        credential; see `configure_provider`.
        """
        super().__init__()
        self._provider_config_store = provider_config_store
        self._secret_getter = secret_getter
        self._secret_putter = secret_putter
        self._credential_resolver = (
            TenantCredentialResolver(provider_config_store, secret_getter)
            if provider_config_store is not None and secret_getter is not None
            else None
        )
        self._default_generation_timeout_seconds = default_generation_timeout_seconds
        self._throttler = throttler if throttler is not None else TenantConcurrencyThrottler()
        self._provider_registry = provider_registry if provider_registry is not None else ProviderRegistry()
        self._model_router = model_router if model_router is not None else ModelRouter(registry=self._provider_registry)
        self._memory_manager = (
            memory_manager if memory_manager is not None else AIMemoryManager(store=InMemoryConversationStore())
        )
        self._tool_registry = tool_registry if tool_registry is not None else ToolRegistry()
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
        self._telemetry = telemetry if telemetry is not None else AITelemetryEmitter(diagnostics=self._diagnostics)
        self._governance_manager = (
            governance_manager
            if governance_manager is not None
            else AIGovernanceManager(tool_registry=self._tool_registry)
        )

        # B4.1: the trusted cloud-routing authority. Built here — rather
        # than only in `bootstrap.py` — because every input it needs is
        # already an attribute of this engine, and because a shared instance
        # keeps `generate_response` and the agent path deciding identically.
        # `cloud_routing_authority` may be injected so `bootstrap.py` can
        # hand the same instance to the `RouterLLMExecutionPort` it builds
        # itself; absent an injection, one is constructed whenever there is
        # authoritative state to consult.
        #
        # No provider config store means no authoritative per-tenant state
        # exists, so no authority is built and pre-B4.1 behavior stands
        # unchanged. That is the correct answer rather than a gap: with
        # nowhere for a tenant to have enabled a cloud provider, the trusted
        # rule's first clause could only ever evaluate false.
        self._cloud_routing_authority: TenantCloudRoutingAuthority | None
        if cloud_routing_authority is not None:
            self._cloud_routing_authority = cloud_routing_authority
        elif provider_config_store is not None:
            self._cloud_routing_authority = TenantCloudRoutingAuthority(
                registry=self._provider_registry,
                provider_configs=provider_config_store,
                policy_reader=self._governance_manager,
            )
        else:
            self._cloud_routing_authority = None

        # Wire AgentOrchestrator with production adapters
        if agent_orchestrator is not None:
            self._agent_orchestrator = agent_orchestrator
        else:
            llm_port = RouterLLMExecutionPort(
                router=self._model_router,
                registry=self._provider_registry,
                telemetry=self._telemetry,
                cloud_authority=self._cloud_routing_authority,
                credential_resolver=self._credential_resolver,
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
                requires_execution_context=True,
                required_permissions=["ai:generate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.orchestrate",
                description="Orchestrate a bounded multi-step agent reasoning workflow",
                provider=self.name,
                handler=self.orchestrate_agent,
                requires_execution_context=True,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.resume",
                description="Resume a paused agent reasoning workflow with verified token",
                provider=self.name,
                handler=self.resume_agent,
                requires_execution_context=True,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.tool.invoke",
                description="Invoke an authorized AI tool capability",
                provider=self.name,
                handler=self.invoke_tool,
                requires_execution_context=True,
                required_permissions=["ai:execute"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.conversation.history.get",
                description="Retrieve durable conversation turns for a conversation",
                provider=self.name,
                handler=self.get_conversation_history,
                requires_execution_context=True,
                required_permissions=["ai:read"],
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
                requires_execution_context=True,
                required_permissions=["ai:orchestrate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.status",
                description="Retrieve the persisted status of an agent reasoning task",
                provider=self.name,
                handler=self.get_agent_task,
                requires_execution_context=True,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.agent.list",
                description="List agent reasoning tasks for a tenant, optionally filtered by status",
                provider=self.name,
                handler=self.list_agent_tasks,
                requires_execution_context=True,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )

            # Tenant Provider Configuration Capabilities (Phase B / B1d).
            # `configure` is RESTRICTED and gated on `ai:manage`: it writes a
            # provider credential into the tenant's SecretStore. Its
            # `api_key` parameter name is load-bearing for audit redaction --
            # see `configure_provider`.
            kernel.register_capability(
                name="kortex.ai.provider.configure",
                description="Configure an AI provider for the calling tenant, storing its credential as a secret",
                provider=self.name,
                handler=self.configure_provider,
                requires_execution_context=True,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.provider.config.list",
                description="List the calling tenant's AI provider configurations (never their credentials)",
                provider=self.name,
                handler=self.list_provider_configs,
                requires_execution_context=True,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.provider.config.remove",
                description="Remove one of the calling tenant's AI provider configurations",
                provider=self.name,
                handler=self.remove_provider_config,
                requires_execution_context=True,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            # Phase B / B2: the generic connection-test capability every
            # credentialed cloud provider (OpenAI now; Gemini/Anthropic in
            # B3) shares. RESTRICTED/`ai:manage` like `configure`, not
            # `ai:read` like the read-only `config.list` -- it makes a real
            # outbound network call using the tenant's live credential.
            kernel.register_capability(
                name="kortex.ai.provider.test",
                description="Validate the calling tenant's configured credential against a registered provider",
                provider=self.name,
                handler=self.test_provider_connection,
                requires_execution_context=True,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )

            # AI Governance Capabilities (M5.5)
            kernel.register_capability(
                name="kortex.ai.governance.policy.evaluate",
                description="Evaluate prompts and proposed tool calls against tenant governance policy",
                provider=self.name,
                handler=self.evaluate_governance_policy,
                requires_execution_context=True,
                required_permissions=["ai:governance"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.policy.upsert",
                description="Create or update tenant AI governance and guardrail policy",
                provider=self.name,
                handler=self.upsert_governance_policy,
                requires_execution_context=True,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.governance.policy.get",
                description="Retrieve active AI governance policy for a tenant",
                provider=self.name,
                handler=self.get_governance_policy,
                requires_execution_context=True,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.quota.get",
                description="Retrieve token consumption quota and usage for a tenant",
                provider=self.name,
                handler=self.get_tenant_quota,
                requires_execution_context=True,
                required_permissions=["ai:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.quota.update",
                description="Update tenant token budget limits and concurrency limits",
                provider=self.name,
                handler=self.update_tenant_quota,
                requires_execution_context=True,
                required_permissions=["ai:manage"],
                security_classification="RESTRICTED",
            )
            kernel.register_capability(
                name="kortex.ai.governance.audit.query",
                description="Query immutable AI reasoning decision records",
                provider=self.name,
                handler=self.query_decision_records,
                requires_execution_context=True,
                required_permissions=["audit:read"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.guardrail.check",
                description="Evaluate text against prompt injection, safety patterns, and PII guardrails",
                provider=self.name,
                handler=self.check_content_guardrail,
                requires_execution_context=True,
                required_permissions=["ai:generate"],
                security_classification="INTERNAL",
            )
            kernel.register_capability(
                name="kortex.ai.governance.approval.create",
                description="Create a durable human approval request for an AI action",
                provider=self.name,
                handler=self.create_governance_approval,
                requires_execution_context=True,
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
        """Gracefully shut down active tasks and release resources.

        `_close_providers` (Phase B / B5.2) closes every registered
        provider's owned resources between STOPPING and STOPPED. Placed
        inside the existing `stop()` this engine already implements to
        satisfy `BaseEngine`'s abstract lifecycle contract, and reached
        exactly once per graceful shutdown: `Kernel.shutdown()` ->
        `BootEngine.shutdown_system()` calls `engine.stop()` for every
        registered engine in reverse dependency order, already skipping any
        engine already `STOPPED`/`STOPPING` and already containing any
        exception one engine's `stop()` raises so it cannot block the
        others — this method adds no second shutdown mechanism, it only
        does more work inside the one that already exists.
        """
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        await self._close_providers()
        self._set_state(EngineState.STOPPED)
        self.logger.info("AI Orchestration Engine stopped.")

    async def _close_providers(self) -> None:
        """Close every registered provider's owned resources (Phase B / B5.2).

        Best-effort and per-provider: one provider's `aclose()` raising must
        not prevent the others from closing, and must not prevent this
        engine from reaching `STOPPED` — the same one-bad-engine-must-not-
        block-the-rest discipline `BootEngine.shutdown_system` already
        applies one level up, applied here one level down.

        `aclose` is read via `getattr(..., None)` rather than assumed:
        `BaseAIProvider` declares no such method (see
        `ResilientAIProvider.aclose`'s docstring for why), so a bare
        provider or a test double without one is a normal, safe case, not
        an error. Re-fetches each provider by id rather than iterating
        `list_providers()`'s metadata directly, because that call returns
        `AIProviderMetadata`, not the live provider object `aclose()` lives
        on; a provider unregistered between the snapshot and this call is
        simply no longer this engine's to close.
        """
        for metadata in self._provider_registry.list_providers():
            try:
                provider = self._provider_registry.get(metadata.provider_id)
            except ProviderNotFoundError:
                continue
            aclose = getattr(provider, "aclose", None)
            if aclose is None:
                continue
            try:
                await aclose()
            except Exception:
                self.logger.exception("Provider '%s' raised while closing during shutdown.", metadata.provider_id)

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

    @property
    def cloud_routing_authority(self) -> TenantCloudRoutingAuthority | None:
        """The trusted cloud-routing authority, or None when unwired (B4.1)."""
        return self._cloud_routing_authority

    async def _authorized_routing_context(
        self,
        tenant_id: str,
        requested: RoutingContext | None,
    ) -> RoutingContext:
        """Derive the effective routing context server-side (Phase B / B4.1).

        `requested` is caller-supplied and therefore untrusted with respect
        to cloud egress. This method returns a context whose cloud
        authorization is the trusted decision for `tenant_id`, and rejects
        the two constraints that would otherwise reach a cloud provider
        *around* that decision:

        1. **`allow_cloud`** — replaced, never honored. A caller asking for
           cloud egress it is not entitled to gets a local-only context,
           not an error: `allow_cloud` is a preference the trusted decision
           supersedes.
        2. **`provider_id` pinned at a cloud provider** — rejected.
           `ModelRouter._resolve_pinned` deliberately does not consult
           `allow_cloud` (naming a provider is itself the explicit placement
           decision the default exists to force), so a pin is an
           unguarded route to the vendor unless it is stopped here.
        3. **`endpoint_type="cloud"`** — rejected. In
           `ModelRouter._discover` the `endpoint_type` filter and the
           cloud gate are branches of one `if/elif`, so an explicit
           `endpoint_type` **bypasses the `allow_cloud` check entirely**.
           Left unhandled, `{"endpoint_type": "cloud"}` would reach every
           registered cloud provider regardless of this decision.

        Cases 2 and 3 raise rather than downgrade because both are caller
        assertions about placement; see `CloudRoutingNotPermittedError`.

        With no authority wired there is no authoritative state to consult,
        and the caller's context stands as before B4.1 — the composition
        used by in-memory and unit tests.
        """
        requested = requested if requested is not None else RoutingContext()
        if self._cloud_routing_authority is None:
            return requested

        authority = self._cloud_routing_authority
        permitted = await authority.is_cloud_permitted(tenant_id)

        if not permitted:
            if requested.provider_id is not None and authority.is_cloud_provider(requested.provider_id):
                raise CloudRoutingNotPermittedError(
                    tenant_id,
                    f"provider '{requested.provider_id}' is a cloud provider and no enabled, credentialed "
                    "cloud provider configuration permits cloud routing for this tenant.",
                )
            if requested.endpoint_type == "cloud":
                raise CloudRoutingNotPermittedError(
                    tenant_id,
                    "endpoint_type='cloud' was requested but cloud routing is not permitted for this tenant.",
                )

        return requested.model_copy(update={"allow_cloud": permitted})

    async def generate_response(
        self,
        request: LLMRequest,
        routing_context: RoutingContext | None = None,
        timeout_seconds: float | None = None,
        execution_context: Any = None,
    ) -> LLMResponse:
        """Generate an AI text response with context composition, routing, history tracking, and global timeout.

        Identity arrives as `execution_context` (M6.1-1, corrected in Phase
        B): the Kernel dispatcher builds a `CapabilityExecutionContext` from
        its own verified session and injects it into this parameter because
        the capability registers `requires_execution_context=True`. Typed
        `Any`, not the real class, because `kortex.engines.security` is a
        hard, AST-enforced forbidden import for this module (see
        `test_ai_engine.py::test_m8_files_quarantine_forbidden_imports`);
        only `.principal.tenant_id` is read, duck-typed, never imported.

        `principal` remains for direct in-process callers and the existing
        handler-level tests, and wins when supplied. M6.1-1 declared it
        alone and described it as dispatcher-injected, but the dispatcher
        only injects a bare `principal` for capabilities registered with
        `legacy_principal_bridge=True` — which no AI capability ever was —
        so through dispatch it was always `None` and the re-binding below
        never ran. See `_principal_from`.

        Before that re-binding, tenant scope for governance, quota,
        persistence, and audit came entirely from the caller-constructed
        `request.tenant_id`, with nothing cross-checking it against the
        authenticated caller's real tenant — the same class of gap M6.0-3
        closed on 12 Workflow Engine handlers. A verified identity's
        `tenant_id` is authoritative: the request is corrected to it before
        anything below reads `request.tenant_id`, so every existing line of
        this method (already governance/quota/persistence/audit-tested) is
        unaffected without further changes.
        """
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._default_generation_timeout_seconds
        start_time = time.perf_counter()
        require_identifier(request.tenant_id, "tenant_id")
        require_identifier(request.conversation_id, "conversation_id")

        principal = _principal_from(execution_context)
        if principal is not None:
            principal_tenant_id = require_identifier(getattr(principal, "tenant_id", None), "principal.tenant_id")
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
                already_consumed = pre_quota.daily_tokens_consumed if pre_quota.last_reset_date == today else 0
                if already_consumed >= policy.max_daily_budget_tokens:
                    raise AIGovernanceQuotaExceededError(
                        request.tenant_id,
                        f"Daily token budget of {policy.max_daily_budget_tokens} already exhausted.",
                    )

                # 2. Single-point Context Composition (RAG + Prompt Template)
                enriched_request = await self._context_composer.compose(request)

                # 3-4. Model Routing, Provider Resolution & Execution (with failover)
                # B4.1: `routing_context` arrives from the caller, so it is
                # untrusted with respect to cloud egress. The effective
                # context is derived server-side from the authoritative
                # tenant (`request.tenant_id`, already rebound to the
                # verified principal above).
                effective_context = await self._authorized_routing_context(request.tenant_id, routing_context)
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
                        "Conversation history write failed after successful generation for request '%s': %s",
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
                timeout_err = AIProviderTimeoutError(f"Global AI generation timeout exceeded ({effective_timeout}s).")
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
        execution_context: Any = None,
    ) -> AgentExecutionResult:
        """Orchestrate a bounded multi-step agent reasoning workflow.

        Identity (M6.2-2, wired through `execution_context` in Phase B):
        same fix as `generate_response` (M6.1-1) and
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

        principal = _principal_from(execution_context)
        if principal is not None:
            principal_tenant_id = require_identifier(getattr(principal, "tenant_id", None), "principal.tenant_id")
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
                result = await self._record_agent_conversation_turn(task, result)
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
        execution_context: Any = None,
    ) -> AgentExecutionResult:
        """Resume a paused agent workflow with a verified ResumeToken.

        Identity (M6.2-2, wired through `execution_context` in Phase B):
        same tenant-correction fix as `orchestrate_agent`.
        """
        start_time = time.perf_counter()
        require_identifier(task.tenant_id, "tenant_id")
        require_identifier(task.task_id, "task_id")

        principal = _principal_from(execution_context)
        if principal is not None:
            principal_tenant_id = require_identifier(getattr(principal, "tenant_id", None), "principal.tenant_id")
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
                result = await self._record_agent_conversation_turn(task, result)
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

    async def cancel_agent_task(self, task_id: str, tenant_id: str, execution_context: Any = None) -> bool:
        """Cancel an active or paused agent task across local and durable task stores.

        `tenant_id` is the store's isolation key, so a verified identity
        overrides the caller's claim (`_authoritative_tenant_id`): otherwise
        a caller in tenant B could cancel tenant A's running agent task
        simply by naming tenant A.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
        return await self._agent_orchestrator.cancel_task(task_id, tenant_id)

    async def get_agent_task(
        self, task_id: str, tenant_id: str, execution_context: Any = None
    ) -> PersistedAgentTaskRecord | None:
        """Retrieve a persisted agent task record by identity.

        Tenant scope comes from the verified identity when one is present —
        the task record carries prompts and reasoning steps, so reading
        another tenant's is a disclosure, not just a lookup.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
        return await self._agent_orchestrator.get_task(task_id, tenant_id)

    async def list_agent_tasks(
        self,
        tenant_id: str,
        status: AgentStatus | str | None = None,
        limit: int = 50,
        execution_context: Any = None,
    ) -> list[PersistedAgentTaskRecord]:
        """List persisted agent task records for a tenant, optionally filtered by status.

        Tenant scope comes from the verified identity when one is present,
        for the same disclosure reason as `get_agent_task`.

        `status` accepts a raw string in addition to `AgentStatus` so this
        method is safe to invoke as a Kernel capability handler, where
        parameters cross a JSON-shaped boundary and arrive as plain `str`.
        An unrecognized value raises `ValueError` (from `AgentStatus`
        itself) rather than reaching the store, where a raw string would
        otherwise fail differently on each backend: the in-memory store's
        `StrEnum` equality would silently "work" while the SQL-backed
        store's `status.value` access would raise `AttributeError`.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
        normalized_status = AgentStatus(status) if isinstance(status, str) else status
        return await self._agent_orchestrator.list_tasks(tenant_id, normalized_status, limit)

    async def invoke_tool(
        self,
        tenant_id: str,
        tool_call: ToolCall,
        authorizer: ToolAuthorizer | None = None,
        execution_context: Any = None,
    ) -> ToolResult:
        """Invoke an authorized tool capability through the tool invoker subsystem.

        Identity (M6.2-2, wired through `execution_context` in Phase B):
        same tenant-correction fix as `generate_response`/
        `orchestrate_agent` -- a caller-supplied `tenant_id` is never
        authoritative once a verified principal is available.
        """
        start_time = time.perf_counter()

        principal = _principal_from(execution_context)
        if principal is not None:
            principal_tenant_id = require_identifier(getattr(principal, "tenant_id", None), "principal.tenant_id")
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
                # M7.6-W3: previously called `self._diagnostics.record_tool_invocation`
                # directly, bypassing `AITelemetryEmitter` -- unlike the DENIED/
                # failed branches below, a successful completion published no
                # domain event and incremented no exporter counter. Now
                # symmetric with `emit_tool_failed`/`emit_tool_denied`.
                await self._telemetry.emit_tool_completed(
                    tenant_id=tenant_id,
                    tool_name=tool_call.tool_name,
                    request_id=tool_call.call_id,
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

    async def get_conversation_history(
        self,
        tenant_id: str,
        conversation_id: str,
        offset: int = 0,
        execution_context: Any = None,
    ) -> list[ConversationTurn]:
        """Return the durable conversation turns for `conversation_id` (M7.2).

        A thin, read-only wrapper over the existing `AIMemoryManager` /
        `IConversationStore` -- no new persistence subsystem. Identity
        (same tenant-correction pattern as `invoke_tool`/`generate_response`,
        wired through `execution_context` in Phase B): a caller-supplied
        `tenant_id` is never authoritative once a verified principal is
        available.
        """
        principal = _principal_from(execution_context)
        if principal is not None:
            principal_tenant_id = require_identifier(getattr(principal, "tenant_id", None), "principal.tenant_id")
            tenant_id = principal_tenant_id

        return await self._memory_manager.get_turns(tenant_id, conversation_id, offset=offset)

    async def _record_agent_conversation_turn(
        self, task: AgentTask, result: AgentExecutionResult
    ) -> AgentExecutionResult:
        """Record a completed agent turn into the same durable conversation
        history `generate_response` already writes to (M7.2), so a chat
        surface built on `orchestrate_agent`/`resume_agent` can recover its
        transcript via `get_conversation_history` after a restart -- exactly
        as a plain generated turn already can. Only terminal outcomes with a
        `final_response` are recorded; `PAUSED_FOR_APPROVAL` (and the
        transient `RUNNING`/`RESUMING`) are deliberately skipped -- there is
        nothing resolved yet to show as a turn.
        """
        if result.status in (AgentStatus.PAUSED_FOR_APPROVAL, AgentStatus.RUNNING, AgentStatus.RESUMING):
            return result
        if result.final_response is None:
            return result

        synthetic_request = LLMRequest(
            request_id=f"{task.task_id}:{result.total_steps}",
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            conversation_id=task.conversation_id,
            prompt=task.goal,
        )
        synthetic_response = LLMResponse(
            request_id=synthetic_request.request_id,
            text_content=result.final_response,
        )
        try:
            await self._memory_manager.append_history(
                tenant_id=task.tenant_id,
                conversation_id=task.conversation_id,
                request=synthetic_request,
                response=synthetic_response,
            )
        except ConversationStoreError as exc:
            self.logger.critical(
                "Conversation history write failed after agent task '%s' completed: %s",
                task.task_id,
                exc,
            )
            result = result.model_copy(update={"degraded": True})
        return result

    # -- Tenant Provider Configuration Handlers (Phase B / B1d) --------------

    @property
    def credential_resolver(self) -> TenantCredentialResolver | None:
        """Per-request tenant credential resolution, or None when unwired.

        Deliberately exposed as the *resolver*, never as resolved values:
        there is no `get_api_key`-shaped accessor on this engine, and no
        credential is ever stored on it. See `credentials.py`.
        """
        return self._credential_resolver

    def _require_provider_config_store(self) -> Any:
        if self._provider_config_store is None:
            raise AIEngineNotConfiguredError(
                "Provider configuration is unavailable: the AI engine was constructed without a "
                "provider_config_store. Configure it through KernelProductionBootstrap."
            )
        return self._provider_config_store

    async def configure_provider(
        self,
        provider_id: str,
        api_key: str | None = None,
        default_model: str | None = None,
        enabled: bool = True,
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Configure one AI provider for the calling tenant (Phase B / B1d).

        The secret parameter is named `api_key` on purpose and must keep that
        name: `core.idempotency.sanitize_for_persistence` redacts by exact
        key, and `api_key` is in its `SENSITIVE_KEY_NAMES` set. The Kernel
        dispatcher runs that sanitizer over `request.parameters` before they
        reach the audit log, so a rename to `key`/`token_value`/`credential_
        value` would silently start writing live provider credentials into
        the audit trail. This is why the parameter is not called anything
        more descriptive.

        The key is handed straight to Security Engine's `SecretStore` (via
        the injected `secret_putter`) and only the resulting handle is
        persisted; `ai_provider_configs` has no column that could hold it.
        The returned dict likewise carries the handle and never the value.

        Tenant scope comes from the verified execution context, never from a
        parameter — configuring a provider *for another tenant* would let a
        caller plant a credential the other tenant's requests would then
        use.
        """
        store = self._require_provider_config_store()
        tenant_id = _authoritative_tenant_id(execution_context, "")
        require_identifier(tenant_id, "tenant_id")
        require_identifier(provider_id, "provider_id")

        secret_handle: str | None = None
        if api_key is not None:
            if not api_key.strip():
                raise ValueError("api_key must not be empty or whitespace-only.")
            if self._secret_putter is None:
                raise AIEngineNotConfiguredError(
                    "Cannot store a provider credential: the AI engine was constructed without a "
                    "secret_putter. Refusing to record a configuration that claims a credential it did not store."
                )
            secret_handle = provider_secret_handle(provider_id)
            # Secret first, configuration second, deliberately. If the
            # config write then fails, the stored secret is orphaned but
            # unreachable (nothing references the handle) and the next
            # configure overwrites it. The reverse order would leave a
            # configuration advertising `has_credential=True` for a
            # credential that was never stored — a provider that looks set
            # up and is not.
            await self._secret_putter(secret_handle, tenant_id, api_key)

        config = await store.upsert(
            AIProviderConfig(
                tenant_id=tenant_id,
                provider_id=provider_id,
                enabled=enabled,
                secret_handle=secret_handle,
                default_model=default_model,
            )
        )
        return _provider_config_view(config)

    async def list_provider_configs(self, execution_context: Any = None) -> list[dict[str, Any]]:
        """List the calling tenant's provider configurations.

        Returns handles and flags only -- never a credential, and never
        another tenant's rows (the tenant comes from the verified identity,
        and the store filters in SQL).
        """
        store = self._require_provider_config_store()
        tenant_id = _authoritative_tenant_id(execution_context, "")
        require_identifier(tenant_id, "tenant_id")
        configs = await store.list_for_tenant(tenant_id)
        return [_provider_config_view(config) for config in configs]

    async def remove_provider_config(self, provider_id: str, execution_context: Any = None) -> dict[str, Any]:
        """Remove one of the calling tenant's provider configurations.

        The `SecretStore` entry is intentionally left in place: this engine
        has no authority to delete Security Engine records, and a config row
        removed by mistake is recoverable while a destroyed secret is not.
        The orphaned handle is unreachable without a configuration row
        pointing at it.
        """
        store = self._require_provider_config_store()
        tenant_id = _authoritative_tenant_id(execution_context, "")
        require_identifier(tenant_id, "tenant_id")
        require_identifier(provider_id, "provider_id")
        removed = await store.delete(tenant_id, provider_id)
        return {"provider_id": provider_id, "tenant_id": tenant_id, "removed": removed}

    async def test_provider_connection(self, provider_id: str, execution_context: Any = None) -> dict[str, Any]:
        """Capability handler for `kortex.ai.provider.test` (Phase B / B2).

        Generic across every provider: resolves the registered provider and
        the calling tenant's own credential (never a caller-supplied one --
        tenant scope comes from the verified execution context, same as
        every other provider-configuration handler), then delegates the
        actual validation to `BaseAIProvider.test_connection`, which each
        provider implements on its own terms. This method contains no
        provider-specific logic and never will -- see
        `OpenAIProvider.test_connection`/`discover_models` for where that
        lives.

        Never raises on a provider-side failure: `PermanentProviderError`/
        `TransientProviderError`/`AIProviderTimeoutError` are caught and
        normalized into `{"connected": False, "detail": "..."}` so a bad
        credential is an ordinary, actionable result rather than a thrown
        exception the caller must specially handle. An unexpected exception
        type is NOT caught here and propagates -- "do not swallow provider
        errors" applies to failures this handler cannot already explain.

        Discovered models (when the provider's `test_connection` succeeds)
        are included as `models` -- live, tenant-scoped, and exactly what
        `discover_models` returns; a live source of truth alongside the
        pass/fail signal without inventing a second new capability for it.

        `discover_models` is a SECOND, independent provider call and can
        fail on its own even when `test_connection` just succeeded (a
        transient blip between the two round trips, a rate limit hit on the
        second call). That failure must not undo the connection result:
        the credential IS valid, so `connected` stays `True`, `models` is
        empty, and `detail` reports the discovery failure -- this is a
        successful connection with an incomplete discovery, not a failed
        connection.
        """
        tenant_id = _authoritative_tenant_id(execution_context, "")
        require_identifier(tenant_id, "tenant_id")
        require_identifier(provider_id, "provider_id")

        try:
            provider = self._provider_registry.get(provider_id)
        except ProviderNotFoundError:
            return {
                "provider_id": provider_id,
                "tenant_id": tenant_id,
                "connected": False,
                "detail": f"Provider '{provider_id}' is not registered.",
                "models": [],
            }

        credential: str | None = None
        if self._credential_resolver is not None:
            resolved = await self._credential_resolver.resolve(tenant_id, provider_id)
            credential = resolved.plaintext if resolved is not None else None

        try:
            connected = await provider.test_connection(credential)
        except (PermanentProviderError, TransientProviderError, AIProviderTimeoutError) as exc:
            return {
                "provider_id": provider_id,
                "tenant_id": tenant_id,
                "connected": False,
                "detail": str(exc),
                "models": [],
            }

        models: list[dict[str, Any]] = []
        discovery_detail: str | None = None
        if connected:
            try:
                discovered = await provider.discover_models(credential)
                models = [m.model_dump(mode="json") for m in discovered]
            except (PermanentProviderError, TransientProviderError, AIProviderTimeoutError) as exc:
                discovery_detail = str(exc)

        return {
            "provider_id": provider_id,
            "tenant_id": tenant_id,
            "connected": bool(connected),
            "detail": discovery_detail if connected else "Connection check did not succeed.",
            "models": models,
        }

    # -- AI Governance Capability Handlers (M5.5) ----------------------------

    async def evaluate_governance_policy(
        self,
        tenant_id: str,
        prompt: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Evaluate prompt guardrails and proposed tool calls against tenant policy.

        Tenant scope comes from the verified identity when one is present:
        evaluating against another tenant's policy both reveals that
        tenant's configured guardrails and would let a caller pick whichever
        tenant's policy is most permissive.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
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
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Create or update a tenant AI governance policy.

        The tenant lives inside the submitted policy, so the verified
        identity is applied to the validated model rather than to a
        parameter — without it, a caller could rewrite another tenant's
        guardrails (for example disabling them) by naming that tenant in
        the payload.
        """
        pol = AIGovernancePolicy.model_validate(policy) if isinstance(policy, dict) else policy
        authoritative = _authoritative_tenant_id(execution_context, pol.tenant_id)
        if authoritative != pol.tenant_id:
            pol = pol.model_copy(update={"tenant_id": authoritative})
        saved = await self._governance_manager.set_policy(pol)
        return saved.model_dump(mode="json")

    async def get_governance_policy(
        self,
        tenant_id: str,
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Retrieve the governance policy for a tenant.

        Tenant scope comes from the verified identity when one is present.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
        require_identifier(tenant_id, "tenant_id")
        policy = await self._governance_manager.get_policy(tenant_id)
        return policy.model_dump(mode="json")

    async def get_tenant_quota(
        self,
        tenant_id: str,
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Retrieve token consumption quota for a tenant.

        Tenant scope comes from the verified identity when one is present —
        a quota reading exposes another tenant's AI usage volume.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
        require_identifier(tenant_id, "tenant_id")
        quota = await self._governance_manager.quota_manager.get_or_create_quota(tenant_id)
        return quota.model_dump(mode="json")

    async def update_tenant_quota(
        self,
        quota: dict[str, Any] | AITenantQuota,
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Update token consumption quota and concurrency limits for a tenant.

        The tenant lives inside the submitted quota, so the verified
        identity is applied to the validated model — the same correction
        `upsert_governance_policy` makes, and for the same reason: raising
        another tenant's budget is a cost attack on that tenant.
        """
        q = AITenantQuota.model_validate(quota) if isinstance(quota, dict) else quota
        authoritative = _authoritative_tenant_id(execution_context, q.tenant_id)
        if authoritative != q.tenant_id:
            q = q.model_copy(update={"tenant_id": authoritative})
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
        execution_context: Any = None,
    ) -> list[dict[str, Any]]:
        """Query immutable AI decision audit records.

        Tenant scope comes from the verified identity when one is present.
        Decision records carry prompts, reasoning, and tool arguments, so
        this is the highest-value read in the governance surface.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
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
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Check and sanitize content against prompt injection, safety patterns, and PII.

        `tenant_id` stays optional (`None` means "evaluate against the
        built-in defaults"), but when a verified identity is present it
        selects the policy, so a caller cannot borrow another tenant's more
        permissive guardrails to get text through.
        """
        if _principal_from(execution_context) is not None:
            tenant_id = _authoritative_tenant_id(execution_context, tenant_id or "")
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
        execution_context: Any = None,
    ) -> dict[str, Any]:
        """Create a durable human approval request for an AI action.

        Tenant scope comes from the verified identity when one is present —
        otherwise a caller could inject an approval request into another
        tenant's approval queue.
        """
        tenant_id = _authoritative_tenant_id(execution_context, tenant_id)
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
            {"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))} for c in tool_calls
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

        Tenant concurrency (M7.6-W1): this is the *only* path that resumes
        an approved AI-originated mutation in production (a human decision
        always arrives here, asynchronously, via this event — never through
        the synchronous `resume_agent` API). Before this fix, the resumed
        `resume_task` call below was not wrapped in
        `self._throttler.acquire_agent_slot(...)`, unlike both synchronous
        entry points (`orchestrate_agent`, `resume_agent`), which already
        wrap their own `resume_task`/`run_task` calls in it — a real,
        verified asymmetry (M7.6 planning report §8): every mutating AI
        tool's approval-resume traffic bypassed the same per-tenant
        concurrent-agent-workflow cap the rest of the control plane
        enforces. Fixed by wrapping only the APPROVED branch's `resume_task`
        call (not the whole handler, which would incorrectly also gate the
        REJECTED branch's `cancel_task` -- rejection must never consume a
        slot) in the same, single, authoritative `TenantConcurrencyThrottler`
        already owned by this engine -- no second throttling mechanism.
        `AgentOrchestrator` itself has no throttler awareness (confirmed via
        Graphify: zero `uses` edge to `TenantConcurrencyThrottler`), so this
        is the correct, lowest layer that closes the gap without risking a
        double acquisition against `resume_agent`'s own wrapping.
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
            if record is None or record.status != AgentStatus.PAUSED_FOR_APPROVAL or record.resume_token is None:
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
                try:
                    async with self._throttler.acquire_agent_slot(tenant_id):
                        resumed_result = await self._agent_orchestrator.resume_task(
                            task=record.task,
                            resume_token=record.resume_token,
                            approved_tool_calls=record.pending_tool_calls,
                        )
                        await self._record_agent_conversation_turn(record.task, resumed_result)
                except TenantQuotaExceededError:
                    # The tenant is already at its concurrent-agent-workflow cap
                    # (`acquire_agent_slot` fails fast, it never waits/queues --
                    # see throttling.py). Unlike orchestrate_agent/resume_agent
                    # (synchronous callers that can surface this to a retrying
                    # caller), this is an asynchronous event handler with no
                    # caller to return an error to. The approval decision itself
                    # is already durably recorded by the Workflow Engine (this
                    # handler neither created nor consumes it) -- deliberately
                    # leave the task PAUSED_FOR_APPROVAL rather than cancel a
                    # validly-approved action. Safe under this handler's own
                    # pre-existing idempotency guarantee above (a no-op once the
                    # task is no longer PAUSED_FOR_APPROVAL): a later redelivery
                    # or operator-triggered replay of this exact event can still
                    # resume it once a slot frees up, with zero risk of double
                    # execution.
                    self.logger.warning(
                        "Deferring resume of agent task '%s' for tenant '%s': tenant "
                        "concurrent-agent-workflow limit reached. The approval decision "
                        "remains durably recorded; task stays PAUSED_FOR_APPROVAL for a "
                        "later retry.",
                        task_id,
                        tenant_id,
                    )
                    return
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
