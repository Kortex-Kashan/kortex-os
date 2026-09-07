"""Phase B / B3: Gemini and Anthropic through the real Kernel Capability
Enforcement Boundary.

Drives the actual dispatch path -- real Storage + Security Engines, real
authentication, real RBAC, real `AIProviderConfigStore`/`SecretStore`, real
`TenantCredentialResolver`, real `ResilientAIProvider` wrapping -- with real
`GeminiProvider`/`AnthropicProvider` instances whose outbound HTTP is
intercepted by `httpx.MockTransport`. No real network call is ever made.

This is what proves the full chain end-to-end for both new providers:
capability -> verified execution context -> credential resolver ->
SecretStore -> provider -> (mocked) vendor HTTP -> normalized result. The
per-provider unit suites cover translation detail; this file covers the parts
only the assembled system can demonstrate -- that the generic
`kortex.ai.provider.test`/`configure`/`config.list` capabilities work
unchanged for the new providers, and that credentials stay tenant-scoped
across the whole stack.
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
from kortex.engines.ai.anthropic_provider import AnthropicProvider
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.gemini_provider import GeminiProvider
from kortex.engines.ai.persistence import AIProviderConfigStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x3c" * 32
_TEST_SIGNING_KEY = b"\x5e" * 32
_ROLE = "AI_B3_CLOUD_TEST_ROLE"
_TENANT_A = "tenant_a_b3"
_TENANT_B = "tenant_b_b3"

_GEMINI_KEY_A = "gemini-key-for-tenant-a"  # nosec - test fixture
_GEMINI_KEY_B = "gemini-key-for-tenant-b"  # nosec - test fixture
_ANTHROPIC_KEY_A = "anthropic-key-for-tenant-a"  # nosec - test fixture


def _gemini_handler(seen: list[httpx.Request]):
    """Mock Gemini: authenticates on the `x-goog-api-key` header and answers
    both the catalog and generation endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = request.headers.get("x-goog-api-key", "")
        if key not in (_GEMINI_KEY_A, _GEMINI_KEY_B):
            return httpx.Response(
                401, json={"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}}
            )
        if request.url.path.endswith(":generateContent"):
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "gemini says hi"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
                    "modelVersion": "gemini-2.5-flash",
                },
            )
        # Catalog: tenant A sees two models, tenant B sees one -- so a
        # cross-tenant credential mix-up would be visible in the result.
        models = (
            [
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            ]
            if key == _GEMINI_KEY_A
            else [{"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]}]
        )
        return httpx.Response(200, json={"models": models})

    return handler


def _anthropic_handler(seen: list[httpx.Request]):
    """Mock Anthropic: authenticates on `x-api-key`, requires
    `anthropic-version`, and answers both the catalog and messages
    endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("anthropic-version") != "2023-06-01":
            return httpx.Response(
                400,
                json={
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": "version header required"},
                },
            )
        if request.headers.get("x-api-key") != _ANTHROPIC_KEY_A:
            return httpx.Response(
                401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": "claude-opus-5",
                    "content": [{"type": "text", "text": "claude says hi"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 5, "output_tokens": 6},
                },
            )
        return httpx.Response(200, json={"data": [{"id": "claude-opus-5"}], "has_more": False})

    return handler


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, list[httpx.Request], list[httpx.Request]]]:
    db_path = (tmp_path / f"kortex_b3_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_b3_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    gemini_seen: list[httpx.Request] = []
    anthropic_seen: list[httpx.Request] = []
    gemini_client = httpx.AsyncClient(transport=httpx.MockTransport(_gemini_handler(gemini_seen)))
    anthropic_client = httpx.AsyncClient(transport=httpx.MockTransport(_anthropic_handler(anthropic_seen)))

    # Mirrors what `bootstrap.py` builds internally; injecting the providers
    # via `custom_providers` exercises the caller-precedence path so
    # `bootstrap.py` defers to these mock-transported instances instead of
    # constructing real-network ones.
    resolver = TenantCredentialResolver(AIProviderConfigStore(data_store), security_engine.get_secret)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[
            GeminiProvider(credential_resolver=resolver, client=gemini_client),
            AnthropicProvider(credential_resolver=resolver, client=anthropic_client),
        ],
        registered_engines=list(kernel.get_all_engines().keys()),
        secret_getter=security_engine.get_secret,
        secret_putter=security_engine.put_secret,
    )
    kernel.register_engine(ai_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("ai:manage", "ai:read", "ai:generate"):
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
        yield kernel, ai_engine, gemini_seen, anthropic_seen
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await gemini_client.aclose()
        await anthropic_client.aclose()
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
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_supplied_providers_take_precedence_over_auto_construction(kernel_env) -> None:
    """`bootstrap.py` must defer to a caller-supplied provider of the same id
    rather than constructing a duplicate -- `ProviderRegistry` rejects
    duplicate ids outright, so a regression here would crash assembly. The
    fixture booting at all is most of the proof; this pins the resulting set.
    """
    _kernel, ai_engine, _g, _a = kernel_env
    registered = {m.provider_id for m in ai_engine.provider_registry.list_providers()}
    # OpenAI was NOT supplied by the caller, so bootstrap still auto-built it.
    assert {"gemini", "anthropic", "openai"} <= registered


@pytest.mark.asyncio
async def test_all_cloud_providers_are_registered_with_resilience_wrapping(kernel_env) -> None:
    from kortex.engines.ai.resilience import ResilientAIProvider

    _kernel, ai_engine, _g, _a = kernel_env
    for provider_id in ("gemini", "anthropic", "openai"):
        provider = ai_engine.provider_registry.get(provider_id)
        assert isinstance(provider, ResilientAIProvider)


# ---------------------------------------------------------------------------
# Connection test + discovery through the generic capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_connection_test_fails_before_a_credential_is_configured(kernel_env) -> None:
    kernel, _ai, _g, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="gemini")

    assert result["connected"] is False
    assert result["models"] == []
    assert result["detail"] is not None


@pytest.mark.asyncio
async def test_gemini_connection_test_succeeds_and_filters_discovery(kernel_env) -> None:
    """The generic capability, unchanged, drives Gemini's credentialed probe
    and returns only `generateContent`-capable models."""
    kernel, _ai, gemini_seen, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="gemini", api_key=_GEMINI_KEY_A)
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="gemini")

    assert result["connected"] is True
    assert result["detail"] is None
    # The embedding-only model in the mock catalog must be filtered out.
    assert {m["model_id"] for m in result["models"]} == {"gemini-2.5-flash"}
    # Real credential really travelled in the header, and never in the URL.
    assert gemini_seen[-1].headers["x-goog-api-key"] == _GEMINI_KEY_A
    assert _GEMINI_KEY_A not in str(gemini_seen[-1].url)
    # Never a generation call.
    assert all(":generateContent" not in str(r.url) for r in gemini_seen)


@pytest.mark.asyncio
async def test_anthropic_connection_test_succeeds_with_required_headers(kernel_env) -> None:
    kernel, _ai, _g, anthropic_seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="anthropic", api_key=_ANTHROPIC_KEY_A
    )
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="anthropic")

    assert result["connected"] is True
    assert {m["model_id"] for m in result["models"]} == {"claude-opus-5"}
    assert anthropic_seen[-1].headers["x-api-key"] == _ANTHROPIC_KEY_A
    assert anthropic_seen[-1].headers["anthropic-version"] == "2023-06-01"
    assert all("/messages" not in str(r.url) for r in anthropic_seen)


@pytest.mark.asyncio
async def test_invalid_credential_is_a_normalized_failure_not_an_exception(kernel_env) -> None:
    kernel, _ai, _g, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="gemini", api_key="totally-wrong-key"
    )
    result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="gemini")

    assert result["connected"] is False
    assert result["models"] == []
    assert "totally-wrong-key" not in str(result)


# ---------------------------------------------------------------------------
# Tenant isolation across the whole stack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_tenant_reaches_only_its_own_gemini_credential(kernel_env) -> None:
    """Tenant A and tenant B each configure a different Gemini key; the mock
    catalog answers differently per key, so a cross-tenant leak would show up
    as the wrong model list, not merely a wrong header."""
    kernel, _ai, gemini_seen, _a = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    await _invoke(
        kernel, "kortex.ai.provider.configure", token_a, _TENANT_A, provider_id="gemini", api_key=_GEMINI_KEY_A
    )
    await _invoke(
        kernel, "kortex.ai.provider.configure", token_b, _TENANT_B, provider_id="gemini", api_key=_GEMINI_KEY_B
    )

    result_a = await _invoke(kernel, "kortex.ai.provider.test", token_a, _TENANT_A, provider_id="gemini")
    assert {m["model_id"] for m in result_a["models"]} == {"gemini-2.5-flash"}
    assert gemini_seen[-1].headers["x-goog-api-key"] == _GEMINI_KEY_A

    result_b = await _invoke(kernel, "kortex.ai.provider.test", token_b, _TENANT_B, provider_id="gemini")
    assert {m["model_id"] for m in result_b["models"]} == {"gemini-2.5-pro"}
    assert gemini_seen[-1].headers["x-goog-api-key"] == _GEMINI_KEY_B


@pytest.mark.asyncio
async def test_configuring_one_provider_does_not_credential_another(kernel_env) -> None:
    """Per-provider isolation within one tenant: an Anthropic key must not
    make Gemini appear configured."""
    kernel, _ai, _g, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="anthropic", api_key=_ANTHROPIC_KEY_A
    )

    anthropic_result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="anthropic")
    gemini_result = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id="gemini")

    assert anthropic_result["connected"] is True
    assert gemini_result["connected"] is False


@pytest.mark.asyncio
async def test_no_configured_credential_ever_appears_in_a_listing(kernel_env) -> None:
    kernel, _ai, _g, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="gemini", api_key=_GEMINI_KEY_A)
    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="anthropic", api_key=_ANTHROPIC_KEY_A
    )

    listed = await _invoke(kernel, "kortex.ai.provider.config.list", token, _TENANT_A)

    assert {c["provider_id"] for c in listed} == {"gemini", "anthropic"}
    assert all(c["has_credential"] is True for c in listed)
    assert _GEMINI_KEY_A not in str(listed)
    assert _ANTHROPIC_KEY_A not in str(listed)


@pytest.mark.asyncio
async def test_registry_holds_no_tenant_credential_for_the_new_providers(kernel_env) -> None:
    """The ProviderRegistry rule, re-asserted for B3's providers: configuring
    credentials must leave the process-global registry credential-free."""
    kernel, ai_engine, _g, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    before = {m.provider_id for m in ai_engine.provider_registry.list_providers()}
    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="gemini", api_key=_GEMINI_KEY_A)
    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="anthropic", api_key=_ANTHROPIC_KEY_A
    )
    after = {m.provider_id for m in ai_engine.provider_registry.list_providers()}

    assert after == before  # no per-tenant provider instances were created
    dump = repr(vars(ai_engine.provider_registry))
    assert _GEMINI_KEY_A not in dump
    assert _ANTHROPIC_KEY_A not in dump


# ---------------------------------------------------------------------------
# Real generation through the assembled stack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_generation_through_the_resilient_wrapper(kernel_env) -> None:
    """Proves the wrapped provider really generates with the tenant's own
    credential, through `ResilientAIProvider`, end to end."""
    from kortex.engines.ai.models import LLMRequest

    kernel, ai_engine, gemini_seen, _a = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _invoke(kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="gemini", api_key=_GEMINI_KEY_A)

    provider = ai_engine.provider_registry.get("gemini")
    response = await provider.generate_text(
        LLMRequest(
            request_id="req-b3-gemini",
            tenant_id=_TENANT_A,
            user_id="admin_a",
            conversation_id="conv-b3",
            prompt="hello gemini",
        )
    )

    assert response.text_content == "gemini says hi"
    assert response.provider_id == "gemini"
    assert response.token_usage == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
    generation_calls = [r for r in gemini_seen if ":generateContent" in str(r.url)]
    assert generation_calls and generation_calls[-1].headers["x-goog-api-key"] == _GEMINI_KEY_A


@pytest.mark.asyncio
async def test_anthropic_generation_through_the_resilient_wrapper(kernel_env) -> None:
    from kortex.engines.ai.models import LLMRequest

    kernel, ai_engine, _g, anthropic_seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _invoke(
        kernel, "kortex.ai.provider.configure", token, _TENANT_A, provider_id="anthropic", api_key=_ANTHROPIC_KEY_A
    )

    provider = ai_engine.provider_registry.get("anthropic")
    response = await provider.generate_text(
        LLMRequest(
            request_id="req-b3-anthropic",
            tenant_id=_TENANT_A,
            user_id="admin_a",
            conversation_id="conv-b3",
            prompt="hello claude",
        )
    )

    assert response.text_content == "claude says hi"
    assert response.provider_id == "anthropic"
    assert response.token_usage == {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
    message_calls = [r for r in anthropic_seen if str(r.url).endswith("/messages")]
    assert message_calls
    assert message_calls[-1].headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_generation_without_a_configured_credential_fails_closed(kernel_env) -> None:
    """No tenant configuration means no generation -- never a silent
    unauthenticated call."""
    from kortex.engines.ai.exceptions import PermanentProviderError
    from kortex.engines.ai.models import LLMRequest

    _kernel, ai_engine, _g, _a = kernel_env
    provider = ai_engine.provider_registry.get("anthropic")

    with pytest.raises(PermanentProviderError, match="no enabled, credentialed configuration"):
        await provider.generate_text(
            LLMRequest(
                request_id="req-b3-unconfigured",
                tenant_id=_TENANT_B,
                user_id="admin_b",
                conversation_id="conv-b3",
                prompt="should not reach the vendor",
            )
        )
