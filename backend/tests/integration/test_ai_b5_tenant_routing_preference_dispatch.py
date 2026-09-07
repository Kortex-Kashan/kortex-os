"""Phase B / B5.1: tenant provider/model routing preference through the real
Kernel Capability Enforcement Boundary.

Drives the actual dispatch path exactly as `test_ai_b4_trusted_cloud_
routing_dispatch.py` does -- real Storage + Security Engines, real
authentication, real RBAC, real `AIProviderConfigStore`/`SecretStore`, real
`TenantCredentialResolver`, real `AIGovernanceStore`, real
`TenantCloudRoutingAuthority`, real `ModelRouter`, real `ResilientAIProvider`
wrapping -- with real `OpenAIProvider`/`GeminiProvider`/`AnthropicProvider`
instances whose outbound HTTP is intercepted by `httpx.MockTransport`. No
real network call is ever made and no live vendor credential is required.

**Why this is a separate file from the B4 one, not an extension of it.** B4's
fixture deliberately gives only Gemini a working mock transport (OpenAI and
Anthropic are pinned to a deterministic 401) -- correct for proving *that*
cloud is reachable, irrelevant to proving *which specific provider* a tenant
reaches. B5.1's own requirements ("tenant with enabled OpenAI configuration
can explicitly route to OpenAI", "... Gemini ...", "... Anthropic ...") need
all three to genuinely work, so this file gives each of them a real,
distinct mock handler instead.

**Local-first is preserved by construction.** An `OllamaProvider`-shaped
local provider (`endpoint_type="local_host"`) is always registered
alongside the three cloud ones, exactly as `kernel_bootstrap.py` always
registers a real `OllamaProvider` in production. Every test that expects
local-first routing (no configured preference) asserts the *local*
provider answered; every test that expects the tenant's explicit
preference to win asserts a *specific cloud* provider answered despite a
healthy local provider being available and ranked first by
`_ENDPOINT_RANK` -- so "the preference actually changed the outcome" is
what each assertion proves, not merely "cloud was reachable" (B4's own
question, already answered).
"""

from __future__ import annotations

import json
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
from kortex.engines.ai.exceptions import NoRoutableProviderError, ProviderFallbackExhaustedError
from kortex.engines.ai.gemini_provider import GEMINI_PROVIDER_ID, GeminiProvider
from kortex.engines.ai.ollama_provider import OllamaProvider
from kortex.engines.ai.openai_provider import OpenAIProvider
from kortex.engines.ai.persistence import AIProviderConfigStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x44" * 32
_TEST_SIGNING_KEY = b"\x1a" * 32
_ROLE = "AI_B5_ROUTING_PREFERENCE_ROLE"
_TENANT_A = "tenant_a_b5"
_TENANT_B = "tenant_b_b5"

_OPENAI_KEY = "openai-b5-key"  # nosec - test fixture
_ANTHROPIC_KEY = "anthropic-b5-key"  # nosec - test fixture
_GEMINI_KEY = "gemini-b5-key"  # nosec - test fixture

_OPENAI_MODEL = "gpt-4o"
_ANTHROPIC_MODEL = "claude-opus-5"
_GEMINI_MODEL = "gemini-2.5-flash"
_OLLAMA_MODEL = "llama3"


def _openai_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {_OPENAI_KEY}":
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "openai answered"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    "model": _OPENAI_MODEL,
                },
            )
        return httpx.Response(200, json={"data": [{"id": _OPENAI_MODEL}]})

    return handler


def _anthropic_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("anthropic-version") != "2023-06-01":
            return httpx.Response(
                400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "version required"}}
            )
        if request.headers.get("x-api-key") != _ANTHROPIC_KEY:
            return httpx.Response(
                401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid key"}}
            )
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "msg_b5",
                    "model": _ANTHROPIC_MODEL,
                    "content": [{"type": "text", "text": "anthropic answered"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                },
            )
        return httpx.Response(200, json={"data": [{"id": _ANTHROPIC_MODEL}], "has_more": False})

    return handler


def _gemini_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("x-goog-api-key") != _GEMINI_KEY:
            return httpx.Response(
                401, json={"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}}
            )
        if request.url.path.endswith(":generateContent"):
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "gemini answered"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
                    "modelVersion": _GEMINI_MODEL,
                },
            )
        return httpx.Response(
            200,
            json={"models": [{"name": f"models/{_GEMINI_MODEL}", "supportedGenerationMethods": ["generateContent"]}]},
        )

    return handler


def _ollama_handler(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/api/generate"):
            return httpx.Response(200, json={"response": "ollama answered", "prompt_eval_count": 3, "eval_count": 2})
        return httpx.Response(200, json={"models": [{"name": _OLLAMA_MODEL}]})

    return handler


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, dict[str, list[httpx.Request]]]]:
    db_path = (tmp_path / f"kortex_b5_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_b5_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    seen: dict[str, list[httpx.Request]] = {"openai": [], "anthropic": [], "gemini": [], "ollama": []}
    openai_client = httpx.AsyncClient(transport=httpx.MockTransport(_openai_handler(seen["openai"])))
    anthropic_client = httpx.AsyncClient(transport=httpx.MockTransport(_anthropic_handler(seen["anthropic"])))
    gemini_client = httpx.AsyncClient(transport=httpx.MockTransport(_gemini_handler(seen["gemini"])))
    ollama_client = httpx.AsyncClient(transport=httpx.MockTransport(_ollama_handler(seen["ollama"])))

    resolver = TenantCredentialResolver(AIProviderConfigStore(data_store), security_engine.get_secret)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(environment="production", storage_backend="sqlite", enable_cloud_models=False)
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[
            OllamaProvider(base_url="http://ollama.local", model_name=_OLLAMA_MODEL, client=ollama_client),
            OpenAIProvider(credential_resolver=resolver, client=openai_client),
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
        for permission in ("ai:manage", "ai:read", "ai:generate", "ai:orchestrate", "ai:governance"):
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
        yield kernel, ai_engine, seen
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await openai_client.aclose()
        await anthropic_client.aclose()
        await gemini_client.aclose()
        await ollama_client.aclose()
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


async def _configure(kernel: Kernel, token: Any, tenant: str, provider_id: str, api_key: str, **extra: Any) -> Any:
    return await _invoke(
        kernel, "kortex.ai.provider.configure", token, tenant, provider_id=provider_id, api_key=api_key, **extra
    )


async def _set_strict_local_only(kernel: Kernel, token: Any, tenant: str, value: bool) -> Any:
    return await _invoke(
        kernel,
        "kortex.ai.governance.policy.upsert",
        token,
        tenant,
        policy={"tenant_id": tenant, "strict_local_only": value},
    )


async def _orchestrate(kernel: Kernel, token: Any, tenant: str, goal: str = "Say hello") -> Any:
    return await _invoke(
        kernel,
        "kortex.ai.agent.orchestrate",
        token,
        tenant,
        task={
            "task_id": f"b5-task-{uuid4().hex[:8]}",
            "tenant_id": tenant,
            "user_id": "admin_a",
            "conversation_id": f"b5-conv-{uuid4().hex[:8]}",
            "goal": goal,
        },
    )


# ---------------------------------------------------------------------------
# §1 — No preference: local-first stands (requirement 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_tenant_reaches_local_ollama_not_a_healthy_cloud_provider(kernel_env) -> None:
    """Requirement 1, at the real boundary: no configuration -> ADR #001 local-first."""
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.status.value == "COMPLETED"
    assert result.final_response == "ollama answered"
    assert seen["openai"] == [] and seen["anthropic"] == [] and seen["gemini"] == []


# ---------------------------------------------------------------------------
# §2 — Explicit provider preference wins over local-first (requirements 2-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_configured_openai_is_explicitly_reached_over_healthy_ollama(kernel_env) -> None:
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "openai answered"
    assert seen["ollama"] == []


@pytest.mark.asyncio
async def test_tenant_configured_gemini_is_explicitly_reached_over_healthy_ollama(kernel_env) -> None:
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, GEMINI_PROVIDER_ID, _GEMINI_KEY)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "gemini answered"
    assert seen["ollama"] == []


@pytest.mark.asyncio
async def test_tenant_configured_anthropic_is_explicitly_reached_over_healthy_ollama(kernel_env) -> None:
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "anthropic", _ANTHROPIC_KEY)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "anthropic answered"
    assert seen["ollama"] == []


# ---------------------------------------------------------------------------
# §3 — default_model honored (requirement 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_default_model_reaches_the_provider_in_the_real_request(kernel_env) -> None:
    """Requirement 5: the configured model is what the vendor actually receives."""
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY, default_model=_OPENAI_MODEL)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "openai answered"
    generation_requests = [r for r in seen["openai"] if r.url.path.endswith("/chat/completions")]
    assert len(generation_requests) == 1
    assert json.loads(generation_requests[0].content)["model"] == _OPENAI_MODEL


# ---------------------------------------------------------------------------
# §4 — Simultaneous, independent tenant preferences (requirement 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_tenants_reach_two_different_configured_providers_independently(kernel_env) -> None:
    kernel, _ai_engine, _seen = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")
    await _configure(kernel, token_a, _TENANT_A, "openai", _OPENAI_KEY)
    await _configure(kernel, token_b, _TENANT_B, "anthropic", _ANTHROPIC_KEY)

    result_a = await _orchestrate(kernel, token_a, _TENANT_A)
    result_b = await _orchestrate(kernel, token_b, _TENANT_B)

    assert result_a.final_response == "openai answered"
    assert result_b.final_response == "anthropic answered"


# ---------------------------------------------------------------------------
# §5 — strict_local_only is an absolute deny (requirement 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_local_only_blocks_the_configured_preference_at_the_real_boundary(kernel_env) -> None:
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY)
    await _set_strict_local_only(kernel, token, _TENANT_A, True)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "ollama answered"
    assert seen["openai"] == []


# ---------------------------------------------------------------------------
# §6 — Disabled configuration is not a preference (requirement 12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_configuration_is_not_selected_at_the_real_boundary(kernel_env) -> None:
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY)
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY, enabled=False)

    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.final_response == "ollama answered"
    assert seen["openai"] == []


# ---------------------------------------------------------------------------
# §7 — Stale default_model fails safely (requirement 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_default_model_fails_the_request_instead_of_redirecting_to_ollama(kernel_env) -> None:
    """The sharpest B5.1 safety test, at the real boundary.

    A tenant's configured OpenAI provider is genuinely reachable, but its
    `default_model` names something OpenAI's static `supported_models`
    allow-list does not contain. This must raise -- a silent fallback to
    the healthy local provider would look, to the tenant, exactly like a
    successful cloud request that quietly used the wrong provider.
    """
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY, default_model="gpt-3-ancient-deprecated")

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)

    generation_requests = [r for r in seen["openai"] if r.url.path.endswith("/chat/completions")]
    assert generation_requests == []
    assert seen["ollama"] == []


# ---------------------------------------------------------------------------
# §8 — Removing/clearing a preference restores local-first (round trip)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_removing_the_configuration_restores_local_first_routing(kernel_env) -> None:
    kernel, _ai_engine, _seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure(kernel, token, _TENANT_A, "openai", _OPENAI_KEY)
    assert (await _orchestrate(kernel, token, _TENANT_A)).final_response == "openai answered"

    await _invoke(kernel, "kortex.ai.provider.config.remove", token, _TENANT_A, provider_id="openai")

    result = await _orchestrate(kernel, token, _TENANT_A)
    assert result.final_response == "ollama answered"
