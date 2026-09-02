"""M5 real-HTTP regression coverage for the Connector/Driver Registry.

Drives the actual FastAPI app (`kortex.api.main:app`) — the same
`/capabilities/invoke` route the Tauri/Rust IPC bridge calls in production —
through an in-process ASGI transport, proving the exact HTTP status codes
M5 requires:

    no Authorization header                    -> 401
    valid session token, missing permission     -> 403
    valid session token, "connector:read"       -> 200, payload.result lists the
                                                   production-registered drivers (M7.3-W1)

No mocking of Kernel, Security Engine, or Connector Engine: this boots the
real production `build_and_boot_kernel()` path against isolated per-test
storage/keys, and drives it with `httpx.ASGITransport` on the *same*
pytest-asyncio event loop the kernel's async DB engine is bound to
(`TestClient`'s background-thread portal would instead hand requests to a
second, unrelated event loop — unsafe for an async SQLAlchemy engine, so
it is deliberately not used here).
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
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_ROLE = "connector-http-test-role"
_CREDENTIAL = "connector-http-test-credential"


@pytest_asyncio.fixture
async def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Kernel]:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "connector_http_storage"))
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
    return f"tenant-connector-http-{uuid.uuid4().hex[:8]}"


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


async def _invoke_driver_list(client: httpx.AsyncClient, token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post(
        "/capabilities/invoke",
        headers=headers,
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": "kortex.connector.driver.list",
            "parameters": {},
        },
    )


@pytest.mark.asyncio
async def test_no_authorization_header_returns_401(client: httpx.AsyncClient) -> None:
    response = await _invoke_driver_list(client, token=None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"
    assert "sessionToken" not in body


@pytest.mark.asyncio
async def test_authenticated_without_permission_returns_403(kernel: Kernel, client: httpx.AsyncClient) -> None:
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[])
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke_driver_list(client, token)

    assert response.status_code == 403
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["errors"][0]["category"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_authenticated_with_permission_returns_200_and_registered_drivers(
    kernel: Kernel, client: httpx.AsyncClient
) -> None:
    """M7.3-W1: the real production boot path now registers
    `connector-dummy`/`connector-http-rest` automatically -- prior to M7.3
    this asserted an empty registry, since nothing in production ever
    registered a driver."""
    storage = kernel.get_engine("storage")
    assert isinstance(storage, StorageEngine)
    tenant_id = _tenant()
    await _seed_principal(storage.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage.data, _TEST_ROLE, "connector:read")
    token = await _login(client, tenant_id, "principal-1")

    response = await _invoke_driver_list(client, token)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    driver_ids = {d["driver_id"] for d in body["payload"]["result"]}
    assert driver_ids == {"connector-dummy", "connector-http-rest"}
