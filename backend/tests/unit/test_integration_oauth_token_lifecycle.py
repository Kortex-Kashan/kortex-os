"""Tests for `IntegrationOAuthManager`'s GitHub token lifecycle (Integration
Hub M2 token-lifecycle amendment): expiry, refresh-token rotation, per-profile
refresh synchronization, and `REAUTHORIZATION_REQUIRED` transitions.

Same real `Kernel` + `StorageEngine` + `SecurityEngine` fixture pattern as
`test_integration_oauth_manager.py` — rotation's optimistic compare-and-swap
is only meaningfully exercised against a real `IDataStore` transaction.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import OAuthRefreshInvalidError
from kortex.engines.security.models import IntegrationTokenSet
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x44" * 32
_TEST_AUTH_SIGNING_KEY = b"\x99" * 32


class FakeIntegrationOAuthProvider:
    provider_id = "github"

    def __init__(self) -> None:
        self.exchange_result: IntegrationTokenSet | None = None
        self.refresh_calls: list[str] = []
        self.refresh_result: IntegrationTokenSet | None = None
        self.raise_on_refresh: Exception | None = None

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://fake-github.example/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IntegrationTokenSet:
        assert self.exchange_result is not None
        return self.exchange_result

    async def refresh(self, refresh_token: str) -> IntegrationTokenSet:
        self.refresh_calls.append(refresh_token)
        if self.raise_on_refresh is not None:
            raise self.raise_on_refresh
        assert self.refresh_result is not None
        return self.refresh_result


def _tenant(tmp_path: Path) -> str:
    return f"tenant-lifecycle-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


async def _boot(tmp_path: Path, provider: FakeIntegrationOAuthProvider) -> tuple[Kernel, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "lifecycle_test_storage"))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_AUTH_SIGNING_KEY,
        integration_oauth_providers={"github": provider},
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, security_engine


async def _connect(
    manager, tenant_id: str, profile_id: str, token_set: IntegrationTokenSet, provider: FakeIntegrationOAuthProvider
) -> str:
    provider.exchange_result = token_set
    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id=profile_id, redirect_uri="app://callback"
    )
    result = await manager.complete_authorization(
        provider="github",
        profile_id=profile_id,
        code="code",
        state=begin["state"],
        redirect_uri="app://callback",
        tenant_id=tenant_id,
    )
    return result["secret_handle"]


@pytest.mark.asyncio
async def test_non_expiring_token_is_returned_as_is_and_never_refreshed(tmp_path: Path) -> None:
    """`offline_access` not granted (or the org disabled expiring tokens):
    `access_token_expires_at=None`. No refresh path is ever entered, even
    long after the connection was made."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    secret_handle = await _connect(
        manager, tenant_id, "prof-1", IntegrationTokenSet(access_token="classic-token"), provider
    )

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token == "classic-token"
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_unexpired_token_is_returned_without_a_refresh_call(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    future_expiry = datetime.now(UTC) + timedelta(hours=1)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(
            access_token="fresh-token", refresh_token="refresh-1", access_token_expires_at=future_expiry
        ),
        provider,
    )

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token == "fresh-token"
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_expired_token_triggers_refresh_and_rotation(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    past_expiry = datetime.now(UTC) - timedelta(seconds=5)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(
            access_token="stale-token", refresh_token="refresh-old", access_token_expires_at=past_expiry
        ),
        provider,
    )

    provider.refresh_result = IntegrationTokenSet(
        access_token="rotated-token",
        refresh_token="refresh-new",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token == "rotated-token"
    assert provider.refresh_calls == ["refresh-old"]

    # Rotation persisted: a second resolution (still unexpired) returns the
    # new token without calling refresh again, and the OLD refresh token is
    # never sent to the provider a second time even if something still held
    # a reference to it.
    token_again = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token_again == "rotated-token"
    assert provider.refresh_calls == ["refresh-old"]


@pytest.mark.asyncio
async def test_missing_refresh_token_on_expiry_requires_reauthorization(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    past_expiry = datetime.now(UTC) - timedelta(seconds=5)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(access_token="stale-token", refresh_token=None, access_token_expires_at=past_expiry),
        provider,
    )

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token is None
    assert provider.refresh_calls == []

    status = await manager.get_status(provider="github", tenant_id=tenant_id, profile_id="prof-1")
    assert status["status"] == "REAUTHORIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_invalid_refresh_token_requires_reauthorization(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    past_expiry = datetime.now(UTC) - timedelta(seconds=5)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(
            access_token="stale-token", refresh_token="refresh-bad", access_token_expires_at=past_expiry
        ),
        provider,
    )
    provider.raise_on_refresh = OAuthRefreshInvalidError("bad_refresh_token")

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token is None

    status = await manager.get_status(provider="github", tenant_id=tenant_id, profile_id="prof-1")
    assert status["status"] == "REAUTHORIZATION_REQUIRED"

    # Once flagged, no further refresh attempt is made on subsequent calls.
    provider.refresh_calls.clear()
    token_again = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token_again is None
    assert provider.refresh_calls == []


@pytest.mark.asyncio
async def test_expired_refresh_token_requires_reauthorization_without_calling_provider(tmp_path: Path) -> None:
    """A proactive check: if the refresh token's own recorded expiry has
    passed, don't even attempt a call GitHub would reject anyway."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    past = datetime.now(UTC) - timedelta(seconds=5)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(
            access_token="stale-token",
            refresh_token="refresh-1",
            access_token_expires_at=past,
            refresh_token_expires_at=past,
        ),
        provider,
    )

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token is None
    assert provider.refresh_calls == []

    status = await manager.get_status(provider="github", tenant_id=tenant_id, profile_id="prof-1")
    assert status["status"] == "REAUTHORIZATION_REQUIRED"


@pytest.mark.asyncio
async def test_concurrent_resolution_past_expiry_refreshes_exactly_once(tmp_path: Path) -> None:
    """Per-`(tenant_id, profile_id)` refresh synchronization: two concurrent
    dispatches both seeing an expired token must not both call the
    provider's `refresh()` — the second must observe the first's rotation
    (via the lock's double-check) and reuse its result."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, security_engine = await _boot(tmp_path, provider)
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    past_expiry = datetime.now(UTC) - timedelta(seconds=5)
    secret_handle = await _connect(
        manager,
        tenant_id,
        "prof-1",
        IntegrationTokenSet(
            access_token="stale-token", refresh_token="refresh-old", access_token_expires_at=past_expiry
        ),
        provider,
    )
    provider.refresh_result = IntegrationTokenSet(
        access_token="rotated-token",
        refresh_token="refresh-new",
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    results = await asyncio.gather(
        manager.resolve_access_token(secret_handle, tenant_id, "prof-1"),
        manager.resolve_access_token(secret_handle, tenant_id, "prof-1"),
    )

    assert results == ["rotated-token", "rotated-token"]
    assert provider.refresh_calls == ["refresh-old"]
