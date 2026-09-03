# KORTEX OS — Phase 6 HR & Payroll Module Pilot Implementation Report

**Document Version**: 1.0.0  
**Phase**: Phase 6 — Pilot Business Modules  
**Module**: `kortex.modules.hr_payroll`  
**Status**: Implementation Complete  

---

## 1. Executive Summary

The HR & Payroll business module represents the second canonical pilot business module within KORTEX OS, following `FinanceModule`. It demonstrates the full workforce management vertical slice:
- Employee master profiles and pagination
- Daily attendance tracking, worked hours, and 1.5x overtime derivation
- Leave quota allocation, leave application, overlap prevention, and manager approval
- Monthly payroll batch calculation with exact Decimal monetary arithmetic, 30-day divisor, unpaid leave deductions, gross/deduction/net calculation
- Payslip data projection
- Immutable finalized payroll state
- Asynchronous domain event publication (`kortex.event.payroll.run_finalized`)
- Kernel production boot wiring reaching `ModuleState.ACTIVE`
- Relational persistence across 6 tenant-scoped tables with Alembic upgrade/downgrade migration parity
- Security hardening using `CapabilityExecutionContext` and fail-closed tenant scoping

---

## 2. Domain Boundaries and Policies

### Workforce Entities
- **Employee**: Master workforce record holding personal identification, department, position, status, joined date, base salary, and currency.
- **Attendance**: Daily check-in and check-out logs tracking exact timestamp intervals, duration in hours, and overtime hours (> 8.00 hours per day).
- **Leave**: Leave categories (`ANNUAL`, `SICK`, `CASUAL`, `UNPAID`). Tracks annual allocation and consumption per employee and calendar year. Paid leaves enforce balance availability. Overlapping pending/approved requests are rejected.
- **Payroll**: Monthly batch calculation covering active employees. Itemized compensation entries calculate daily rate, hourly rate, overtime earnings, unpaid leave deductions, gross compensation, and net salary.

### Pure Mathematical Compensation Policies
All calculations utilize Python `Decimal` arithmetic rounded to 2 decimal places using `ROUND_HALF_UP`:
1. **Daily Rate Divisor**: 30 Days  
   $$\text{daily\_rate} = \text{round}\left(\frac{\text{base\_salary}}{30}, 2\right)$$
2. **Standard Work Day**: 8 Hours  
   $$\text{hourly\_rate} = \text{round}\left(\frac{\text{daily\_rate}}{8}, 2\right)$$
3. **Overtime Multiplier**: 1.5x  
   $$\text{overtime\_pay} = \text{round}(\text{overtime\_hours} \times \text{hourly\_rate} \times 1.5, 2)$$
4. **Unpaid Leave Deduction**:  
   $$\text{unpaid\_leave\_deduction} = \text{round}(\text{unpaid\_leave\_days} \times \text{daily\_rate}, 2)$$
5. **Gross & Net Salary**:  
   $$\text{gross\_salary} = \text{base\_salary} + \text{overtime\_pay} + \text{allowances}$$  
   $$\text{total\_deductions} = \text{unpaid\_leave\_deduction} + \text{deductions}$$  
   $$\text{net\_salary} = \text{gross\_salary} - \text{total\_deductions}$$

---

## 3. Module Capabilities

The module registers 12 public capabilities under the canonical namespace `kortex.hr_payroll`:

| Capability Name | Required Permission | Description |
|---|---|---|
| `kortex.hr_payroll.employee.create` | `hr:employee:write` | Register new employee master record and seed leave balances |
| `kortex.hr_payroll.employee.get` | `hr:employee:read` | Retrieve employee profile by ID |
| `kortex.hr_payroll.employee.list` | `hr:employee:read` | Filter and paginate employee profiles |
| `kortex.hr_payroll.attendance.check_in` | `hr:attendance:write` | Record daily check-in |
| `kortex.hr_payroll.attendance.check_out` | `hr:attendance:write` | Record daily check-out and derive hours worked |
| `kortex.hr_payroll.attendance.list` | `hr:attendance:read` | Query attendance records by date range |
| `kortex.hr_payroll.leave.balance_get` | `hr:leave:read` | Retrieve employee leave balances per year |
| `kortex.hr_payroll.leave.request` | `hr:leave:write` | Submit leave application with balance and overlap checks |
| `kortex.hr_payroll.leave.decide` | `hr:leave:approve` | Approve or reject pending leave application |
| `kortex.hr_payroll.payroll.calculate` | `payroll:run:write` | Calculate and persist monthly payroll run |
| `kortex.hr_payroll.payroll.run_get` | `payroll:run:read` | Retrieve payroll run summary and itemized entries |
| `kortex.hr_payroll.payslip.get` | `payroll:payslip:read` | Retrieve individual employee payslip data |

---

## 4. Security & Tenant Isolation

1. **Hardened Identity Injection**: All 12 capability descriptors declare `requires_execution_context=True`. Handlers accept `execution_context: CapabilityExecutionContext`.
2. **Authoritative Identity**: The authenticated `execution_context.tenant_id` is the single source of truth. Caller request models contain no `tenant_id` parameter.
3. **Reserved Parameter Rejection**: Attempts to pass reserved keys (`principal`, `execution_context`) in `request.parameters` are rejected with `ReservedParameterError`.
4. **Enumeration Resistance**: Cross-tenant record lookups fail closed by raising domain `NotFoundError` (`HREmployeeNotFoundError`, `HRAttendanceNotFoundError`, `HRLeaveNotFoundError`, `HRPayrollRunNotFoundError`), preventing attackers from inferring entity existence across tenants.

---

## 5. Persistence & Migration Architecture

### Six Canonical Tables
1. `hr_employees`: Master workforce records (UQ: `(tenant_id, employee_code)`).
2. `hr_attendance_records`: Daily attendance records (UQ: `(tenant_id, employee_id, work_date)`).
3. `hr_leave_balances`: Leave quotas and consumption (UQ: `(tenant_id, employee_id, leave_type, year)`).
4. `hr_leave_requests`: Leave applications and decision status.
5. `hr_payroll_runs`: Batch run totals and status (UQ: `(tenant_id, period_start, period_end)`).
6. `hr_payroll_entries`: Itemized employee compensation records (UQ: `(payroll_run_id, employee_id)`).

### Alembic Migration
Migration revision `c7d8e9f1a2b3` chains off `b4e89f123c5a`:
- Upgrades cleanly from baseline creating all 6 tables, indexes, and unique constraints.
- Downgrades cleanly, restoring the pre-HR database schema.
- Verified with 100% schema parity against `Base.metadata.create_all()`.

---

## 6. Asynchronous Event Integration

Upon payroll finalization, the module publishes the canonical domain event to the `EventEngine`:
- **Topic**: `kortex.event.payroll.run_finalized`
- **Sender**: `hr_payroll`
- **Payload**:
  - `tenant_id`: Authoritative tenant ID
  - `run_id`: Finalized payroll run ID
  - `period_start`: Start date string
  - `period_end`: End date string
  - `total_gross`: Aggregate gross amount
  - `total_deductions`: Aggregate deduction amount
  - `total_net`: Aggregate net compensation amount
  - `currency`: Currency code
  - `employee_count`: Total active employees in run

---

## 7. Finance Boundary Discipline

Zero direct Python imports exist between `kortex.modules.hr_payroll` and `kortex.modules.finance`.
- HR & Payroll owns workforce compensation, attendance, leave, and payroll.
- Finance owns commercial billing, customer invoices, and receivables.
- The `kortex.event.payroll.run_finalized` event serves as the sole asynchronous integration boundary.
- General Ledger journal entries and Salary Sheet integrations remain deferred.

---

## 8. Explicitly Deferred Capabilities

Consistent with the ratified Phase 6 boundary, the following capabilities remain deferred:
- Recruitment & Applicant Tracking
- Biometric hardware integration & physical device drivers
- Statutory multi-jurisdiction tax withholding engines
- External banking & wire transfer protocols
- Dynamic marketplace packaging (`.kortex-module`)
- Phase 7 production hardening (Sentinel, Prometheus metrics)
