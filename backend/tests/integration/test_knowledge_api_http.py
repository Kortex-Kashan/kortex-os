"""Slice 4.7 real-HTTP regression coverage for the Knowledge graph.

Drives the actual FastAPI app (`kortex.api.main:app`) through an
in-process ASGI transport, proving the exact HTTP status codes Slice 4.7
requires for `kortex.knowledge.graph.list` (entity discovery) and
`kortex.knowledge.graph.traverse` (relationship exploration):

    no Authorization header                    -> 401
    valid session token, missing permission     -> 403
    valid session token, "knowledge:read"       -> 200

`kortex.knowledge.query.search` is deliberately not exercised here — see
`test_knowledge_capability_dispatch.py`'s module docstring for the
pre-existing defect that makes it unusable over real dict-based
parameters (confirmed here too: this file's own JSON body IS exactly that
dict-based path, so a real HTTP test of `search` would just reproduce a
500, not a meaningful 200/403/401 case).

See `test_document_api_http.py`'s module docstring for why
`httpx.ASGITransport` is used here rather than `TestClient`.
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
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.models import KnowledgeNode
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_ROLE = "knowledge-http-test-role"
_CREDENTIAL = "knowledge-http-test-credential"


@pytest_asyncio.fixture
async def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Kernel]:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "knowledge_http_storage"))
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
    return f"tenant-knowledge-http-{uuid.uuid4().hex[:8]}"


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
    client: httpx.AsyncClient, capability_name: str, parameters: dict[str, object], token: str | None
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post(
        "/capabilities/invoke",
        headers=headers,
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": capability_name,
            "parameters": parameters,
        },
    )


@pytest.mark.asyncio
async def test_no_authorization_header_returns_401_for_graph_list(client: httpx.AsyncClient) -> None:
    response = await _invoke(client, "kortex.knowledge.graph.list", {"tenant_id": "irrelevant"}, token=None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"
    assert "sessionToken" not in body


@pytest.mark.asyncio
async def test_authenticated_without_permission_returns_403_for_graph_list(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[])
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(client, "kortex.knowledge.graph.list", {"tenant_id": tenant_id}, token)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_authenticated_with_permission_returns_200_and_real_seeded_node(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    knowledge_engine = kernel.get_engine("knowledge")
    assert isinstance(knowledge_engine, KnowledgeEngine)
    tenant_id = _tenant()
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-1", tenant_id=tenant_id, entity_type="Concept", label="Distributed Systems")
    )

    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "knowledge:read")
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(client, "kortex.knowledge.graph.list", {"tenant_id": tenant_id}, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert len(body["payload"]["result"]) == 1
    assert body["payload"]["result"][0]["node_id"] == "node-1"


@pytest.mark.asyncio
async def test_no_authorization_header_returns_401_for_traverse(client: httpx.AsyncClient) -> None:
    response = await _invoke(
        client,
        "kortex.knowledge.graph.traverse",
        {"node_id": "n", "tenant_id": "t", "max_hops": 1},
        token=None,
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_with_permission_traverse_returns_200(kernel: Kernel, client: httpx.AsyncClient) -> None:
    knowledge_engine = kernel.get_engine("knowledge")
    assert isinstance(knowledge_engine, KnowledgeEngine)
    tenant_id = _tenant()
    knowledge_engine.graph.add_node(
        KnowledgeNode(node_id="node-1", tenant_id=tenant_id, entity_type="Concept", label="Distributed Systems")
    )

    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "knowledge:read")
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke(
        client,
        "kortex.knowledge.graph.traverse",
        {"node_id": "node-1", "tenant_id": tenant_id, "max_hops": 1},
        token,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["payload"]["result"] == []
