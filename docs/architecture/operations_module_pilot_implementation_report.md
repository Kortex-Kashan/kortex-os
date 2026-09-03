# KORTEX OS — Phase 6 Operations Module Pilot Implementation Report

**Document Version**: 1.0.0  
**Phase**: Phase 6 — Pilot Business Modules  
**Module**: `kortex.modules.operations`  
**Status**: Implementation Complete  

---

## 1. Executive Summary

The Operations business module represents the third canonical pilot business module within KORTEX OS, following `FinanceModule` and `HRPayrollModule`. It delivers the fleet operations vertical slice comprising two core domains:
- **Vehicle Tracking**: Fleet vehicle master registry, driver assignment/unassignment, lifecycle state management, point-in-time odometer and location tracking records, strict monotonic odometer enforcement, and reverse-chronological tracking history.
- **Incident Management**: Operational incident reporting, classification, severity scoring, driver/vehicle linkage, tenant-scoped sequential incident numbering (`INC-{YYYY}-{seq:04d}`), investigation resolution records, and formal immutable terminal closure.

Key architectural and platform highlights:
- **Clean Architecture & Decoupling**: Business logic resides strictly in `kortex.modules.operations`. Zero imports from `hr_payroll` or `finance`. Driver IDs and actor IDs are opaque strings (`String(64)`).
- **Security & Identity Invariant**: $\text{AUTHENTICATED IDENTITY} \equiv \text{EXECUTION IDENTITY}$. Request models strictly omit caller-controlled `tenant_id`, `principal`, and `execution_context`. Context is derived exclusively by the dispatcher and passed via `CapabilityExecutionContext`.
- **Concurrency & Monotonicity**: Concurrency-safe incident numbering with bounded collision retry. Strict monotonic odometer progression ($O_{\text{new}} \ge O_{\text{current}}$) updating tracking logs and vehicle master atomically in a single transaction.
- **Terminal Immutability**: Closed incidents are terminal and sealed; mutation or re-closure attempts raise `OpsIncidentAlreadyClosedError`.
- **Relational Persistence & Migration Parity**: 3 tenant-scoped SQLAlchemy tables (`ops_vehicles`, `ops_vehicle_tracking_records`, `ops_incidents`) managed by Alembic migration revision `4c99c2ff7376` chained from current head `c7d8e9f1a2b3`, achieving 100% schema parity with `Base.metadata.create_all()`.
- **Production Boot Integration**: Registered in `kernel_bootstrap.py` via `kernel.register_engine(OperationsModule())`, reaching `ModuleState.ACTIVE` and registering 13 capabilities.
- **Domain Events**: Post-commit emission of `kortex.event.operations.vehicle.status_changed`, `kortex.event.operations.incident.reported`, and `kortex.event.operations.incident.closed` via `EventEngine`.

---

## 2. Domain Boundaries and Policies

### Vehicle Tracking Domain
- **Vehicle Master**: Stores license plate, VIN, make, model, manufacturing year, vehicle type (`SEDAN`, `SUV`, `TRUCK`, `VAN`, `MOTORCYCLE`, `HEAVY_EQUIPMENT`, `OTHER`), operational status (`ACTIVE`, `MAINTENANCE`, `DECOMMISSIONED`), current odometer, and assigned driver.
- **Plate Uniqueness**: Scoped strictly by tenant via `UniqueConstraint("tenant_id", "license_plate")`. Same plate in different tenants is fully supported.
- **Vehicle Lifecycle State Machine**:
  - `ACTIVE` $\leftrightarrow$ `MAINTENANCE`
  - `ACTIVE` $\rightarrow$ `DECOMMISSIONED`
  - `MAINTENANCE` $\rightarrow$ `DECOMMISSIONED`
  - `DECOMMISSIONED` is terminal. Transitioning out of `DECOMMISSIONED` or assigning a driver to a decommissioned vehicle is rejected with `OpsVehicleValidationError`. Transitioning to `DECOMMISSIONED` automatically unassigns any active driver.
- **Odometer Progression Policy**:
  - Strict monotonicity: Any new tracking reading must satisfy $O_{\text{reading}} \ge O_{\text{current}}$. If lower, transaction aborts with `OpsTrackingRecordValidationError`.
  - Atomicity: Writing `OpsVehicleTrackingRow` and advancing `OpsVehicleRow.current_odometer` occur atomically inside `IDataStore.execute_in_transaction`.
- **Tracking History**: Reverse-chronological ordering (`recorded_at DESC, id DESC`) with limit/offset pagination.

### Incident Management Domain
- **Incident Entities**: Captures incident type (`ACCIDENT`, `BREAKDOWN`, `TRAFFIC_VIOLATION`, `THEFT_VANDALISM`, `PROPERTY_DAMAGE`, `NEAR_MISS`, `OTHER`), severity tier (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), title, narrative description, occurrence timestamp (non-future), reporting principal, involved vehicle ID (optional, verified tenant-scoped), driver ID (opaque), location, and estimated financial cost.
- **Tenant-Scoped Sequential Incident Numbering**:
  - Pattern: `INC-{YYYY}-{seq:04d}` (e.g. `INC-2026-0001`).
  - Sequence generation queries tenant incidents for the target year and derives $\max(\text{seq}) + 1$.
  - Protected by unique constraint `uq_ops_incident_tenant_number` and wrapped in a bounded retry loop (3 attempts) to guarantee deterministic recovery against concurrent race conditions.
- **Incident State Machine**:
  - `REPORTED` $\rightarrow$ `RESOLVED` (fast path for direct resolution)
  - `REPORTED` $\rightarrow$ `UNDER_INVESTIGATION` $\rightarrow$ `RESOLVED`
  - `UNDER_INVESTIGATION` $\rightarrow$ `ACTION_REQUIRED` $\rightarrow$ `RESOLVED`
  - `RESOLVED` $\rightarrow$ `CLOSED` (terminal)
  - Direct transitions from `REPORTED` to `CLOSED` are forbidden. Incidents must be formally investigated and `RESOLVED` with resolution notes before closure.
  - Closed incidents are sealed: any mutation, note update, resolution, or re-closure attempt raises `OpsIncidentAlreadyClosedError`.
- **Architectural Decoupling**: Critical severity incidents do not implicitly change vehicle status to `MAINTENANCE`. Fleet maintenance remains explicitly governed by `vehicle.status_update`.

---

## 3. Capability Surface

All 13 capabilities are registered in the Kernel capability registry under namespace `kortex.operations` with `provider="operations"`, `requires_authentication=True`, and `requires_execution_context=True`.

| Capability Name | Type | Required Permissions | Description |
|---|---|---|---|
| `kortex.operations.vehicle.create` | Mutation | `operations:vehicle:write` | Register new fleet vehicle master record |
| `kortex.operations.vehicle.get` | Query | `operations:vehicle:read` | Retrieve vehicle details by ID within tenant |
| `kortex.operations.vehicle.list` | Query | `operations:vehicle:read` | Query and paginate vehicles with optional status filter |
| `kortex.operations.vehicle.assign` | Mutation | `operations:vehicle:write` | Assign active driver to vehicle |
| `kortex.operations.vehicle.unassign` | Mutation | `operations:vehicle:write` | Release driver assignment from vehicle |
| `kortex.operations.vehicle.status_update` | Mutation | `operations:vehicle:write` | Transition vehicle operational lifecycle status |
| `kortex.operations.vehicle.tracking_record` | Mutation | `operations:vehicle:write` | Record monotonic odometer log and location check-in |
| `kortex.operations.vehicle.tracking_history` | Query | `operations:vehicle:read` | Retrieve reverse-chronological tracking logs |
| `kortex.operations.incident.report` | Mutation | `operations:incident:write` | File new operational incident report |
| `kortex.operations.incident.get` | Query | `operations:incident:read` | Retrieve incident report by ID within tenant |
| `kortex.operations.incident.list` | Query | `operations:incident:read` | Query and paginate incidents with filters |
| `kortex.operations.incident.resolve` | Mutation | `operations:incident:write` | Record investigation notes and mark as RESOLVED |
| `kortex.operations.incident.close` | Mutation | `operations:incident:manage` | Formally seal and close incident (terminal state) |

### RBAC Permission Catalog
- `operations:vehicle:read`: Read-only queries for vehicles and tracking logs.
- `operations:vehicle:write`: Mutations for vehicle creation, status updates, driver assignments, and tracking logs.
- `operations:incident:read`: Read-only queries for incident reports.
- `operations:incident:write`: Incident reporting and investigation resolution notes.
- `operations:incident:manage`: High-privilege administrative action to formally seal and close incidents.

---

## 4. Tenant Isolation and Security Architecture

### Execution Context Derivation
The canonical invariant $\text{AUTHENTICATED IDENTITY} \equiv \text{EXECUTION IDENTITY}$ is enforced across all capability invocations:
1. `CapabilityDispatcher` validates incoming bearer session tokens using `SecurityEngine`.
2. Token claims populate a trusted `CapabilityExecutionContext` containing authoritative `tenant_id` and `PrincipalRecord`.
3. Handler signatures declare `execution_context: CapabilityExecutionContext`.
4. Domain managers extract actor IDs (`recorded_by`, `reported_by_id`, `resolved_by`, `closed_by`) directly from `execution_context.principal.principal_id`.
5. Any caller attempting to pass `tenant_id`, `principal`, or `execution_context` in request parameters is blocked by dispatcher validation with `ReservedParameterError` or Python invocation `TypeError`.

### Enumeration-Resistant Tenant Scoping
All database queries include `tenant_id == execution_context.tenant_id`. Cross-tenant lookup requests fail closed, raising domain `NotFoundError` (`OpsVehicleNotFoundError` or `OpsIncidentNotFoundError`) to prevent tenant resource enumeration.

---

## 5. Persistence Schema & Alembic Migration

### Relational Schema
```
ops_vehicles
├── id (String(36), PK)
├── tenant_id (String(255), Index)
├── license_plate (String(32))
├── vin (String(64), Nullable, Index)
├── make (String(64))
├── model (String(64))
├── year (Integer, Nullable)
├── vehicle_type (String(32))
├── status (String(32), default "ACTIVE")
├── current_odometer (Numeric(12, 2), default 0.00)
├── assigned_driver_id (String(64), Nullable, Index)
├── assigned_at (DateTime(timezone=True), Nullable)
├── created_at (DateTime(timezone=True))
├── updated_at (DateTime(timezone=True))
└── UniqueConstraint("tenant_id", "license_plate", name="uq_ops_vehicle_tenant_plate")

ops_vehicle_tracking_records
├── id (String(36), PK)
├── tenant_id (String(255), Index)
├── vehicle_id (String(36), Index)
├── recorded_at (DateTime(timezone=True))
├── odometer_reading (Numeric(12, 2))
├── location_name (String(255), Nullable)
├── driver_id (String(64), Nullable)
├── notes (Text, Nullable)
├── recorded_by (String(64))
├── created_at (DateTime(timezone=True))
├── updated_at (DateTime(timezone=True))
└── Index("ix_ops_tracking_tenant_vehicle_recorded", "tenant_id", "vehicle_id", "recorded_at")

ops_incidents
├── id (String(36), PK)
├── tenant_id (String(255), Index)
├── incident_number (String(64))
├── incident_type (String(32))
├── severity (String(32))
├── status (String(32), default "REPORTED")
├── title (String(255))
├── description (Text)
├── occurred_at (DateTime(timezone=True))
├── reported_at (DateTime(timezone=True))
├── reported_by_id (String(64))
├── vehicle_id (String(36), Nullable, Index)
├── driver_id (String(64), Nullable)
├── location (String(255), Nullable)
├── estimated_cost (Numeric(18, 2), Nullable)
├── resolution_notes (Text, Nullable)
├── resolved_at (DateTime(timezone=True), Nullable)
├── resolved_by (String(64), Nullable)
├── closed_at (DateTime(timezone=True), Nullable)
├── closed_by (String(64), Nullable)
├── created_at (DateTime(timezone=True))
├── updated_at (DateTime(timezone=True))
└── UniqueConstraint("tenant_id", "incident_number", name="uq_ops_incident_tenant_number")
```

### Alembic Migration
- **Revision ID**: `4c99c2ff7376`
- **Down Revision**: `c7d8e9f1a2b3` (HR & Payroll tables)
- **Heads**: Single linear head confirmed via `alembic heads`.
- **Parity**: Tested and verified equivalent to `Base.metadata.create_all()`.

---

## 6. Domain Event Publication

Events are published asynchronously via `EventEngine` strictly post-transaction-commit:
1. `kortex.event.operations.vehicle.status_changed`:
   Payload: `{"tenant_id", "vehicle_id", "license_plate", "previous_status", "new_status", "reason", "timestamp"}`
2. `kortex.event.operations.incident.reported`:
   Payload: `{"tenant_id", "incident_id", "incident_number", "incident_type", "severity", "vehicle_id", "driver_id", "title", "occurred_at"}`
3. `kortex.event.operations.incident.closed`:
   Payload: `{"tenant_id", "incident_id", "incident_number", "closed_by", "closed_at"}`

---

## 7. Verification and Quality Gates

- **Ruff Linting**: Zero errors across all new and modified files (`ruff check` clean).
- **Mypy Strict Typing**: Zero issues found across 9 source files (`mypy` clean).
- **Alembic Parity**: 6/6 tests passing in `test_alembic_migrations.py`.
- **Kernel Bootstrap Integration**: `test_operations_module_registers_on_production_boot_path` passing.
- **Operations Domain Test Suite**:
  - `test_operations_models.py`: 33 passed
  - `test_operations_manager.py`: 10 passed
  - `test_operations_capabilities.py`: 3 passed
  - Total targeted tests: 53 passed
- **Baseline Failure Debt Comparison**: Exactly 48 pre-existing baseline failures verified; zero regressions introduced by Operations.
