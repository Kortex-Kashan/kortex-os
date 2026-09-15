"""Tests for `IntegrationOAuthManager` (Integration Hub M2).

Mirrors `test_oauth.py`'s exact fixture pattern (a real `Kernel` +
`StorageEngine` + `SecurityEngine`, booted fully, against real SQLite) — the
atomic single-use state consumption and optimistic-CAS rotation this module
implements are only meaningfully tested against a real `IDataStore`
transaction, not a fake.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    IntegrationCredentialNotFoundError,
    IntegrationOAuthReplayError,
    OAuthProviderNotConfiguredError,
    OAuthStateError,
    SecretNotFoundError,
)
from kortex.engines.security.models import IntegrationTokenSet
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_AUTH_SIGNING_KEY = b"\x88" * 32


class FakeIntegrationOAuthProvider:
    """Minimal `IIntegrationOAuthProvider` test double — no network calls."""

    provider_id = "github"

    def __init__(self) -> None:
        self.exchanged_codes: list[str] = []
        self.refreshed_tokens: list[str] = []
        self.next_exchange_result: IntegrationTokenSet | None = None
        self.next_refresh_result: IntegrationTokenSet | None = None
        self.raise_on_refresh: Exception | None = None

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://fake-github.example/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IntegrationTokenSet:
        self.exchanged_codes.append(code)
        if self.next_exchange_result is not None:
            return self.next_exchange_result
        return IntegrationTokenSet(access_token=f"access-for-{code}")

    async def refresh(self, refresh_token: str) -> IntegrationTokenSet:
        self.refreshed_tokens.append(refresh_token)
        if self.raise_on_refresh is not None:
            raise self.raise_on_refresh
        if self.next_refresh_result is not None:
            return self.next_refresh_result
        return IntegrationTokenSet(access_token="rotated-access-token", refresh_token="rotated-refresh-token")


def _tenant(tmp_path: Path) -> str:
    return f"tenant-int-oauth-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


async def _boot(
    tmp_path: Path, providers: dict[str, object] | None = None
) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "integration_oauth_test_storage"))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_AUTH_SIGNING_KEY,
        integration_oauth_providers=providers or {},
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


@pytest.mark.asyncio
async def test_begin_rejects_unconfigured_provider(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {})
    manager = security_engine.integration_oauth_manager

    with pytest.raises(OAuthProviderNotConfiguredError):
        await manager.begin_authorization(
            provider="github", tenant_id=_tenant(tmp_path), profile_id="prof-1", redirect_uri="app://callback"
        )


@pytest.mark.asyncio
async def test_begin_returns_authorization_url_and_state(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager

    result = await manager.begin_authorization(
        provider="github",
        tenant_id=_tenant(tmp_path),
        profile_id="prof-1",
        redirect_uri="app://callback",
        principal_id="user-1",
        principal_type="USER",
    )

    assert "authorization_url" in result
    assert result["state"]
    assert result["state"] in result["authorization_url"]


@pytest.mark.asyncio
async def test_complete_happy_path_writes_secret_and_credential_record(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github",
        tenant_id=tenant_id,
        profile_id="prof-1",
        redirect_uri="app://callback",
        principal_id="user-1",
        principal_type="USER",
    )

    result = await manager.complete_authorization(
        provider="github",
        profile_id="prof-1",
        code="auth-code-xyz",
        state=begin["state"],
        redirect_uri="app://callback",
        tenant_id=tenant_id,
        principal_id="user-1",
        principal_type="USER",
    )

    assert result["connected"] is True
    secret_handle = result["secret_handle"]
    assert secret_handle
    assert provider.exchanged_codes == ["auth-code-xyz"]

    status = await manager.get_status(provider="github", tenant_id=tenant_id, profile_id="prof-1")
    assert status["connected"] is True

    token = await manager.resolve_access_token(secret_handle, tenant_id, "prof-1")
    assert token == "access-for-auth-code-xyz"


@pytest.mark.asyncio
async def test_complete_rejects_tampered_state(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-1", redirect_uri="app://callback"
    )
    tampered_state = begin["state"][:-4] + "abcd"

    with pytest.raises(OAuthStateError):
        await manager.complete_authorization(
            provider="github",
            profile_id="prof-1",
            code="auth-code",
            state=tampered_state,
            redirect_uri="app://callback",
            tenant_id=tenant_id,
        )


@pytest.mark.asyncio
async def test_complete_rejects_mismatched_profile(tmp_path: Path) -> None:
    """The signed state is bound to the exact profile it was issued for —
    completing against a different `profile_id` must fail, not silently
    connect the wrong profile."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-1", redirect_uri="app://callback"
    )

    with pytest.raises(OAuthStateError):
        await manager.complete_authorization(
            provider="github",
            profile_id="prof-DIFFERENT",
            code="auth-code",
            state=begin["state"],
            redirect_uri="app://callback",
            tenant_id=tenant_id,
        )


@pytest.mark.asyncio
async def test_complete_rejects_mismatched_principal(tmp_path: Path) -> None:
    """Hard principal binding, mirroring `SecurityEngine.oauth_link_complete_
    capability`'s own precedent over the identical desktop deep-link
    transport: a state signed for one principal cannot be completed by a
    different one, even within the same tenant."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github",
        tenant_id=tenant_id,
        profile_id="prof-1",
        redirect_uri="app://callback",
        principal_id="user-1",
        principal_type="USER",
    )

    with pytest.raises(OAuthStateError):
        await manager.complete_authorization(
            provider="github",
            profile_id="prof-1",
            code="auth-code",
            state=begin["state"],
            redirect_uri="app://callback",
            tenant_id=tenant_id,
            principal_id="user-DIFFERENT",
            principal_type="USER",
        )


@pytest.mark.asyncio
async def test_complete_is_single_use(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-1", redirect_uri="app://callback"
    )

    await manager.complete_authorization(
        provider="github",
        profile_id="prof-1",
        code="auth-code",
        state=begin["state"],
        redirect_uri="app://callback",
        tenant_id=tenant_id,
    )

    with pytest.raises(IntegrationOAuthReplayError):
        await manager.complete_authorization(
            provider="github",
            profile_id="prof-1",
            code="auth-code-2",
            state=begin["state"],
            redirect_uri="app://callback",
            tenant_id=tenant_id,
        )


@pytest.mark.asyncio
async def test_concurrent_complete_with_same_state_resolves_exactly_one_success(tmp_path: Path) -> None:
    """The point-4 guarantee: two concurrent callback deliveries for the same
    `state` must never both succeed. Proven with `asyncio.gather`, not just
    asserted by reading the implementation."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-1", redirect_uri="app://callback"
    )

    async def _attempt() -> object:
        try:
            return await manager.complete_authorization(
                provider="github",
                profile_id="prof-1",
                code="auth-code",
                state=begin["state"],
                redirect_uri="app://callback",
                tenant_id=tenant_id,
            )
        except IntegrationOAuthReplayError as exc:
            return exc

    results = await asyncio.gather(_attempt(), _attempt())
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, IntegrationOAuthReplayError)]
    assert len(successes) == 1
    assert len(failures) == 1


@pytest.mark.asyncio
async def test_resolve_access_token_passes_through_a_plain_non_oauth_secret(tmp_path: Path) -> None:
    """MCP/any other existing connector's plain-string secret must resolve
    unchanged — this manager's OAuth marker check must never mistake a plain
    bearer token for one of its own JSON-managed blobs."""
    _kernel, _storage, security_engine = await _boot(tmp_path, {})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    await security_engine.secret_store.put_secret("connector/mcp-profile", tenant_id, "plain-bearer-token-xyz")

    resolved = await manager.resolve_access_token("connector/mcp-profile", tenant_id, "mcp-profile")
    assert resolved == "plain-bearer-token-xyz"


@pytest.mark.asyncio
async def test_resolve_access_token_refuses_a_handle_that_does_not_belong_to_the_profile(tmp_path: Path) -> None:
    """Cross-profile binding check (§D): even a syntactically valid
    OAuth-managed secret handle must be refused if it isn't the one on
    record for the requested `(tenant_id, profile_id)`."""
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin_a = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-a", redirect_uri="app://callback"
    )
    result_a = await manager.complete_authorization(
        provider="github",
        profile_id="prof-a",
        code="code-a",
        state=begin_a["state"],
        redirect_uri="app://callback",
        tenant_id=tenant_id,
    )

    # Resolving Profile A's own handle through Profile B (which has no
    # credential record of its own) must be refused, not return A's token.
    resolved = await manager.resolve_access_token(result_a["secret_handle"], tenant_id, "prof-b")
    assert resolved is None


@pytest.mark.asyncio
async def test_get_status_raises_for_unconnected_profile(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {})
    manager = security_engine.integration_oauth_manager

    with pytest.raises(IntegrationCredentialNotFoundError):
        await manager.get_status(provider="github", tenant_id=_tenant(tmp_path), profile_id="prof-none")


@pytest.mark.asyncio
async def test_disconnect_credential_is_idempotent(tmp_path: Path) -> None:
    provider = FakeIntegrationOAuthProvider()
    _kernel, _storage, security_engine = await _boot(tmp_path, {"github": provider})
    manager = security_engine.integration_oauth_manager
    tenant_id = _tenant(tmp_path)

    begin = await manager.begin_authorization(
        provider="github", tenant_id=tenant_id, profile_id="prof-1", redirect_uri="app://callback"
    )
    result = await manager.complete_authorization(
        provider="github",
        profile_id="prof-1",
        code="auth-code",
        state=begin["state"],
        redirect_uri="app://callback",
        tenant_id=tenant_id,
    )

    first = await manager.disconnect_credential(tenant_id, "prof-1", "github")
    second = await manager.disconnect_credential(tenant_id, "prof-1", "github")
    assert first is True
    assert second is True  # idempotent: revoking an already-revoked record is still a clean success

    # The SecretStore entry is actually deleted (not merely marked
    # unusable) — `resolve_access_token` propagates `SecretNotFoundError`
    # exactly as the default, non-OAuth resolver already would for any
    # other deleted secret; `ConnectorPipeline`'s own existing exception
    # handling turns this into an ordinary authentication-failed result.
    with pytest.raises(SecretNotFoundError):
        await manager.resolve_access_token(result["secret_handle"], tenant_id, "prof-1")


@pytest.mark.asyncio
async def test_disconnect_credential_no_op_when_never_connected(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {})
    manager = security_engine.integration_oauth_manager

    result = await manager.disconnect_credential(_tenant(tmp_path), "prof-never-connected", "github")
    assert result is False
