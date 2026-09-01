"""M7.1 — the required cold-start acceptance test.

Exercises the real application path end-to-end, exactly as the M7.1 master
prompt's acceptance test demands:

    FRESH INSTALLATION
          |
    EMPTY DATABASE
          |
    BACKEND READY            (GET /health)
          |
    FIRST-RUN DETECTED       (GET /health -> bootstrap_required: true)
          |
    CREATE ADMIN + TENANT    (POST /capabilities/invoke ->
          |                   kortex.security.bootstrap.create_admin,
          |                   no Authorization header, no pre-seeded principal)
    AUTHENTICATE             (POST /capabilities/invoke ->
          |                   kortex.security.auth.authenticate, same credentials)
    ENTER KORTEX SHELL       (the minted session token authorizes a real,
          |                   RBAC-gated capability call — proving the new
          |                   administrator isn't just authenticated but
          |                   actually usable)
    RESTART APPLICATION      (the FastAPI `app.state.kernel` lifespan is
          |                   torn down and rebuilt from scratch, exactly as
          |                   a real process restart would — see
          |                   `_restarted_client` below)
    PERSISTED KEY/SECURITY STATE REMAINS VALID
                              (the session token minted before "restart"
                               still verifies after it, and the same
                               administrator can still authenticate)

Does not manually start uvicorn (`TestClient` drives the real FastAPI
`app`, including its real `lifespan` -> `build_and_boot_kernel()` -> real
engine boot sequence). Does not pre-create the administrator (no
`PrincipalRecord` is seeded anywhere in this file). Does not bypass the
real bootstrap capability (every step below goes through
`POST /capabilities/invoke`, the same transport the desktop app uses).

Isolates both `KORTEX_STORAGE_DIR` and `KORTEX_DATABASE_URL` to a
`tmp_path`-scoped SQLite file — see `test_security_bootstrap.py`'s module
docstring for why this is necessary, not optional, for any test that
depends on the system starting genuinely empty. `KORTEX_MASTER_KEY`/
`KORTEX_AUTH_SIGNING_PRIVATE_KEY` are pinned to fixed, deterministic
values and kept identical across the "restart" — this is the test-level
stand-in for what `apps/desktop/src-tauri/src/secure_keys.rs` guarantees
in the real app (the same persisted key is supplied to the backend on
every launch); this test's whole point is to prove that when that
guarantee holds, sessions and the administrator account both survive a
restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kortex.api.main import app

pytestmark = pytest.mark.e2e

_MASTER_KEY = "0x" + ("aa" * 32)
_SIGNING_KEY = "0x" + ("bb" * 32)


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KORTEX_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'cold_start.db').as_posix()}")
    monkeypatch.setenv("KORTEX_MASTER_KEY", _MASTER_KEY)
    monkeypatch.setenv("KORTEX_AUTH_SIGNING_PRIVATE_KEY", _SIGNING_KEY)


def _invoke(client: TestClient, capability_name: str, parameters: dict[str, Any], token: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(
        "/capabilities/invoke",
        json={"requestId": "req-1", "capabilityName": capability_name, "parameters": parameters},
        headers=headers,
    )
    return response


@pytest.mark.usefixtures("isolated_env")
def test_cold_start_bootstrap_authenticate_restart_and_persist() -> None:
    session_token_before_restart: str

    # --- Launch 1: fresh install --------------------------------------
    with TestClient(app) as client:
        # BACKEND READY + FIRST-RUN DETECTED
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["bootstrap_required"] is True

        # Bootstrap is not reachable via a bare read — proving this is a
        # real capability dispatch, not a stub: an unknown capability name
        # 404s, confirming the endpoint genuinely routes by name.
        unknown = _invoke(client, "kortex.does.not.exist", {})
        assert unknown.status_code == 404

        # CREATE ADMIN + TENANT — no Authorization header at all.
        created = _invoke(
            client,
            "kortex.security.bootstrap.create_admin",
            {"tenant_id": "acme", "principal_id": "owner", "password": "correct horse battery staple"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["payload"]["created"] is True

        # First-run detection flips immediately after bootstrap.
        health_after = client.get("/health")
        assert health_after.json()["bootstrap_required"] is False

        # Bootstrap is now permanently closed — a second attempt, even
        # with entirely different (and otherwise-valid) credentials, is
        # rejected.
        second_attempt = _invoke(
            client,
            "kortex.security.bootstrap.create_admin",
            {"tenant_id": "intruder-corp", "principal_id": "intruder", "password": "another-strong-password"},
        )
        assert second_attempt.status_code == 401
        assert second_attempt.json()["errors"][0]["category"] == "PERMISSION_DENIED"

        # AUTHENTICATE as the just-created administrator.
        login = _invoke(
            client,
            "kortex.security.auth.authenticate",
            {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": "acme",
                    "principal_id": "owner",
                    "password": "correct horse battery staple",
                }
            },
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert body["payload"]["result"]["principal_id"] == "owner"
        session_token = body["sessionToken"]
        assert session_token

        # ENTER KORTEX SHELL — the minted token must actually authorize a
        # real RBAC-gated capability, not merely authenticate. Uses the
        # exact inert probe the desktop app itself uses on startup
        # (`checkStoredSession` -> `kortex.security.signature.verify`).
        probe = _invoke(
            client,
            "kortex.security.signature.verify",
            {"data": "kortex-desktop-session-check", "signature": "00", "public_key": "00"},
            token=session_token,
        )
        assert probe.status_code == 200, probe.text

        session_token_before_restart = session_token

    # --- RESTART APPLICATION -------------------------------------------
    # Exiting the `with TestClient(app)` block above already ran the real
    # `_lifespan` shutdown path (`kernel.shutdown()`). Entering a fresh one
    # below re-runs `_lifespan` startup -> `build_and_boot_kernel()` from
    # scratch — a new `Kernel`, new engine instances, nothing carried over
    # in memory — while `KORTEX_STORAGE_DIR`/`KORTEX_DATABASE_URL`/
    # `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` remain identical,
    # exactly as a real process restart looks from the backend's own
    # perspective when Rust supplies the same persisted keys again.
    with TestClient(app) as client:
        # PERSISTED KEY/SECURITY STATE REMAINS VALID: the token minted
        # before "restart" is still valid after it — this is only true
        # because the signing key didn't change. If it had (the pre-M7.1
        # ephemeral-key behavior), this exact call would now return 401.
        probe_after_restart = _invoke(
            client,
            "kortex.security.signature.verify",
            {"data": "kortex-desktop-session-check", "signature": "00", "public_key": "00"},
            token=session_token_before_restart,
        )
        assert probe_after_restart.status_code == 200, probe_after_restart.text

        # The administrator account itself also survived the restart (real
        # DB persistence, not an in-memory-only store).
        login_after_restart = _invoke(
            client,
            "kortex.security.auth.authenticate",
            {
                "credentials": {
                    "principal_type": "USER",
                    "tenant_id": "acme",
                    "principal_id": "owner",
                    "password": "correct horse battery staple",
                }
            },
        )
        assert login_after_restart.status_code == 200, login_after_restart.text

        # Bootstrap remains closed after the restart too — persistence
        # cuts both ways, it doesn't quietly reopen first-run setup.
        health_after_restart = client.get("/health")
        assert health_after_restart.json()["bootstrap_required"] is False
