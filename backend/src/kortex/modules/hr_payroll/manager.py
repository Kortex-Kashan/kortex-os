"""Domain manager orchestrating persistence and transactions for KORTEX HR & Payroll.

All mutations and queries execute within `IDataStore.execute_in_transaction`.
All SQL statements enforce tenant isolation using the authoritative `tenant_id`.
Cross-tenant entity access raises domain `NotFoundError` for enumeration resistance.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.event.engine import EventEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.hr_payroll.calculator import (
    calculate_payroll_entry,
    calculate_worked_hours,
    round_money,
)
from kortex.modules.hr_payroll.exceptions import (
    HRAttendanceConflictError,
    HRAttendanceNotFoundError,
    HRAttendanceValidationError,
    HREmployeeConflictError,
    HREmployeeNotFoundError,
    HRLeaveBalanceExceededError,
    HRLeaveNotFoundError,
    HRLeaveOverlapError,
    HRLeaveValidationError,
    HRPayrollRunAlreadyFinalizedError,
    HRPayrollRunNotFoundError,
)
from kortex.modules.hr_payroll.models import (
    ApplyLeaveRequest,
    AttendanceResponse,
    AttendanceStatus,
    CalculatePayrollRequest,
    CheckInRequest,
    CheckOutRequest,
    CreateEmployeeRequest,
    DecideLeaveRequest,
    EmployeeResponse,
    EmployeeStatus,
    LeaveBalanceResponse,
    LeaveRequestResponse,
    LeaveStatus,
    LeaveType,
    ListAttendanceRequest,
    ListEmployeesRequest,
    PayrollEntryResponse,
    PayrollRunResponse,
    PayrollRunStatus,
    PayslipResponse,
)
from kortex.modules.hr_payroll.persistence import (
    HRAttendanceRow,
    HREmployeeRow,
    HRLeaveBalanceRow,
    HRLeaveRequestRow,
    HRPayrollEntryRow,
    HRPayrollRunRow,
)

logger = logging.getLogger("kortex.modules.hr_payroll.manager")


class HRPayrollManager:
    """Orchestrates HR & Payroll business operations and transactional persistence."""

    def __init__(self, data_store: IDataStore, event_engine: EventEngine | None = None) -> None:
        self._data_store = data_store
        self._event_engine = event_engine

    async def _emit_event(self, topic: str, payload: dict[str, object]) -> None:
        if self._event_engine is not None:
            try:
                await self._event_engine.publish(
                    topic=topic,
                    payload=payload,
                    sender="hr_payroll",
                )
            except Exception as exc:
                logger.warning("Failed to publish event '%s': %s", topic, exc)

    # -- Employee Operations --------------------------------------------------

    async def create_employee(self, request: CreateEmployeeRequest, tenant_id: str) -> EmployeeResponse:
        """Register a new employee master record with default leave balances."""
        employee_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> HREmployeeRow:
            # Check unique employee code within tenant
            stmt_exist = select(HREmployeeRow).where(
                HREmployeeRow.tenant_id == tenant_id,
                HREmployeeRow.employee_code == request.employee_code,
            )
            existing = (await session.execute(stmt_exist)).scalar_one_or_none()
            if existing is not None:
                raise HREmployeeConflictError(
                    f"Employee with code '{request.employee_code}' already exists for tenant '{tenant_id}'."
                )

            employee_row = HREmployeeRow(
                id=employee_id,
                tenant_id=tenant_id,
                employee_code=request.employee_code,
                first_name=request.first_name,
                last_name=request.last_name,
                email=request.email,
                department=request.department,
                position=request.position,
                status=EmployeeStatus.ACTIVE.value,
                joined_date=request.joined_date,
                base_salary=request.base_salary,
                currency=request.currency,
                created_at=now,
                updated_at=now,
            )
            session.add(employee_row)

            # Seed initial leave balances for current calendar year
            year = request.joined_date.year
            annual_days = (
                request.initial_annual_leave_days if request.initial_annual_leave_days is not None else Decimal("20.0")
            )
            sick_days = (
                request.initial_sick_leave_days if request.initial_sick_leave_days is not None else Decimal("10.0")
            )
            casual_days = (
                request.initial_casual_leave_days if request.initial_casual_leave_days is not None else Decimal("5.0")
            )

            balances = [
                HRLeaveBalanceRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    employee_id=employee_id,
                    leave_type=LeaveType.ANNUAL.value,
                    year=year,
                    allocated_days=annual_days,
                    used_days=Decimal("0.0"),
                    created_at=now,
                    updated_at=now,
                ),
                HRLeaveBalanceRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    employee_id=employee_id,
                    leave_type=LeaveType.SICK.value,
                    year=year,
                    allocated_days=sick_days,
                    used_days=Decimal("0.0"),
                    created_at=now,
                    updated_at=now,
                ),
                HRLeaveBalanceRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    employee_id=employee_id,
                    leave_type=LeaveType.CASUAL.value,
                    year=year,
                    allocated_days=casual_days,
                    used_days=Decimal("0.0"),
                    created_at=now,
                    updated_at=now,
                ),
                HRLeaveBalanceRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    employee_id=employee_id,
                    leave_type=LeaveType.UNPAID.value,
                    year=year,
                    allocated_days=Decimal("0.0"),
                    used_days=Decimal("0.0"),
                    created_at=now,
                    updated_at=now,
                ),
            ]
            session.add_all(balances)
            return employee_row

        row = await self._data_store.execute_in_transaction(_action)

        await self._emit_event(
            "kortex.event.hr.employee_created",
            {
                "tenant_id": tenant_id,
                "employee_id": employee_id,
                "employee_code": request.employee_code,
                "joined_date": str(request.joined_date),
            },
        )

        return EmployeeResponse(
            employee_id=row.id,
            tenant_id=row.tenant_id,
            employee_code=row.employee_code,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            department=row.department,
            position=row.position,
            status=EmployeeStatus(row.status),
            joined_date=row.joined_date,
            base_salary=row.base_salary,
            currency=row.currency,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get_employee(self, employee_id: str, tenant_id: str) -> EmployeeResponse:
        """Retrieve one employee by ID with strict tenant scoping."""

        async def _action(session: AsyncSession) -> HREmployeeRow | None:
            stmt = select(HREmployeeRow).where(
                HREmployeeRow.id == employee_id,
                HREmployeeRow.tenant_id == tenant_id,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

        row = await self._data_store.execute_in_transaction(_action)
        if row is None:
            raise HREmployeeNotFoundError(f"Employee '{employee_id}' not found.")

        return EmployeeResponse(
            employee_id=row.id,
            tenant_id=row.tenant_id,
            employee_code=row.employee_code,
            first_name=row.first_name,
            last_name=row.last_name,
            email=row.email,
            department=row.department,
            position=row.position,
            status=EmployeeStatus(row.status),
            joined_date=row.joined_date,
            base_salary=row.base_salary,
            currency=row.currency,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def list_employees(self, request: ListEmployeesRequest, tenant_id: str) -> list[EmployeeResponse]:
        """List and paginate employees for the authenticated tenant."""

        async def _action(session: AsyncSession) -> list[HREmployeeRow]:
            stmt = select(HREmployeeRow).where(HREmployeeRow.tenant_id == tenant_id)
            if request.status is not None:
                stmt = stmt.where(HREmployeeRow.status == request.status.value)
            if request.department is not None:
                stmt = stmt.where(HREmployeeRow.department == request.department)

            stmt = stmt.order_by(HREmployeeRow.created_at.desc()).offset(request.offset).limit(request.limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [
            EmployeeResponse(
                employee_id=r.id,
                tenant_id=r.tenant_id,
                employee_code=r.employee_code,
                first_name=r.first_name,
                last_name=r.last_name,
                email=r.email,
                department=r.department,
                position=r.position,
                status=EmployeeStatus(r.status),
                joined_date=r.joined_date,
                base_salary=r.base_salary,
                currency=r.currency,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    # -- Attendance Operations ------------------------------------------------

    async def check_in(self, request: CheckInRequest, tenant_id: str) -> AttendanceResponse:
        """Record daily check-in for an employee."""
        # Ensure employee exists under this tenant
        await self.get_employee(request.employee_id, tenant_id)

        work_date = request.work_date or datetime.now(UTC).date()
        check_in_time = request.check_in_time or datetime.now(UTC)
        record_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> HRAttendanceRow:
            # Check unique (tenant_id, employee_id, work_date)
            stmt = select(HRAttendanceRow).where(
                HRAttendanceRow.tenant_id == tenant_id,
                HRAttendanceRow.employee_id == request.employee_id,
                HRAttendanceRow.work_date == work_date,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                raise HRAttendanceConflictError(
                    f"Attendance already recorded for employee '{request.employee_id}' on date '{work_date}'."
                )

            row = HRAttendanceRow(
                id=record_id,
                tenant_id=tenant_id,
                employee_id=request.employee_id,
                work_date=work_date,
                check_in=check_in_time,
                check_out=None,
                total_hours=Decimal("0.00"),
                status=AttendanceStatus.PRESENT.value,
                notes=request.notes,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return AttendanceResponse(
            record_id=row.id,
            tenant_id=row.tenant_id,
            employee_id=row.employee_id,
            work_date=row.work_date,
            check_in=row.check_in,
            check_out=row.check_out,
            total_hours=row.total_hours,
            overtime_hours=Decimal("0.00"),
            status=AttendanceStatus(row.status),
            notes=row.notes,
            created_at=row.created_at,
        )

    async def check_out(self, request: CheckOutRequest, tenant_id: str) -> AttendanceResponse:
        """Record daily check-out, compute worked hours and overtime."""
        await self.get_employee(request.employee_id, tenant_id)

        work_date = request.work_date or datetime.now(UTC).date()
        check_out_time = request.check_out_time or datetime.now(UTC)

        async def _action(session: AsyncSession) -> tuple[HRAttendanceRow, Decimal]:
            stmt = select(HRAttendanceRow).where(
                HRAttendanceRow.tenant_id == tenant_id,
                HRAttendanceRow.employee_id == request.employee_id,
                HRAttendanceRow.work_date == work_date,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise HRAttendanceNotFoundError(
                    f"No active check-in record found for employee '{request.employee_id}' on '{work_date}'."
                )

            check_in_dt = row.check_in.replace(tzinfo=UTC) if row.check_in.tzinfo is None else row.check_in
            check_out_dt = check_out_time.replace(tzinfo=UTC) if check_out_time.tzinfo is None else check_out_time

            if check_out_dt < check_in_dt:
                raise HRAttendanceValidationError(
                    f"check_out ({check_out_dt}) cannot precede check_in ({check_in_dt})."
                )

            total_hours, overtime_hours = calculate_worked_hours(check_in_dt, check_out_dt)
            status = (
                AttendanceStatus.HALF_DAY.value if total_hours < Decimal("4.00") else AttendanceStatus.PRESENT.value
            )

            row.check_out = check_out_dt
            row.total_hours = total_hours
            row.status = status
            if request.notes:
                row.notes = f"{row.notes or ''}\n{request.notes}".strip()
            row.updated_at = datetime.now(UTC)
            return row, overtime_hours

        row, overtime_hours = await self._data_store.execute_in_transaction(_action)

        await self._emit_event(
            "kortex.event.hr.attendance_recorded",
            {
                "tenant_id": tenant_id,
                "employee_id": row.employee_id,
                "work_date": str(row.work_date),
                "total_hours": str(row.total_hours),
                "overtime_hours": str(overtime_hours),
            },
        )

        return AttendanceResponse(
            record_id=row.id,
            tenant_id=row.tenant_id,
            employee_id=row.employee_id,
            work_date=row.work_date,
            check_in=row.check_in,
            check_out=row.check_out,
            total_hours=row.total_hours,
            overtime_hours=overtime_hours,
            status=AttendanceStatus(row.status),
            notes=row.notes,
            created_at=row.created_at,
        )

    async def list_attendance(self, request: ListAttendanceRequest, tenant_id: str) -> list[AttendanceResponse]:
        """Query attendance records within a date interval."""

        async def _action(session: AsyncSession) -> list[HRAttendanceRow]:
            stmt = select(HRAttendanceRow).where(
                HRAttendanceRow.tenant_id == tenant_id,
                HRAttendanceRow.work_date >= request.start_date,
                HRAttendanceRow.work_date <= request.end_date,
            )
            if request.employee_id is not None:
                stmt = stmt.where(HRAttendanceRow.employee_id == request.employee_id)

            stmt = stmt.order_by(HRAttendanceRow.work_date.desc()).offset(request.offset).limit(request.limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        results: list[AttendanceResponse] = []
        for r in rows:
            ot = max(Decimal("0.00"), r.total_hours - Decimal("8.00"))
            results.append(
                AttendanceResponse(
                    record_id=r.id,
                    tenant_id=r.tenant_id,
                    employee_id=r.employee_id,
                    work_date=r.work_date,
                    check_in=r.check_in,
                    check_out=r.check_out,
                    total_hours=r.total_hours,
                    overtime_hours=ot,
                    status=AttendanceStatus(r.status),
                    notes=r.notes,
                    created_at=r.created_at,
                )
            )
        return results

    # -- Leave Operations -----------------------------------------------------

    async def get_leave_balances(self, employee_id: str, year: int, tenant_id: str) -> list[LeaveBalanceResponse]:
        """Retrieve leave balance quotas and consumption for an employee."""
        await self.get_employee(employee_id, tenant_id)

        async def _action(session: AsyncSession) -> list[HRLeaveBalanceRow]:
            stmt = select(HRLeaveBalanceRow).where(
                HRLeaveBalanceRow.tenant_id == tenant_id,
                HRLeaveBalanceRow.employee_id == employee_id,
                HRLeaveBalanceRow.year == year,
            )
            return list((await session.execute(stmt)).scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [
            LeaveBalanceResponse(
                balance_id=r.id,
                tenant_id=r.tenant_id,
                employee_id=r.employee_id,
                leave_type=LeaveType(r.leave_type),
                year=r.year,
                allocated_days=r.allocated_days,
                used_days=r.used_days,
                available_days=max(Decimal("0.0"), r.allocated_days - r.used_days),
            )
            for r in rows
        ]

    async def apply_leave(self, request: ApplyLeaveRequest, tenant_id: str) -> LeaveRequestResponse:
        """Submit a leave application with balance and overlap verification."""
        await self.get_employee(request.employee_id, tenant_id)
        days_count = Decimal(str((request.end_date - request.start_date).days + 1))
        leave_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> HRLeaveRequestRow:
            # Check overlap against existing PENDING or APPROVED requests
            stmt_overlap = select(HRLeaveRequestRow).where(
                HRLeaveRequestRow.tenant_id == tenant_id,
                HRLeaveRequestRow.employee_id == request.employee_id,
                HRLeaveRequestRow.status.in_([LeaveStatus.PENDING.value, LeaveStatus.APPROVED.value]),
                HRLeaveRequestRow.start_date <= request.end_date,
                HRLeaveRequestRow.end_date >= request.start_date,
            )
            overlap = (await session.execute(stmt_overlap)).scalar_one_or_none()
            if overlap is not None:
                raise HRLeaveOverlapError(
                    f"Leave request overlaps with existing {overlap.status} request '{overlap.id}' "
                    f"({overlap.start_date} to {overlap.end_date})."
                )

            # If paid leave (ANNUAL, CASUAL), verify balance
            if request.leave_type in (LeaveType.ANNUAL, LeaveType.CASUAL):
                stmt_bal = select(HRLeaveBalanceRow).where(
                    HRLeaveBalanceRow.tenant_id == tenant_id,
                    HRLeaveBalanceRow.employee_id == request.employee_id,
                    HRLeaveBalanceRow.leave_type == request.leave_type.value,
                    HRLeaveBalanceRow.year == request.start_date.year,
                )
                bal = (await session.execute(stmt_bal)).scalar_one_or_none()
                if bal is None or (bal.used_days + days_count > bal.allocated_days):
                    allocated = bal.allocated_days if bal else Decimal("0.0")
                    used = bal.used_days if bal else Decimal("0.0")
                    raise HRLeaveBalanceExceededError(
                        f"Insufficient {request.leave_type.value} leave balance: requested {days_count} days, "
                        f"available {allocated - used} days (allocated: {allocated}, used: {used})."
                    )

            row = HRLeaveRequestRow(
                id=leave_id,
                tenant_id=tenant_id,
                employee_id=request.employee_id,
                leave_type=request.leave_type.value,
                start_date=request.start_date,
                end_date=request.end_date,
                days_count=days_count,
                reason=request.reason,
                status=LeaveStatus.PENDING.value,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return LeaveRequestResponse(
            leave_id=row.id,
            tenant_id=row.tenant_id,
            employee_id=row.employee_id,
            leave_type=LeaveType(row.leave_type),
            start_date=row.start_date,
            end_date=row.end_date,
            days_count=row.days_count,
            reason=row.reason,
            status=LeaveStatus(row.status),
            decision_reason=row.decision_reason,
            decided_at=row.decided_at,
            decided_by=row.decided_by,
            created_at=row.created_at,
        )

    async def decide_leave(
        self,
        request: DecideLeaveRequest,
        decider_principal_id: str,
        tenant_id: str,
    ) -> LeaveRequestResponse:
        """Approve or reject a pending leave application."""
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> HRLeaveRequestRow:
            stmt = select(HRLeaveRequestRow).where(
                HRLeaveRequestRow.id == request.leave_id,
                HRLeaveRequestRow.tenant_id == tenant_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise HRLeaveNotFoundError(f"Leave request '{request.leave_id}' not found.")

            if row.status != LeaveStatus.PENDING.value:
                raise HRLeaveValidationError(
                    f"Leave request '{request.leave_id}' is already {row.status}; cannot decide."
                )

            if request.decision == "APPROVE":
                row.status = LeaveStatus.APPROVED.value
                row.decided_at = now
                row.decided_by = decider_principal_id
                row.decision_reason = request.decision_reason
                row.updated_at = now

                # Increment used_days on the corresponding balance
                stmt_bal = select(HRLeaveBalanceRow).where(
                    HRLeaveBalanceRow.tenant_id == tenant_id,
                    HRLeaveBalanceRow.employee_id == row.employee_id,
                    HRLeaveBalanceRow.leave_type == row.leave_type,
                    HRLeaveBalanceRow.year == row.start_date.year,
                )
                bal = (await session.execute(stmt_bal)).scalar_one_or_none()
                if bal is not None:
                    bal.used_days += row.days_count
                    bal.updated_at = now
            else:
                row.status = LeaveStatus.REJECTED.value
                row.decided_at = now
                row.decided_by = decider_principal_id
                row.decision_reason = request.decision_reason
                row.updated_at = now

            return row

        row = await self._data_store.execute_in_transaction(_action)

        await self._emit_event(
            "kortex.event.hr.leave_decided",
            {
                "tenant_id": tenant_id,
                "employee_id": row.employee_id,
                "leave_id": row.id,
                "status": row.status,
                "days_count": str(row.days_count),
            },
        )

        return LeaveRequestResponse(
            leave_id=row.id,
            tenant_id=row.tenant_id,
            employee_id=row.employee_id,
            leave_type=LeaveType(row.leave_type),
            start_date=row.start_date,
            end_date=row.end_date,
            days_count=row.days_count,
            reason=row.reason,
            status=LeaveStatus(row.status),
            decision_reason=row.decision_reason,
            decided_at=row.decided_at,
            decided_by=row.decided_by,
            created_at=row.created_at,
        )

    # -- Payroll Operations ---------------------------------------------------

    async def calculate_payroll(
        self,
        request: CalculatePayrollRequest,
        tenant_id: str,
    ) -> PayrollRunResponse:
        """Calculate and persist monthly payroll entries across active employees."""
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> tuple[HRPayrollRunRow, list[HRPayrollEntryRow]]:
            # Check existing run for period
            stmt_run = select(HRPayrollRunRow).where(
                HRPayrollRunRow.tenant_id == tenant_id,
                HRPayrollRunRow.period_start == request.period_start,
                HRPayrollRunRow.period_end == request.period_end,
            )
            existing_run = (await session.execute(stmt_run)).scalar_one_or_none()
            if existing_run is not None:
                if existing_run.status == PayrollRunStatus.FINALIZED.value:
                    raise HRPayrollRunAlreadyFinalizedError(
                        f"Payroll run for period {request.period_start} to {request.period_end} is already FINALIZED."
                    )
                # Recalculating DRAFT/CALCULATED run: delete old entries and old run
                await session.execute(
                    delete(HRPayrollEntryRow).where(HRPayrollEntryRow.payroll_run_id == existing_run.id)
                )
                await session.delete(existing_run)

            # Query all active employees
            stmt_emp = select(HREmployeeRow).where(
                HREmployeeRow.tenant_id == tenant_id,
                HREmployeeRow.status == EmployeeStatus.ACTIVE.value,
            )
            employees = list((await session.execute(stmt_emp)).scalars().all())

            entry_rows: list[HRPayrollEntryRow] = []
            total_gross = Decimal("0.00")
            total_deductions = Decimal("0.00")
            total_net = Decimal("0.00")

            currency = "USD"
            if employees:
                currency = employees[0].currency

            for emp in employees:
                # Query attendance records in period
                stmt_att = select(HRAttendanceRow).where(
                    HRAttendanceRow.tenant_id == tenant_id,
                    HRAttendanceRow.employee_id == emp.id,
                    HRAttendanceRow.work_date >= request.period_start,
                    HRAttendanceRow.work_date <= request.period_end,
                )
                att_records = list((await session.execute(stmt_att)).scalars().all())

                worked_days = 0
                overtime_hours = Decimal("0.00")
                for att in att_records:
                    if att.total_hours > 0:
                        worked_days += 1
                        overtime_hours += max(Decimal("0.00"), att.total_hours - Decimal("8.00"))

                # Query approved unpaid leave days overlapping period
                stmt_leave = select(HRLeaveRequestRow).where(
                    HRLeaveRequestRow.tenant_id == tenant_id,
                    HRLeaveRequestRow.employee_id == emp.id,
                    HRLeaveRequestRow.leave_type == LeaveType.UNPAID.value,
                    HRLeaveRequestRow.status == LeaveStatus.APPROVED.value,
                    HRLeaveRequestRow.start_date <= request.period_end,
                    HRLeaveRequestRow.end_date >= request.period_start,
                )
                leave_records = list((await session.execute(stmt_leave)).scalars().all())

                unpaid_leave_days = Decimal("0.0")
                for lr in leave_records:
                    # Clip to period boundaries
                    overlap_start = max(lr.start_date, request.period_start)
                    overlap_end = min(lr.end_date, request.period_end)
                    if overlap_end >= overlap_start:
                        unpaid_leave_days += Decimal(str((overlap_end - overlap_start).days + 1))

                calc = calculate_payroll_entry(
                    base_salary=emp.base_salary,
                    worked_days=worked_days,
                    unpaid_leave_days=unpaid_leave_days,
                    overtime_hours=overtime_hours,
                )

                details = {
                    "employee_code": emp.employee_code,
                    "employee_name": f"{emp.first_name} {emp.last_name}",
                    "daily_rate": str(calc["daily_rate"]),
                    "hourly_rate": str(calc["hourly_rate"]),
                    "overtime_hours": str(overtime_hours),
                    "overtime_pay": str(calc["overtime_pay"]),
                    "unpaid_leave_days": str(unpaid_leave_days),
                    "unpaid_leave_deduction": str(calc["unpaid_leave_deduction"]),
                    "allowances": "0.00",
                    "deductions": "0.00",
                }

                entry = HRPayrollEntryRow(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    payroll_run_id=run_id,
                    employee_id=emp.id,
                    base_salary=emp.base_salary,
                    worked_days=worked_days,
                    unpaid_leave_days=unpaid_leave_days,
                    overtime_hours=overtime_hours,
                    overtime_pay=calc["overtime_pay"],
                    allowances=Decimal("0.00"),
                    deductions=Decimal("0.00"),
                    gross_salary=calc["gross_salary"],
                    total_deductions=calc["total_deductions"],
                    net_salary=calc["net_salary"],
                    currency=emp.currency,
                    details_json=json.dumps(details),
                    created_at=now,
                    updated_at=now,
                )
                entry_rows.append(entry)

                total_gross += calc["gross_salary"]
                total_deductions += calc["total_deductions"]
                total_net += calc["net_salary"]

            status = PayrollRunStatus.FINALIZED.value if request.finalize else PayrollRunStatus.CALCULATED.value
            finalized_at = now if request.finalize else None

            run_row = HRPayrollRunRow(
                id=run_id,
                tenant_id=tenant_id,
                period_start=request.period_start,
                period_end=request.period_end,
                currency=currency,
                total_gross=round_money(total_gross),
                total_deductions=round_money(total_deductions),
                total_net=round_money(total_net),
                employee_count=len(employees),
                status=status,
                finalized_at=finalized_at,
                created_at=now,
                updated_at=now,
            )
            session.add(run_row)
            session.add_all(entry_rows)
            return run_row, entry_rows

        run_row, entry_rows = await self._data_store.execute_in_transaction(_action)

        if run_row.status == PayrollRunStatus.FINALIZED.value:
            await self._emit_event(
                "kortex.event.payroll.run_finalized",
                {
                    "tenant_id": tenant_id,
                    "run_id": run_row.id,
                    "period_start": str(run_row.period_start),
                    "period_end": str(run_row.period_end),
                    "total_gross": str(run_row.total_gross),
                    "total_deductions": str(run_row.total_deductions),
                    "total_net": str(run_row.total_net),
                    "currency": run_row.currency,
                    "employee_count": run_row.employee_count,
                },
            )

        entries_response = [
            PayrollEntryResponse(
                entry_id=e.id,
                tenant_id=e.tenant_id,
                payroll_run_id=e.payroll_run_id,
                employee_id=e.employee_id,
                employee_code=json.loads(e.details_json or "{}").get("employee_code", ""),
                employee_name=json.loads(e.details_json or "{}").get("employee_name", ""),
                base_salary=e.base_salary,
                worked_days=e.worked_days,
                unpaid_leave_days=e.unpaid_leave_days,
                overtime_hours=e.overtime_hours,
                overtime_pay=e.overtime_pay,
                allowances=e.allowances,
                deductions=e.deductions,
                unpaid_leave_deduction=Decimal(
                    json.loads(e.details_json or "{}").get("unpaid_leave_deduction", "0.00")
                ),
                gross_salary=e.gross_salary,
                total_deductions=e.total_deductions,
                net_salary=e.net_salary,
                currency=e.currency,
                details_json=e.details_json,
            )
            for e in entry_rows
        ]

        return PayrollRunResponse(
            run_id=run_row.id,
            tenant_id=run_row.tenant_id,
            period_start=run_row.period_start,
            period_end=run_row.period_end,
            currency=run_row.currency,
            total_gross=run_row.total_gross,
            total_deductions=run_row.total_deductions,
            total_net=run_row.total_net,
            employee_count=run_row.employee_count,
            status=PayrollRunStatus(run_row.status),
            finalized_at=run_row.finalized_at,
            entries=entries_response,
            created_at=run_row.created_at,
        )

    async def get_payroll_run(self, run_id: str, tenant_id: str) -> PayrollRunResponse:
        """Retrieve one payroll run by ID with itemized entries."""

        async def _action(session: AsyncSession) -> tuple[HRPayrollRunRow | None, list[HRPayrollEntryRow]]:
            stmt = select(HRPayrollRunRow).where(
                HRPayrollRunRow.id == run_id,
                HRPayrollRunRow.tenant_id == tenant_id,
            )
            run = (await session.execute(stmt)).scalar_one_or_none()
            if run is None:
                return None, []

            stmt_entries = select(HRPayrollEntryRow).where(
                HRPayrollEntryRow.payroll_run_id == run_id,
                HRPayrollEntryRow.tenant_id == tenant_id,
            )
            entries = list((await session.execute(stmt_entries)).scalars().all())
            return run, entries

        run, entries = await self._data_store.execute_in_transaction(_action)
        if run is None:
            raise HRPayrollRunNotFoundError(f"Payroll run '{run_id}' not found.")

        entries_response = [
            PayrollEntryResponse(
                entry_id=e.id,
                tenant_id=e.tenant_id,
                payroll_run_id=e.payroll_run_id,
                employee_id=e.employee_id,
                employee_code=json.loads(e.details_json or "{}").get("employee_code", ""),
                employee_name=json.loads(e.details_json or "{}").get("employee_name", ""),
                base_salary=e.base_salary,
                worked_days=e.worked_days,
                unpaid_leave_days=e.unpaid_leave_days,
                overtime_hours=e.overtime_hours,
                overtime_pay=e.overtime_pay,
                allowances=e.allowances,
                deductions=e.deductions,
                unpaid_leave_deduction=Decimal(
                    json.loads(e.details_json or "{}").get("unpaid_leave_deduction", "0.00")
                ),
                gross_salary=e.gross_salary,
                total_deductions=e.total_deductions,
                net_salary=e.net_salary,
                currency=e.currency,
                details_json=e.details_json,
            )
            for e in entries
        ]

        return PayrollRunResponse(
            run_id=run.id,
            tenant_id=run.tenant_id,
            period_start=run.period_start,
            period_end=run.period_end,
            currency=run.currency,
            total_gross=run.total_gross,
            total_deductions=run.total_deductions,
            total_net=run.total_net,
            employee_count=run.employee_count,
            status=PayrollRunStatus(run.status),
            finalized_at=run.finalized_at,
            entries=entries_response,
            created_at=run.created_at,
        )

    async def get_payslip(self, run_id: str, employee_id: str, tenant_id: str) -> PayslipResponse:
        """Project an individual employee payslip from a calculated payroll run."""

        async def _action(
            session: AsyncSession,
        ) -> tuple[HRPayrollRunRow | None, HRPayrollEntryRow | None, HREmployeeRow | None]:
            stmt_run = select(HRPayrollRunRow).where(
                HRPayrollRunRow.id == run_id,
                HRPayrollRunRow.tenant_id == tenant_id,
            )
            run = (await session.execute(stmt_run)).scalar_one_or_none()

            stmt_entry = select(HRPayrollEntryRow).where(
                HRPayrollEntryRow.payroll_run_id == run_id,
                HRPayrollEntryRow.employee_id == employee_id,
                HRPayrollEntryRow.tenant_id == tenant_id,
            )
            entry = (await session.execute(stmt_entry)).scalar_one_or_none()

            stmt_emp = select(HREmployeeRow).where(
                HREmployeeRow.id == employee_id,
                HREmployeeRow.tenant_id == tenant_id,
            )
            emp = (await session.execute(stmt_emp)).scalar_one_or_none()

            return run, entry, emp

        run, entry, emp = await self._data_store.execute_in_transaction(_action)
        if run is None or entry is None or emp is None:
            raise HRPayrollRunNotFoundError(f"Payslip not found for run '{run_id}' and employee '{employee_id}'.")

        details = json.loads(entry.details_json or "{}")
        unpaid_deduct = Decimal(details.get("unpaid_leave_deduction", "0.00"))

        return PayslipResponse(
            payslip_id=entry.id,
            tenant_id=tenant_id,
            run_id=run.id,
            period_start=run.period_start,
            period_end=run.period_end,
            employee_id=emp.id,
            employee_code=emp.employee_code,
            employee_name=f"{emp.first_name} {emp.last_name}",
            department=emp.department,
            position=emp.position,
            base_salary=entry.base_salary,
            worked_days=entry.worked_days,
            unpaid_leave_days=entry.unpaid_leave_days,
            unpaid_leave_deduction=unpaid_deduct,
            overtime_hours=entry.overtime_hours,
            overtime_pay=entry.overtime_pay,
            allowances=entry.allowances,
            deductions=entry.deductions,
            gross_salary=entry.gross_salary,
            total_deductions=entry.total_deductions,
            net_salary=entry.net_salary,
            currency=entry.currency,
            generated_at=datetime.now(UTC),
        )


__all__ = ["HRPayrollManager"]
