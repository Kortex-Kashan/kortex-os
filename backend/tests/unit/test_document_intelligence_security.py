"""Tenant-authority adversarial tests for Document Intelligence.

KORTEX Platform Security — Capability Identity Propagation: proves the
corrected invariant. The authoritative identity is exclusively the Kernel's
dispatcher-authenticated principal, delivered to handlers via the trusted,
dispatcher-injected `CapabilityExecutionContext` — never a value the caller
supplies. `DocumentParseRequest` no longer carries any credential field at
all, so the original exploit vector (a nested `session_token` for a
different tenant than the top-level, dispatcher-authenticated one) is now
structurally impossible, not merely guarded against.

Reuses the repository's existing adversarial-test helpers
(`test_capability_dispatch_adversarial.py`) rather than duplicating kernel/
principal/token setup.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.exceptions import ReservedParameterError
from kortex.core.kernel import Kernel
from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine
from kortex.engines.document_intelligence.exceptions import StorageAccessError
from kortex.engines.document_intelligence.models import DocumentParseRequest
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.storage.engine import StorageEngine
from tests.unit.test_capability_dispatch_adversarial import (
    _TEST_MASTER_KEY,
    _TEST_SIGNING_KEY,
    _grant_role_permission,
    _issue_token,
    _seed_principal,
    _tenant,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "document_intelligence"


async def _build_and_boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine]:
    from kortex.core.db import DatabaseEngineManager
    from kortex.engines.security.engine import SecurityEngine

    kernel = Kernel()
    db_file = tmp_path / "docint_security.db"
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_file}")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(DocumentIntelligenceEngine())
    await kernel.boot()
    return kernel, storage_engine


async def _authorize_pdf_parse(storage_engine: StorageEngine, tenant_id: str) -> str:
    """Grant a principal in `tenant_id` the permission Document Intelligence
    requires, mirroring the platform's real RBAC seeding pattern."""
    role = f"role-docint-{tenant_id}"
    await _grant_role_permission(storage_engine.data, role, "document_intelligence:parse")
    return role


@pytest.mark.asyncio
async def test_tenant_a_reads_its_own_object(tmp_path: Path) -> None:
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-a")
    role = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    # Store tenant A's own object under the exact tenant-scoped key the
    # engine will construct: docint/{tenant_id}/{bucket_name}.
    await storage_engine.object.put_object(
        bucket_name=f"docint/{tenant_a}/documents", object_key="doc.pdf", data=b"%PDF-1.4 tenant-a-content"
    )

    request = DocumentParseRequest(bucket_name="documents", object_key="doc.pdf", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": request},
        context={"resource_tenant_id": tenant_a},
    )
    # Malformed PDF bytes are fine here — we only need to prove which
    # object was *fetched*, which happens before parsing is attempted.
    with pytest.raises(Exception) as exc_info:
        await kernel.invoke_capability(cap_request)
    # A CorruptedDocumentError (not StorageAccessError) proves tenant A's
    # own object was actually retrieved and handed to the parser.
    assert "CorruptedDocumentError" in type(exc_info.value).__name__ or "Corrupted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_forged_bucket_cannot_redirect_to_tenant_b_storage(tmp_path: Path) -> None:
    """Resource isolation: authenticated principal belongs to tenant A; the
    request's `bucket_name`/`object_key` are deliberately set to the exact
    same values as tenant B's real, existing object. Proves tenant A's
    request can never resolve to tenant B's bytes, because the engine always
    prefixes the *verified* principal's tenant onto the storage path —
    never the request's own claim."""
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-fa")
    tenant_b = _tenant(tmp_path, "-fb")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    # Tenant B has a real object at "documents"/"shared-name.pdf" containing
    # genuinely well-formed PDF bytes.
    real_pdf = (FIXTURES / "normal_text.pdf").read_bytes()
    await storage_engine.object.put_object(
        bucket_name=f"docint/{tenant_b}/documents", object_key="shared-name.pdf", data=real_pdf
    )
    # Tenant A has NOT stored anything at that same bucket/key.

    forged_request = DocumentParseRequest(
        bucket_name="documents",  # identical to tenant B's real bucket name
        object_key="shared-name.pdf",  # identical to tenant B's real object key
        mime_type="application/pdf",
    )
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": forged_request},
        context={"resource_tenant_id": tenant_a},
    )

    with pytest.raises(StorageAccessError):
        await kernel.invoke_capability(cap_request)
    # StorageAccessError (object not found under tenant A's own namespace)
    # — not tenant B's real PDF content — proves the forged bucket/key
    # never redirected the read into tenant B's storage.


@pytest.mark.asyncio
async def test_missing_session_token_is_rejected_before_handler_runs(tmp_path: Path) -> None:
    kernel, _storage_engine = await _build_and_boot_kernel(tmp_path)

    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=None,
        parameters={
            "request": {
                "content": b"%PDF-1.4",
                "mime_type": "application/pdf",
            }
        },
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(cap_request)


@pytest.mark.asyncio
async def test_abac_tenant_mismatch_rejected_before_handler_runs(tmp_path: Path) -> None:
    """`context["resource_tenant_id"]` not matching the authenticated
    principal's real tenant must deny at the ABAC layer — before the
    handler (and therefore before any Storage access) runs at all."""
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-mm")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    request = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": request},
        context={"resource_tenant_id": "some-other-tenant"},  # forged mismatch
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(cap_request)


@pytest.mark.asyncio
async def test_missing_abac_context_rejected_before_handler_runs(tmp_path: Path) -> None:
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-nc")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    request = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": request},
        # no context at all — ABAC must fail closed (ABAC_TENANT_MISSING).
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(cap_request)


@pytest.mark.asyncio
async def test_unauthorized_principal_without_permission_is_rejected(tmp_path: Path) -> None:
    """A genuinely authenticated principal, correctly declaring its own
    tenant, but never granted `document_intelligence:parse` — RBAC must
    deny."""
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-np")
    await _seed_principal(storage_engine.data, tenant_a, "principal-unauth", roles=[])
    token = await _issue_token(security_engine, tenant_a, "principal-unauth")

    request = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token,
        parameters={"request": request},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(cap_request)


# ============================================================================
# The flipped regression test — was xfail, now REQUIRED to pass.
# ============================================================================


@pytest.mark.asyncio
async def test_identity_confusion_attack_is_structurally_impossible(tmp_path: Path) -> None:
    """THE security regression this milestone exists to close.

    Original vulnerability: `CapabilityRequest.session_token` = a valid
    tenant-A token (dispatcher authenticates/RBAC/ABAC-authorizes as A);
    a *nested* nested `DocumentParseRequest.session_token` = a separately
    valid tenant-B token with zero granted permissions. The handler used to
    independently re-verify the nested token and execute AS tenant B,
    successfully returning tenant B's real stored content — an operation
    RBAC/ABAC never evaluated for tenant B at all.

    `DocumentParseRequest` no longer has a `session_token` field (or any
    credential field) at all — there is nothing left to nest a second
    identity inside. This test proves the attack fails at every layer that
    could conceivably still carry it:
      1. constructing the old exploit shape is now a no-op (Pydantic drops
         the unknown field silently — verified explicitly, not assumed);
      2. the closest surviving attack shape — smuggling identity through
         the dispatcher's own reserved kwargs — is rejected outright;
      3. end-to-end, tenant A's request can only ever touch tenant A's own
         storage, and tenant B's real content is never returned.
    """
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-mix-a")
    tenant_b = _tenant(tmp_path, "-mix-b")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    await _seed_principal(storage_engine.data, tenant_b, "principal-b", roles=[])  # zero permissions
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")
    token_b = await _issue_token(security_engine, tenant_b, "principal-b")

    real_pdf = (FIXTURES / "normal_text.pdf").read_bytes()
    await storage_engine.object.put_object(
        bucket_name=f"docint/{tenant_b}/documents", object_key="secret.pdf", data=real_pdf
    )

    # --- Step 1: the original exploit shape no longer exists at all. ---
    # Constructing DocumentParseRequest with a "session_token" kwarg (the
    # exact original vector) silently drops it — Pydantic's default
    # extra="ignore" behavior — leaving no credential on the object at all.
    attempted_request = DocumentParseRequest(
        bucket_name="documents",
        object_key="secret.pdf",
        mime_type="application/pdf",
        **{"session_token": token_b},  # type: ignore[arg-type]
    )
    assert not hasattr(attempted_request, "session_token")

    # --- Step 2: the closest surviving attack shape is rejected outright. ---
    for reserved_key, forged_value in (
        ("principal", {"principal_id": "principal-b", "tenant_id": tenant_b}),
        ("execution_context", {"tenant_id": tenant_b}),
    ):
        cap_request = CapabilityRequest(
            capability_name="kortex.document_intelligence.pdf.parse",
            session_token=token_a,
            parameters={"request": attempted_request, reserved_key: forged_value},
            context={"resource_tenant_id": tenant_a},
        )
        with pytest.raises(ReservedParameterError):
            await kernel.invoke_capability(cap_request)

    # --- Step 3: end-to-end, tenant A can only ever touch tenant A's own
    # storage; tenant B's real content is never returned. ---
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": attempted_request},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(StorageAccessError):
        await kernel.invoke_capability(cap_request)


@pytest.mark.asyncio
async def test_caller_supplied_principal_parameter_is_rejected(tmp_path: Path) -> None:
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-po")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    request = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": request, "principal": "attacker-controlled-value"},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(ReservedParameterError):
        await kernel.invoke_capability(cap_request)


@pytest.mark.asyncio
async def test_caller_supplied_execution_context_parameter_is_rejected(tmp_path: Path) -> None:
    kernel, storage_engine = await _build_and_boot_kernel(tmp_path)
    security_engine = cast(SecurityEngine, kernel.get_engine("security"))

    tenant_a = _tenant(tmp_path, "-eo")
    role_a = await _authorize_pdf_parse(storage_engine, tenant_a)
    await _seed_principal(storage_engine.data, tenant_a, "principal-a", roles=[role_a])
    token_a = await _issue_token(security_engine, tenant_a, "principal-a")

    forged_context = CapabilityExecutionContext(
        request_id="forged",
        correlation_id="forged",
        capability_name="kortex.document_intelligence.pdf.parse",
        principal=None,
        tenant_id="attacker-tenant",
    )
    request = DocumentParseRequest(content=b"%PDF-1.4", mime_type="application/pdf")
    cap_request = CapabilityRequest(
        capability_name="kortex.document_intelligence.pdf.parse",
        session_token=token_a,
        parameters={"request": request, "execution_context": forged_context},
        context={"resource_tenant_id": tenant_a},
    )
    with pytest.raises(ReservedParameterError):
        await kernel.invoke_capability(cap_request)
