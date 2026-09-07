"""Phase B / B1d: `kortex.ai.provider.configure` through the real Kernel
Capability Enforcement Boundary.

The load-bearing assertion in this file is the redaction one. The secret
parameter is named `api_key` because `core.idempotency.sanitize_for_persistence`
redacts by EXACT key name and `api_key` is in its `SENSITIVE_KEY_NAMES` set;
the dispatcher runs that sanitizer over `request.parameters` before they reach
the audit trail. A rename to something more descriptive would compile, pass
every functional test, and start writing live provider credentials into
persisted audit records. `test_the_api_key_parameter_name_is_the_redaction_
contract` fails the moment that name drifts.

Everything else here is the tenant-binding and no-plaintext-egress surface:
a configuration belongs to the verified caller's tenant, and no response,
listing, or stored row ever carries the key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.idempotency import SENSITIVE_KEY_NAMES, sanitize_for_persistence
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.credentials import provider_secret_handle
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\xab" * 32
_TEST_SIGNING_KEY = b"\xcd" * 32
_ROLE = "AI_PROVIDER_CONFIG_TEST_ROLE"
_READER_ROLE = "AI_PROVIDER_CONFIG_READER_ROLE"
_TENANT_A = "tenant_a_cfg"
_TENANT_B = "tenant_b_cfg"
_API_KEY_A = "sk-tenant-a-live-openai-key"  # nosec - test fixture
_API_KEY_B = "sk-tenant-b-live-openai-key"  # nosec - test fixture


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, SecurityEngine]]:
    db_path = (tmp_path / f"kortex_cfg_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_cfg_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        registered_engines=list(kernel.get_all_engines().keys()),
        # The production composition from `api/kernel_bootstrap.py`: bound
        # Security Engine methods, never the engine itself.
        secret_getter=security_engine.get_secret,
        secret_putter=security_engine.put_secret,
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("ai:manage", "ai:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=permission))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_READER_ROLE, permission="ai:read"))
        for tenant, principal, role in (
            (_TENANT_A, "admin_a", _ROLE),
            (_TENANT_B, "admin_b", _ROLE),
            (_TENANT_A, "reader_a", _READER_ROLE),
        ):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=principal,
                    principal_type="USER",
                    credential_hash=hasher.hash(f"pass-{principal}"),
                    roles=[role],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
        await session.flush()

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    try:
        yield kernel, ai_engine, security_engine
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str) -> Any:
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": f"pass-{principal_id}",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _invoke(kernel: Kernel, capability: str, token: Any, tenant: str, **parameters: Any) -> Any:
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=parameters,
            context={"resource_tenant_id": tenant},
        )
    )


# ---------------------------------------------------------------------------
# The redaction contract
# ---------------------------------------------------------------------------


def test_the_api_key_parameter_name_is_the_redaction_contract() -> None:
    """If this fails, either the parameter was renamed or the sanitizer's key
    set changed — and provider credentials are about to reach the audit log.

    Asserted against the real sanitizer, on the real parameter shape the
    capability accepts, rather than against a copy of the name.
    """
    import inspect

    from kortex.engines.ai.engine import AIOrchestrationEngine

    assert "api_key" in inspect.signature(AIOrchestrationEngine.configure_provider).parameters
    assert "api_key" in SENSITIVE_KEY_NAMES

    sanitized = sanitize_for_persistence({"provider_id": "openai", "api_key": _API_KEY_A})
    assert _API_KEY_A not in str(sanitized)
    assert sanitized["provider_id"] == "openai"


@pytest.mark.asyncio
async def test_the_key_is_stored_in_secretstore_and_nowhere_else(kernel_env) -> None:
    kernel, ai_engine, security_engine = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    result = await _invoke(
        kernel,
        "kortex.ai.provider.configure",
        token,
        _TENANT_A,
        provider_id="openai",
        api_key=_API_KEY_A,
        default_model="gpt-4o",
    )

    # The response carries neither the value nor the handle. B1 returned the
    # handle here (it is a SecretStore reference, not secret material); B4
    # withdrew it, because B4 gives the response a real frontend consumer
    # and the standing requirement is that the frontend never *receives* a
    # secret handle rather than merely declining to display one. Asserted as
    # an explicit absence, not by deleting the old assertion, so a later
    # change that re-widens the wire view fails here.
    assert _API_KEY_A not in str(result)
    assert result["has_credential"] is True
    assert "secret_handle" not in result

    # The persisted configuration row still records the handle -- it is the
    # server-side pointer resolution depends on. Only the wire view narrowed.
    stored = await ai_engine._provider_config_store.get(_TENANT_A, "openai")
    assert stored is not None
    assert stored.secret_handle == provider_secret_handle("openai")
    assert _API_KEY_A not in str(stored.model_dump())

    # ...and the real key really is retrievable from SecretStore under the
    # caller's tenant, so this is genuine storage, not a discarded write.
    assert await security_engine.get_secret(provider_secret_handle("openai"), _TENANT_A) == _API_KEY_A


@pytest.mark.asyncio
async def test_listing_configurations_never_returns_a_credential(kernel_env) -> None:
    kernel, _ai_engine, _security = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    listed = await _invoke(kernel, "kortex.ai.provider.config.list", token, _TENANT_A)

    assert len(listed) == 1
    assert _API_KEY_A not in str(listed)
    assert listed[0]["has_credential"] is True


# ---------------------------------------------------------------------------
# Tenant binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_configuration_belongs_to_the_verified_tenant_only(kernel_env) -> None:
    """The capability takes no tenant parameter at all — the tenant comes from
    the verified identity — so this test proves the two tenants' configurations
    stay separate rather than that a spoof was rejected."""
    kernel, _ai_engine, security_engine = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    await _invoke(kernel, "kortex.ai.provider.configure", token_a, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    await _invoke(kernel, "kortex.ai.provider.configure", token_b, _TENANT_B, provider_id="openai", api_key=_API_KEY_B)

    listed_a = await _invoke(kernel, "kortex.ai.provider.config.list", token_a, _TENANT_A)
    listed_b = await _invoke(kernel, "kortex.ai.provider.config.list", token_b, _TENANT_B)

    assert [c["tenant_id"] for c in listed_a] == [_TENANT_A]
    assert [c["tenant_id"] for c in listed_b] == [_TENANT_B]

    # Same handle, two independently-encrypted secrets.
    handle = provider_secret_handle("openai")
    assert await security_engine.get_secret(handle, _TENANT_A) == _API_KEY_A
    assert await security_engine.get_secret(handle, _TENANT_B) == _API_KEY_B


@pytest.mark.asyncio
async def test_the_credential_resolver_returns_each_tenants_own_key(kernel_env) -> None:
    """End-to-end proof that the resolver, the config store and SecretStore
    compose into per-tenant credential resolution."""
    kernel, ai_engine, _security = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    await _invoke(kernel, "kortex.ai.provider.configure", token_a, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    await _invoke(kernel, "kortex.ai.provider.configure", token_b, _TENANT_B, provider_id="openai", api_key=_API_KEY_B)

    resolver = ai_engine.credential_resolver
    assert resolver is not None

    resolved_a = await resolver.resolve(_TENANT_A, "openai")
    resolved_b = await resolver.resolve(_TENANT_B, "openai")
    assert resolved_a is not None and resolved_b is not None
    assert resolved_a.plaintext == _API_KEY_A
    assert resolved_b.plaintext == _API_KEY_B


@pytest.mark.asyncio
async def test_the_registry_never_holds_a_tenant_credential(kernel_env) -> None:
    """The ProviderRegistry rule, asserted where it could actually be broken:
    configuring providers for two tenants must leave the process-global
    registry unchanged and credential-free."""
    kernel, ai_engine, _security = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    before = {m.provider_id for m in ai_engine.provider_registry.list_providers()}

    await _invoke(kernel, "kortex.ai.provider.configure", token_a, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    await _invoke(kernel, "kortex.ai.provider.configure", token_b, _TENANT_B, provider_id="openai", api_key=_API_KEY_B)

    after = {m.provider_id for m in ai_engine.provider_registry.list_providers()}
    assert after == before  # no per-tenant provider instances were created

    registry_dump = repr(vars(ai_engine.provider_registry))
    assert _API_KEY_A not in registry_dump
    assert _API_KEY_B not in registry_dump


# ---------------------------------------------------------------------------
# Authorization and input handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_read_alone_cannot_configure_a_provider(kernel_env) -> None:
    """Reading configurations and writing a credential are separately grantable."""
    kernel, _ai_engine, _security = kernel_env
    reader = await _token(kernel, _TENANT_A, "reader_a")

    with pytest.raises(AuthorizationDeniedError):
        await _invoke(
            kernel, "kortex.ai.provider.configure", reader, _TENANT_A, provider_id="openai", api_key=_API_KEY_A
        )

    # ...but the same principal may list.
    assert await _invoke(kernel, "kortex.ai.provider.config.list", reader, _TENANT_A) == []


@pytest.mark.asyncio
async def test_configuring_without_a_key_leaves_an_existing_one_intact(kernel_env) -> None:
    kernel, _ai_engine, security_engine = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    updated = await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", default_model="gpt-4o-mini"
    )

    assert updated["has_credential"] is True
    assert updated["default_model"] == "gpt-4o-mini"
    assert await security_engine.get_secret(provider_secret_handle("openai"), _TENANT_A) == _API_KEY_A


@pytest.mark.asyncio
async def test_a_blank_api_key_is_rejected_rather_than_stored(kernel_env) -> None:
    kernel, _ai_engine, _security = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    with pytest.raises(ValueError):
        await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key="   ")


@pytest.mark.asyncio
async def test_remove_deletes_the_configuration(kernel_env) -> None:
    kernel, _ai_engine, _security = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    removed = await _invoke(kernel, "kortex.ai.provider.config.remove", token, _TENANT_A, provider_id="openai")

    assert removed["removed"] is True
    assert await _invoke(kernel, "kortex.ai.provider.config.list", token, _TENANT_A) == []


@pytest.mark.asyncio
async def test_a_disabled_configuration_resolves_no_credential(kernel_env) -> None:
    """`enabled=False` is a real gate on the resolution path, not a UI flag."""
    kernel, ai_engine, _security = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", enabled=False)

    resolver = ai_engine.credential_resolver
    assert resolver is not None
    assert await resolver.resolve(_TENANT_A, "openai") is None
