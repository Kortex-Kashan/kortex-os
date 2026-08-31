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
