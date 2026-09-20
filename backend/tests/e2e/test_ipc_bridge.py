"""End-to-end tests for the M3 IPC Bridge (`kortex.api.main:app`).

Establishes the first real content in `backend/tests/e2e/` (previously
empty, per `phase3_desktop_architecture.md` §14). Exercises the real HTTP
surface with `TestClient` — no mocked dispatcher, no mocked Security
Engine — proving the acceptance criteria in
`phase3_desktop_architecture.md` §17 M3 (a real round trip, a real
event-stream scenario, a real permission-denial) against the actual FastAPI
app, not a unit-level approximation.

Seeding follows `test_capability_dispatch.py`'s established convention
exactly (`PrincipalRecord` via `IDataStore`, argon2-hashed
"dispatch-test-credential") — reached through `app.state.kernel` after the
`TestClient` context has driven the app's own `lifespan` (and therefore
`kernel_bootstrap.build_and_boot_kernel()`) to completion, rather than
constructing a second, parallel Kernel.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.main import app
from kortex.api.token_codec import decode_token
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord

pytestmark = pytest.mark.e2e


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Fresh app instance per test: isolated storage dir + deterministic
    keys, so tests never share state or depend on run order."""
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("KORTEX_MASTER_KEY", "0x" + ("11" * 32))
    monkeypatch.setenv("KORTEX_AUTH_SIGNING_PRIVATE_KEY", "0x" + ("22" * 32))
    with TestClient(app) as c:
        yield c


async def _seed_principal(data_store: Any, tenant_id: str, principal_id: str, roles: list[str]) -> None:
    credential_hash = PasswordHasher().hash("dispatch-test-credential")

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


async def _grant_role_permission(data_store: Any, role: str, permission: str) -> None:
    from sqlalchemy import select

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


def _login(client: Any, tenant_id: str, principal_id: str) -> str:
    """Real round trip #1: dispatch `kortex.security.auth.authenticate`
    through the actual HTTP endpoint and extract the minted session token —
    exactly what Rust's `invoke_capability` command will do."""
    response = client.post(
        "/capabilities/invoke",
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": "kortex.security.auth.authenticate",
            "parameters": {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                    "password": "dispatch-test-credential",
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert "sessionToken" in body, "login must mint a session token"
    return body["sessionToken"]


def _login_with_refresh(client: Any, tenant_id: str, principal_id: str) -> tuple[str, str]:
    """Same real login round trip as `_login`, but also captures the
    refresh token minted alongside the access token (Phase F security
    correction) -- used only by tests that exercise the refresh flow
    itself; every pre-existing caller of `_login` needs just the access
    token and is left untouched."""
    response = client.post(
        "/capabilities/invoke",
        json={
            "requestId": str(uuid.uuid4()),
            "capabilityName": "kortex.security.auth.authenticate",
            "parameters": {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                    "password": "dispatch-test-credential",
                }
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert "sessionToken" in body, "login must mint an access token"
    assert "refreshToken" in body, "login must also mint a refresh token"
    return body["sessionToken"], body["refreshToken"]


class TestHealth:
    def test_health_reports_running_kernel(self, client: Any) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["kernel_state"] == "RUNNING"
        assert body["system_health"]["status"] in ("healthy", "degraded")


class TestCapabilityInvocation:
    def test_unknown_capability_returns_capability_not_found(self, client: Any) -> None:
        response = client.post(
            "/capabilities/invoke",
            json={"requestId": str(uuid.uuid4()), "capabilityName": "kortex.nonexistent.thing.do", "parameters": {}},
        )
        assert response.status_code == 404
        body = response.json()
        assert body["status"] == "FAILURE"
        assert body["errors"][0]["category"] == "CAPABILITY_NOT_FOUND"

    def test_authenticate_with_wrong_password_is_permission_denied(self, client: Any) -> None:
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "alice", [])

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.auth.authenticate",
                "parameters": {
                    "credentials": {
                        "principal_type": "USER",
                        "tenant_id": tenant_id,
                        "principal_id": "alice",
                        "password": "wrong-password",
                    }
                },
            },
        )
        assert response.status_code in (401, 403)
        body = response.json()
        assert body["status"] == "FAILURE"
        assert body["errors"][0]["category"] == "PERMISSION_DENIED"
        assert "sessionToken" not in body

    def test_real_capability_round_trip_and_permission_denial(self, client: Any) -> None:
        """Acceptance #1 (real round trip) and #4 (permission denial) in one
        flow: login -> real Security Engine round trip; then the SAME
        token is denied `kortex.security.secret.get` (real,
        `security:read`-gated capability) because Bob was never granted
        that permission -> denied by the Kernel's own automatic
        authorization check inside `dispatch()` *before* the handler is
        ever reached (`SecretStore.get_secret` is never called) -> the
        documented `IpcError` shape."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "bob", [])

        token = _login(client, tenant_id, "bob")

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "does-not-matter", "tenant_id": tenant_id},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["status"] == "FAILURE"
        assert body["errors"][0]["category"] == "PERMISSION_DENIED"

    def test_authorized_capability_succeeds_with_minted_token(self, client: Any) -> None:
        """A real success round trip through a *different* real capability
        than login: Carol is granted `security:read`, a real secret is
        seeded via `SecretStore.put_secret` (the same engine method the
        capability handler itself calls), and `kortex.security.secret.get`
        returns the real plaintext end-to-end."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        security_engine = kernel.get_engine("security")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "carol", ["reader"])
        client.portal.call(_grant_role_permission, storage.data, "reader", "security:read")
        client.portal.call(security_engine.secret_store.put_secret, "demo-secret", tenant_id, "demo-plaintext")

        token = _login(client, tenant_id, "carol")
        decoded = decode_token(token)
        assert decoded.tenant_id == tenant_id
        assert decoded.principal_id == "carol"

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "demo-secret", "tenant_id": tenant_id},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "SUCCESS"
        assert body["payload"]["result"] == "demo-plaintext"

    def test_missing_token_on_authenticated_capability_is_permission_denied(self, client: Any) -> None:
        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "x", "tenant_id": "any"},
            },
        )
        assert response.status_code == 401, response.text
        assert response.json()["errors"][0]["category"] == "PERMISSION_DENIED"

    def test_session_token_never_appears_in_response_payload(self, client: Any) -> None:
        """Acceptance #3's backend half: the minted token is a sibling field
        on the raw HTTP body, never nested inside `payload` (the field the
        frontend's rendering/business logic actually reads)."""
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "dana", [])

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.auth.authenticate",
                "parameters": {
                    "credentials": {
                        "principal_type": "USER",
                        "tenant_id": tenant_id,
                        "principal_id": "dana",
                        "password": "dispatch-test-credential",
                    }
                },
            },
        )
        body = response.json()
        assert "sessionToken" not in (body.get("payload") or {})
        assert base64.urlsafe_b64decode(body["sessionToken"].encode("ascii"))  # decodes without error


# ---------------------------------------------------------------------------
# Phase F (AI Studio functional stabilization) — session-refresh token
# lifecycle, POST security correction.
#
# The approved (corrected) architecture: the access token stays short-lived
# (`_TOKEN_TTL`, unchanged at 15 minutes — see `test_authentication_manager.
# py`'s own Phase F assertions for that). Ordinary authenticated capability
# dispatch never mints or reissues anything, for any capability -- an
# earlier version of this suite (`TestPhaseFSlidingTokenReissue`, replaced
# below) covered a design where any successful authenticated call reissued
# a fresh token, which the Phase F security review found made a stolen
# access token effectively renewable indefinitely. Renewal is now possible
# only through the narrow `kortex.security.auth.refresh` capability, which
# requires the separate refresh token minted only at login and bounded by
# its own absolute `_REFRESH_TOKEN_TTL` ceiling (24 hours), independent of
# activity. These tests exercise that corrected lifecycle end-to-end
# through the real HTTP surface -- no mocked dispatcher, no mocked Security
# Engine, matching this file's own established convention.
#
# "An expired access token is rejected and never reissued" is deliberately
# NOT re-tested here: `test_authentication_manager.py::test_verify_token_
# expired_denied` already proves `verify_token` rejects an expired-but-
# validly-signed token at the unit level, and ordinary dispatch never
# reaches any reissue step at all post-correction, so there is no distinct
# code path here left to prove for that case.
# ---------------------------------------------------------------------------


class TestPhaseFTokenLifecycle:
    """AI Studio Functional Stabilization, Phase F security correction.

    Replaces the earlier `TestPhaseFSlidingTokenReissue` suite, whose first
    two tests asserted the vulnerable behavior this correction removes: an
    ordinary, successful authenticated capability call (`kortex.security.
    secret.get`) used to mint and return a fresh access token. That made a
    stolen access token effectively renewable indefinitely -- anyone
    holding a valid bearer token could keep it alive forever just by
    replaying ordinary calls, with nothing to distinguish that from
    genuine, legitimate use. This suite proves the corrected properties
    instead: ordinary capability dispatch never mints or renews any token,
    and renewal is possible only through the narrow `kortex.security.
    auth.refresh` capability, which requires the separate refresh token
    minted only at login -- never the access token used for every ordinary
    call.
    """

    def test_login_mints_both_an_access_token_and_a_refresh_token(self, client: Any) -> None:
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "erin", ["reader"])

        access_token, refresh_token = _login_with_refresh(client, tenant_id, "erin")
        assert access_token != refresh_token, "the two tokens must be distinct credentials"

    def test_ordinary_authenticated_capability_calls_never_mint_or_reissue_any_token(self, client: Any) -> None:
        """The core Phase F regression this correction closes: a successful
        `kortex.security.secret.get` call (or any other ordinary
        authenticated capability) must never carry a `sessionToken` or
        `refreshToken` in its response -- not once, and not on a tenth
        repeat. A stolen access token replaying ordinary calls gains
        nothing."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "erin", ["reader"])
        client.portal.call(_grant_role_permission, storage.data, "reader", "security:read")
        security_engine = kernel.get_engine("security")
        client.portal.call(security_engine.secret_store.put_secret, "demo-secret", tenant_id, "demo-plaintext")

        original_token = _login(client, tenant_id, "erin")

        for _ in range(10):
            response = client.post(
                "/capabilities/invoke",
                json={
                    "requestId": str(uuid.uuid4()),
                    "capabilityName": "kortex.security.secret.get",
                    "parameters": {"secret_handle": "demo-secret", "tenant_id": tenant_id},
                },
                headers={"Authorization": f"Bearer {original_token}"},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "SUCCESS"
            assert "sessionToken" not in body, "ordinary capability dispatch must never mint an access token"
            assert "refreshToken" not in body, "ordinary capability dispatch must never mint a refresh token"

    def test_the_original_access_token_keeps_working_across_many_ordinary_calls_with_no_new_token_ever_appearing(
        self, client: Any
    ) -> None:
        """Mandatory Phase F property: a valid access token cannot be
        indefinitely rolled forward merely by repeatedly invoking ordinary
        capabilities. There is no rolling to observe at all -- the SAME
        original token keeps authenticating every one of many repeated
        calls, because nothing ever replaces it."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "frank", ["reader"])
        client.portal.call(_grant_role_permission, storage.data, "reader", "security:read")
        security_engine = kernel.get_engine("security")
        client.portal.call(security_engine.secret_store.put_secret, "demo-secret-2", tenant_id, "second-plaintext")

        original_token = _login(client, tenant_id, "frank")

        for _ in range(5):
            response = client.post(
                "/capabilities/invoke",
                json={
                    "requestId": str(uuid.uuid4()),
                    "capabilityName": "kortex.security.secret.get",
                    "parameters": {"secret_handle": "demo-secret-2", "tenant_id": tenant_id},
                },
                headers={"Authorization": f"Bearer {original_token}"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["payload"]["result"] == "second-plaintext"

    def test_refresh_capability_exchanges_a_valid_refresh_token_for_a_fresh_access_token(self, client: Any) -> None:
        """Mandatory Phase F property: legitimate session renewal works."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "grace", ["reader"])
        client.portal.call(_grant_role_permission, storage.data, "reader", "security:read")
        security_engine = kernel.get_engine("security")
        client.portal.call(security_engine.secret_store.put_secret, "demo-secret-3", tenant_id, "third-plaintext")

        access_token, refresh_token = _login_with_refresh(client, tenant_id, "grace")

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.auth.refresh",
                "parameters": {"refresh_token": refresh_token},
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "SUCCESS"
        assert "sessionToken" in body, "a valid refresh must mint a fresh access token"
        new_access_token = body["sessionToken"]
        assert new_access_token != access_token, "the refreshed access token must be genuinely new"
        # Refreshing must never mint a NEW refresh token -- the original
        # refresh token's own absolute ceiling is never itself extended.
        assert "refreshToken" not in body, "kortex.security.auth.refresh must never mint a new refresh token"

        decoded_original = decode_token(access_token)
        decoded_new = decode_token(new_access_token)
        assert decoded_new.principal_id == decoded_original.principal_id
        assert decoded_new.tenant_id == decoded_original.tenant_id
        assert decoded_new.issued_at_utc > decoded_original.issued_at_utc

        # The refreshed access token is itself genuinely usable, not merely
        # well-formed.
        follow_up = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "demo-secret-3", "tenant_id": tenant_id},
            },
            headers={"Authorization": f"Bearer {new_access_token}"},
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["payload"]["result"] == "third-plaintext"

    def test_refresh_capability_rejects_an_access_token_presented_as_a_refresh_token(self, client: Any) -> None:
        """Mandatory Phase F property: an access token cannot be used to
        obtain a fresh token. Domain separation means a real, currently
        valid access token -- exactly the artifact exposed on every
        ordinary capability call, and therefore the one most likely to be
        stolen -- is worthless at the refresh endpoint, even though it is a
        genuine, correctly-signed KORTEX token for the same principal. The
        failure surfaces as 403 (not 401): the payload is well-formed and
        does decode, but its signature was never computed over the
        refresh-token domain prefix, so it fails the same
        `InvalidSignatureError` path -- and the same 403 mapping
        (`errors.py`) -- as any other cryptographic signature mismatch.
        Either way, no token of any kind is minted."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "heidi", ["reader"])

        access_token = _login(client, tenant_id, "heidi")

        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.auth.refresh",
                "parameters": {"refresh_token": access_token},
            },
        )
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["status"] == "FAILURE"
        assert "sessionToken" not in body
        assert "refreshToken" not in body

    def test_refresh_capability_rejects_a_malformed_refresh_token(self, client: Any) -> None:
        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.auth.refresh",
                "parameters": {"refresh_token": "not-a-real-token"},
            },
        )
        assert response.status_code == 401, response.text
        assert response.json()["status"] == "FAILURE"

    def test_an_unauthenticated_dispatch_failure_never_reissues_a_token(self, client: Any) -> None:
        """A call with no bearer token at all fails before `verify_token`
        ever runs -- there is no principal to reissue for, and the response
        must carry no `sessionToken` field whatsoever."""
        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "x", "tenant_id": "any"},
            },
        )
        assert response.status_code == 401, response.text
        assert "sessionToken" not in response.json()

    def test_an_authorization_denial_never_reissues_a_token(self, client: Any) -> None:
        """A genuinely authenticated caller, denied by RBAC for THIS
        specific capability, must not have any token minted on the way to
        the 403 -- token minting only ever happens inside the SUCCESS path
        of `kortex.security.auth.authenticate`/`oauth.login_complete`/
        `auth.refresh`; an `AuthorizationDeniedError` on an ordinary
        capability never reaches that path at all (and, post-Phase-F,
        ordinary capabilities never mint anything regardless of outcome)."""
        kernel = client.app.state.kernel
        storage = kernel.get_engine("storage")
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, storage.data, tenant_id, "grace", [])  # no roles/permissions

        token = _login(client, tenant_id, "grace")
        response = client.post(
            "/capabilities/invoke",
            json={
                "requestId": str(uuid.uuid4()),
                "capabilityName": "kortex.security.secret.get",
                "parameters": {"secret_handle": "does-not-matter", "tenant_id": tenant_id},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403, response.text
        assert "sessionToken" not in response.json()


class TestEventStream:
    def test_unauthenticated_connection_is_rejected(self, client: Any) -> None:
        with pytest.raises(Exception), client.websocket_connect("/events/stream") as ws:  # noqa: B017 - starlette raises WebSocketDisconnect on close(1008)
            ws.receive_json()

    def test_authenticated_subscriber_receives_published_event(self, client: Any) -> None:
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "erin", [])
        token = _login(client, tenant_id, "erin")

        with client.websocket_connect(
            "/events/stream?topic=kortex.event.test.thing.created",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "kortex.event.test.thing.created",
                {"tenant_id": tenant_id, "thing_id": "abc"},
            )
            received = ws.receive_json()
            assert received["topic"] == "kortex.event.test.thing.created"
            assert received["payload"]["thing_id"] == "abc"

    def test_cross_tenant_event_is_not_delivered(self, client: Any) -> None:
        kernel = client.app.state.kernel
        tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
        tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_a, "frank", [])
        token = _login(client, tenant_a, "frank")

        with client.websocket_connect(
            "/events/stream?topic=kortex.event.test.thing.created",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "kortex.event.test.thing.created",
                {"tenant_id": tenant_b, "thing_id": "should-not-arrive"},
            )
            client.portal.call(
                kernel.publish_event,
                "kortex.event.test.thing.created",
                {"tenant_id": tenant_a, "thing_id": "should-arrive"},
            )
            received = ws.receive_json()
            assert received["payload"]["thing_id"] == "should-arrive"


class TestApprovalDecisionEventRedaction:
    """M6.4-0: `WorkflowEngine.decide_approval_request` mints a live,
    fully-usable session token for the deciding principal and embeds it in
    the `workflow.approval.decided` event payload (`decider_session_token`)
    so the internal resume subscribers (`AIOrchestrationEngine`,
    `ExternalExecutionManager`) can dispatch with real authenticated
    identity. That same event was being relayed VERBATIM by `/events/stream`
    to every authenticated same-tenant WebSocket client -- not just the
    approver -- a live session-token leak. These tests prove the fix at the
    real HTTP/WS boundary: the token never reaches the wire, for any
    same-tenant subscriber, while the rest of the payload (needed by any
    legitimate UI observer) is unaffected.
    """

    def test_decider_session_token_is_redacted_from_relayed_event(self, client: Any) -> None:
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "grace", [])
        token = _login(client, tenant_id, "grace")

        with client.websocket_connect(
            "/events/stream?topic=workflow.approval.decided",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "workflow.approval.decided",
                {
                    "request_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "decision": "APPROVED",
                    "correlation_id": "corr-123",
                    "action_fingerprint": "deadbeef",
                    "context_snapshot": {"action": "external_execution", "execution_id": "exec-1"},
                    "decider_session_token": {
                        "principal_id": "approver_bob",
                        "signature": "should-never-be-visible-on-the-wire",
                    },
                },
            )
            received = ws.receive_json()
            assert received["topic"] == "workflow.approval.decided"
            # The token is gone -- redacted to None, not merely renamed or hidden.
            assert received["payload"]["decider_session_token"] is None
            # Everything a legitimate UI observer needs is still present.
            assert received["payload"]["decision"] == "APPROVED"
            assert received["payload"]["correlation_id"] == "corr-123"
            assert received["payload"]["action_fingerprint"] == "deadbeef"
            assert received["payload"]["context_snapshot"]["execution_id"] == "exec-1"

    def test_another_same_tenant_user_cannot_obtain_decider_session_token(self, client: Any) -> None:
        """Not just the approver -- ANY authenticated same-tenant user
        connected to the stream must never see the token, since the relay
        has no per-subscriber scoping beyond tenant match."""
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "heidi", [])
        # A low-privilege bystander in the same tenant, unrelated to the ticket.
        bystander_token = _login(client, tenant_id, "heidi")

        with client.websocket_connect(
            "/events/stream?topic=workflow.approval.decided",
            headers={"Authorization": f"Bearer {bystander_token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "workflow.approval.decided",
                {
                    "request_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "decision": "APPROVED",
                    "decider_session_token": {"principal_id": "approver_someone_else", "secret": "leak-me-not"},
                },
            )
            received = ws.receive_json()
            assert received["payload"]["decider_session_token"] is None

    def test_cross_tenant_user_does_not_receive_approval_decided_event_at_all(self, client: Any) -> None:
        kernel = client.app.state.kernel
        tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
        tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_a, "ivan", [])
        token = _login(client, tenant_a, "ivan")

        with client.websocket_connect(
            "/events/stream?topic=workflow.approval.decided",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "workflow.approval.decided",
                {
                    "request_id": str(uuid.uuid4()),
                    "tenant_id": tenant_b,
                    "decision": "APPROVED",
                    "decider_session_token": {"principal_id": "approver", "secret": "tenant-b-secret"},
                },
            )
            client.portal.call(
                kernel.publish_event,
                "workflow.approval.decided",
                {"request_id": str(uuid.uuid4()), "tenant_id": tenant_a, "decision": "REJECTED"},
            )
            # Only the tenant-A event arrives; the tenant-B event (which
            # would have carried a real token) never reaches this socket.
            received = ws.receive_json()
            assert received["payload"]["tenant_id"] == tenant_a
            assert received["payload"]["decision"] == "REJECTED"

    def test_other_event_topics_are_unaffected_by_redaction(self, client: Any) -> None:
        """The sanitizer only nulls known-sensitive key names -- an
        unrelated event's ordinary fields must pass through unchanged."""
        kernel = client.app.state.kernel
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
        client.portal.call(_seed_principal, kernel.get_engine("storage").data, tenant_id, "judy", [])
        token = _login(client, tenant_id, "judy")

        with client.websocket_connect(
            "/events/stream?topic=kortex.event.test.thing.created",
            headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            client.portal.call(
                kernel.publish_event,
                "kortex.event.test.thing.created",
                {"tenant_id": tenant_id, "thing_id": "abc", "note": "ordinary field"},
            )
            received = ws.receive_json()
            assert received["payload"]["thing_id"] == "abc"
            assert received["payload"]["note"] == "ordinary field"
