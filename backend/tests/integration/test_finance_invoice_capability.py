"""Finance-pilot planning pass: integration coverage for
`kortex.finance.invoice.create` through the real Kernel dispatch chain.

Mirrors the established M6.3-M7.6 harness pattern
(`_tenant`/`_build_kernel`/`_boot_kernel`/`_seed_principal`/
`_grant_role_permission`/`_issue_token`/`_authorized_token`, e.g.
`test_document_tenant_isolation_dispatch.py`, `test_knowledge_tenant_
isolation_dispatch.py`) -- real, unmodified Storage + Security Engines, real
`FinanceModule`, dispatched through the real, unmodified
`CapabilityDispatcher` (`kernel.invoke_capability`), never a direct Python
method call.

Per the implementation boundary: no `invoice.get`/`.list` capability is
built or needed here -- tenant ownership is verified by direct `IDataStore`
inspection of the persisted row after `create` dispatches, exactly as the
boundary pass specified.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.finance.module import FinanceModule
from kortex.modules.finance.persistence import FinanceInvoiceRow

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_TEST_ROLE = "finance-invoice-test-role"


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-finance-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


def _build_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, FinanceModule]:
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "finance_capability_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    finance_module = FinanceModule()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(finance_module)
    return kernel, storage_engine, security_engine, finance_module


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, FinanceModule]:
    kernel, storage_engine, security_engine, finance_module = _build_kernel(tmp_path)
    await kernel.boot()
    return kernel, storage_engine, security_engine, finance_module


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("finance-capability-test-credential")

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
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


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "finance-capability-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine, security_engine: SecurityEngine, tenant_id: str
) -> TokenPayload:
    await _seed_principal(storage_engine.data, tenant_id, "principal-1", roles=[_TEST_ROLE])
    await _grant_role_permission(storage_engine.data, _TEST_ROLE, "finance:invoice:write")
    return await _issue_token(security_engine, tenant_id, "principal-1")


def _create_request(**overrides: object) -> dict:
    payload = {
        "customer_name": "Acme Corp",
        "amount": "1500.00",
        "currency": "USD",
    }
    payload.update(overrides)
    return payload


async def _fetch_row(data_store: IDataStore, invoice_id: str) -> FinanceInvoiceRow | None:
    async def _action(session: AsyncSession) -> FinanceInvoiceRow | None:
        stmt = select(FinanceInvoiceRow).where(FinanceInvoiceRow.id == invoice_id)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    return await data_store.execute_in_transaction(_action)


# -- Category C: real capability dispatch integration test -------------------


@pytest.mark.asyncio
async def test_invoice_create_dispatches_through_real_kernel_and_persists(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _finance = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id)

    request = CapabilityRequest(
        capability_name="kortex.finance.invoice.create",
        session_token=token,
        parameters={"request": _create_request()},
        context={"resource_tenant_id": tenant_id},
    )
    result = await kernel.invoke_capability(request)

    assert result.customer_name == "Acme Corp"
    assert result.amount == Decimal("1500.00")
    assert result.currency == "USD"
    assert result.status.value == "DRAFT"
    assert result.tenant_id == tenant_id
    assert result.invoice_id

    # Category F: persistence -- verify the row genuinely exists via
    # IDataStore, independent of the capability's own return value.
    row = await _fetch_row(storage_engine.data, result.invoice_id)
    assert row is not None
    assert row.tenant_id == tenant_id
    assert row.customer_name == "Acme Corp"
    assert row.currency == "USD"
    assert row.status == "DRAFT"
    assert Decimal(row.amount) == Decimal("1500.00")


# -- Category D: tenant-authority adversarial test ---------------------------


@pytest.mark.asyncio
async def test_invoice_tenant_ownership_is_principal_authoritative_not_caller_supplied(tmp_path: Path) -> None:
    """`CreateInvoiceRequest` has no `tenant_id` field at all -- there is
    nothing for a caller to spoof. This test proves the persisted invoice's
    tenant_id is exactly the authenticated principal's own tenant,
    regardless of which tenant issued the request, by creating invoices
    under two different tenants and confirming each row lands under its
    own real tenant, never cross-contaminated."""
    kernel, storage_engine, security_engine, _finance = await _boot_kernel(tmp_path)

    tenant_a = _tenant(tmp_path, "-a")
    tenant_b = _tenant(tmp_path, "-b")
    token_a = await _authorized_token(storage_engine, security_engine, tenant_a)
    token_b = await _authorized_token(storage_engine, security_engine, tenant_b)

    request_a = CapabilityRequest(
        capability_name="kortex.finance.invoice.create",
        session_token=token_a,
        parameters={"request": _create_request(customer_name="Tenant A Customer")},
        context={"resource_tenant_id": tenant_a},
    )
    request_b = CapabilityRequest(
        capability_name="kortex.finance.invoice.create",
        session_token=token_b,
        parameters={"request": _create_request(customer_name="Tenant B Customer")},
        context={"resource_tenant_id": tenant_b},
    )

    result_a = await kernel.invoke_capability(request_a)
    result_b = await kernel.invoke_capability(request_b)

    assert result_a.tenant_id == tenant_a
    assert result_b.tenant_id == tenant_b
    assert result_a.tenant_id != result_b.tenant_id

    row_a = await _fetch_row(storage_engine.data, result_a.invoice_id)
    row_b = await _fetch_row(storage_engine.data, result_b.invoice_id)
    assert row_a is not None and row_a.tenant_id == tenant_a
    assert row_b is not None and row_b.tenant_id == tenant_b


# -- Category E: authorization tests -----------------------------------------


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security, _finance = await _boot_kernel(tmp_path)

    request = CapabilityRequest(capability_name="kortex.finance.invoice.create", session_token=None)
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)


@pytest.mark.asyncio
async def test_authenticated_without_finance_invoice_write_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _finance = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    await _seed_principal(storage_engine.data, tenant_id, "principal-1")  # no roles/permissions granted
    token = await _issue_token(security_engine, tenant_id, "principal-1")

    request = CapabilityRequest(
        capability_name="kortex.finance.invoice.create",
        session_token=token,
        parameters={"request": _create_request()},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(request)


# -- Invalid input fails through existing validation --------------------------


@pytest.mark.asyncio
async def test_invalid_invoice_input_fails_through_existing_validation(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _finance = await _boot_kernel(tmp_path)

    tenant_id = _tenant(tmp_path)
    token = await _authorized_token(storage_engine, security_engine, tenant_id)

    request = CapabilityRequest(
        capability_name="kortex.finance.invoice.create",
        session_token=token,
        parameters={"request": _create_request(amount="-5.00")},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(Exception):  # Pydantic ValidationError, surfaced generically -- no new error path
        await kernel.invoke_capability(request)
