"""AI Provider Registry for the KORTEX OS AI Orchestration Engine.

Implements `ProviderRegistry`: a small, deterministic, thread-safe, purely
in-memory registry of `BaseAIProvider` instances, keyed by `provider_id`.
Mirrors the structural role of
`kortex.engines.connector.registry.ConnectorDriverRegistry`.

Out of scope here, and left entirely to later milestones: model routing/
selection (Milestone 3), credential resolution and authorization (Security
Engine, unmodified), Kernel capability registration and event publication
(Milestone 7), and any provider health-state machine beyond the single
`bool` `BaseAIProvider.health_check()` already defines.
"""

from __future__ import annotations

import threading

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    AIProviderError,
    ProviderAlreadyRegisteredError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from kortex.engines.ai.models import AIProviderMetadata, EndpointType, LLMRequest, LLMResponse


class MetadataOnlyAIProvider(BaseAIProvider):
    """A `BaseAIProvider` wrapper around bare `AIProviderMetadata`, with no
    underlying implementation behind it.

    Exists so a provider's identity/metadata can be registered — and
    therefore discovered via `list_providers()`/`find_providers_*()` —
    before or without a working implementation, mirroring
    `ConnectorDriverRegistry`'s `MetadataDriverWrapper`. It never pretends
    to be a real provider: `generate_text`/`generate_embeddings` always
    raise `AIProviderError` rather than fabricating a response, and
    `health_check` always returns `False` rather than ever falsely
    reporting reachability. It never contacts any external service.
    """

    def __init__(self, metadata: AIProviderMetadata) -> None:
        self._metadata = metadata

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise AIProviderError(
            f"Provider '{self.provider_id}' is registered as metadata only; "
            "it has no implementation to generate text."
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise AIProviderError(
            f"Provider '{self.provider_id}' is registered as metadata only; "
            "it has no implementation to generate embeddings."
        )

    async def health_check(self) -> bool:
        """Always `False` — a metadata-only registration has no real
        endpoint to be reachable, and must never falsely report as healthy."""
        return False


class ProviderRegistry:
    """Thread-safe registry for managing `BaseAIProvider` instances.

    Responsibilities:
    1. Registering `BaseAIProvider` instances (or bare `AIProviderMetadata`,
       wrapped in `MetadataOnlyAIProvider`) under their `provider_id`.
    2. Validating provider contract/metadata completeness prior to registration.
    3. Rejecting duplicate `provider_id` registrations.
    4. Endpoint-type and supported-model based provider discovery.
    5. Thread-safe, deterministic (registration-order) provider resolution.

    Deliberately does not expose any health-check delegation method:
    `ConnectorDriverRegistry` has none either — health/connectivity is a
    property of a provider instance, called directly by whoever holds it
    (`await registry.get(provider_id).health_check()`), never mediated by
    the registry itself. The registry stores and retrieves; it does not
    maintain, cache, or transition any health state.

    Deliberately does not provide a single-result "pick one match"
    convenience for endpoint-type or model lookups (unlike
    `ConnectorDriverRegistry.get_driver_by_action`/`get_driver_by_capability`,
    which auto-select the first match): selecting among multiple candidates
    is a routing decision and belongs to Milestone 3, not to this registry.
    `find_providers_by_endpoint_type`/`find_providers_supporting_model`
    return unranked candidate lists only.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory provider catalog and reentrant thread lock."""
        self._providers: dict[str, BaseAIProvider] = {}
        self._lock = threading.RLock()

    def register(self, provider: BaseAIProvider | AIProviderMetadata) -> BaseAIProvider:
        """Register a `BaseAIProvider` instance, or bare `AIProviderMetadata`.

        When `AIProviderMetadata` is supplied, it is wrapped in
        `MetadataOnlyAIProvider` before registration.

        Does not call `provider.health_check()`, `generate_text()`, or
        `generate_embeddings()` — an unhealthy or currently-unreachable
        provider is still a valid registration, and registration is purely
        a bookkeeping operation on an already-constructed object.

        Credential consistency (secret handle required when a credential
        type is declared) is already enforced by `AIProviderMetadata`'s own
        Pydantic validator at construction time; this method does not
        duplicate that check.

        Args:
            provider: A `BaseAIProvider` instance, or an `AIProviderMetadata`
                instance to register as metadata-only.

        Returns:
            The registered `BaseAIProvider` instance (the `provider` argument
            itself, or the `MetadataOnlyAIProvider` wrapping it).

        Raises:
            ProviderValidationError: If `provider` is neither a
                `BaseAIProvider` nor an `AIProviderMetadata`, if its
                `metadata` property raises, if `metadata` is not an
                `AIProviderMetadata` instance, if `provider_id` is empty
                or whitespace-only, or if `provider_id` contains leading
                or trailing whitespace (rejected rather than silently
                trimmed, so the registry key is always identical to
                `provider.metadata.provider_id` — never a separately
                normalized form).
            ProviderAlreadyRegisteredError: If `provider_id` is already
                registered. The existing registration is left untouched.
        """
        with self._lock:
            if isinstance(provider, AIProviderMetadata):
                provider_obj: BaseAIProvider = MetadataOnlyAIProvider(provider)
            elif isinstance(provider, BaseAIProvider):
                provider_obj = provider
            else:
                raise ProviderValidationError(
                    "Invalid provider object: must be a BaseAIProvider instance or AIProviderMetadata."
                )

            try:
                metadata = provider_obj.metadata
            except Exception as err:
                raise ProviderValidationError(
                    f"Failed to access provider metadata: {err}"
                ) from err

            if not isinstance(metadata, AIProviderMetadata):
                raise ProviderValidationError(
                    "Provider metadata property must return an AIProviderMetadata instance."
                )

            provider_id = metadata.provider_id
            if not provider_id or not provider_id.strip():
                raise ProviderValidationError(
                    "Missing required metadata field: 'provider_id' cannot be empty."
                )
            if provider_id != provider_id.strip():
                raise ProviderValidationError(
                    f"provider_id must not contain leading or trailing whitespace: {provider_id!r}"
                )

            if provider_id in self._providers:
                raise ProviderAlreadyRegisteredError(
                    f"Duplicate provider registration: '{provider_id}' is already registered."
                )

            self._providers[provider_id] = provider_obj
            return provider_obj

    def unregister(self, provider_id: str) -> bool:
        """Remove a provider registration by `provider_id`.

        Performs no cleanup call on the removed instance — `BaseAIProvider`
        defines no close/shutdown hook to call.

        Args:
            provider_id: Canonical provider identifier string.

        Returns:
            `True` if a provider was removed, `False` if `provider_id` was
            not registered.

        `provider_id` is matched exactly against the registered key — no
        whitespace normalization is applied here or in `get()`, matching
        `register()`'s rejection of any `provider_id` with leading/trailing
        whitespace at registration time. This keeps the registry key and
        `provider.metadata.provider_id` identical by construction, with no
        separate "canonical vs. as-registered" form to reconcile.
        """
        with self._lock:
            if provider_id not in self._providers:
                return False
            del self._providers[provider_id]
            return True

    def get(self, provider_id: str) -> BaseAIProvider:
        """Retrieve the exact registered `BaseAIProvider` instance.

        Args:
            provider_id: Canonical provider identifier string.

        Returns:
            The live `BaseAIProvider` instance registered under `provider_id`.

        Raises:
            ProviderNotFoundError: If `provider_id` is not registered.

        `provider_id` is matched exactly — see `unregister()`'s docstring
        for why no whitespace normalization is applied here.
        """
        with self._lock:
            if provider_id not in self._providers:
                raise ProviderNotFoundError(f"Provider '{provider_id}' not found in registry.")
            return self._providers[provider_id]

    def list_providers(self) -> list[AIProviderMetadata]:
        """Return metadata for all registered providers, in registration order.

        "Registration order" reflects each provider's most recent
        `register()` call, not its original insertion: unregistering a
        `provider_id` and later re-registering it moves it to the end of
        the order, exactly as a plain `dict` would (this registry's
        internal storage).

        Returns a new list, not a reference to any internal collection —
        mutating the returned list has no effect on the registry's
        internal state. The `AIProviderMetadata` objects inside it are the
        same frozen instances the provider itself holds (not copies); this
        is safe because `AIProviderMetadata` is immutable
        (`ConfigDict(frozen=True)` in `models.py`), so no caller can use
        them to mutate registry state either way.
        """
        with self._lock:
            return [provider.metadata for provider in self._providers.values()]

    def find_providers_by_endpoint_type(self, endpoint_type: EndpointType) -> list[AIProviderMetadata]:
        """Return metadata for all registered providers matching `endpoint_type`.

        Returns an empty list if no registered provider matches. Preserves
        registration order. Does not select or rank a single result — see
        class docstring.
        """
        with self._lock:
            return [
                provider.metadata
                for provider in self._providers.values()
                if provider.metadata.endpoint_type == endpoint_type
            ]

    def find_providers_supporting_model(self, model_id: str) -> list[AIProviderMetadata]:
        """Return metadata for all registered providers whose `supported_models` includes `model_id`.

        Returns an empty list if no registered provider matches. Preserves
        registration order. Does not select or rank a single result — see
        class docstring.
        """
        with self._lock:
            return [
                provider.metadata
                for provider in self._providers.values()
                if model_id in provider.metadata.supported_models
            ]

    def clear(self) -> None:
        """Remove all provider registrations.

        Test utility only. This is not an execution or lifecycle operation —
        it performs no cleanup calls on removed instances, exactly like
        `unregister()`, and exists solely to reset registry state between
        test cases.
        """
        with self._lock:
            self._providers.clear()


__all__ = ["MetadataOnlyAIProvider", "ProviderRegistry"]
