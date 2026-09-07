"""Phase B / B1d: tenant provider configuration + credential resolution.

Two things are under test, and the second matters more than the first:

1. `AIProviderConfigStore` persists *which* provider a tenant enabled and
   *where* its credential lives — never the credential.
2. `TenantCredentialResolver` fetches that credential per request and keeps
   nothing. The architectural rule it exists to enforce is that
   `ProviderRegistry` must not become a tenant credential store: no cached
   API keys, no one-provider-instance-per-tenant holding a secret. Several
   tests below assert the *absence* of caching directly, because "we simply
   didn't write a cache" is not a property a future edit would preserve on
   its own.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.engines.ai.credentials import (
    CredentialResolutionError,
    ResolvedCredential,
    TenantCredentialResolver,
    provider_secret_handle,
)
from kortex.engines.ai.models import AIProviderConfig
from kortex.engines.ai.persistence import AIProviderConfigStore
from kortex.engines.storage.stores.data_store import RelationalDataStore

_KEY_A = "sk-tenant-a-openai-key"  # nosec - test fixture
_KEY_B = "sk-tenant-b-openai-key"  # nosec - test fixture


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    db_path = (tmp_path / f"ai_cfg_{uuid.uuid4().hex[:8]}.db").as_posix()
    manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await manager.connect()
    await manager.create_all_tables()
    try:
        yield AIProviderConfigStore(RelationalDataStore(manager))
    finally:
        await manager.disconnect()


# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_row_has_nowhere_to_put_a_plaintext_credential() -> None:
    """Structural, not behavioural: `AIProviderConfig` must have no field a
    credential could be assigned to. Adding one would break this test before
    it could ever leak a key."""
    fields = set(AIProviderConfig.model_fields)
    assert fields == {
        "tenant_id",
        "provider_id",
        "enabled",
        "secret_handle",
        "default_model",
        "created_at",
        "updated_at",
    }
    for forbidden in ("api_key", "plaintext", "credential", "secret", "token"):
        assert forbidden not in fields


@pytest.mark.asyncio
async def test_upsert_then_get_round_trip(store: AIProviderConfigStore) -> None:
    saved = await store.upsert(
        AIProviderConfig(
            tenant_id="tenant-a",
            provider_id="openai",
            secret_handle=provider_secret_handle("openai"),
            default_model="gpt-4o",
        )
    )
    assert saved.secret_handle == "kortex/ai/providers/openai"

    fetched = await store.get("tenant-a", "openai")
    assert fetched is not None
    assert fetched.provider_id == "openai"
    assert fetched.default_model == "gpt-4o"
    assert fetched.enabled is True


@pytest.mark.asyncio
async def test_upsert_is_idempotent_per_tenant_and_provider(store: AIProviderConfigStore) -> None:
    """Configuring twice updates one row rather than accumulating history."""
    await store.upsert(AIProviderConfig(tenant_id="tenant-a", provider_id="openai", default_model="gpt-4o"))
    await store.upsert(AIProviderConfig(tenant_id="tenant-a", provider_id="openai", default_model="gpt-4o-mini"))

    configs = await store.list_for_tenant("tenant-a")
    assert len(configs) == 1
    assert configs[0].default_model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_reconfiguring_without_a_handle_does_not_orphan_the_existing_one(
    store: AIProviderConfigStore,
) -> None:
    """Changing the default model must not silently detach the stored
    credential — the handle would be orphaned in SecretStore and the tenant's
    provider would stop working for no visible reason."""
    await store.upsert(
        AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle="kortex/ai/providers/openai")
    )
    await store.upsert(AIProviderConfig(tenant_id="tenant-a", provider_id="openai", default_model="gpt-4o"))

    config = await store.get("tenant-a", "openai")
    assert config is not None
    assert config.secret_handle == "kortex/ai/providers/openai"


@pytest.mark.asyncio
async def test_two_tenants_configure_the_same_provider_independently(store: AIProviderConfigStore) -> None:
    await store.upsert(AIProviderConfig(tenant_id="tenant-a", provider_id="openai", default_model="gpt-4o"))
    await store.upsert(AIProviderConfig(tenant_id="tenant-b", provider_id="openai", default_model="gpt-4o-mini"))

    a = await store.get("tenant-a", "openai")
    b = await store.get("tenant-b", "openai")
    assert a is not None and b is not None
    assert a.default_model == "gpt-4o"
    assert b.default_model == "gpt-4o-mini"

    assert [c.tenant_id for c in await store.list_for_tenant("tenant-a")] == ["tenant-a"]


@pytest.mark.asyncio
async def test_clear_and_delete(store: AIProviderConfigStore) -> None:
    await store.upsert(
        AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle="kortex/ai/providers/openai")
    )

    assert await store.clear_secret_handle("tenant-a", "openai") is True
    cleared = await store.get("tenant-a", "openai")
    assert cleared is not None
    assert cleared.secret_handle is None

    assert await store.delete("tenant-a", "openai") is True
    assert await store.get("tenant-a", "openai") is None
    assert await store.delete("tenant-a", "openai") is False


@pytest.mark.asyncio
async def test_handle_carries_no_tenant_component() -> None:
    """SecretStore keys on `(tenant_id, secret_handle)` and binds the tenant
    into the ciphertext's AAD, so the handle must not restate it."""
    handle = provider_secret_handle("openai")
    assert handle == "kortex/ai/providers/openai"
    assert "tenant" not in handle


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


class _FakeConfigReader:
    def __init__(self, configs: dict[tuple[str, str], AIProviderConfig]) -> None:
        self._configs = configs

    async def get(self, tenant_id: str, provider_id: str) -> AIProviderConfig | None:
        return self._configs.get((tenant_id, provider_id))


class _CountingSecretGetter:
    """Records every resolution so a cache would be visible as a missing call."""

    def __init__(self, secrets: dict[tuple[str, str], str]) -> None:
        self._secrets = secrets
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, secret_handle: str, tenant_id: str) -> str:
        self.calls.append((secret_handle, tenant_id))
        try:
            return self._secrets[(secret_handle, tenant_id)]
        except KeyError as exc:
            raise LookupError("no such secret") from exc


def _resolver(
    configs: dict[tuple[str, str], AIProviderConfig],
    secrets: dict[tuple[str, str], str],
) -> tuple[TenantCredentialResolver, _CountingSecretGetter]:
    getter = _CountingSecretGetter(secrets)
    return TenantCredentialResolver(_FakeConfigReader(configs), getter), getter


@pytest.mark.asyncio
async def test_resolves_the_callers_own_credential() -> None:
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {(handle, "tenant-a"): _KEY_A},
    )

    resolved = await resolver.resolve("tenant-a", "openai")

    assert resolved is not None
    assert resolved.plaintext == _KEY_A
    assert resolved.secret_handle == handle


@pytest.mark.asyncio
async def test_each_tenant_resolves_its_own_key_for_the_same_provider() -> None:
    """The shared `provider_id` and shared handle must not collapse two
    tenants' credentials into one."""
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {
            ("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle),
            ("tenant-b", "openai"): AIProviderConfig(tenant_id="tenant-b", provider_id="openai", secret_handle=handle),
        },
        {(handle, "tenant-a"): _KEY_A, (handle, "tenant-b"): _KEY_B},
    )

    a = await resolver.resolve("tenant-a", "openai")
    b = await resolver.resolve("tenant-b", "openai")

    assert a is not None and b is not None
    assert a.plaintext == _KEY_A
    assert b.plaintext == _KEY_B


@pytest.mark.asyncio
async def test_every_resolution_refetches_the_secret() -> None:
    """THE ANTI-CACHE ASSERTION. Ten resolutions must perform ten fetches. A
    cache would satisfy the value assertions above while silently outliving
    the authorization that produced the key and surviving its revocation."""
    handle = provider_secret_handle("openai")
    resolver, getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {(handle, "tenant-a"): _KEY_A},
    )

    for _ in range(10):
        await resolver.resolve("tenant-a", "openai")

    assert len(getter.calls) == 10


@pytest.mark.asyncio
async def test_a_rotated_key_takes_effect_on_the_very_next_resolution() -> None:
    """The observable consequence of not caching: rotating a credential does
    not require a restart or an eviction."""
    handle = provider_secret_handle("openai")
    secrets = {(handle, "tenant-a"): _KEY_A}
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        secrets,
    )

    first = await resolver.resolve("tenant-a", "openai")
    secrets[(handle, "tenant-a")] = "sk-rotated"  # nosec - test fixture
    second = await resolver.resolve("tenant-a", "openai")

    assert first is not None and second is not None
    assert first.plaintext == _KEY_A
    assert second.plaintext == "sk-rotated"


@pytest.mark.asyncio
async def test_the_resolver_holds_no_credential_state() -> None:
    """Structural guard on the same rule: after resolving, no attribute of the
    resolver may contain the plaintext."""
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {(handle, "tenant-a"): _KEY_A},
    )

    await resolver.resolve("tenant-a", "openai")

    assert _KEY_A not in repr(vars(resolver))


@pytest.mark.asyncio
async def test_resolved_credential_keeps_the_plaintext_out_of_repr() -> None:
    """`repr()` is how a secret usually escapes: a log line, a traceback frame
    summary, a debugger dump."""
    resolved = ResolvedCredential(
        provider_id="openai", tenant_id="tenant-a", secret_handle="kortex/ai/providers/openai", plaintext=_KEY_A
    )

    assert _KEY_A not in repr(resolved)
    assert "openai" in repr(resolved)
    assert resolved.plaintext == _KEY_A


@pytest.mark.asyncio
async def test_unconfigured_provider_resolves_to_none() -> None:
    resolver, getter = _resolver({}, {})
    assert await resolver.resolve("tenant-a", "openai") is None
    assert getter.calls == []


@pytest.mark.asyncio
async def test_disabled_configuration_resolves_to_none_and_never_fetches() -> None:
    """`enabled=False` is a real gate: a disabled provider's key must not even
    be read out of SecretStore."""
    handle = provider_secret_handle("openai")
    resolver, getter = _resolver(
        {
            ("tenant-a", "openai"): AIProviderConfig(
                tenant_id="tenant-a", provider_id="openai", secret_handle=handle, enabled=False
            )
        },
        {(handle, "tenant-a"): _KEY_A},
    )

    assert await resolver.resolve("tenant-a", "openai") is None
    assert getter.calls == []


@pytest.mark.asyncio
async def test_credentialless_provider_resolves_to_none() -> None:
    """A local provider (Ollama) legitimately needs no credential — that is
    `None`, not an error."""
    resolver, _getter = _resolver(
        {("tenant-a", "ollama-llama3"): AIProviderConfig(tenant_id="tenant-a", provider_id="ollama-llama3")},
        {},
    )
    assert await resolver.resolve("tenant-a", "ollama-llama3") is None


@pytest.mark.asyncio
async def test_an_unresolvable_handle_fails_loudly_rather_than_degrading() -> None:
    """A revoked or missing secret on an ENABLED provider must not silently
    become an unauthenticated call that fails later as a confusing vendor 401."""
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {},
    )

    with pytest.raises(CredentialResolutionError) as excinfo:
        await resolver.resolve("tenant-a", "openai")

    assert "openai" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_empty_stored_credential_is_rejected() -> None:
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {(handle, "tenant-a"): ""},
    )

    with pytest.raises(CredentialResolutionError):
        await resolver.resolve("tenant-a", "openai")


# ---------------------------------------------------------------------------
# Phase B / B2 -- ResolvedCredential.default_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_credential_carries_the_tenants_configured_default_model() -> None:
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {
            ("tenant-a", "openai"): AIProviderConfig(
                tenant_id="tenant-a", provider_id="openai", secret_handle=handle, default_model="gpt-4.1"
            )
        },
        {(handle, "tenant-a"): _KEY_A},
    )

    resolved = await resolver.resolve("tenant-a", "openai")

    assert resolved is not None
    assert resolved.default_model == "gpt-4.1"


@pytest.mark.asyncio
async def test_resolved_credential_default_model_is_none_when_tenant_configured_none() -> None:
    """A tenant that never set a default model gets `None`, not a fabricated
    guess -- the provider itself decides what its own fallback should be."""
    handle = provider_secret_handle("openai")
    resolver, _getter = _resolver(
        {("tenant-a", "openai"): AIProviderConfig(tenant_id="tenant-a", provider_id="openai", secret_handle=handle)},
        {(handle, "tenant-a"): _KEY_A},
    )

    resolved = await resolver.resolve("tenant-a", "openai")

    assert resolved is not None
    assert resolved.default_model is None


def test_resolved_credential_default_model_is_excluded_from_frozen_identity_by_nothing_special() -> None:
    """Structural guard: `default_model` must never be mistaken for
    credential material -- it is plain, non-sensitive config, so unlike
    `plaintext` it is NOT excluded from `repr()`."""
    resolved = ResolvedCredential(
        provider_id="openai",
        tenant_id="tenant-a",
        secret_handle="kortex/ai/providers/openai",
        plaintext=_KEY_A,
        default_model="gpt-4o",
    )
    assert "gpt-4o" in repr(resolved)
    assert _KEY_A not in repr(resolved)
