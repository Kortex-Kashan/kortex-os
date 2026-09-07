"""Phase B / B4: trusted cloud routing through the real Kernel Capability
Enforcement Boundary.

Drives the actual dispatch path -- real Storage + Security Engines, real
authentication, real RBAC, real `AIProviderConfigStore`/`SecretStore`, real
`TenantCredentialResolver`, real `AIGovernanceStore`, real
`TenantCloudRoutingAuthority`, real `ModelRouter`, real `ResilientAIProvider`
wrapping -- with a real `GeminiProvider` whose outbound HTTP is intercepted
by `httpx.MockTransport`. No real network call is ever made and no live
vendor credential is required.

This file proves the chain B4 exists to deliver, end to end:

    configure -> test -> model discovery -> default model
             -> agent.orchestrate -> trusted routing -> provider invocation

**Every registered provider is cloud, and no local provider exists.**
`bootstrap.py` registers OpenAI, Gemini and Anthropic whenever a credential
resolver is available -- which B4 requires -- so all three are present. With
no local provider to fall back to, "did cloud routing happen?" is answered
unambiguously by whether the request succeeded, rather than by inspecting a
routing decision that a test could assert while the decision was ignored
downstream.

Only Gemini's mock transport ever succeeds; OpenAI's and Anthropic's answer
`401` to everything. That is not decoration. All three are cloud, so they
are all ranked candidates, and `ProviderFallbackChain` walks the list -- an
unmocked provider in that chain would attempt a **real network call** the
moment Gemini failed. Pinning them to a deterministic offline failure keeps
"Gemini answered" unambiguous and keeps the suite hermetic.

`enable_cloud_models` is left `False` throughout, exactly as
`kernel_bootstrap.py` leaves it in production. Every success below is
therefore attributable to the tenant-scoped authority and to nothing else.
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
from kortex.engines.ai.exceptions import (
    CloudRoutingNotPermittedError,
    NoRoutableProviderError,
    ProviderFallbackExhaustedError,
)
from kortex.engines.ai.gemini_provider import GEMINI_PROVIDER_ID, GeminiProvider
from kortex.engines.ai.openai_provider import OpenAIProvider
from kortex.engines.ai.persistence import AIProviderConfigStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x71" * 32
_TEST_SIGNING_KEY = b"\x2d" * 32
_ROLE = "AI_B4_CLOUD_ROUTING_ROLE"
_TENANT_A = "tenant_a_b4"
_TENANT_B = "tenant_b_b4"

_KEY_A = "gemini-b4-key-tenant-a"  # nosec - test fixture
_KEY_B = "gemini-b4-key-tenant-b"  # nosec - test fixture

_MODEL_A = "gemini-2.5-flash"
_MODEL_B = "gemini-2.5-pro"


def _gemini_handler(seen: list[httpx.Request]):
    """Mock Gemini: authenticates on `x-goog-api-key`, answers the catalog
    and generation endpoints, and reports a *different* catalog per key so a
    cross-tenant credential mix-up would be visible in the result."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = request.headers.get("x-goog-api-key", "")
        if key not in (_KEY_A, _KEY_B):
            return httpx.Response(
                401,
                json={"error": {"code": 401, "message": "API key not valid", "status": "UNAUTHENTICATED"}},
            )
        if request.url.path.endswith(":generateContent"):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "cloud provider answered"}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
                    "modelVersion": _MODEL_A,
                },
            )
        models = (
            [{"name": f"models/{_MODEL_A}", "supportedGenerationMethods": ["generateContent"]}]
            if key == _KEY_A
            else [{"name": f"models/{_MODEL_B}", "supportedGenerationMethods": ["generateContent"]}]
        )
        return httpx.Response(200, json={"models": models})

    return handler


def _always_unauthorized(request: httpx.Request) -> httpx.Response:
    """Deterministic offline failure for the two providers B4 does not
    exercise, so the fallback chain can never reach the real network."""
    return httpx.Response(401, json={"error": {"message": "not exercised by this suite"}})


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, Any, list[httpx.Request]]]:
    db_path = (tmp_path / f"kortex_b4_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_b4_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(_gemini_handler(seen)))
    inert_transport = httpx.MockTransport(_always_unauthorized)
    openai_client = httpx.AsyncClient(transport=inert_transport)
    anthropic_client = httpx.AsyncClient(transport=inert_transport)
    resolver = TenantCredentialResolver(AIProviderConfigStore(data_store), security_engine.get_secret)

    bootstrap = KernelProductionBootstrap(
        config=AIEngineRuntimeConfig(
            environment="production",
            storage_backend="sqlite",
            # Left False on purpose: this is what production bootstrap
            # produces, so nothing below can succeed via the global flag.
            enable_cloud_models=False,
        )
    )
    ai_engine = bootstrap.create_ai_engine(
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=data_store,
        custom_providers=[
            GeminiProvider(credential_resolver=resolver, client=client),
            OpenAIProvider(credential_resolver=resolver, client=openai_client),
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
        await client.aclose()
        await openai_client.aclose()
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


async def _configure_gemini(kernel: Kernel, token: Any, tenant: str, api_key: str, **extra: Any) -> Any:
    return await _invoke(
        kernel,
        "kortex.ai.provider.configure",
        token,
        tenant,
        provider_id=GEMINI_PROVIDER_ID,
        api_key=api_key,
        **extra,
    )


async def _orchestrate(kernel: Kernel, token: Any, tenant: str, goal: str = "Say hello") -> Any:
    return await _invoke(
        kernel,
        "kortex.ai.agent.orchestrate",
        token,
        tenant,
        task={
            "task_id": f"b4-task-{uuid4().hex[:8]}",
            "tenant_id": tenant,
            "user_id": "admin_a",
            "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
            "goal": goal,
        },
    )


async def _set_strict_local_only(kernel: Kernel, token: Any, tenant: str, value: bool) -> Any:
    return await _invoke(
        kernel,
        "kortex.ai.governance.policy.upsert",
        token,
        tenant,
        # No `tenant_id` parameter: `upsert_governance_policy` takes the
        # tenant from inside the submitted policy and then overrides it with
        # the verified identity, so a caller cannot rewrite another tenant's
        # guardrails by naming that tenant in the payload.
        policy={"tenant_id": tenant, "strict_local_only": value},
    )


# ---------------------------------------------------------------------------
# §1 — The fixture's own premises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_registered_provider_is_cloud_and_the_authority_is_wired(kernel_env) -> None:
    """Every routing assertion below depends on these two premises.

    If a local provider were registered, a "cloud denied" test could pass
    while cloud routing was in fact wide open, because the local provider
    would have served the request either way. And if the authority were not
    wired, the fail-closed `default_routing_context` alone would deny
    everything — which would make the denial tests pass for the wrong
    reason.
    """
    _kernel, ai_engine, _seen = kernel_env
    metadata = ai_engine.provider_registry.list_providers()

    assert {m.provider_id for m in metadata} == {GEMINI_PROVIDER_ID, "openai", "anthropic"}
    assert {m.endpoint_type for m in metadata} == {"cloud"}
    assert ai_engine.cloud_routing_authority is not None
    assert ai_engine.cloud_routing_authority.cloud_provider_ids() == {
        GEMINI_PROVIDER_ID,
        "openai",
        "anthropic",
    }


# ---------------------------------------------------------------------------
# §2 — The B4 objective, end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_orchestration_cannot_reach_cloud_before_configuration(kernel_env) -> None:
    """Requirement 1, at the real boundary: unconfigured tenant, no cloud.

    Also the "before" half of the objective below -- proving the success
    there is caused by configuring the provider and not by the provider
    simply always being reachable.
    """
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)

    # Nothing was sent to the vendor: the denial happened before egress.
    assert seen == []


@pytest.mark.asyncio
async def test_configure_test_discover_default_model_then_orchestrate_reaches_the_provider(
    kernel_env,
) -> None:
    """The exact chain B4 was authorized to deliver, in one test.

    configure -> test -> model discovery -> default model ->
    agent.orchestrate -> trusted routing -> provider invocation.
    """
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    # 1. Configure: the key goes to SecretStore, only a handle is persisted,
    #    and the response carries neither the key nor the handle.
    configured = await _configure_gemini(kernel, token, _TENANT_A, _KEY_A)
    assert configured["has_credential"] is True
    assert "secret_handle" not in configured
    assert _KEY_A not in str(configured)

    # 2 + 3. Test the connection and discover models live, with the tenant's
    #        own credential resolved server-side.
    tested = await _invoke(kernel, "kortex.ai.provider.test", token, _TENANT_A, provider_id=GEMINI_PROVIDER_ID)
    assert tested["connected"] is True
    assert [m["model_id"] for m in tested["models"]] == [_MODEL_A]

    # 4. Persist one of the discovered models as the tenant's default.
    with_default = await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)
    assert with_default["default_model"] == _MODEL_A

    # 5-7. Orchestrate through the agent path -- which carries no routing
    #      context at all -- and reach the cloud provider.
    result = await _orchestrate(kernel, token, _TENANT_A)

    assert result.status.value == "COMPLETED"
    assert result.final_response == "cloud provider answered"

    # The provider really was invoked, and really was authenticated with
    # this tenant's own key.
    generation_requests = [r for r in seen if r.url.path.endswith(":generateContent")]
    assert len(generation_requests) == 1
    assert generation_requests[0].headers["x-goog-api-key"] == _KEY_A


# ---------------------------------------------------------------------------
# §3 — strict_local_only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strict_local_only_blocks_a_fully_configured_cloud_provider(kernel_env) -> None:
    """Requirement 3, at the real boundary, through the real governance capability.

    The provider is configured, enabled and credentialed, and has already
    served a request in this very test -- so the denial after the policy
    change can only be the policy.
    """
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)

    permitted = await _orchestrate(kernel, token, _TENANT_A)
    assert permitted.status.value == "COMPLETED"
    egress_before = len([r for r in seen if r.url.path.endswith(":generateContent")])

    await _set_strict_local_only(kernel, token, _TENANT_A, True)

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)

    # No further egress: the denial preceded the vendor call.
    assert len([r for r in seen if r.url.path.endswith(":generateContent")]) == egress_before


@pytest.mark.asyncio
async def test_clearing_strict_local_only_restores_cloud_routing(kernel_env) -> None:
    """Requirement 4: the block is a live policy read, not a one-way latch.

    A cached decision would leave the tenant blocked after the policy was
    cleared -- which is why `TenantCloudRoutingAuthority` has no cache.
    """
    kernel, _ai_engine, _seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)

    await _set_strict_local_only(kernel, token, _TENANT_A, True)
    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)

    await _set_strict_local_only(kernel, token, _TENANT_A, False)

    result = await _orchestrate(kernel, token, _TENANT_A)
    assert result.status.value == "COMPLETED"


# ---------------------------------------------------------------------------
# §4 — Configuration state is a live gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabling_the_configuration_revokes_cloud_routing(kernel_env) -> None:
    """`AIProviderConfig.enabled` is documented as a gate that "must not route".

    Before B4 that half of the invariant was unenforced: a disabled
    configuration stopped resolving a credential but nothing stopped the
    provider being routed to.
    """
    kernel, _ai_engine, _seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)
    assert (await _orchestrate(kernel, token, _TENANT_A)).status.value == "COMPLETED"

    await _invoke(
        kernel,
        "kortex.ai.provider.configure",
        token,
        _TENANT_A,
        provider_id=GEMINI_PROVIDER_ID,
        enabled=False,
    )

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)


@pytest.mark.asyncio
async def test_removing_the_configuration_revokes_cloud_routing(kernel_env) -> None:
    """Removal is a real revocation, not just a disappearance from a list."""
    kernel, _ai_engine, _seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)
    assert (await _orchestrate(kernel, token, _TENANT_A)).status.value == "COMPLETED"

    removed = await _invoke(
        kernel, "kortex.ai.provider.config.remove", token, _TENANT_A, provider_id=GEMINI_PROVIDER_ID
    )
    assert removed["removed"] is True

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token, _TENANT_A)


# ---------------------------------------------------------------------------
# §5 — Tenant isolation (requirements 5 and 6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_tenants_configuration_does_not_authorize_another(kernel_env) -> None:
    """Requirement 5, at the real boundary."""
    kernel, _ai_engine, _seen = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")

    await _configure_gemini(kernel, token_a, _TENANT_A, _KEY_A, default_model=_MODEL_A)

    assert (await _orchestrate(kernel, token_a, _TENANT_A)).status.value == "COMPLETED"
    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _orchestrate(kernel, token_b, _TENANT_B)


@pytest.mark.asyncio
async def test_each_tenant_reaches_only_its_own_credential_and_catalog(kernel_env) -> None:
    """Requirement 5, sharpened: both tenants are authorized, so the test
    can only pass if each one's *own* credential was resolved.

    The mock reports a different catalog per key, so a credential mix-up
    shows up as the wrong model list rather than as an auth failure.
    """
    kernel, _ai_engine, _seen = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")
    await _configure_gemini(kernel, token_a, _TENANT_A, _KEY_A)
    await _configure_gemini(kernel, token_b, _TENANT_B, _KEY_B)

    tested_a = await _invoke(kernel, "kortex.ai.provider.test", token_a, _TENANT_A, provider_id=GEMINI_PROVIDER_ID)
    tested_b = await _invoke(kernel, "kortex.ai.provider.test", token_b, _TENANT_B, provider_id=GEMINI_PROVIDER_ID)

    assert [m["model_id"] for m in tested_a["models"]] == [_MODEL_A]
    assert [m["model_id"] for m in tested_b["models"]] == [_MODEL_B]


@pytest.mark.asyncio
async def test_a_claimed_tenant_id_cannot_borrow_another_tenants_authorization(kernel_env) -> None:
    """Requirement 6: a caller-supplied `tenant_id` does not decide routing.

    Tenant A is authorized; the caller authenticates as tenant B and puts
    `tenant_id: _TENANT_A` in the request body. `generate_response` rebinds
    the request to the verified principal's tenant before routing, so the
    claim buys nothing.
    """
    kernel, _ai_engine, seen = kernel_env
    token_a = await _token(kernel, _TENANT_A, "admin_a")
    token_b = await _token(kernel, _TENANT_B, "admin_b")
    await _configure_gemini(kernel, token_a, _TENANT_A, _KEY_A, default_model=_MODEL_A)
    egress_before = len([r for r in seen if r.url.path.endswith(":generateContent")])

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _invoke(
            kernel,
            "kortex.ai.response.generate",
            token_b,
            _TENANT_B,
            request={
                "request_id": f"b4-req-{uuid4().hex[:8]}",
                "tenant_id": _TENANT_A,
                "user_id": "admin_b",
                "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
                "prompt": "borrow tenant A's authorization",
            },
        )

    assert len([r for r in seen if r.url.path.endswith(":generateContent")]) == egress_before


# ---------------------------------------------------------------------------
# §6 — Caller-supplied routing context is untrusted (requirement 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_supplied_allow_cloud_cannot_authorize_egress(kernel_env) -> None:
    """Requirement 7 at the real boundary.

    This is reachable in earnest: `CapabilityDispatcher._coerce_model_
    parameters` (M7.2) validates a plain dict into the handler's declared
    `RoutingContext`, so `{"allow_cloud": true}` from a client really does
    arrive as a genuine `RoutingContext(allow_cloud=True)`. Without the
    server-side override this request would reach the vendor.
    """
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await _invoke(
            kernel,
            "kortex.ai.response.generate",
            token,
            _TENANT_A,
            request={
                "request_id": f"b4-req-{uuid4().hex[:8]}",
                "tenant_id": _TENANT_A,
                "user_id": "admin_a",
                "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
                "prompt": "please go to the cloud",
            },
            routing_context={"allow_cloud": True},
        )

    assert seen == []


@pytest.mark.asyncio
async def test_caller_supplied_cloud_provider_pin_is_rejected(kernel_env) -> None:
    """A `provider_id` pin is the sharpest bypass, since
    `ModelRouter._resolve_pinned` deliberately ignores `allow_cloud`."""
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    with pytest.raises(CloudRoutingNotPermittedError):
        await _invoke(
            kernel,
            "kortex.ai.response.generate",
            token,
            _TENANT_A,
            request={
                "request_id": f"b4-req-{uuid4().hex[:8]}",
                "tenant_id": _TENANT_A,
                "user_id": "admin_a",
                "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
                "prompt": "pin me to the vendor",
            },
            routing_context={"provider_id": GEMINI_PROVIDER_ID},
        )

    assert seen == []


@pytest.mark.asyncio
async def test_caller_supplied_cloud_endpoint_type_is_rejected(kernel_env) -> None:
    """`endpoint_type="cloud"` bypasses the `allow_cloud` gate in
    `ModelRouter._discover`'s `if/elif`, so it needs its own rejection."""
    kernel, _ai_engine, seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")

    with pytest.raises(CloudRoutingNotPermittedError):
        await _invoke(
            kernel,
            "kortex.ai.response.generate",
            token,
            _TENANT_A,
            request={
                "request_id": f"b4-req-{uuid4().hex[:8]}",
                "tenant_id": _TENANT_A,
                "user_id": "admin_a",
                "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
                "prompt": "route me anywhere cloud",
            },
            routing_context={"endpoint_type": "cloud"},
        )

    assert seen == []


@pytest.mark.asyncio
async def test_an_authorized_tenant_may_still_supply_a_routing_context(kernel_env) -> None:
    """The rejections above are authorization-conditional, not a blanket ban.

    Without this, a change that rejected every cloud routing context
    unconditionally would look correct to every other test in this file.
    """
    kernel, _ai_engine, _seen = kernel_env
    token = await _token(kernel, _TENANT_A, "admin_a")
    await _configure_gemini(kernel, token, _TENANT_A, _KEY_A, default_model=_MODEL_A)

    response = await _invoke(
        kernel,
        "kortex.ai.response.generate",
        token,
        _TENANT_A,
        request={
            "request_id": f"b4-req-{uuid4().hex[:8]}",
            "tenant_id": _TENANT_A,
            "user_id": "admin_a",
            "conversation_id": f"b4-conv-{uuid4().hex[:8]}",
            "prompt": "hello",
        },
        routing_context={"provider_id": GEMINI_PROVIDER_ID},
    )

    assert response.text_content == "cloud provider answered"
