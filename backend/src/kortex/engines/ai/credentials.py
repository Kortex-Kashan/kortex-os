"""Tenant credential resolution for AI providers (Phase B / B1d).

`kortex.engines.ai` may not import `kortex.engines.security` — that is an
AST-enforced authority boundary, not a style rule. So this module never
reaches for `SecretStore`; it takes an injected async callable
(`SecretGetter`) that composition code supplies, exactly as Connector Engine
resolves its own profile credentials. `bootstrap.py` is where the two are
joined; nothing here knows what is on the other side of that callable.

**The rule this module exists to enforce** (KORTEX Phase B, ProviderRegistry
constraint): `ProviderRegistry` must not become a tenant credential store.
It must not cache tenant API keys, and it must not hold one permanent
provider instance per tenant containing that tenant's secret. The registry
stays a process-global catalogue of *which providers exist*; a tenant's
credential is fetched here, per request, from `SecretStore`, used, and
dropped.

Concretely, and deliberately:

* No cache. Not an LRU, not a dict, not a `functools.lru_cache`, not a
  "just for the duration of this agent run" memo. A resolved plaintext is a
  local variable in one call and is never stored on `self`. A cache would
  outlive the authorization that produced it, survive a revoked key, and
  turn a single process-memory disclosure into every tenant's credentials at
  once.
* No plaintext in any returned model, log line, exception message, or event
  payload. `ResolvedCredential` exists so a caller receives the value
  alongside the handle it came from without the value ever entering a
  `repr()`.
* Disabled configurations resolve to nothing. `enabled=False` is a real
  gate, not a UI flag.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from kortex.engines.ai.exceptions import AIOrchestrationError
from kortex.engines.ai.models import AIProviderConfig

SecretGetter = Callable[[str, str], Awaitable[str]]
"""`(secret_handle, tenant_id) -> plaintext`.

Matches `SecurityEngine.get_secret`'s signature exactly, so composition is a
direct method reference rather than an adapter lambda. It is async because
secret resolution touches storage.
"""

SecretPutter = Callable[[str, str, str], Awaitable[object]]
"""`(secret_handle, tenant_id, plaintext) -> ...`.

Matches `SecurityEngine.put_secret`. The return value is deliberately
ignored and typed `object`: `SecretEntry` lives in `kortex.engines.security`,
which this package may not import, and nothing here needs it.
"""


def provider_secret_handle(provider_id: str) -> str:
    """The `SecretStore` handle holding one tenant's key for `provider_id`.

    Carries no tenant component, and must not: `SecretStore` rows are keyed
    `(tenant_id, secret_handle)` and the ciphertext's AAD binds the tenant
    already, so two tenants storing `kortex/ai/providers/openai` hold two
    separate, mutually undecryptable secrets. Putting the tenant in the
    handle would duplicate that binding in a place where a caller could get
    it wrong.
    """
    return f"kortex/ai/providers/{provider_id}"


class ProviderConfigReader(Protocol):
    """The read surface `TenantCredentialResolver` needs from the config store.

    A Protocol rather than a concrete import so this module stays testable
    with a plain fake and carries no persistence dependency of its own.
    """

    async def get(self, tenant_id: str, provider_id: str) -> AIProviderConfig | None: ...


class CredentialResolutionError(AIOrchestrationError):
    """A configured provider's credential could not be resolved.

    Raised instead of returning `None` when a provider *is* configured and
    enabled and *does* name a secret handle, but that handle does not
    resolve — a missing or revoked secret must fail loudly rather than
    degrade into an unauthenticated provider call that fails later with a
    confusing vendor 401. Its message names the provider and the handle,
    never the secret.
    """


@dataclass(frozen=True)
class ResolvedCredential:
    """One provider credential, resolved for exactly one request.

    `plaintext` is excluded from `repr()` (`field(repr=False)`), so the value
    cannot reach a log line, traceback frame summary, or debugger dump
    through the ordinary act of printing the object. Treat every instance as
    request-scoped: use it, let it go out of scope, never store it.

    `default_model` (Phase B / B2) carries the SAME tenant's configured
    `AIProviderConfig.default_model` forward, so a provider that needs both
    "this tenant's credential" and "this tenant's preferred model when the
    request didn't pin one" gets both from the one call it already has to
    make — resolving credential and default model through two different
    reads would risk them disagreeing if a config changed between the two,
    and would mean two places reading the same config row instead of one.
    """

    provider_id: str
    tenant_id: str
    secret_handle: str
    plaintext: str = field(repr=False)
    default_model: str | None = None


class TenantCredentialResolver:
    """Resolves a tenant's provider credential, per request, without caching.

    Stateless by construction: the only instance attributes are the two
    injected collaborators. There is nowhere for a credential to accumulate,
    which is the point — see the module docstring.
    """

    def __init__(self, config_store: ProviderConfigReader, secret_getter: SecretGetter) -> None:
        self._config_store = config_store
        self._secret_getter = secret_getter

    async def resolve(self, tenant_id: str, provider_id: str) -> ResolvedCredential | None:
        """Resolve `provider_id`'s credential for `tenant_id`.

        Returns `None` — not an error — when the provider is genuinely not
        credentialed for this tenant: no configuration row, the
        configuration is disabled, or it names no secret handle (a local
        provider such as Ollama legitimately needs none). Callers treat
        `None` as "call this provider without a credential, if it supports
        that", which is exactly what an unconfigured local endpoint wants.

        Raises:
            CredentialResolutionError: The provider is enabled and names a
                handle, but that handle does not resolve. Fails loudly
                rather than silently proceeding unauthenticated.
        """
        config = await self._config_store.get(tenant_id, provider_id)
        if config is None or not config.enabled or not config.secret_handle:
            return None

        try:
            plaintext = await self._secret_getter(config.secret_handle, tenant_id)
        except Exception as exc:
            # The original exception is chained for diagnosis, but this
            # message is what surfaces: handle and provider, never the value
            # and never the underlying store's own text, which could quote
            # parameters.
            raise CredentialResolutionError(
                f"Provider '{provider_id}' is enabled for this tenant but its credential handle "
                f"'{config.secret_handle}' could not be resolved."
            ) from exc

        if not plaintext:
            raise CredentialResolutionError(
                f"Provider '{provider_id}' resolved an empty credential from handle '{config.secret_handle}'."
            )

        return ResolvedCredential(
            provider_id=provider_id,
            tenant_id=tenant_id,
            secret_handle=config.secret_handle,
            plaintext=plaintext,
            default_model=config.default_model,
        )


__all__ = [
    "CredentialResolutionError",
    "ProviderConfigReader",
    "ResolvedCredential",
    "SecretGetter",
    "SecretPutter",
    "TenantCredentialResolver",
    "provider_secret_handle",
]
