"""Custom exception hierarchy for the KORTEX OS AI Orchestration Engine.

All AI Orchestration Engine exceptions inherit from `KortexError`
(kortex.core.exceptions), following the existing KORTEX exception
convention established by the Security Engine.

No exception raised by this package may ever include a plaintext API key,
bearer token, or other credential material in its message or any other
attribute. Credentials are referenced exclusively via Security Engine
secret handle (see `models.AIProviderMetadata.secret_handle`); this package
never resolves or holds the underlying secret value.
"""

from __future__ import annotations

from kortex.core.exceptions import KortexError


class AIOrchestrationError(KortexError):
    """Base exception for all AI Orchestration Engine errors."""


class AIBootstrapError(AIOrchestrationError):
    """Raised when runtime bootstrap, dependency assembly, or startup validation fails."""



class AIProviderError(AIOrchestrationError):
    """Base exception for AI provider adapter errors.

    Intentionally left as a single base class in Milestone 1. Specific leaf
    exception types (unavailable, authentication failure, rate limit,
    timeout, model not found) are introduced in Milestone 2 once the
    reference provider's error-simulation behavior determines which are
    actually needed, rather than being guessed in advance.
    """


class AIProviderTimeoutError(AIProviderError, TimeoutError):
    """Raised when an AI provider execution exceeds its configured timeout threshold."""


class CircuitBreakerOpenError(AIProviderError):
    """Raised when a request is rejected because the provider circuit breaker is OPEN."""


class ProviderFallbackExhaustedError(AIProviderError):
    """Raised when all candidate providers in a fallback chain fail."""


class TransientProviderError(AIProviderError):
    """Raised when an AI provider encounters a temporary, retryable error."""


class PermanentProviderError(AIProviderError):
    """Raised when an AI provider encounters a non-retryable error."""



class ProviderAlreadyRegisteredError(AIOrchestrationError):
    """Raised by `ProviderRegistry.register` when `provider_id` is already registered.

    Subclasses `AIOrchestrationError` directly, not `AIProviderError`:
    this is a registry bookkeeping failure, not a provider execution
    failure, mirroring Connector Engine's own separation between
    `ConnectorDriverError` (registry-level) and `ConnectorOperationError`
    (execution-level).
    """


class ProviderNotFoundError(AIOrchestrationError):
    """Raised when a requested `provider_id` is not registered in `ProviderRegistry`."""


class ProviderValidationError(AIOrchestrationError):
    """Raised when a provider object or its metadata fails registration validation
    (not a `BaseAIProvider`, inaccessible/invalid `metadata` property, or empty
    `provider_id`)."""


class RoutingError(AIOrchestrationError):
    """Base exception for `ModelRouter` routing failures.

    Distinct from both `AIProviderError` (provider *execution* failures) and
    the registry bookkeeping errors above: a routing failure means no
    suitable provider could be *chosen*, which is neither a registry
    integrity problem nor an execution problem. A common base is provided
    (unlike the flat registry errors) because callers have a real need to
    catch "any routing failure" as one category.
    """


class RoutingValidationError(RoutingError):
    """Raised when a routing context is malformed, contains an unknown key,
    or carries a value of the wrong type.

    Messages report offending field names and error types only — never the
    submitted values.
    """


class NoRoutableProviderError(RoutingError):
    """Raised when no registered provider satisfies the routing constraints."""


class ProviderNotRoutableError(RoutingError):
    """Raised when an explicitly pinned provider is registered but cannot be
    routed to — it is metadata-only (non-executable), or it misreports its
    own identity relative to the registry key it is stored under.
    """


class MemoryValidationError(AIOrchestrationError):
    """Raised when conversation-memory arguments are invalid.

    Covers a blank/whitespace `tenant_id` or `conversation_id`, and a
    request whose own `tenant_id`/`conversation_id` disagree with the
    explicit arguments given to `append_history` — the latter would
    otherwise store one tenant's content under another tenant's key.

    Note there is deliberately no `MemoryError` base class: that name is a
    Python builtin, and shadowing it inside this package would make
    `except MemoryError` mean different things depending on import order.
    The two concrete errors subclass `AIOrchestrationError` directly.
    """


class ConversationStoreError(AIOrchestrationError):
    """Raised when a conversation store operation fails.

    Covers transaction/storage failures and a lost sequence race (surfaced
    as an integrity violation rather than a silently duplicated ordinal).
    Never swallowed: a history failure means the conversation record is
    wrong, which callers must be able to detect.
    """


class ContextCompositionError(AIOrchestrationError):
    """Raised when a context-composition request is inconsistent.

    Principally the anti-dead-port rule: asking for knowledge retrieval
    when no `IKnowledgeQueryPort` is configured. Connector Engine's
    `secret_resolver` shows the failure mode this prevents — an injection
    port that ships unwired and silently no-ops, so the capability appears
    present while doing nothing.
    """


class KnowledgeRetrievalError(AIOrchestrationError):
    """Raised when knowledge retrieval was requested and could not be completed.

    Retrieval is opt-in, so a caller reaching this asked for grounded
    context. Returning an ungrounded answer that is indistinguishable from
    a grounded one is the worse outcome, so retrieval failures surface
    rather than degrade. Also raised when an adapter violates the port
    contract by returning more documents than it was asked for.

    Never carries retrieved content: documents are tenant-sensitive.
    """


class ToolInvocationError(AIOrchestrationError):
    """Base exception for all tool invocation failures in the AI Engine."""


class ToolValidationError(ToolInvocationError):
    """Raised when tool arguments fail schema validation, exceed byte size limits,
    or tool definitions contain invalid names/parameters."""


class ToolNotFoundError(ToolInvocationError):
    """Raised when a requested tool name is not registered in the ToolRegistry."""


class ToolAuthorizationError(ToolInvocationError):
    """Raised when a tool authorizer explicitly denies invocation of a capability."""


class ToolExecutionError(ToolInvocationError):
    """Raised when an underlying tool execution port throws an unhandled error."""


class ToolTimeoutError(ToolInvocationError):
    """Raised when a tool execution exceeds its configured timeout threshold."""


# ---------------------------------------------------------------------------
# M7 — Agent Orchestration exceptions
# ---------------------------------------------------------------------------


class AgentOrchestrationError(AIOrchestrationError):
    """Base exception for all M7 Agent Orchestration failures.

    Every leaf type carries a `task_id` field so callers can correlate
    exceptions with the agent task that raised them. Sensitive content
    (tenant data, prompt text, tool arguments) is NEVER included in
    exception messages.
    """

    def __init__(self, task_id: str, message: str) -> None:
        super().__init__(message)
        self.task_id = task_id


class AgentValidationError(AgentOrchestrationError):
    """Raised when an `AgentTask` is malformed, identifiers are invalid,
    or a `ResumeToken` fails verification (mismatch, expiry, or bad hash)."""


class AgentExecutionTimeoutError(AgentOrchestrationError):
    """Raised when an agent task exceeds its overall `timeout_seconds` budget.

    Maps to `AgentStatus.TIMED_OUT`.
    """


class AgentStepLimitExceededError(AgentOrchestrationError):
    """Raised when an agent task exceeds its `max_steps` budget.

    Maps to `AgentStatus.STEP_LIMIT_EXCEEDED`.
    """


class AgentLoopDetectedError(AgentOrchestrationError):
    """Raised when an agent produces identical tool call batches on
    `LOOP_DETECTION_WINDOW` consecutive iterations with no state change.

    Maps to `AgentStatus.LOOP_DETECTED`.
    """


class AgentCancelledError(AgentOrchestrationError):
    """Raised when an agent task is explicitly cancelled via a cancellation token.

    Maps to `AgentStatus.CANCELLED`.
    """


class AgentNotFoundError(AgentOrchestrationError):
    """Raised when an agent task cannot be found in memory or persistent storage."""


class AgentStateConflictError(AgentOrchestrationError):
    """Raised when a concurrent worker conflicts on optimistic task resumption or illegal state transition."""


class AgentTaskStoreError(AIOrchestrationError):
    """Raised when underlying task storage operations fail."""


class BridgeValidationError(AIOrchestrationError, ValueError):
    """Raised when bridge invocation arguments fail identity or name validation."""


class BridgeExecutionError(AIOrchestrationError):
    """Raised when capability invocation through the Kernel bridge fails."""


class TenantQuotaExceededError(AIOrchestrationError):
    """Raised when a tenant exceeds active concurrency or rate quota limits."""

    def __init__(self, tenant_id: str, message: str) -> None:
        super().__init__(f"Tenant '{tenant_id}' quota exceeded: {message}")
        self.tenant_id = tenant_id


__all__ = [
    "AIBootstrapError",
    "AIOrchestrationError",
    "AIProviderError",
    "AIProviderTimeoutError",
    "AgentCancelledError",
    "AgentExecutionTimeoutError",
    "AgentLoopDetectedError",
    "AgentNotFoundError",
    "AgentOrchestrationError",
    "AgentStateConflictError",
    "AgentStepLimitExceededError",
    "AgentTaskStoreError",
    "AgentValidationError",
    "BridgeExecutionError",
    "BridgeValidationError",
    "CircuitBreakerOpenError",
    "ContextCompositionError",
    "ConversationStoreError",
    "KnowledgeRetrievalError",
    "MemoryValidationError",
    "NoRoutableProviderError",
    "PermanentProviderError",
    "ProviderAlreadyRegisteredError",
    "ProviderFallbackExhaustedError",
    "ProviderNotFoundError",
    "ProviderNotRoutableError",
    "ProviderValidationError",
    "RoutingError",
    "RoutingValidationError",
    "TenantQuotaExceededError",
    "ToolAuthorizationError",
    "ToolExecutionError",
    "ToolInvocationError",
    "ToolNotFoundError",
    "ToolTimeoutError",
    "ToolValidationError",
    "TransientProviderError",
]


