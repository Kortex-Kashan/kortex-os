"""Model Router for the KORTEX OS AI Orchestration Engine.

Implements `ModelRouter`: a stateless, deterministic, I/O-free component that
selects which registered, executable AI provider should handle a request.
Governed by `docs/architecture/ai_engine_m3_model_router_spec.md`.

The router answers exactly one question — "of the providers currently
registered, which ones qualify, and in what order?" It never executes a
provider, never retries, never falls back on failure, and never checks
health. Execution and everything downstream of it belong to the engine
facade (Milestone 7); health-aware routing belongs to a future component
that supplies an availability view as an input.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    NoRoutableProviderError,
    ProviderNotFoundError,
    ProviderNotRoutableError,
    RoutingValidationError,
)
from kortex.engines.ai.models import AIProviderMetadata, EndpointType, LLMRequest
from kortex.engines.ai.registry import MetadataOnlyAIProvider, ProviderRegistry

_ENDPOINT_RANK: dict[str, int] = {"local_host": 0, "network": 1, "cloud": 2}
"""Candidate ordering: on-premise before off-premise.

Grounded in ADR #001 §3 (`.kortex/decisions.md`) — "Primary LLM execution
relies on local inference engines (Ollama). Cloud AI providers are strictly
optional secondary adapters" — and its data-sovereignty rationale, which
places a LAN-hosted model above a cloud vendor. This mapping is the single
place ordering policy lives; a future cost/latency-aware policy replaces
this sort key and nothing else.
"""

_MODEL_ID_REJECTION = (
    "Routing context key 'model_id' is not supported. The router selects a "
    "provider, not a model: LLMRequest carries no model field, so a routed "
    "model choice cannot reach the provider that executes it, and honouring "
    "it would risk silently running a different model than requested. "
    "Model-granular routing requires an additive LLMRequest.model_id field "
    "(see ai_engine_m3_model_router_spec.md sections 7 and 18/D1)."
)


class RoutingContext(BaseModel):
    """Validated routing constraints supplied by the caller.

    All routing input arrives here because `LLMRequest` carries no routable
    field. Unknown keys are rejected rather than ignored, so a typo such as
    ``{"endpointtype": "cloud"}`` fails loudly instead of silently widening
    the candidate set.

    There is deliberately no ``model_id`` field — see `_MODEL_ID_REJECTION`.
    """

    # `strict=True` is security-relevant, not stylistic: under Pydantic's
    # default lax mode `allow_cloud=1` or `allow_cloud="yes"` would coerce to
    # True and silently enable cloud egress. Strict mode is applied to the
    # whole model so no future field can reintroduce a coercion surprise.
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider_id: str | None = None
    endpoint_type: EndpointType | None = None
    allow_cloud: bool = False

    @field_validator("provider_id")
    @classmethod
    def _reject_blank_provider_id(cls, value: str | None) -> str | None:
        """Reject an empty or whitespace-only pin.

        Values are never normalized: a padded id is left exactly as given, so
        it simply fails to match any registry key (consistent with the
        registry's own exact-match rule) rather than being silently trimmed
        into matching a different provider.
        """
        if value is not None and not value.strip():
            raise ValueError("provider_id must not be empty or whitespace-only")
        return value


def _is_executable(provider: BaseAIProvider) -> bool:
    """Sole authority on whether a registered provider may be routed to.

    A `MetadataOnlyAIProvider` is registered and discoverable by design, but
    its `generate_text` unconditionally raises — selecting one would
    guarantee failure. This is a static, synchronous, I/O-free structural
    check whose answer never changes; it is emphatically not a health check.

    This predicate is the single extension point for executability policy: a
    future non-executable provider type, or a capability flag on provider
    metadata, changes this function body and nothing else.
    """
    return not isinstance(provider, MetadataOnlyAIProvider)


def _parse_context(context: dict[str, Any]) -> RoutingContext:
    """Validate a raw routing context into a `RoutingContext`.

    Raises:
        RoutingValidationError: If `context` is not a dict, contains an
            unknown key, or carries a value of the wrong type. The message
            names offending fields and error types only — never the
            submitted values, which may be caller-sensitive.
    """
    if not isinstance(context, dict):
        raise RoutingValidationError(f"Routing context must be a dict, got {type(context).__name__}.")

    if "model_id" in context:
        raise RoutingValidationError(_MODEL_ID_REJECTION)

    try:
        return RoutingContext.model_validate(context)
    except ValidationError as err:
        problems = ", ".join(
            f"{'.'.join(str(part) for part in problem['loc']) or '<root>'} ({problem['type']})"
            for problem in err.errors()
        )
        raise RoutingValidationError(f"Invalid routing context: {problems}") from err


def _dedupe_preserving_order(provider_ids: list[str]) -> list[str]:
    """Remove duplicate ids, keeping first-seen order.

    Candidate ids are read from provider metadata, which is an abstract
    property with no stability guarantee, so two providers can report the
    same id. Without this, one provider could enter the candidate list twice
    and break both uniqueness and the total ordering guarantee.
    """
    return list(dict.fromkeys(provider_ids))


class ModelRouter:
    """Selects the provider that should handle a request.

    Stateless and lock-free: the only instance attribute is the registry
    reference, so concurrent calls cannot interfere and there is nothing for
    a lock to protect. Synchronization is inherited entirely from
    `ProviderRegistry`, whose individual methods are atomic.

    Both public methods are `async` for `IModelRouter` conformance and
    forward-compatibility. Neither awaits anything: the router performs no
    I/O, and every decision is derived from registry contents plus
    caller-supplied constraints.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def select_candidates(self, request: LLMRequest, context: dict[str, Any]) -> list[AIProviderMetadata]:
        """Return every qualifying provider, best first.

        `request` is accepted for interface conformance but no field of it is
        read: `LLMRequest` carries no classification, task type, model, or
        provider preference. It becomes meaningful when such a field is added
        additively, which is why the parameter is part of the frozen
        signature.

        An explicit pin is treated as a caller assertion — violating it
        raises. Discovery is treated as a query — matching nothing returns an
        empty list.

        Raises:
            RoutingValidationError: The routing context is invalid.
            ProviderNotFoundError: A pinned `provider_id` is not registered.
            ProviderNotRoutableError: A pinned provider is non-executable or
                misreports its identity.
            NoRoutableProviderError: A pinned provider fails an explicit
                `endpoint_type` constraint.
        """
        ctx = _parse_context(context)

        if ctx.provider_id is not None:
            return self._resolve_pinned(ctx, ctx.provider_id)

        return self._discover(ctx)

    async def select_model(self, request: LLMRequest, context: dict[str, Any]) -> AIProviderMetadata:
        """Return the single best-ranked qualifying provider.

        Defined as the first element of `select_candidates`, so ranking lives
        in exactly one place and this method's determinism follows from that
        method's.

        Raises:
            NoRoutableProviderError: No provider qualifies.
            (Plus every exception `select_candidates` may raise.)
        """
        ctx = _parse_context(context)
        candidates = await self.select_candidates(request, context)
        if not candidates:
            hint = ""
            if ctx.endpoint_type is None and not ctx.allow_cloud:
                hint = " Cloud providers are excluded unless allow_cloud=True is passed explicitly."
            raise NoRoutableProviderError(f"No routable AI provider matched the routing constraints.{hint}")
        return candidates[0]

    def _resolve_pinned(self, ctx: RoutingContext, pinned_id: str) -> list[AIProviderMetadata]:
        """Resolve an explicitly pinned provider, or raise explaining why not.

        `allow_cloud` is deliberately not consulted: naming a provider is
        itself the explicit, conscious placement decision that the
        cloud-egress default exists to force.
        """
        provider = self._registry.get(pinned_id)
        metadata = provider.metadata  # single authoritative read

        if metadata.provider_id != pinned_id:
            raise ProviderNotRoutableError(
                f"Provider registered as '{pinned_id}' reports a different identity; it cannot be routed to."
            )
        if not _is_executable(provider):
            raise ProviderNotRoutableError(
                f"Provider '{pinned_id}' is registered as metadata only and cannot execute requests."
            )
        if ctx.endpoint_type is not None and metadata.endpoint_type != ctx.endpoint_type:
            raise NoRoutableProviderError(
                f"Provider '{pinned_id}' has endpoint type '{metadata.endpoint_type}', "
                f"which does not satisfy the requested '{ctx.endpoint_type}'."
            )
        return [metadata]

    def _discover(self, ctx: RoutingContext) -> list[AIProviderMetadata]:
        """Enumerate, filter, and rank every qualifying registered provider."""
        enumerated_ids = _dedupe_preserving_order(
            [metadata.provider_id for metadata in self._registry.list_providers()]
        )

        candidates: list[AIProviderMetadata] = []
        for provider_id in enumerated_ids:
            try:
                provider = self._registry.get(provider_id)
            except ProviderNotFoundError:
                # Unregistered between the snapshot and this resolution.
                # A vanished provider is not an error, it is simply not a candidate.
                continue

            metadata = provider.metadata  # single authoritative read

            if metadata.provider_id != provider_id:
                continue  # misreports its identity; unroutable
            if not _is_executable(provider):
                continue
            if ctx.endpoint_type is not None:
                if metadata.endpoint_type != ctx.endpoint_type:
                    continue
            elif metadata.endpoint_type == "cloud" and not ctx.allow_cloud:
                # Fail closed: absent an explicit decision, data never leaves the premises.
                continue

            candidates.append(metadata)

        candidates.sort(key=lambda metadata: _ENDPOINT_RANK[metadata.endpoint_type])
        return candidates


__all__ = ["ModelRouter", "RoutingContext"]
