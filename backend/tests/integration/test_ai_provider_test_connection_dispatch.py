"""Phase B / B2: `kortex.ai.provider.test` through the real Kernel Capability
Enforcement Boundary.

Drives the actual dispatch path (real Storage + Security Engines, real
authentication, real RBAC, real `AIProviderConfigStore`/`SecretStore`) with
a real `OpenAIProvider` -- but its outbound HTTP calls are intercepted by
`httpx.MockTransport`, exactly like the provider's own unit tests, so no
real network call is ever made. This is what proves the FULL chain
end-to-end: capability -> execution context -> credential resolver ->
SecretStore -> provider -> (mocked) HTTP call -> normalized result --
not just that each link works in isolation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.openai_provider import OpenAIProvider
from kortex.engines.ai.persistence import AIProviderConfigStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\xef" * 32
_TEST_SIGNING_KEY = b"\xfe" * 32
_ROLE = "AI_PROVIDER_TEST_ROLE"
_TENANT_A = "tenant_a_test_conn"
_TENANT_B = "tenant_b_test_conn"
_API_KEY_A = "sk-tenant-a-real-looking-key"  # nosec - test fixture
_API_KEY_B = "sk-tenant-b-real-looking-key"  # nosec - test fixture


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, SecurityEngine, list[httpx.Request]]]:
    """Same shape as `test_ai_provider_configure_dispatch.py`'s fixture, plus
    a mock-transported `OpenAIProvider` injected via `custom_providers` so
    `bootstrap.py`'s own auto-construction defers to it (see
    `already_supplied_openai` in `bootstrap.py`) rather than building a
    second, real-network `OpenAIProvider` alongside it.
    """
    db_path = (tmp_path / f"kortex_test_conn_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_test_conn_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {_API_KEY_A}":
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]})
        if auth == f"Bearer {_API_KEY_B}":
            return httpx.Response(200, json={"data": [{"id": "gpt-4.1"}]})
        return httpx.Response(401, json={"error": {"message": "invalid_api_key"}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Mirrors exactly what `bootstrap.py` builds internally for the real
    # path -- a second, functionally-identical, harmless resolver instance.
    resolver = TenantCredentialResolver(AIProviderConfigStore(data_store), security_engine.get_secret)
    mock_openai = OpenAIProvider(credential_resolver=resolver, client=mock_client)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[mock_openai],
        registered_engines=list(kernel.get_all_engines().keys()),
        secret_getter=security_engine.get_secret,
        secret_putter=security_engine.put_secret,
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("ai:manage", "ai:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=permission))
        for tenant, principal in ((_TENANT_A, "admin_a"), (_TENANT_B, "admin_b")):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant,
                    principal_id=principal,
                    principal_type="USER",
                    credential_hash=hasher.hash(f"pass-{principal}"),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )
        await session.flush()

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    try:
        yield kernel, ai_engine, security_engine, captured_requests
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await mock_client.aclose()
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


@pytest.mark.asyncio
async def test_connection_test_fails_before_any_credential_is_configured(kernel_env) -> None:
    kernel, _ai_engine, _security, _captured = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="openai")

    assert result["connected"] is False
    assert result["models"] == []
    assert result["detail"] is not None


@pytest.mark.asyncio
async def test_connection_test_succeeds_once_a_valid_credential_is_configured(kernel_env) -> None:
    kernel, _ai_engine, _security, captured = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="openai")

    assert result["connected"] is True
    assert result["detail"] is None
    assert {m["model_id"] for m in result["models"]} == {"gpt-4o", "gpt-4o-mini"}

    # The real mocked HTTP call actually carried the tenant's real credential.
    assert captured[-1].headers["Authorization"] == f"Bearer {_API_KEY_A}"


@pytest.mark.asyncio
async def test_connection_test_reports_failure_for_an_invalid_credential(kernel_env) -> None:
    kernel, _ai_engine, _security, _captured = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key="sk-totally-wrong"
    )
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="openai")

    assert result["connected"] is False
    assert "sk-totally-wrong" not in str(result)
    assert result["models"] == []


@pytest.mark.asyncio
async def test_connection_test_uses_the_callers_own_tenant_credential_never_anothers(kernel_env) -> None:
    """Tenant B configures its OWN key; testing the connection as tenant B
    must never reach tenant A's credential even if tenant A also has one."""
    kernel, _ai_engine, _security, captured = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    await _invoke(kernel, "kortex.ai.provider.configure", token_a, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    await _invoke(kernel, "kortex.ai.provider.configure", token_b, _TENANT_B, provider_id="openai", api_key=_API_KEY_B)

    result_b = await _invoke(kernel, "kortex.ai.provider.test", token_b, _TENANT_B, provider_id="openai")

    assert result_b["connected"] is True
    assert {m["model_id"] for m in result_b["models"]} == {"gpt-4.1"}
    assert captured[-1].headers["Authorization"] == f"Bearer {_API_KEY_B}"
    assert captured[-1].headers["Authorization"] != f"Bearer {_API_KEY_A}"


@pytest.mark.asyncio
async def test_connection_test_for_an_unregistered_provider_is_a_clean_failure_not_an_exception(kernel_env) -> None:
    kernel, _ai_engine, _security, _captured = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="nonexistent-vendor")

    assert result["connected"] is False
    assert "not registered" in result["detail"]


@pytest.mark.asyncio
async def test_connection_test_result_never_contains_the_credential_anywhere(kernel_env) -> None:
    kernel, _ai_engine, _security, _captured = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="openai")

    assert _API_KEY_A not in str(result)


# ---------------------------------------------------------------------------
# B2 revision -- discover_models failing AFTER test_connection already
# succeeded must not escape the capability as an uncaught exception.
#
# `OpenAIProvider.test_connection` and `.discover_models` both call the same
# `/v1/models` endpoint via the same internal `_fetch_models` helper. Every
# `kernel_env`-based test above uses a STATIC handler that returns the same
# response on every call, so it can never expose "call #1 succeeds, call #2
# fails" -- exactly why this defect went undetected until the full diff
# review. This fixture uses a call-COUNTING handler instead: the first
# request to `/models` succeeds (proving `test_connection` passed for real,
# not because failures were suppressed), and the second fails with a real
# HTTP error status, reproducing the actual two-round-trip race a live
# upstream can hit.
# ---------------------------------------------------------------------------


@pytest.fixture
async def kernel_env_with_sequential_openai(
    tmp_path: Path,
) -> AsyncIterator[tuple[Kernel, dict[str, int], dict[str, int]]]:
    """Same shape/wiring as `kernel_env`, except the mock OpenAI transport's
    response to `/v1/models` depends on how many times it has been called:
    the 1st call (from `test_connection`) succeeds, the 2nd call (from
    `discover_models`) fails with whatever status `next_failure_status`
    currently holds -- set it per-test before invoking the capability.
    """
    db_path = (tmp_path / f"kortex_seq_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_seq_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    call_count = {"n": 0}
    next_failure_status = {"status": 429}  # mutated by the test before the 2nd call happens

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        return httpx.Response(
            next_failure_status["status"],
            json={"error": {"message": "synthetic second-call failure"}},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolver = TenantCredentialResolver(AIProviderConfigStore(data_store), security_engine.get_secret)
    mock_openai = OpenAIProvider(credential_resolver=resolver, client=mock_client)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[mock_openai],
        registered_engines=list(kernel.get_all_engines().keys()),
        secret_getter=security_engine.get_secret,
        secret_putter=security_engine.put_secret,
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("ai:manage", "ai:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=permission))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="admin_a",
                principal_type="USER",
                credential_hash=hasher.hash("pass-admin_a"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        await session.flush()

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    try:
        yield kernel, call_count, next_failure_status
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await mock_client.aclose()
        await db_manager.disconnect()


@pytest.mark.parametrize(
    ("failure_status", "expected_detail_fragment"),
    [
        (429, "rate-limited"),
        (401, "invalid"),
    ],
    ids=["transient-429", "permanent-401"],
)
@pytest.mark.asyncio
async def test_connection_test_survives_discover_models_failing_after_test_connection_succeeds(
    kernel_env_with_sequential_openai, failure_status: int, expected_detail_fragment: str
) -> None:
    """The B2-revision regression: `test_connection` (call #1) succeeds for
    real against the mock, `discover_models` (call #2) then fails with a
    normalized provider error -- both `TransientProviderError` (429) and
    `PermanentProviderError` (401) are exercised, since the fix's except
    clause must catch both classes, not just one. The capability must still
    return a successful, connected result with an empty model list and the
    discovery failure surfaced in `detail` -- never an uncaught exception.
    """
    kernel, call_count, next_failure_status = kernel_env_with_sequential_openai
    next_failure_status["status"] = failure_status
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="openai", api_key=_API_KEY_A)

    # No exception must propagate out of this call -- that IS the assertion.
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="openai")

    assert result["connected"] is True
    assert result["models"] == []
    assert result["detail"] is not None
    assert expected_detail_fragment in result["detail"].lower()
    # Proves both calls genuinely happened: call #1 (test_connection) really
    # succeeded before call #2 (discover_models) failed -- not a test that
    # coincidentally passes because nothing was actually invoked twice.
    assert call_count["n"] == 2
