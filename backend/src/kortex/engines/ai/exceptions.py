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


class AIProviderError(AIOrchestrationError):
    """Base exception for AI provider adapter errors.

    Intentionally left as a single base class in Milestone 1. Specific leaf
    exception types (unavailable, authentication failure, rate limit,
    timeout, model not found) are introduced in Milestone 2 once the
    reference provider's error-simulation behavior determines which are
    actually needed, rather than being guessed in advance.
    """


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


__all__ = [
    "AIOrchestrationError",
    "AIProviderError",
    "NoRoutableProviderError",
    "ProviderAlreadyRegisteredError",
    "ProviderNotFoundError",
    "ProviderNotRoutableError",
    "ProviderValidationError",
    "RoutingError",
    "RoutingValidationError",
]
