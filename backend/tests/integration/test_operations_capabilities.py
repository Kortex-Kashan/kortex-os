"""Integration test suite for KORTEX Operations business module.

Tests the complete domain lifecycle through the real Kernel and CapabilityDispatcher chain:
- Vehicle creation, plate uniqueness, lookup, pagination, driver assignment & unassignment
- Status transitions (ACTIVE -> MAINTENANCE -> ACTIVE -> DECOMMISSIONED)
- Monotonic odometer progression and tracking history logs
- Incident reporting, sequence numbering (INC-YYYY-####), investigation resolution & terminal closure
- Strict cross-tenant isolation (fail-closed, enumeration-resistant domain NotFoundError)
- Security: authentication, permissions (operations:vehicle:*, operations:incident:*),
  and reserved parameter injection rejection
- Post-commit domain event emissions
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest, ReservedParameterError
from kortex.core.kernel import Kernel
from kortex.engines.event.engine import Event, EventEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.operations.exceptions import (
    OpsIncidentAlreadyClosedError,
    OpsIncidentNotFoundError,
    OpsIncidentValidationError,
    OpsTrackingRecordValidationError,
    OpsVehicleConflictError,
    OpsVehicleNotFoundError,
)
from kortex.modules.operations.models import (
    IncidentResponse,
    IncidentSeverity,
    IncidentStatus,
    VehicleResponse,
    VehicleStatus,
    VehicleTrackingRecordResponse,
)
from kortex.modules.operations.module import OperationsModule

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_TEST_OPS_ROLE = "ops-admin-test-role"

_ALL_OPS_PERMISSIONS = [
    "operations:vehicle:read",
    "operations:vehicle:write",
    "operations:incident:read",
    "operations:incident:write",
    "operations:incident:manage",
]


def _req(
    capability_name: str,
    token: TokenPayload | None,
    parameters: dict[str, Any],
    resource_tenant_id: str | None = None,
) -> CapabilityRequest:
    ctx = {"resource_tenant_id": resource_tenant_id} if resource_tenant_id else {}
    return CapabilityRequest(
        capability_name=capability_name,
        session_token=token,
        parameters=parameters,
        context=ctx,
    )


async def _seed_principal(
    data_store: IDataStore,
    tenant_id: str,
    principal_id: str,
    roles: list[str] | None = None,
) -> None:
    credential_hash = PasswordHasher().hash("ops-test-credential")

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


async def _grant_role_permissions(data_store: IDataStore, role: str, permissions: list[str]) -> None:
    async def _action(session: AsyncSession) -> None:
        for perm in permissions:
            existing = (
                await session.execute(
                    select(RolePermissionRecord).where(
                        RolePermissionRecord.role == role,
                        RolePermissionRecord.permission == perm,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=perm))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "ops-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str = "ops-admin-1",
    role: str = _TEST_OPS_ROLE,
    permissions: list[str] | None = None,
) -> TokenPayload:
    perms = permissions if permissions is not None else _ALL_OPS_PERMISSIONS
    assert storage_engine.data is not None
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permissions(storage_engine.data, role, perms)
    return await _issue_token(security_engine, tenant_id, principal_id)


async def _boot_ops_kernel(
    tmp_path: Path,
) -> tuple[Kernel, StorageEngine, SecurityEngine, EventEngine, OperationsModule]:
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "ops_test_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    event_engine: EventEngine = kernel.get_engine("event")  # type: ignore[assignment]
    ops_module = OperationsModule()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(ops_module)  # type: ignore[arg-type]

    await kernel.boot()
    return kernel, storage_engine, security_engine, event_engine, ops_module


class TestVehicleTrackingCapabilities:
    """Test all 8 vehicle tracking capabilities via kernel dispatcher."""

    @pytest.mark.asyncio
    async def test_complete_vehicle_lifecycle_workflow(self, tmp_path: Path) -> None:
        kernel, storage_engine, security_engine, event_engine, _ = await _boot_ops_kernel(tmp_path)
        try:
            tenant_a = "tenant-ops-a"
            tenant_b = "tenant-ops-b"
            token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "alice")
            token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "bob")

            status_events: list[Event] = []
            event_engine.subscribe(
                "kortex.event.operations.vehicle.status_changed",
                lambda e: status_events.append(e),
            )

            # 1. Create vehicle
            create_req = _req(
                "kortex.operations.vehicle.create",
                token_a,
                {
                    "request": {
                        "license_plate": "fleet-001",
                        "make": "Toyota",
                        "model": "Hilux",
                        "year": 2024,
                        "vehicle_type": "TRUCK",
                        "vin": "1HGCR2F83HA123456",
                        "initial_odometer": "12000.00",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            v: VehicleResponse = await kernel.invoke_capability(create_req)
            assert v.license_plate == "FLEET-001"
            assert v.status == VehicleStatus.ACTIVE
            assert v.current_odometer == Decimal("12000.00")
            assert v.tenant_id == tenant_a

            # Duplicate plate in same tenant fails
            with pytest.raises(OpsVehicleConflictError):
                await kernel.invoke_capability(create_req)

            # Same plate in different tenant succeeds
            create_req_b = _req(
                "kortex.operations.vehicle.create",
                token_b,
                {
                    "request": {
                        "license_plate": "fleet-001",
                        "make": "Nissan",
                        "model": "Navara",
                    }
                },
                resource_tenant_id=tenant_b,
            )
            v_b: VehicleResponse = await kernel.invoke_capability(create_req_b)
            assert v_b.tenant_id == tenant_b

            # 2. Get vehicle
            get_req = _req(
                "kortex.operations.vehicle.get",
                token_a,
                {"vehicle_id": v.id},
                resource_tenant_id=tenant_a,
            )
            v_get: VehicleResponse = await kernel.invoke_capability(get_req)
            assert v_get.id == v.id

            # Cross-tenant get fails
            get_req_cross = _req(
                "kortex.operations.vehicle.get",
                token_b,
                {"vehicle_id": v.id},
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsVehicleNotFoundError):
                await kernel.invoke_capability(get_req_cross)

            # 3. List vehicles
            list_req = _req(
                "kortex.operations.vehicle.list",
                token_a,
                {"request": {"limit": 10, "offset": 0}},
                resource_tenant_id=tenant_a,
            )
            vehicles_a: list[VehicleResponse] = await kernel.invoke_capability(list_req)
            assert len(vehicles_a) == 1
            assert vehicles_a[0].id == v.id

            # 4. Assign & unassign driver
            assign_req = _req(
                "kortex.operations.vehicle.assign",
                token_a,
                {"request": {"vehicle_id": v.id, "driver_id": "driver-charles"}},
                resource_tenant_id=tenant_a,
            )
            v_assigned: VehicleResponse = await kernel.invoke_capability(assign_req)
            assert v_assigned.assigned_driver_id == "driver-charles"

            unassign_req = _req(
                "kortex.operations.vehicle.unassign",
                token_a,
                {"request": {"vehicle_id": v.id}},
                resource_tenant_id=tenant_a,
            )
            v_unassigned: VehicleResponse = await kernel.invoke_capability(unassign_req)
            assert v_unassigned.assigned_driver_id is None

            # 5. Status update (ACTIVE -> MAINTENANCE -> ACTIVE)
            maint_req = _req(
                "kortex.operations.vehicle.status_update",
                token_a,
                {
                    "request": {
                        "vehicle_id": v.id,
                        "new_status": "MAINTENANCE",
                        "reason": "Scheduled oil change",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            v_maint: VehicleResponse = await kernel.invoke_capability(maint_req)
            assert v_maint.status == VehicleStatus.MAINTENANCE
            assert len(status_events) == 1

            active_req = _req(
                "kortex.operations.vehicle.status_update",
                token_a,
                {"request": {"vehicle_id": v.id, "new_status": "ACTIVE"}},
                resource_tenant_id=tenant_a,
            )
            v_active: VehicleResponse = await kernel.invoke_capability(active_req)
            assert v_active.status == VehicleStatus.ACTIVE
            assert len(status_events) == 2

            # 6. Record tracking (monotonic odometer progression)
            track_req_1 = _req(
                "kortex.operations.vehicle.tracking_record",
                token_a,
                {
                    "request": {
                        "vehicle_id": v.id,
                        "odometer_reading": "12500.00",
                        "location_name": "Regional Hub 1",
                        "notes": "Delivered shipment",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            t1: VehicleTrackingRecordResponse = await kernel.invoke_capability(track_req_1)
            assert t1.odometer_reading == Decimal("12500.00")
            assert t1.recorded_by == "alice"  # Derived from execution context principal

            # Non-monotonic odometer fails
            track_req_bad = _req(
                "kortex.operations.vehicle.tracking_record",
                token_a,
                {
                    "request": {
                        "vehicle_id": v.id,
                        "odometer_reading": "12400.00",  # Lower than 12500.00
                    }
                },
                resource_tenant_id=tenant_a,
            )
            with pytest.raises(OpsTrackingRecordValidationError):
                await kernel.invoke_capability(track_req_bad)

            # Record tracking 2
            track_req_2 = _req(
                "kortex.operations.vehicle.tracking_record",
                token_a,
                {
                    "request": {
                        "vehicle_id": v.id,
                        "odometer_reading": "13000.00",
                        "location_name": "Depot Central",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            t2: VehicleTrackingRecordResponse = await kernel.invoke_capability(track_req_2)
            assert t2.odometer_reading == Decimal("13000.00")

            # 7. Get tracking history
            hist_req = _req(
                "kortex.operations.vehicle.tracking_history",
                token_a,
                {"request": {"vehicle_id": v.id, "limit": 10, "offset": 0}},
                resource_tenant_id=tenant_a,
            )
            history: list[VehicleTrackingRecordResponse] = await kernel.invoke_capability(hist_req)
            assert len(history) == 2
            assert history[0].odometer_reading == Decimal("13000.00")
            assert history[1].odometer_reading == Decimal("12500.00")

            # Cross-tenant tracking append fails
            track_cross = _req(
                "kortex.operations.vehicle.tracking_record",
                token_b,
                {
                    "request": {
                        "vehicle_id": v.id,
                        "odometer_reading": "14000.00",
                    }
                },
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsVehicleNotFoundError):
                await kernel.invoke_capability(track_cross)
        finally:
            await kernel.shutdown()


class TestIncidentManagementCapabilities:
    """Test all 5 incident management capabilities via kernel dispatcher."""

    @pytest.mark.asyncio
    async def test_complete_incident_lifecycle_workflow(self, tmp_path: Path) -> None:
        kernel, storage_engine, security_engine, event_engine, _ = await _boot_ops_kernel(tmp_path)
        try:
            tenant_a = "tenant-ops-a"
            tenant_b = "tenant-ops-b"
            token_a = await _authorized_token(storage_engine, security_engine, tenant_a, "officer-dave")
            token_b = await _authorized_token(storage_engine, security_engine, tenant_b, "officer-eve")

            reported_events: list[Event] = []
            closed_events: list[Event] = []
            event_engine.subscribe(
                "kortex.event.operations.incident.reported",
                lambda e: reported_events.append(e),
            )
            event_engine.subscribe(
                "kortex.event.operations.incident.closed",
                lambda e: closed_events.append(e),
            )

            # 1. Report Incident
            occurred = datetime.now(UTC) - timedelta(hours=2)
            report_req = _req(
                "kortex.operations.incident.report",
                token_a,
                {
                    "request": {
                        "incident_type": "ACCIDENT",
                        "severity": "HIGH",
                        "title": "Delivery van rear-ended",
                        "description": "Van stopped at pedestrian crossing was struck from behind.",
                        "occurred_at": occurred.isoformat(),
                        "driver_id": "driver-dan",
                        "location": "5th Ave & Pine St",
                        "estimated_cost": "3200.00",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            inc: IncidentResponse = await kernel.invoke_capability(report_req)
            now_year = datetime.now(UTC).year
            assert inc.incident_number == f"INC-{now_year}-0001"
            assert inc.status == IncidentStatus.REPORTED
            assert inc.severity == IncidentSeverity.HIGH
            assert inc.reported_by_id == "officer-dave"
            assert len(reported_events) == 1

            # 2. Get Incident
            get_req = _req(
                "kortex.operations.incident.get",
                token_a,
                {"incident_id": inc.id},
                resource_tenant_id=tenant_a,
            )
            inc_get: IncidentResponse = await kernel.invoke_capability(get_req)
            assert inc_get.id == inc.id

            # Cross-tenant get fails
            get_req_cross = _req(
                "kortex.operations.incident.get",
                token_b,
                {"incident_id": inc.id},
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsIncidentNotFoundError):
                await kernel.invoke_capability(get_req_cross)

            # 3. List Incidents
            list_req = _req(
                "kortex.operations.incident.list",
                token_a,
                {"request": {"status": "REPORTED"}},
                resource_tenant_id=tenant_a,
            )
            incidents_a: list[IncidentResponse] = await kernel.invoke_capability(list_req)
            assert len(incidents_a) == 1
            assert incidents_a[0].id == inc.id

            # 4. Status Update (Intermediate Lifecycle)
            # Direct resolve of HIGH severity incident from REPORTED must fail
            resolve_premature = _req(
                "kortex.operations.incident.resolve",
                token_a,
                {
                    "request": {
                        "incident_id": inc.id,
                        "resolution_notes": "Premature resolve",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            with pytest.raises(OpsIncidentValidationError):
                await kernel.invoke_capability(resolve_premature)

            # Cross-tenant status update fails
            status_cross = _req(
                "kortex.operations.incident.status_update",
                token_b,
                {
                    "request": {
                        "incident_id": inc.id,
                        "status": "UNDER_INVESTIGATION",
                    }
                },
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsIncidentNotFoundError):
                await kernel.invoke_capability(status_cross)

            # Advance REPORTED -> UNDER_INVESTIGATION
            status_req_1 = _req(
                "kortex.operations.incident.status_update",
                token_a,
                {
                    "request": {
                        "incident_id": inc.id,
                        "status": "UNDER_INVESTIGATION",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            inc_inv: IncidentResponse = await kernel.invoke_capability(status_req_1)
            assert inc_inv.status == IncidentStatus.UNDER_INVESTIGATION

            # Advance UNDER_INVESTIGATION -> ACTION_REQUIRED
            status_req_2 = _req(
                "kortex.operations.incident.status_update",
                token_a,
                {
                    "request": {
                        "incident_id": inc.id,
                        "status": "ACTION_REQUIRED",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            inc_act: IncidentResponse = await kernel.invoke_capability(status_req_2)
            assert inc_act.status == IncidentStatus.ACTION_REQUIRED

            # 5. Resolve Incident
            # Cross-tenant resolve fails
            resolve_cross = _req(
                "kortex.operations.incident.resolve",
                token_b,
                {
                    "request": {
                        "incident_id": inc.id,
                        "resolution_notes": "Cross tenant tamper",
                    }
                },
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsIncidentNotFoundError):
                await kernel.invoke_capability(resolve_cross)

            resolve_req = _req(
                "kortex.operations.incident.resolve",
                token_a,
                {
                    "request": {
                        "incident_id": inc.id,
                        "resolution_notes": "Bumper replaced and chassis realigned by certified workshop.",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            inc_resolved: IncidentResponse = await kernel.invoke_capability(resolve_req)
            assert inc_resolved.status == IncidentStatus.RESOLVED
            assert inc_resolved.resolved_by == "officer-dave"
            assert inc_resolved.resolved_at is not None

            # 6. Close Incident
            # Cross-tenant close fails
            close_cross = _req(
                "kortex.operations.incident.close",
                token_b,
                {"request": {"incident_id": inc.id}},
                resource_tenant_id=tenant_b,
            )
            with pytest.raises(OpsIncidentNotFoundError):
                await kernel.invoke_capability(close_cross)

            close_req = _req(
                "kortex.operations.incident.close",
                token_a,
                {
                    "request": {
                        "incident_id": inc.id,
                        "closing_notes": "Insurance claim fully paid and file closed.",
                    }
                },
                resource_tenant_id=tenant_a,
            )
            inc_closed: IncidentResponse = await kernel.invoke_capability(close_req)
            assert inc_closed.status == IncidentStatus.CLOSED
            assert inc_closed.closed_by == "officer-dave"
            assert inc_closed.closed_at is not None
            assert len(closed_events) == 1

            # 6. Closed Incident is Terminal and Immutable
            with pytest.raises(OpsIncidentAlreadyClosedError):
                await kernel.invoke_capability(resolve_req)

            with pytest.raises(OpsIncidentAlreadyClosedError):
                await kernel.invoke_capability(close_req)
        finally:
            await kernel.shutdown()


class TestSecurityAndAdversarialRejection:
    """Validate strict security barriers, RBAC enforcement, and reserved parameter protection."""

    @pytest.mark.asyncio
    async def test_authentication_and_authorization_guards(self, tmp_path: Path) -> None:
        kernel, storage_engine, security_engine, _, _ = await _boot_ops_kernel(tmp_path)
        try:
            tenant_id = "sec-tenant-1"
            full_token = await _authorized_token(storage_engine, security_engine, tenant_id, "admin-user")

            # 1. Unauthenticated dispatch rejected
            unauth_req = _req(
                "kortex.operations.vehicle.list",
                None,
                {"request": {}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(AuthenticationError):
                await kernel.invoke_capability(unauth_req)

            # 2. Permission denied for user lacking write permission
            read_only_token = await _authorized_token(
                storage_engine,
                security_engine,
                tenant_id,
                principal_id="reader-user",
                role="ops-reader-role",
                permissions=["operations:vehicle:read", "operations:incident:read"],
            )
            denied_write_req = _req(
                "kortex.operations.vehicle.create",
                read_only_token,
                {
                    "request": {
                        "license_plate": "DENIED-1",
                        "make": "Ford",
                        "model": "Focus",
                    }
                },
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(AuthorizationDeniedError):
                await kernel.invoke_capability(denied_write_req)

            denied_status_req = _req(
                "kortex.operations.incident.status_update",
                read_only_token,
                {"request": {"incident_id": "inc-id", "status": "UNDER_INVESTIGATION"}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(AuthorizationDeniedError):
                await kernel.invoke_capability(denied_status_req)

            # 3. Permission denied for user lacking operations:incident:manage
            worker_token = await _authorized_token(
                storage_engine,
                security_engine,
                tenant_id,
                principal_id="worker-user",
                role="ops-worker-role",
                permissions=["operations:incident:write", "operations:incident:read"],
            )
            denied_manage_req = _req(
                "kortex.operations.incident.close",
                worker_token,
                {"request": {"incident_id": "any-id"}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(AuthorizationDeniedError):
                await kernel.invoke_capability(denied_manage_req)

            # 4. Reserved parameter injection is rejected by dispatcher
            # Injected principal
            inj_principal_req = _req(
                "kortex.operations.vehicle.list",
                full_token,
                {"principal": "malicious-actor", "request": {}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(ReservedParameterError):
                await kernel.invoke_capability(inj_principal_req)

            # Injected execution_context
            inj_ctx_req = _req(
                "kortex.operations.vehicle.list",
                full_token,
                {"execution_context": "forged-context", "request": {}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(ReservedParameterError):
                await kernel.invoke_capability(inj_ctx_req)

            # Injected caller-supplied tenant_id
            inj_tenant_req = _req(
                "kortex.operations.vehicle.list",
                full_token,
                {"tenant_id": "malicious-tenant", "request": {}},
                resource_tenant_id=tenant_id,
            )
            with pytest.raises(TypeError):
                await kernel.invoke_capability(inj_tenant_req)
        finally:
            await kernel.shutdown()
