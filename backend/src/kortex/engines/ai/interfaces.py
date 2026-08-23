"""Public abstract interfaces and protocol declarations for the KORTEX OS AI Orchestration Engine.

Defines the five Protocol interfaces named in
`ai_orchestration_engine_implementation_spec.md` section 4
(`IAIOrchestrationEngine`, `IBaseAIProvider`, `IModelRouter`,
`IAIMemoryManager`, `IAIToolInvoker`), plus the two the production runtime
added: `IEngineDiagnostics` (the platform-standard diagnostics surface) and
`IKernelBridge` (the sole route from this package to the Kernel).

Milestone 1 declared several `IAIOrchestrationEngine` parameters as
`dict[str, Any]` placeholders because the models they referred to
(`AgentTask`, `ToolCall`, `ToolResult`) did not exist yet. Those
placeholders have since been narrowed to the concrete types, so every
signature here now matches its implementation exactly. The concrete types
are imported under `TYPE_CHECKING` because `tools.py` imports this module
and a runtime import would be circular.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse

if TYPE_CHECKING:
    # Type-only. `tools.py` imports this module, so importing these at
    # runtime would be circular; `from __future__ import annotations` above
    # keeps every annotation a string, so this guard is sufficient.
    from kortex.engines.ai.agent import AgentExecutionResult, AgentTask
    from kortex.engines.ai.base_provider import BaseAIProvider
    from kortex.engines.ai.router import RoutingContext
    from kortex.engines.ai.tools import ToolCall, ToolResult

ToolAuthorizer = Callable[[str, dict[str, Any]], Awaitable[bool]]
"""Signature for the mandatory authorization check `IAIToolInvoker.invoke` requires.

Takes the tool/capability name and its call arguments, returns whether the
call is authorized. Making this a required parameter — never optional, and
never satisfied by an internal, engine-local check — is a deliberate
Milestone 1 decision: Connector Engine's own tool/action dispatch
(`kortex.engines.connector.engine.ConnectorEngine.execute_action`) performs
a local, self-contained permission check instead of calling Security
Engine's `AuthorizationEngine`. `IAIToolInvoker` must not repeat that
pattern; the real implementation (Milestone 5) is expected to satisfy this
callable with `SecurityEngine.authorize_strict`, not a bespoke check.
"""


@runtime_checkable
class IBaseAIProvider(Protocol):
    """Structural protocol for all AI provider adapters.

    Mirrors `base_provider.BaseAIProvider` (the nominal ABC providers
    actually inherit from) as a duck-typed equivalent, following the same
    dual representation Connector Engine uses for
    `IBaseConnectorDriver`/`BaseConnectorDriver`.
    """

    @property
    def provider_id(self) -> str:
        """Return unique provider identifier string."""
        ...

    @property
    def metadata(self) -> AIProviderMetadata:
        """Return immutable provider metadata object."""
        ...

    @property
    def supported_models(self) -> list[str]:
        """Return list of model identifiers this provider exposes."""
        ...

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a text completion for the given request."""
        ...

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for the given input texts."""
        ...

    async def health_check(self) -> bool:
        """Return whether this provider is currently reachable."""
        ...


@runtime_checkable
class IModelRouter(Protocol):
    """Protocol for selecting the provider/model that should handle a request.

    Routing logic itself (task type, latency, cost, privacy, offline state)
    is Milestone 3 scope; only the selection signature is fixed here.
    """

    async def select_model(
        self, request: LLMRequest, context: dict[str, Any]
    ) -> AIProviderMetadata:
        """Select the provider/model that should handle the given request."""
        ...


@runtime_checkable
class IAIMemoryManager(Protocol):
    """Protocol for conversation context retrieval and history recording.

    No implementation, storage, or retrieval logic exists in Milestone 1 —
    this only fixes the shape a future Storage Engine-backed implementation
    (Milestone 4) must satisfy, keyed by `conversation_id` and `tenant_id`
    so that history remains provider/model-independent.
    """

    async def get_context(self, tenant_id: str, conversation_id: str) -> list[str]:
        """Return prior context document references for the given conversation."""
        ...

    async def append_history(
        self, tenant_id: str, conversation_id: str, request: LLMRequest, response: LLMResponse
    ) -> None:
        """Record a completed request/response turn against the conversation history."""
        ...


@runtime_checkable
class IAIToolInvoker(Protocol):
    """Protocol for translating an AI-requested tool call into an authorized capability invocation.

    `authorizer` is mandatory, not optional — see `ToolAuthorizer` above.
    """

    async def invoke(
        self, tool_call: dict[str, Any], authorizer: ToolAuthorizer
    ) -> dict[str, Any]:
        """Invoke a tool/capability call after confirming authorization via `authorizer`."""
        ...


@runtime_checkable
class IAIOrchestrationEngine(Protocol):
    """Primary facade interface for the AI Orchestration Engine.

    Implemented by `engine.AIOrchestrationEngine`. The signatures below are
    the real, current contract: Milestone 1 declared them with
    `dict[str, Any]` placeholders because `AgentTask`, `ToolCall`, and
    `ToolResult` did not exist yet, and they were narrowed to the concrete
    models once those milestones landed.

    The concrete types are imported under `TYPE_CHECKING` only. `tools.py`
    imports this module, so a runtime import of `tools`/`agent` here would
    be circular; `from __future__ import annotations` makes every
    annotation below a string, so the guarded import is sufficient.
    """

    async def generate_response(
        self,
        request: LLMRequest,
        routing_context: RoutingContext | None = None,
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        """Generate an AI response for the given request."""
        ...

    async def orchestrate_agent(
        self, task: AgentTask, authorizer: ToolAuthorizer | None = None
    ) -> AgentExecutionResult:
        """Orchestrate a bounded multi-step agent reasoning workflow."""
        ...

    async def invoke_tool(
        self,
        tenant_id: str,
        tool_call: ToolCall,
        authorizer: ToolAuthorizer | None = None,
    ) -> ToolResult:
        """Invoke a tool/capability call on behalf of an AI request.

        Unlike `IAIToolInvoker.invoke`, whose `authorizer` is mandatory,
        this facade-level parameter is an **optional defense-in-depth
        pre-check**. It is not the authorization gate and must never be
        mistaken for one: the production execution port
        (`engine.KernelToolExecutionPort`) reaches capabilities only through
        `IKernelBridge.invoke_capability`, so every call is authenticated
        and authorized by `CapabilityDispatcher` -> Security Engine before a
        handler runs. That is the mandatory gate, per
        `ai_orchestration_engine_implementation_spec.md` section 18 ("Tool
        invocation strictly checked by Kernel authorization middleware") and
        the Constitution's rule that authority decisions live in Security
        Engine, never in a calling engine.

        Supplying an `authorizer` adds an earlier, engine-local rejection;
        omitting it does not skip authorization. The bootstrap assembler
        refuses to build a production engine without a kernel bridge, so
        the Kernel-backed port cannot be silently absent in production.
        """
        ...

    def register_provider(self, provider: BaseAIProvider) -> None:
        """Register an AI provider with the engine.

        Takes the provider itself, not its metadata: the registry stores
        executable providers, and a metadata-only registration produces a
        deliberately non-executable `MetadataOnlyAIProvider`.
        """
        ...


@runtime_checkable
class IEngineDiagnostics(Protocol):
    """Standardized diagnostics interface exposed by all KORTEX System Engines."""

    def health(self) -> dict[str, Any]:
        """Return operational health status and diagnostic checks."""
        ...

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and throughput metrics."""
        ...

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and system environment details."""
        ...

    def status(self) -> str:
        """Return current engine state name string."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> list[str]:
        """Return list of capability strings registered by the engine."""
        ...


@runtime_checkable
class IKernelBridge(Protocol):
    """Bridge protocol decoupling the AI engine from concrete Kernel runtime."""

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Callable[..., Any] | None = None,
        parameters_schema: dict[str, Any] | None = None,
        returns_schema: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
    ) -> object:
        """Register a canonical system capability with the Kernel Registry."""
        ...

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        sender: str = "ai",
    ) -> object:
        """Publish an asynchronous system event to the Kernel Event Engine."""
        ...

    async def invoke_capability(
        self,
        name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        user_id: str | None = None,
    ) -> object:
        """Invoke a registered capability through the Kernel enforcement boundary."""
        ...


__all__ = [
    "IAIMemoryManager",
    "IAIOrchestrationEngine",
    "IAIToolInvoker",
    "IBaseAIProvider",
    "IEngineDiagnostics",
    "IKernelBridge",
    "IModelRouter",
    "ToolAuthorizer",
]
