"""Unit tests for `AISystemIdentity` (M6.2-1).

Exercises the token holder in isolation via fake `provisioner`/`authenticator`
callables -- no real Security Engine, no database. The real, end-to-end trust
boundary (actual `PrincipalRecord` provisioning, actual Argon2id
authentication, actual RBAC-gated dispatch) is proven separately in
`tests/integration/test_ai_system_identity_dispatch.py`.
"""

from __future__ import annotations

import asyncio

import pytest

from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE, AISystemIdentity


class _FakeClock:
    """Deterministic stand-in for `time.monotonic()`."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _make_identity(monkeypatch: pytest.MonkeyPatch, clock: _FakeClock):  # noqa: ANN001
    monkeypatch.setattr("kortex.engines.ai.identity.time.monotonic", clock)

    provisioned: list[str] = []
    authenticated: list[str] = []

    async def _provisioner(tenant_id: str) -> None:
        provisioned.append(tenant_id)

    async def _authenticator(tenant_id: str):  # noqa: ANN202
        authenticated.append(tenant_id)
        return f"token-for-{tenant_id}-{len(authenticated)}"

    identity = AISystemIdentity(provisioner=_provisioner, authenticator=_authenticator)
    return identity, provisioned, authenticated


def test_constants_are_stable_well_known_values() -> None:
    """These are configuration surface -- operators grant RBAC permissions
    against `AI_SYSTEM_ROLE` and never grant it whatever `required_role` an
    AI-created approval ticket carries. Regressing either string silently
    breaks that operational contract."""
    assert AI_SYSTEM_PRINCIPAL_ID == "kortex-ai-system"
    assert AI_SYSTEM_ROLE == "AI_SYSTEM_ACTOR"


@pytest.mark.asyncio
async def test_first_call_provisions_then_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    identity, provisioned, authenticated = _make_identity(monkeypatch, clock)

    token = await identity.get_session_token("acme")

    assert token == "token-for-acme-1"
    assert provisioned == ["acme"]
    assert authenticated == ["acme"]


@pytest.mark.asyncio
async def test_second_call_same_tenant_does_not_reprovision_and_reuses_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provisioning is a one-time, idempotent setup step per tenant per process
    lifetime; a cached, still-valid token must not trigger re-authentication."""
    clock = _FakeClock()
    identity, provisioned, authenticated = _make_identity(monkeypatch, clock)

    first = await identity.get_session_token("acme")
    clock.now += 10.0  # well within the refresh margin
    second = await identity.get_session_token("acme")

    assert first == second
    assert provisioned == ["acme"]
    assert authenticated == ["acme"]


@pytest.mark.asyncio
async def test_token_refreshes_proactively_before_assumed_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    identity, provisioned, authenticated = _make_identity(monkeypatch, clock)

    first = await identity.get_session_token("acme")
    # 15 minutes - 120s margin = 780s. Advance past that.
    clock.now += 781.0
    second = await identity.get_session_token("acme")

    assert first != second
    assert authenticated == ["acme", "acme"]
    # Provisioning still happens only once -- it is not a per-refresh step.
    assert provisioned == ["acme"]


@pytest.mark.asyncio
async def test_tenants_are_provisioned_and_cached_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """One credential/PrincipalRecord per tenant -- a token for tenant A must
    never leak into or satisfy a request for tenant B."""
    clock = _FakeClock()
    identity, provisioned, authenticated = _make_identity(monkeypatch, clock)

    token_a = await identity.get_session_token("tenant_a")
    token_b = await identity.get_session_token("tenant_b")

    assert token_a != token_b
    assert set(provisioned) == {"tenant_a", "tenant_b"}
    assert set(authenticated) == {"tenant_a", "tenant_b"}


@pytest.mark.asyncio
async def test_concurrent_requests_for_same_tenant_do_not_double_provision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adversarial/race guard: two concurrent first-calls for the same tenant
    must not race into two separate provisioning attempts."""
    clock = _FakeClock()

    provision_calls = 0
    authenticate_calls = 0

    async def _provisioner(tenant_id: str) -> None:
        nonlocal provision_calls
        provision_calls += 1
        await asyncio.sleep(0)  # yield, to actually exercise interleaving

    async def _authenticator(tenant_id: str) -> str:
        nonlocal authenticate_calls
        authenticate_calls += 1
        return "token"

    monkeypatch.setattr("kortex.engines.ai.identity.time.monotonic", clock)
    identity = AISystemIdentity(provisioner=_provisioner, authenticator=_authenticator)

    results = await asyncio.gather(
        identity.get_session_token("acme"),
        identity.get_session_token("acme"),
    )

    assert results == ["token", "token"]
    assert provision_calls == 1
    assert authenticate_calls == 1
