"""KORTEX HR & Payroll Business Module Facade (`HRPayrollModule`).

Implements Phase 6 canonical business capabilities for:
- Employee Management
- Attendance Tracking & Overtime
- Leave Management & Balances
- Monthly Payroll Runs & Payslip Data

Inherits from `kortex.core.base_module.BaseModule` and participates in the
established platform module lifecycle.
All capability handlers declare `execution_context: CapabilityExecutionContext`
and register with `requires_execution_context=True`, guaranteeing strict
tenant isolation and authenticated identity propagation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.core.base_module import BaseModule, ModuleState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import KortexError
from kortex.engines.event.engine import EventEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.hr_payroll.manager import HRPayrollManager
from kortex.modules.hr_payroll.models import (
    ApplyLeaveRequest,
    AttendanceResponse,
    CalculatePayrollRequest,
    CheckInRequest,
    CheckOutRequest,
    CreateEmployeeRequest,
    DecideLeaveRequest,
    EmployeeResponse,
    LeaveBalanceResponse,
    LeaveRequestResponse,
    ListAttendanceRequest,
    ListEmployeesRequest,
    PayrollRunResponse,
    PayslipResponse,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

_REGISTERED_CAPABILITIES: list[str] = [
    "kortex.hr_payroll.employee.create",
    "kortex.hr_payroll.employee.get",
    "kortex.hr_payroll.employee.list",
    "kortex.hr_payroll.attendance.check_in",
    "kortex.hr_payroll.attendance.check_out",
    "kortex.hr_payroll.attendance.list",
    "kortex.hr_payroll.leave.balance_get",
    "kortex.hr_payroll.leave.request",
    "kortex.hr_payroll.leave.decide",
    "kortex.hr_payroll.payroll.calculate",
    "kortex.hr_payroll.payroll.run_get",
    "kortex.hr_payroll.payslip.get",
]


class HRPayrollModule(BaseModule):
    """KORTEX HR & Payroll Business Module Facade."""

    def __init__(self) -> None:
        super().__init__()
        self._manager: HRPayrollManager | None = None

    @property
    def name(self) -> str:
        return "hr_payroll"

    @property
    def namespace(self) -> str:
        return "kortex.hr_payroll"

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security"]

    # -- Lifecycle Implementation ---------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Resolve storage and security engines and register capabilities."""
        self.ensure_state(ModuleState.UNINITIALIZED)
        self._set_state(ModuleState.INITIALIZING)
        self.logger.info("Initializing KORTEX HR & Payroll Module...")

        try:
            storage_engine = kernel.get_engine("storage")
            data_store: IDataStore | None = getattr(storage_engine, "data", None)
            if data_store is None:
                raise KortexError("Storage Engine did not provide an IDataStore instance.")

            event_engine: EventEngine | None = None
            try:
                ev = kernel.get_engine("event")
                if isinstance(ev, EventEngine):
                    event_engine = ev
            except Exception:
                self.logger.debug("Event engine not available; events will not be emitted.")

            self._manager = HRPayrollManager(data_store=data_store, event_engine=event_engine)

            # 1. Employee capabilities
            kernel.register_capability(
                name="kortex.hr_payroll.employee.create",
                description="Register a new employee master profile.",
                provider=self.name,
                handler=self.create_employee,
                requires_authentication=True,
                required_permissions=["hr:employee:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.employee.get",
                description="Retrieve an employee profile by ID.",
                provider=self.name,
                handler=self.get_employee,
                requires_authentication=True,
                required_permissions=["hr:employee:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.employee.list",
                description="List and paginate employee profiles.",
                provider=self.name,
                handler=self.list_employees,
                requires_authentication=True,
                required_permissions=["hr:employee:read"],
                requires_execution_context=True,
            )

            # 2. Attendance capabilities
            kernel.register_capability(
                name="kortex.hr_payroll.attendance.check_in",
                description="Record daily check-in for an employee.",
                provider=self.name,
                handler=self.check_in,
                requires_authentication=True,
                required_permissions=["hr:attendance:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.attendance.check_out",
                description="Record daily check-out and calculate hours worked.",
                provider=self.name,
                handler=self.check_out,
                requires_authentication=True,
                required_permissions=["hr:attendance:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.attendance.list",
                description="Query attendance records by date range.",
                provider=self.name,
                handler=self.list_attendance,
                requires_authentication=True,
                required_permissions=["hr:attendance:read"],
                requires_execution_context=True,
            )

            # 3. Leave capabilities
            kernel.register_capability(
                name="kortex.hr_payroll.leave.balance_get",
                description="Retrieve leave quota balances for an employee.",
                provider=self.name,
                handler=self.get_leave_balances,
                requires_authentication=True,
                required_permissions=["hr:leave:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.leave.request",
                description="Submit an employee leave application.",
                provider=self.name,
                handler=self.apply_leave,
                requires_authentication=True,
                required_permissions=["hr:leave:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.leave.decide",
                description="Approve or reject a pending leave application.",
                provider=self.name,
                handler=self.decide_leave,
                requires_authentication=True,
                required_permissions=["hr:leave:approve"],
                requires_execution_context=True,
            )

            # 4. Payroll capabilities
            kernel.register_capability(
                name="kortex.hr_payroll.payroll.calculate",
                description="Calculate and persist monthly payroll run.",
                provider=self.name,
                handler=self.calculate_payroll,
                requires_authentication=True,
                required_permissions=["payroll:run:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.payroll.run_get",
                description="Retrieve payroll run summary and itemized entries.",
                provider=self.name,
                handler=self.get_payroll_run,
                requires_authentication=True,
                required_permissions=["payroll:run:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.hr_payroll.payslip.get",
                description="Retrieve individual employee payslip.",
                provider=self.name,
                handler=self.get_payslip,
                requires_authentication=True,
                required_permissions=["payroll:payslip:read"],
                requires_execution_context=True,
            )

            self._set_state(ModuleState.ACTIVE)
            self.logger.info("HR & Payroll Module initialized successfully.")
        except Exception as exc:
            self._set_state(ModuleState.FAILED)
            self.logger.error("Failed to initialize HR & Payroll Module: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)

    async def stop(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)
        self._set_state(ModuleState.STOPPING)
        self._set_state(ModuleState.STOPPED)
        self.logger.info("HR & Payroll Module stopped.")

    async def health_check(self) -> dict[str, Any]:
        return {
            "module": self.name,
            "status": "healthy" if self._state == ModuleState.ACTIVE else "unhealthy",
            "state": self._state.value,
        }

    def capabilities(self) -> list[str]:
        """Return list of capability names registered by this module."""
        return list(_REGISTERED_CAPABILITIES)

    def metrics(self) -> dict[str, Any]:
        return {}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "capabilities": self.capabilities(),
        }

    # -- Capability Handlers --------------------------------------------------

    def _require_principal(self, execution_context: CapabilityExecutionContext, cap_name: str) -> None:
        if execution_context.principal is None:
            raise KortexError(f"{cap_name} requires a verified principal; none was provided.")

    async def create_employee(
        self,
        request: CreateEmployeeRequest,
        execution_context: CapabilityExecutionContext,
    ) -> EmployeeResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.employee.create")
        assert self._manager is not None
        return await self._manager.create_employee(request, tenant_id=execution_context.tenant_id)

    async def get_employee(
        self,
        employee_id: str,
        execution_context: CapabilityExecutionContext,
    ) -> EmployeeResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.employee.get")
        assert self._manager is not None
        return await self._manager.get_employee(employee_id, tenant_id=execution_context.tenant_id)

    async def list_employees(
        self,
        request: ListEmployeesRequest,
        execution_context: CapabilityExecutionContext,
    ) -> list[EmployeeResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.employee.list")
        assert self._manager is not None
        return await self._manager.list_employees(request, tenant_id=execution_context.tenant_id)

    async def check_in(
        self,
        request: CheckInRequest,
        execution_context: CapabilityExecutionContext,
    ) -> AttendanceResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.attendance.check_in")
        assert self._manager is not None
        return await self._manager.check_in(request, tenant_id=execution_context.tenant_id)

    async def check_out(
        self,
        request: CheckOutRequest,
        execution_context: CapabilityExecutionContext,
    ) -> AttendanceResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.attendance.check_out")
        assert self._manager is not None
        return await self._manager.check_out(request, tenant_id=execution_context.tenant_id)

    async def list_attendance(
        self,
        request: ListAttendanceRequest,
        execution_context: CapabilityExecutionContext,
    ) -> list[AttendanceResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.attendance.list")
        assert self._manager is not None
        return await self._manager.list_attendance(request, tenant_id=execution_context.tenant_id)

    async def get_leave_balances(
        self,
        employee_id: str,
        year: int,
        execution_context: CapabilityExecutionContext,
    ) -> list[LeaveBalanceResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.leave.balance_get")
        assert self._manager is not None
        return await self._manager.get_leave_balances(employee_id, year=year, tenant_id=execution_context.tenant_id)

    async def apply_leave(
        self,
        request: ApplyLeaveRequest,
        execution_context: CapabilityExecutionContext,
    ) -> LeaveRequestResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.leave.request")
        assert self._manager is not None
        return await self._manager.apply_leave(request, tenant_id=execution_context.tenant_id)

    async def decide_leave(
        self,
        request: DecideLeaveRequest,
        execution_context: CapabilityExecutionContext,
    ) -> LeaveRequestResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.leave.decide")
        assert self._manager is not None
        decider_id = execution_context.principal.principal_id if execution_context.principal else "unknown"
        return await self._manager.decide_leave(
            request,
            decider_principal_id=decider_id,
            tenant_id=execution_context.tenant_id,
        )

    async def calculate_payroll(
        self,
        request: CalculatePayrollRequest,
        execution_context: CapabilityExecutionContext,
    ) -> PayrollRunResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.payroll.calculate")
        assert self._manager is not None
        return await self._manager.calculate_payroll(request, tenant_id=execution_context.tenant_id)

    async def get_payroll_run(
        self,
        run_id: str,
        execution_context: CapabilityExecutionContext,
    ) -> PayrollRunResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.payroll.run_get")
        assert self._manager is not None
        return await self._manager.get_payroll_run(run_id, tenant_id=execution_context.tenant_id)

    async def get_payslip(
        self,
        run_id: str,
        employee_id: str,
        execution_context: CapabilityExecutionContext,
    ) -> PayslipResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.hr_payroll.payslip.get")
        assert self._manager is not None
        return await self._manager.get_payslip(run_id, employee_id=employee_id, tenant_id=execution_context.tenant_id)


__all__ = ["HRPayrollModule"]
