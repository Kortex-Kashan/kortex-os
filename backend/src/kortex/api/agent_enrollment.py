"""
Agent enrollment HTTP boundary (Phase 5).

Exposes `POST /api/v1/agent/enroll`, the one endpoint a not-yet-enrolled
Desktop Agent may call. It is deliberately unauthenticated in the session
sense — the agent has no identity yet — and is authenticated instead by
possession of a single-use enrollment token, which the service layer verifies
against a stored SHA-256 digest.

Why this route parses its own body
----------------------------------
The application's global `RequestValidationError` handler renders
`str(exc)` into the response envelope, and a Pydantic validation error
stringifies the offending *input* along with the failure. For every other
route that is a helpful diagnostic; for this one it would echo a raw
enrollment token back to whoever sent a slightly malformed request, and into
anything that captures that response. So this route reads the raw body and
validates it by hand, guaranteeing that no code path can serialize the token
into an error message.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from kortex.engines.security.agent_enrollment import (
    AgentEnrollmentService,
    EnrollmentConflictError,
    EnrollmentInvariantError,
    EnrollmentRejectedError,
)
from kortex.engines.security.exceptions import CsrValidationError
from kortex.engines.security.pki import KortexPki

logger = logging.getLogger("kortex.api.agent_enrollment")

router = APIRouter(prefix="/api/v1/agent", tags=["agent-enrollment"])

# A canonical UUIDv4 is 36 characters; the column is 64. Anything longer is
# rejected outright rather than silently truncated by the database.
_MAX_MACHINE_ID_LENGTH = 64
_MAX_CSR_LENGTH = 16_384


def _error(status_code: int, message: str) -> JSONResponse:
    """Return an error body that never contains request material."""
    return JSONResponse(status_code=status_code, content={"error": message})


def _build_service(request: Request) -> AgentEnrollmentService:
    kernel = request.app.state.kernel
    security_engine = kernel.get_engine("security")
    storage_engine = kernel.get_engine("storage")
    return AgentEnrollmentService(data_store=storage_engine.data, pki=KortexPki(security_engine.secret_store))


@router.post("/enroll")
async def enroll_agent(request: Request) -> Response:
    """Enroll a Desktop Agent and return its signed client certificate.

    Responses:
        200: PEM client certificate.
        400: Malformed request (never echoes the submitted values).
        403: Token unknown, expired, or replayed with different material.
        409: This MachineInstallationId is already enrolled.
        500: Fail-closed error (inconsistent enrollment state, PKI failure).
    """
    try:
        body: Any = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return _error(400, "Request body is not valid JSON.")

    if not isinstance(body, dict):
        return _error(400, "Request body must be a JSON object.")

    raw_token = body.get("token")
    csr = body.get("csr")
    machine_installation_id = body.get("machine_installation_id")

    if not isinstance(raw_token, str) or not raw_token:
        return _error(400, "Field 'token' is required and must be a string.")
    if not isinstance(csr, str) or not csr:
        return _error(400, "Field 'csr' is required and must be a string.")
    if not isinstance(machine_installation_id, str) or not machine_installation_id:
        return _error(400, "Field 'machine_installation_id' is required and must be a string.")
    if len(machine_installation_id) > _MAX_MACHINE_ID_LENGTH:
        return _error(400, "Field 'machine_installation_id' is too long.")
    if len(csr) > _MAX_CSR_LENGTH:
        return _error(400, "Field 'csr' is too large.")

    service = _build_service(request)

    try:
        certificate_pem = await service.enroll(
            raw_token=raw_token,
            csr_bytes=csr.encode("ascii", errors="strict"),
            machine_installation_id=machine_installation_id,
        )
    except UnicodeEncodeError:
        return _error(400, "Field 'csr' must be ASCII-encoded PEM.")
    except CsrValidationError:
        # The caller sent something that is not a usable CSR. That is a bad
        # request, not a server fault and not an authorization decision, so it
        # is neither a 500 nor a 403 — reporting it as 403 would also add noise
        # to the signal operators watch for rejected credentials.
        logger.warning(
            "Agent enrollment rejected a malformed CSR.",
            extra={"machine_installation_id": machine_installation_id},
        )
        return _error(400, "Field 'csr' is not a valid certificate signing request.")
    except EnrollmentRejectedError:
        # Logged without the token, the CSR, or which specific check failed.
        logger.warning("Agent enrollment refused.", extra={"machine_installation_id": machine_installation_id})
        return _error(403, "Enrollment was refused.")
    except EnrollmentConflictError:
        logger.warning(
            "Agent enrollment conflict: machine already enrolled.",
            extra={"machine_installation_id": machine_installation_id},
        )
        return _error(409, "This machine installation is already enrolled.")
    except EnrollmentInvariantError:
        logger.error(
            "Agent enrollment failed closed on an inconsistent enrollment record.",
            extra={"machine_installation_id": machine_installation_id},
        )
        return _error(500, "Enrollment cannot be completed.")
    except Exception:
        # Fail closed. The exception is logged with a stack trace but never
        # rendered into the response, because a PKI or storage error message
        # could otherwise disclose handles or internal state.
        logger.exception("Agent enrollment failed with an unexpected error.")
        return _error(500, "Enrollment cannot be completed.")

    return Response(content=certificate_pem, media_type="application/x-pem-file", status_code=200)
