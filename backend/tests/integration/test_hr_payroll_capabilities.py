"""Integration test suite for KORTEX HR & Payroll business module.

Tests the complete domain lifecycle through the real Kernel dispatch chain:
- Employee creation, lookup, pagination, and tenant isolation
- Attendance check-in, duplicate prevention, check-out, worked hours & overtime
- Leave application, balance deduction, overlap prevention, approval/rejection
- Monthly payroll calculation, Decimal exactness, immutability, payslips
- Finalization event emission (kortex.event.payroll.run_finalized)
- Security: authentication, permissions, reserved parameter injection rejection
"""

from __future__ import annotations

import uuid
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
from kortex.modules.hr_payroll.exceptions import (
    HRAttendanceConflictError,
    HRAttendanceValidationError,
    HREmployeeConflictError,
    HREmployeeNotFoundError,
    HRLeaveBalanceExceededError,
    HRLeaveOverlapError,
    HRPayrollRunAlreadyFinalizedError,
)
from kortex.modules.hr_payroll.models import (
    AttendanceResponse,
    EmployeeResponse,
    LeaveBalanceResponse,
    LeaveRequestResponse,
    PayrollRunResponse,
    PayslipResponse,
)
from kortex.modules.hr_payroll.module import HRPayrollModule

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_TEST_HR_ROLE = "hr-admin-test-role"

_ALL_HR_PERMISSIONS = [
    "hr:employee:write",
    "hr:employee:read",
    "hr:attendance:write",
    "hr:attendance:read",
    "hr:leave:write",
    "hr:leave:read",
    "hr:leave:approve",
    "payroll:run:write",
    "payroll:run:read",
    "payroll:payslip:read",
]


def _tenant(tmp_path: Path, suffix: str = "") -> str:
    return f"tenant-hr-{tmp_path.name}{suffix}-{uuid.uuid4().hex[:8]}"


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
    credential_hash = PasswordHasher().hash("hr-test-credential")

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
            existing = await session.scalar(
                select(RolePermissionRecord).where(
                    RolePermissionRecord.role == role,
                    RolePermissionRecord.permission == perm,
                )
            )
            if existing is None:
                session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=perm))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "hr-test-credential",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _authorized_token(
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str = "hr-admin-1",
    role: str = _TEST_HR_ROLE,
    permissions: list[str] | None = None,
) -> TokenPayload:
    perms = permissions if permissions is not None else _ALL_HR_PERMISSIONS
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permissions(storage_engine.data, role, perms)
    return await _issue_token(security_engine, tenant_id, principal_id)


async def _boot_hr_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine, EventEngine, HRPayrollModule]:
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "hr_test_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    event_engine: EventEngine = kernel.get_engine("event")  # type: ignore[assignment]
    hr_module = HRPayrollModule()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(hr_module)

    await kernel.boot()
    return kernel, storage_engine, security_engine, event_engine, hr_module


# -- Tests --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_employee_lifecycle_and_tenant_isolation(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _, _ = await _boot_hr_kernel(tmp_path)
    try:
        tenant_a = _tenant(tmp_path, "-a")
        tenant_b = _tenant(tmp_path, "-b")

        token_a = await _authorized_token(storage_engine, security_engine, tenant_a)
        token_b = await _authorized_token(storage_engine, security_engine, tenant_b)

        # 1. Create employee under Tenant A
        create_req = _req(
            "kortex.hr_payroll.employee.create",
            token_a,
            {
                "request": {
                    "employee_code": "EMP-001",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "email": "alice@example.com",
                    "department": "Engineering",
                    "position": "Staff Engineer",
                    "joined_date": "2026-01-15",
                    "base_salary": "6000.00",
                    "currency": "USD",
                }
            },
            resource_tenant_id=tenant_a,
        )
        emp_a: EmployeeResponse = await kernel.invoke_capability(create_req)
        assert emp_a.employee_code == "EMP-001"
        assert emp_a.first_name == "Alice"
        assert emp_a.tenant_id == tenant_a
        assert emp_a.base_salary == Decimal("6000.00")

        # 2. Duplicate employee code under same tenant fails
        with pytest.raises(HREmployeeConflictError):
            await kernel.invoke_capability(create_req)

        # 3. Retrieve employee under Tenant A
        get_req = _req(
            "kortex.hr_payroll.employee.get",
            token_a,
            {"employee_id": emp_a.employee_id},
            resource_tenant_id=tenant_a,
        )
        fetched_a: EmployeeResponse = await kernel.invoke_capability(get_req)
        assert fetched_a.employee_id == emp_a.employee_id

        # 4. Cross-tenant access: Tenant B cannot retrieve Tenant A's employee
        get_req_b = _req(
            "kortex.hr_payroll.employee.get",
            token_b,
            {"employee_id": emp_a.employee_id},
            resource_tenant_id=tenant_b,
        )
        with pytest.raises(HREmployeeNotFoundError):
            await kernel.invoke_capability(get_req_b)

        # 5. List employees under Tenant A vs Tenant B
        list_req_a = _req(
            "kortex.hr_payroll.employee.list",
            token_a,
            {"request": {}},
            resource_tenant_id=tenant_a,
        )
        list_a: list[EmployeeResponse] = await kernel.invoke_capability(list_req_a)
        assert len(list_a) == 1
        assert list_a[0].employee_id == emp_a.employee_id

        list_req_b = _req(
            "kortex.hr_payroll.employee.list",
            token_b,
            {"request": {}},
            resource_tenant_id=tenant_b,
        )
        list_b: list[EmployeeResponse] = await kernel.invoke_capability(list_req_b)
        assert len(list_b) == 0

        # 6. Verify default leave balances seeded for employee
        bal_req = _req(
            "kortex.hr_payroll.leave.balance_get",
            token_a,
            {"employee_id": emp_a.employee_id, "year": 2026},
            resource_tenant_id=tenant_a,
        )
        balances: list[LeaveBalanceResponse] = await kernel.invoke_capability(bal_req)
        assert len(balances) == 4
        by_type = {b.leave_type.value: b for b in balances}
        assert by_type["ANNUAL"].allocated_days == Decimal("20.0")
        assert by_type["SICK"].allocated_days == Decimal("10.0")
        assert by_type["CASUAL"].allocated_days == Decimal("5.0")
        assert by_type["UNPAID"].allocated_days == Decimal("0.0")
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_attendance_lifecycle_and_overtime(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _, _ = await _boot_hr_kernel(tmp_path)
    try:
        tenant_id = _tenant(tmp_path)
        token = await _authorized_token(storage_engine, security_engine, tenant_id)

        # Create employee
        create_emp_req = _req(
            "kortex.hr_payroll.employee.create",
            token,
            {
                "request": {
                    "employee_code": "EMP-002",
                    "first_name": "Bob",
                    "last_name": "Jones",
                    "joined_date": "2026-02-01",
                    "base_salary": "4000.00",
                }
            },
            resource_tenant_id=tenant_id,
        )
        emp: EmployeeResponse = await kernel.invoke_capability(create_emp_req)

        # Check-in on 2026-09-01 at 09:00:00 UTC
        work_date = "2026-09-01"
        check_in_time = "2026-09-01T09:00:00Z"
        check_in_req = _req(
            "kortex.hr_payroll.attendance.check_in",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "work_date": work_date,
                    "check_in_time": check_in_time,
                }
            },
            resource_tenant_id=tenant_id,
        )
        att: AttendanceResponse = await kernel.invoke_capability(check_in_req)
        assert att.employee_id == emp.employee_id
        assert att.check_out is None
        assert att.total_hours == Decimal("0.00")

        # Duplicate check-in fails
        with pytest.raises(HRAttendanceConflictError):
            await kernel.invoke_capability(check_in_req)

        # Check-out earlier than check-in fails
        earlier_out_req = _req(
            "kortex.hr_payroll.attendance.check_out",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "work_date": work_date,
                    "check_out_time": "2026-09-01T08:00:00Z",
                }
            },
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(HRAttendanceValidationError):
            await kernel.invoke_capability(earlier_out_req)

        # Valid check-out with 10.5 hours worked (09:00 to 19:30 UTC) -> 2.5 hours overtime
        valid_out_req = _req(
            "kortex.hr_payroll.attendance.check_out",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "work_date": work_date,
                    "check_out_time": "2026-09-01T19:30:00Z",
                    "notes": "Late project deployment",
                }
            },
            resource_tenant_id=tenant_id,
        )
        att_out: AttendanceResponse = await kernel.invoke_capability(valid_out_req)
        assert att_out.total_hours == Decimal("10.50")
        assert att_out.overtime_hours == Decimal("2.50")
        assert att_out.status.value == "PRESENT"

        # List attendance
        list_att_req = _req(
            "kortex.hr_payroll.attendance.list",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-30",
                }
            },
            resource_tenant_id=tenant_id,
        )
        records: list[AttendanceResponse] = await kernel.invoke_capability(list_att_req)
        assert len(records) == 1
        assert records[0].total_hours == Decimal("10.50")
        assert records[0].overtime_hours == Decimal("2.50")
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_leave_application_balance_and_overlap_prevention(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _, _ = await _boot_hr_kernel(tmp_path)
    try:
        tenant_id = _tenant(tmp_path)
        token = await _authorized_token(storage_engine, security_engine, tenant_id)

        # Create employee with 3 days casual leave
        emp: EmployeeResponse = await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.employee.create",
                token,
                {
                    "request": {
                        "employee_code": "EMP-003",
                        "first_name": "Carol",
                        "last_name": "White",
                        "joined_date": "2026-01-01",
                        "base_salary": "3500.00",
                        "initial_casual_leave_days": "3.0",
                    }
                },
                resource_tenant_id=tenant_id,
            )
        )

        # 1. Apply for 2 days casual leave (2026-10-01 to 2026-10-02)
        leave_req = _req(
            "kortex.hr_payroll.leave.request",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "leave_type": "CASUAL",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-02",
                    "reason": "Doctor appointment",
                }
            },
            resource_tenant_id=tenant_id,
        )
        leave_app: LeaveRequestResponse = await kernel.invoke_capability(leave_req)
        assert leave_app.status.value == "PENDING"
        assert leave_app.days_count == Decimal("2.0")

        # 2. Overlapping leave application rejected
        overlap_req = _req(
            "kortex.hr_payroll.leave.request",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "leave_type": "CASUAL",
                    "start_date": "2026-10-02",
                    "end_date": "2026-10-04",
                    "reason": "Personal errands",
                }
            },
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(HRLeaveOverlapError):
            await kernel.invoke_capability(overlap_req)

        # 3. Approve leave request
        decide_req = _req(
            "kortex.hr_payroll.leave.decide",
            token,
            {
                "request": {
                    "leave_id": leave_app.leave_id,
                    "decision": "APPROVE",
                    "decision_reason": "Approved by manager",
                }
            },
            resource_tenant_id=tenant_id,
        )
        decided: LeaveRequestResponse = await kernel.invoke_capability(decide_req)
        assert decided.status.value == "APPROVED"
        assert decided.decision_reason == "Approved by manager"

        # 4. Check that balance was decremented (allocated: 3.0, used: 2.0, available: 1.0)
        bal_req = _req(
            "kortex.hr_payroll.leave.balance_get",
            token,
            {"employee_id": emp.employee_id, "year": 2026},
            resource_tenant_id=tenant_id,
        )
        balances: list[LeaveBalanceResponse] = await kernel.invoke_capability(bal_req)
        casual_bal = next(b for b in balances if b.leave_type.value == "CASUAL")
        assert casual_bal.allocated_days == Decimal("3.0")
        assert casual_bal.used_days == Decimal("2.0")
        assert casual_bal.available_days == Decimal("1.0")

        # 5. Applying for 2 days now exceeds available balance (only 1 day available)
        exceed_req = _req(
            "kortex.hr_payroll.leave.request",
            token,
            {
                "request": {
                    "employee_id": emp.employee_id,
                    "leave_type": "CASUAL",
                    "start_date": "2026-10-10",
                    "end_date": "2026-10-11",
                    "reason": "More time off",
                }
            },
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(HRLeaveBalanceExceededError):
            await kernel.invoke_capability(exceed_req)
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_payroll_run_calculation_immutability_payslip_and_event(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, event_engine, _ = await _boot_hr_kernel(tmp_path)
    published_events: list[Event] = []

    async def _event_handler(event: Event) -> None:
        published_events.append(event)

    event_engine.subscribe("kortex.event.payroll.run_finalized", _event_handler)

    try:
        tenant_id = _tenant(tmp_path)
        token = await _authorized_token(storage_engine, security_engine, tenant_id)

        # 1. Create Employee: base_salary = 3000.00 USD
        # daily_rate = 3000 / 30 = 100.00, hourly_rate = 100 / 8 = 12.50
        emp: EmployeeResponse = await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.employee.create",
                token,
                {
                    "request": {
                        "employee_code": "EMP-PAY-1",
                        "first_name": "Diana",
                        "last_name": "Prince",
                        "department": "Security",
                        "position": "Director",
                        "joined_date": "2026-01-01",
                        "base_salary": "3000.00",
                        "currency": "USD",
                    }
                },
                resource_tenant_id=tenant_id,
            )
        )

        # 2. Record Attendance with Overtime:
        # Day 1: 12 hours (4 hours overtime)
        await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.attendance.check_in",
                token,
                {
                    "request": {
                        "employee_id": emp.employee_id,
                        "work_date": "2026-09-01",
                        "check_in_time": "2026-09-01T08:00:00Z",
                    }
                },
                resource_tenant_id=tenant_id,
            )
        )
        await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.attendance.check_out",
                token,
                {
                    "request": {
                        "employee_id": emp.employee_id,
                        "work_date": "2026-09-01",
                        "check_out_time": "2026-09-01T20:00:00Z",
                    }
                },
                resource_tenant_id=tenant_id,
            )
        )

        # 3. Record Approved Unpaid Leave: 2 days (2026-09-10 to 2026-09-11)
        leave_app: LeaveRequestResponse = await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.leave.request",
                token,
                {
                    "request": {
                        "employee_id": emp.employee_id,
                        "leave_type": "UNPAID",
                        "start_date": "2026-09-10",
                        "end_date": "2026-09-11",
                        "reason": "Personal sabbatical",
                    }
                },
                resource_tenant_id=tenant_id,
            )
        )
        await kernel.invoke_capability(
            _req(
                "kortex.hr_payroll.leave.decide",
                token,
                {"request": {"leave_id": leave_app.leave_id, "decision": "APPROVE"}},
                resource_tenant_id=tenant_id,
            )
        )

        # 4. Calculate Payroll with finalize=True
        # Overtime: 4 hours * 12.50 * 1.5 = 75.00
        # Unpaid leave deduction: 2 days * 100.00 = 200.00
        # Gross salary: 3000.00 + 75.00 = 3075.00
        # Total deductions: 200.00
        # Net salary: 3075.00 - 200.00 = 2875.00
        calc_req = _req(
            "kortex.hr_payroll.payroll.calculate",
            token,
            {
                "request": {
                    "period_start": "2026-09-01",
                    "period_end": "2026-09-30",
                    "finalize": True,
                }
            },
            resource_tenant_id=tenant_id,
        )
        payroll_run: PayrollRunResponse = await kernel.invoke_capability(calc_req)

        assert payroll_run.status.value == "FINALIZED"
        assert payroll_run.total_gross == Decimal("3075.00")
        assert payroll_run.total_deductions == Decimal("200.00")
        assert payroll_run.total_net == Decimal("2875.00")
        assert payroll_run.employee_count == 1
        assert len(payroll_run.entries) == 1

        entry = payroll_run.entries[0]
        assert entry.employee_id == emp.employee_id
        assert entry.overtime_hours == Decimal("4.00")
        assert entry.overtime_pay == Decimal("75.00")
        assert entry.unpaid_leave_days == Decimal("2.0")
        assert entry.unpaid_leave_deduction == Decimal("200.00")
        assert entry.gross_salary == Decimal("3075.00")
        assert entry.net_salary == Decimal("2875.00")

        # 5. Verify Event Emission
        assert len(published_events) == 1
        event = published_events[0]
        assert event.topic == "kortex.event.payroll.run_finalized"
        assert event.payload["tenant_id"] == tenant_id
        assert event.payload["run_id"] == payroll_run.run_id
        assert event.payload["total_gross"] == "3075.00"
        assert event.payload["total_net"] == "2875.00"

        # 6. Retrieve Payroll Run
        get_run_req = _req(
            "kortex.hr_payroll.payroll.run_get",
            token,
            {"run_id": payroll_run.run_id},
            resource_tenant_id=tenant_id,
        )
        fetched_run: PayrollRunResponse = await kernel.invoke_capability(get_run_req)
        assert fetched_run.run_id == payroll_run.run_id
        assert len(fetched_run.entries) == 1

        # 7. Retrieve Payslip
        payslip_req = _req(
            "kortex.hr_payroll.payslip.get",
            token,
            {"run_id": payroll_run.run_id, "employee_id": emp.employee_id},
            resource_tenant_id=tenant_id,
        )
        payslip: PayslipResponse = await kernel.invoke_capability(payslip_req)
        assert payslip.employee_code == "EMP-PAY-1"
        assert payslip.employee_name == "Diana Prince"
        assert payslip.base_salary == Decimal("3000.00")
        assert payslip.overtime_pay == Decimal("75.00")
        assert payslip.unpaid_leave_deduction == Decimal("200.00")
        assert payslip.gross_salary == Decimal("3075.00")
        assert payslip.net_salary == Decimal("2875.00")

        # 8. Immutability: Attempting to recalculate finalized run raises error
        with pytest.raises(HRPayrollRunAlreadyFinalizedError):
            await kernel.invoke_capability(calc_req)
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_security_and_reserved_parameter_rejection(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine, _, _ = await _boot_hr_kernel(tmp_path)
    try:
        tenant_id = _tenant(tmp_path)
        token = await _authorized_token(storage_engine, security_engine, tenant_id)

        # 1. Unauthenticated dispatch fails
        unauth_req = _req(
            "kortex.hr_payroll.employee.list",
            None,
            {"request": {}},
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(AuthenticationError):
            await kernel.invoke_capability(unauth_req)

        # 2. Insufficient permissions fails
        limited_token = await _authorized_token(
            storage_engine,
            security_engine,
            tenant_id,
            principal_id="viewer-1",
            role="hr-viewer-role",
            permissions=["hr:employee:read"],
        )
        unauth_write_req = _req(
            "kortex.hr_payroll.employee.create",
            limited_token,
            {
                "request": {
                    "employee_code": "EMP-FAIL",
                    "first_name": "Eve",
                    "last_name": "Hacker",
                    "joined_date": "2026-01-01",
                    "base_salary": "5000.00",
                }
            },
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(AuthorizationDeniedError):
            await kernel.invoke_capability(unauth_write_req)

        # 3. Reserved parameter injection is rejected by dispatcher
        # Attempt to inject principal (reserved key)
        injection_principal_req = _req(
            "kortex.hr_payroll.employee.list",
            token,
            {"principal": "fake-principal", "request": {}},
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(ReservedParameterError):
            await kernel.invoke_capability(injection_principal_req)

        # Attempt to inject execution_context (reserved key)
        injection_ctx_req = _req(
            "kortex.hr_payroll.employee.list",
            token,
            {"execution_context": "fake-context", "request": {}},
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(ReservedParameterError):
            await kernel.invoke_capability(injection_ctx_req)

        # Attempt to inject unexpected caller tenant_id into handler parameters is rejected
        injection_tenant_req = _req(
            "kortex.hr_payroll.employee.list",
            token,
            {"tenant_id": "malicious-tenant", "request": {}},
            resource_tenant_id=tenant_id,
        )
        with pytest.raises(TypeError):
            await kernel.invoke_capability(injection_tenant_req)
    finally:
        await kernel.shutdown()
