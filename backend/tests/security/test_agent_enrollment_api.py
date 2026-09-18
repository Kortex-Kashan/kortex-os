"""HTTP-boundary tests for `POST /api/v1/agent/enroll`.

The service-level tests in `test_agent_enrollment.py` prove the enrollment
semantics; these prove the HTTP contract those semantics are exposed through —
spec S8's status codes and acceptance criteria 6 and 7, which are stated in
terms of HTTP 403/409 rather than exception types.

They also cover the boundary's own responsibility: never rendering the raw
enrollment token into a response body, on any path including malformed input.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kortex.api.agent_enrollment import router as enrollment_router
from kortex.engines.security.agent_enrollment import AgentEnrollmentService
from tests.security.conftest import Phase5Stack, build_phase5_stack

_TENANT = "tenant-alpha"


class _FakeSecurityEngine:
    def __init__(self, secret_store: object) -> None:
        self.secret_store = secret_store


class _FakeKernel:
    """Presents the two engines the router resolves, over the real stack."""

    def __init__(self, stack: Phase5Stack) -> None:
        self._engines = {
            "security": _FakeSecurityEngine(stack.secret_store),
            "storage": stack.storage_engine,
        }

    def get_engine(self, name: str) -> object:
        return self._engines[name]


@pytest.fixture
async def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[TestClient, Phase5Stack]]:
    """A TestClient over the real router, real PKI and real database."""
    stack = await build_phase5_stack(tmp_path, monkeypatch, name="enroll_api")
    await stack.pki.initialize_ca(certificate_path=tmp_path / "certs" / "ca.crt")

    app = FastAPI()
    app.include_router(enrollment_router)
    app.state.kernel = _FakeKernel(stack)

    try:
        with TestClient(app) as client:
            yield client, stack
    finally:
        await stack.kernel.db.disconnect()


def _make_csr(common_name: str = "agent") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


async def _token(stack: Phase5Stack, tenant_id: str = _TENANT) -> str:
    service = AgentEnrollmentService(data_store=stack.storage_engine.data, pki=stack.pki)
    return await service.create_enrollment_token(tenant_id)


async def test_enroll_returns_certificate(api: tuple[TestClient, Phase5Stack]) -> None:
    """A valid enrollment returns 200 and a PEM certificate."""
    client, stack = api
    token = await _token(stack)

    response = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "csr": _make_csr(), "machine_installation_id": "11111111-2222-4333-8444-555555555555"},
    )

    assert response.status_code == 200
    assert b"BEGIN CERTIFICATE" in response.content


async def test_enroll_replay_with_different_csr_is_403(api: tuple[TestClient, Phase5Stack]) -> None:
    """Acceptance criterion 6: same token, different CSR -> HTTP 403."""
    client, stack = api
    token = await _token(stack)
    machine_id = "11111111-2222-4333-8444-555555555555"

    first = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "csr": _make_csr(), "machine_installation_id": machine_id},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "csr": _make_csr("other"), "machine_installation_id": machine_id},
    )
    assert second.status_code == 403


async def test_enroll_replay_with_different_machine_id_is_403(api: tuple[TestClient, Phase5Stack]) -> None:
    """Acceptance criterion 7: same token, different machine id -> HTTP 403."""
    client, stack = api
    token = await _token(stack)
    csr = _make_csr()

    first = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "csr": csr, "machine_installation_id": "11111111-2222-4333-8444-555555555555"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/agent/enroll",
        json={"token": token, "csr": csr, "machine_installation_id": "99999999-2222-4333-8444-555555555555"},
    )
    assert second.status_code == 403


async def test_enroll_idempotent_replay_is_200_with_same_certificate(
    api: tuple[TestClient, Phase5Stack],
) -> None:
    """An identical retry returns 200 and the byte-identical certificate."""
    client, stack = api
    token = await _token(stack)
    csr = _make_csr()
    machine_id = "11111111-2222-4333-8444-555555555555"
    body = {"token": token, "csr": csr, "machine_installation_id": machine_id}

    first = client.post("/api/v1/agent/enroll", json=body)
    second = client.post("/api/v1/agent/enroll", json=body)

    assert first.status_code == second.status_code == 200
    assert first.content == second.content


async def test_enroll_duplicate_machine_id_is_409(api: tuple[TestClient, Phase5Stack]) -> None:
    """A second token for an already-enrolled machine -> HTTP 409."""
    client, stack = api
    machine_id = "11111111-2222-4333-8444-555555555555"

    first = client.post(
        "/api/v1/agent/enroll",
        json={"token": await _token(stack), "csr": _make_csr(), "machine_installation_id": machine_id},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/v1/agent/enroll",
        json={"token": await _token(stack), "csr": _make_csr(), "machine_installation_id": machine_id},
    )
    assert second.status_code == 409


async def test_unknown_token_is_403(api: tuple[TestClient, Phase5Stack]) -> None:
    """An unissued token -> HTTP 403, indistinguishable from a used one."""
    client, _stack = api

    response = client.post(
        "/api/v1/agent/enroll",
        json={"token": "0" * 64, "csr": _make_csr(), "machine_installation_id": "11111111-2222-4333-8444-555555555555"},
    )
    assert response.status_code == 403


async def test_malformed_csr_is_400(api: tuple[TestClient, Phase5Stack]) -> None:
    """A CSR that does not parse is a client error, not a 500."""
    client, stack = api

    response = client.post(
        "/api/v1/agent/enroll",
        json={
            "token": await _token(stack),
            "csr": "-----BEGIN CERTIFICATE REQUEST-----\nnot-a-csr\n-----END CERTIFICATE REQUEST-----\n",
            "machine_installation_id": "11111111-2222-4333-8444-555555555555",
        },
    )
    assert response.status_code == 400


async def test_malformed_requests_never_echo_the_token(api: tuple[TestClient, Phase5Stack]) -> None:
    """No response body may contain the submitted token, on any path.

    This is the reason the route parses its own body: the application's shared
    validation handler renders the offending input into the error envelope,
    which for this route would mean handing the raw token back to the caller
    and into anything that records responses.
    """
    client, stack = api
    token = await _token(stack)

    bodies = [
        {"token": token},  # missing csr and machine id
        {"token": token, "csr": _make_csr()},  # missing machine id
        {"token": token, "csr": "", "machine_installation_id": "x"},  # empty csr
        {"token": token, "csr": _make_csr(), "machine_installation_id": "x" * 200},  # oversized id
        {"token": token, "csr": _make_csr(), "machine_installation_id": 12345},  # wrong type
    ]

    for body in bodies:
        response = client.post("/api/v1/agent/enroll", json=body)
        assert response.status_code in {400, 403, 409}, response.status_code
        assert token not in response.text, f"token echoed for body {list(body)}"

    # And a body that is not even JSON.
    response = client.post(
        "/api/v1/agent/enroll",
        content=f'{{"token": "{token}", broken'.encode(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert token not in response.text
