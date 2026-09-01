"""Slice 4.6 real-HTTP regression coverage for the AI Studio provider/model
registry, extended by M7.2 to cover `kortex.ai.response.generate` and
`kortex.ai.conversation.history.get` at the same real-HTTP boundary.

Drives the actual FastAPI app (`kortex.api.main:app`) — the same
`/capabilities/invoke` route the Tauri/Rust IPC bridge calls in production —
through an in-process ASGI transport, proving the exact HTTP status codes
Slice 4.6 requires, for `kortex.ai.provider.list`, `kortex.ai.model.list`,
and (M7.2) `kortex.ai.response.generate`/`kortex.ai.conversation.history.get`:

    no Authorization header                    -> 401
    valid session token, missing permission     -> 403
    valid session token, correct permission     -> 200, real result

This exercises the real `build_and_boot_kernel()` production path — the
same one `kernel_bootstrap.py` uses for every other engine — so the AI
Engine's production wiring (`KernelBridgeAdapter` + `RelationalDataStore`)
is proven end to end, not just in isolation. M7.2's `generate` coverage is
the first real-HTTP proof that a plain, JSON-shaped `request` body (a dict,
never a live `LLMRequest`) actually reaches a real provider —
`core.dispatch._coerce_model_parameters` is what makes this possible; see
that module's docstring for the defect this closes.

See `test_marketplace_api_http.py`'s module docstring for why
`httpx.ASGITransport` is used here rather than `TestClient` (both drive the
app on the *same* pytest-asyncio event loop the kernel's async DB engine is
bound to).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.kernel_bootstrap import build_and_boot_kernel
from kortex.api.main import app
from kortex.core.kernel import Kernel
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_ROLE = "ai-http-test-role"
_CREDENTIAL = "ai-http-test-credential"


class _HttpTestProvider(BaseAIProvider):
    """A real, functioning provider that never depends on Ollama being
    reachable — registered directly on the booted engine (M7.2), by the
    individual tests that need real generation to succeed, so the pre-existing
    provider/model-registry-count assertions elsewhere in this file are left
    undisturbed. The real `OllamaProvider` the production boot path also
    registers is tried first by the model router's fallback chain and simply
    fails over to this one when unreachable, exactly as `_generate_with_fallback`
    already does in production for any unavailable candidate."""

    def __init__(self) -> None:
        self._metadata = AIProviderMetadata(
            provider_id="http-test-provider",
            display_name="HTTP Test Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=["http-test-model"],
            credential_requirement="none",
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content=f"answer to: {request.prompt}",
            tool_calls=[],
            token_usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


@pytest_asyncio.fixture
async def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Kernel]:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "ai_http_storage"))
    booted_kernel = await build_and_boot_kernel()
    app.state.kernel = booted_kernel
    try:
        yield booted_kernel
    finally:
        await booted_kernel.shutdown()


@pytest_asyncio.fixture
async def client(kernel: Kernel) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


def _tenant() -> str:
    return f"tenant-ai-http-{uuid.uuid4().hex[:8]}"


async def _seed_principal(data_store: IDataStore, tenant_id: str, principal_id: str, roles: list[str]) -> None:
    credential_hash = PasswordHasher().hash(_CREDENTIAL)

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles,
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        existing = await session.scalar(
            select(RolePermissionRecord).where(
                RolePermissionRecord.role == role,
                RolePermissionRecord.permission == permission,
            )
        )
        if existing is None:
            session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _login(client: httpx.AsyncClient, tenant_id: str, principal_id: str) -> str:
    response = await client.post(
        "/capabilities/invoke",
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": "kortex.security.auth.authenticate",
            "parameters": {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                    "password": _CREDENTIAL,
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    token = response.json().get("sessionToken")
    assert isinstance(token, str) and token, "Login did not mint a sessionToken"
    return token


async def _invoke(
    client: httpx.AsyncClient,
    capability_name: str,
    token: str | None,
    parameters: dict | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post(
        "/capabilities/invoke",
        headers=headers,
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": capability_name,
            "parameters": parameters or {},
        },
    )


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_no_authorization_header_returns_401(client: httpx.AsyncClient, capability_name: str) -> None:
    response = await _invoke(client, capability_name, token=None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"
    assert "sessionToken" not in body


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_authenticated_without_permission_returns_403(
    kernel: Kernel, client: httpx.AsyncClient, capability_name: str
) -> None:
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[])
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(client, capability_name, token)

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("capability_name", ["kortex.ai.provider.list", "kortex.ai.model.list"])
@pytest.mark.asyncio
async def test_authenticated_with_permission_returns_200_and_real_registry(
    kernel: Kernel, client: httpx.AsyncClient, capability_name: str
) -> None:
    """M6.1-2: the production boot path now registers one real `OllamaProvider`
    unconditionally, so this registry is no longer empty -- see
    `test_kernel_bootstrap.py::test_ai_provider_registry_has_real_ollama_provider_on_production_boot_path`
    for the direct, non-HTTP assertion of its exact contents. This test's own
    job is unchanged: prove the real HTTP path returns 200/SUCCESS for an
    authorized caller, whatever the registry currently holds."""
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "ai:read")
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(client, capability_name, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert len(body["payload"]["result"]) == 1


# -- M7.2: kortex.ai.response.generate real-HTTP coverage --------------------


@pytest.mark.asyncio
async def test_generate_no_authorization_header_returns_401(client: httpx.AsyncClient) -> None:
    response = await _invoke(
        client,
        "kortex.ai.response.generate",
        token=None,
        parameters={
            "request": {
                "request_id": "req-1",
                "tenant_id": "irrelevant",
                "user_id": "irrelevant",
                "conversation_id": "conv-1",
                "prompt": "hello",
            }
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_generate_authenticated_without_permission_returns_403(kernel: Kernel, client: httpx.AsyncClient) -> None:
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[])
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(
        client,
        "kortex.ai.response.generate",
        token,
        parameters={
            "request": {
                "request_id": "req-1",
                "tenant_id": tenant_id,
                "user_id": "principal-1",
                "conversation_id": "conv-1",
                "prompt": "hello",
            }
        },
    )

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_generate_authenticated_with_permission_returns_200_with_real_response(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    """The critical M7.2 regression: `request` arrives over real JSON/HTTP
    as a plain dict, exactly what the desktop's `invokeCapability` sends —
    proving `core.dispatch._coerce_model_parameters` makes this capability
    genuinely reachable from the transport the desktop actually uses, not
    only from a same-process test that hand-constructs an `LLMRequest`."""
    ai_engine = kernel.get_engine("ai")
    ai_engine.register_provider(_HttpTestProvider())
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "ai:generate")
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(
        client,
        "kortex.ai.response.generate",
        token,
        parameters={
            "request": {
                "request_id": "req-generate-http-1",
                "tenant_id": tenant_id,
                "user_id": "principal-1",
                "conversation_id": "conv-generate-http-1",
                "prompt": "hello from the real HTTP boundary",
            }
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["payload"]["result"]["text_content"] == "answer to: hello from the real HTTP boundary"


# -- M7.2: kortex.ai.conversation.history.get real-HTTP coverage -------------


@pytest.mark.asyncio
async def test_conversation_history_no_authorization_header_returns_401(client: httpx.AsyncClient) -> None:
    response = await _invoke(
        client,
        "kortex.ai.conversation.history.get",
        token=None,
        parameters={"tenant_id": "irrelevant", "conversation_id": "conv-1"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_conversation_history_authenticated_without_permission_returns_403(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[])
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(
        client,
        "kortex.ai.conversation.history.get",
        token,
        parameters={"tenant_id": tenant_id, "conversation_id": "conv-1"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_conversation_history_round_trips_through_real_generation_and_http(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    """The full M7.2 restart-recovery contract, proven at the real HTTP
    boundary: a turn written by a real `generate` call is durably readable
    back through `kortex.ai.conversation.history.get` — never a different
    tenant's history, and never the caller-supplied tenant_id (deliberately
    wrong here) rather than the authenticated principal's real one."""
    ai_engine = kernel.get_engine("ai")
    ai_engine.register_provider(_HttpTestProvider())
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "ai:generate")
    await _grant_role_permission(storage.data, _TEST_ROLE, "ai:read")
    token = await _login(client, tenant_id, "principal-1")

    conversation_id = "conv-history-http-1"
    generate_response = await _invoke(
        client,
        "kortex.ai.response.generate",
        token,
        parameters={
            "request": {
                "request_id": "req-history-http-1",
                "tenant_id": tenant_id,
                "user_id": "principal-1",
                "conversation_id": conversation_id,
                "prompt": "remember this",
            }
        },
    )
    assert generate_response.status_code == 200, generate_response.text

    history_response = await _invoke(
        client,
        "kortex.ai.conversation.history.get",
        token,
        # Deliberately wrong tenant_id -- the authenticated principal's real
        # tenant must be used instead, never this caller-supplied value.
        parameters={"tenant_id": "some-other-tenant-entirely", "conversation_id": conversation_id},
    )

    assert history_response.status_code == 200, history_response.text
    body = history_response.json()
    turns = body["payload"]["result"]
    assert len(turns) == 1
    assert turns[0]["user_content"] == "remember this"
    assert turns[0]["assistant_content"] == "answer to: remember this"
