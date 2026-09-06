"""Tests for the Phase A OAuth sign-in capabilities (Google/Microsoft).

Uses a `FakeOAuthProvider` test double (implements `IOAuthProvider`) injected
via `SecurityEngine(oauth_providers=...)` — no real network calls, no real
credentials. Mirrors `test_security_engine.py`'s exact `_boot_kernel_with_security`
fixture pattern, extended with the OAuth provider override.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    OAuthLinkConflictError,
    OAuthNoLinkedAccountError,
    OAuthProviderNotConfiguredError,
    OAuthStateError,
)
from kortex.engines.security.models import SecurityPrincipal
from kortex.engines.security.oauth.base import OAuthUserInfo
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x22" * 32
_TEST_AUTH_SIGNING_KEY = b"\x77" * 32


class FakeOAuthProvider:
    """Minimal `IOAuthProvider` test double — no network calls."""

    def __init__(self, provider_id: str, subject: str = "external-subject-1", email: str | None = "user@example.com"):
        self.provider_id = provider_id
        self._subject = subject
        self._email = email
        self.exchanged_codes: list[str] = []

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://fake-provider.example/authorize?state={state}&redirect_uri={redirect_uri}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthUserInfo:
        self.exchanged_codes.append(code)
        return OAuthUserInfo(subject=self._subject, email=self._email)


def _tenant(tmp_path: Path) -> str:
    return f"tenant-oauth-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


def _subject(tmp_path: Path) -> str:
    """A globally-unique external subject id per test — `OAuthIdentityLinkRecord`'s
    `(provider, external_subject)` uniqueness is deliberately global, not
    tenant-scoped (see its docstring), so a fixed literal would collide
    across repeated runs against `Kernel()`'s shared, non-test-scoped
    default SQLite file — mirroring `test_authentication_manager.py`'s own
    `_tenant`/`_tenant_b` randomization for the identical reason."""
    return f"subject-{tmp_path.name}-{uuid.uuid4().hex[:8]}"


async def _boot(
    tmp_path: Path, oauth_providers: dict[str, object] | None = None
) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "oauth_test_storage"))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_AUTH_SIGNING_KEY,
        oauth_providers=oauth_providers or {},
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


def _execution_context_for(principal: SecurityPrincipal, capability_name: str) -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        request_id="req-1",
        correlation_id="corr-1",
        capability_name=capability_name,
        principal=principal,
        tenant_id=principal.tenant_id,
    )


@pytest.mark.asyncio
async def test_get_config_reports_configured_and_unconfigured_providers(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": FakeOAuthProvider("google")})

    config = await security_engine.oauth_get_config_capability()

    assert config == {"google": True, "microsoft": False}


@pytest.mark.asyncio
async def test_login_begin_rejects_unconfigured_provider(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {})

    with pytest.raises(OAuthProviderNotConfiguredError):
        await security_engine.oauth_login_begin_capability(provider="google")


@pytest.mark.asyncio
async def test_login_begin_returns_authorization_url_and_state(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": FakeOAuthProvider("google")})

    result = await security_engine.oauth_login_begin_capability(provider="google")

    assert "authorization_url" in result
    assert "state" in result
    assert result["state"]


@pytest.mark.asyncio
async def test_login_complete_returns_security_principal_for_linked_account(tmp_path: Path) -> None:
    """The raw return type must be `SecurityPrincipal`, not a dict — this is
    exactly what `kortex.api.main._invoke`'s existing bootstrap-exempt
    session-minting mechanism keys off of."""
    subject = _subject(tmp_path)
    fake = FakeOAuthProvider("google", subject=subject)
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    await security_engine.authentication_manager.link_oauth_identity(
        tenant_id=tenant,
        principal_id="alice",
        principal_type="USER",
        provider="google",
        external_subject=subject,
    )

    begin = await security_engine.oauth_login_begin_capability(provider="google")
    principal = await security_engine.oauth_login_complete_capability(
        provider="google", code="fake-code", state=begin["state"]
    )

    assert isinstance(principal, SecurityPrincipal)
    assert principal.principal_id == "alice"
    assert principal.tenant_id == tenant
    assert fake.exchanged_codes == ["fake-code"]


@pytest.mark.asyncio
async def test_login_complete_no_linked_account_is_honest_not_autocreated(tmp_path: Path) -> None:
    """OAuth is a second sign-in method for an existing account, never a
    self-registration path."""
    fake = FakeOAuthProvider("google", subject=_subject(tmp_path))
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})

    begin = await security_engine.oauth_login_begin_capability(provider="google")

    with pytest.raises(OAuthNoLinkedAccountError):
        await security_engine.oauth_login_complete_capability(provider="google", code="fake-code", state=begin["state"])


@pytest.mark.asyncio
async def test_login_complete_rejects_tampered_state(tmp_path: Path) -> None:
    fake = FakeOAuthProvider("google")
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})

    begin = await security_engine.oauth_login_begin_capability(provider="google")
    tampered_state = begin["state"][:-4] + "abcd"

    with pytest.raises(OAuthStateError):
        await security_engine.oauth_login_complete_capability(provider="google", code="fake-code", state=tampered_state)


@pytest.mark.asyncio
async def test_login_complete_rejects_a_link_intent_state(tmp_path: Path) -> None:
    """A state issued for linking (bound to an authenticated caller) must
    never be usable to complete a login."""
    fake = FakeOAuthProvider("google")
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    principal = SecurityPrincipal(principal_id="alice", principal_type="USER", tenant_id=tenant, roles=["member"])
    link_begin = await security_engine.oauth_link_begin_capability(
        provider="google", execution_context=_execution_context_for(principal, "kortex.security.oauth.link_begin")
    )

    with pytest.raises(OAuthStateError):
        await security_engine.oauth_login_complete_capability(
            provider="google", code="fake-code", state=link_begin["state"]
        )


@pytest.mark.asyncio
async def test_link_then_login_round_trip(tmp_path: Path) -> None:
    fake = FakeOAuthProvider("google", subject=_subject(tmp_path))
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    principal = SecurityPrincipal(principal_id="alice", principal_type="USER", tenant_id=tenant, roles=["member"])
    ctx = _execution_context_for(principal, "kortex.security.oauth.link_begin")

    link_begin = await security_engine.oauth_link_begin_capability(provider="google", execution_context=ctx)
    link_result = await security_engine.oauth_link_complete_capability(
        provider="google", code="fake-code", state=link_begin["state"], execution_context=ctx
    )
    assert link_result == {"linked": True, "provider": "google"}

    login_begin = await security_engine.oauth_login_begin_capability(provider="google")
    resolved = await security_engine.oauth_login_complete_capability(
        provider="google", code="fake-code-2", state=login_begin["state"]
    )
    assert resolved.principal_id == "alice"


@pytest.mark.asyncio
async def test_link_complete_rejects_state_bound_to_a_different_caller(tmp_path: Path) -> None:
    """Defense in depth: even a validly-signed link state must match the
    execution_context's own identity — a state stolen from one session
    cannot be replayed by a different, currently-logged-in session."""
    fake = FakeOAuthProvider("google")
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="bob", password="correct-secret", roles=["member"]
    )
    alice = SecurityPrincipal(principal_id="alice", principal_type="USER", tenant_id=tenant, roles=["member"])
    bob = SecurityPrincipal(principal_id="bob", principal_type="USER", tenant_id=tenant, roles=["member"])

    alice_ctx = _execution_context_for(alice, "kortex.security.oauth.link_begin")
    link_begin = await security_engine.oauth_link_begin_capability(provider="google", execution_context=alice_ctx)

    bob_ctx = _execution_context_for(bob, "kortex.security.oauth.link_complete")
    with pytest.raises(OAuthStateError):
        await security_engine.oauth_link_complete_capability(
            provider="google", code="fake-code", state=link_begin["state"], execution_context=bob_ctx
        )


@pytest.mark.asyncio
async def test_link_rejects_identity_already_linked_to_a_different_principal(tmp_path: Path) -> None:
    fake = FakeOAuthProvider("google", subject=_subject(tmp_path))
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="bob", password="correct-secret", roles=["member"]
    )
    alice = SecurityPrincipal(principal_id="alice", principal_type="USER", tenant_id=tenant, roles=["member"])
    bob = SecurityPrincipal(principal_id="bob", principal_type="USER", tenant_id=tenant, roles=["member"])

    alice_ctx = _execution_context_for(alice, "kortex.security.oauth.link_begin")
    alice_begin = await security_engine.oauth_link_begin_capability(provider="google", execution_context=alice_ctx)
    await security_engine.oauth_link_complete_capability(
        provider="google", code="code-1", state=alice_begin["state"], execution_context=alice_ctx
    )

    bob_ctx = _execution_context_for(bob, "kortex.security.oauth.link_begin")
    bob_begin = await security_engine.oauth_link_begin_capability(provider="google", execution_context=bob_ctx)
    with pytest.raises(OAuthLinkConflictError):
        await security_engine.oauth_link_complete_capability(
            provider="google", code="code-2", state=bob_begin["state"], execution_context=bob_ctx
        )


@pytest.mark.asyncio
async def test_unlink_and_list_links(tmp_path: Path) -> None:
    fake = FakeOAuthProvider("google", subject=_subject(tmp_path))
    _kernel, _storage, security_engine = await _boot(tmp_path, {"google": fake})
    tenant = _tenant(tmp_path)

    await security_engine.authentication_manager.register_principal(
        tenant_id=tenant, principal_id="alice", password="correct-secret", roles=["member"]
    )
    principal = SecurityPrincipal(principal_id="alice", principal_type="USER", tenant_id=tenant, roles=["member"])
    ctx = _execution_context_for(principal, "kortex.security.oauth.link_begin")

    link_begin = await security_engine.oauth_link_begin_capability(provider="google", execution_context=ctx)
    await security_engine.oauth_link_complete_capability(
        provider="google", code="code-1", state=link_begin["state"], execution_context=ctx
    )

    listed = await security_engine.oauth_list_links_capability(execution_context=ctx)
    assert listed == {"providers": ["google"]}

    unlinked = await security_engine.oauth_unlink_capability(provider="google", execution_context=ctx)
    assert unlinked == {"unlinked": True}

    listed_after = await security_engine.oauth_list_links_capability(execution_context=ctx)
    assert listed_after == {"providers": []}
