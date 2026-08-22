"""Public abstract interfaces and protocol declarations for the KORTEX OS AI Orchestration Engine.

Defines the five Protocol interfaces named in
`ai_orchestration_engine_implementation_spec.md` section 4
(`IAIOrchestrationEngine`, `IBaseAIProvider`, `IModelRouter`,
`IAIMemoryManager`, `IAIToolInvoker`) and no others. Diagnostics
(`IEngineDiagnostics`), capability registration, and Kernel wiring are
explicitly out of scope for Milestone 1 (see `base_provider.py` and
`ai_orchestration_engine_implementation_spec.md` section 1.7-1.8, which are
Milestone 7 concerns).

Where a method's parameter or return type depends on a model deferred past
Milestone 1 (`ToolDefinition`, Milestone 5; `AgentTask`, Milestone 6), the
signature uses a generic `dict[str, Any]` placeholder rather than omitting
the method — the approved specification names all four
`IAIOrchestrationEngine` methods, and a placeholder type can be narrowed
later without removing a method a caller may already depend on.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse

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

    Assembled in Milestone 7; no implementation exists yet. Fixing this
    shape now lets Workflow Engine's future integration
    (`generate_response`) and the provider registry's future integration
    (`register_provider`) plan against a stable contract.
    """

    async def generate_response(self, request: LLMRequest) -> LLMResponse:
        """Generate an AI response for the given request."""
        ...

    async def orchestrate_agent(self, task: dict[str, Any]) -> dict[str, Any]:
        """Orchestrate a multi-step agent task.

        `task`/return type are generic placeholders pending Milestone 6's
        `AgentTask` model.
        """
        ...

    async def invoke_tool(
        self, tool_call: dict[str, Any], authorizer: ToolAuthorizer
    ) -> dict[str, Any]:
        """Invoke a tool/capability call on behalf of an AI request.

        `tool_call`/return type are generic placeholders pending Milestone
        5's `ToolDefinition` model.
        """
        ...

    def register_provider(self, provider: AIProviderMetadata) -> None:
        """Register an AI provider with the engine."""
        ...


__all__ = [
    "IAIMemoryManager",
    "IAIOrchestrationEngine",
    "IAIToolInvoker",
    "IBaseAIProvider",
    "IModelRouter",
    "ToolAuthorizer",
]
