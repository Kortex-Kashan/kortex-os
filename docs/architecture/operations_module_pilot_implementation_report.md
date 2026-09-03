# KORTEX OS — Phase 6 Operations Module Pilot Implementation Report

**Document Version**: 1.1.0  
**Phase**: Phase 6 — Pilot Business Modules  
**Module**: `kortex.modules.operations`  
**Status**: Implementation Complete & Certified  

---

## 1. Executive Summary

The Operations business module represents the third canonical pilot business module within KORTEX OS, following `FinanceModule` and `HRPayrollModule`. It delivers the fleet operations vertical slice comprising two core domains:
- **Vehicle Tracking**: Fleet vehicle master registry, driver assignment/unassignment invariants, lifecycle state management, point-in-time odometer and location tracking records, strict monotonic odometer enforcement, and reverse-chronological tracking history.
- **Incident Management**: Operational incident reporting, classification, severity scoring, driver/vehicle linkage, tenant-scoped sequential incident numbering (`INC-{YYYY}-{seq:04d}` with server-generated year and concurrency retry), fail-closed intermediate status transitions (`REPORTED -> UNDER_INVESTIGATION -> ACTION_REQUIRED`), investigation resolution records, and formal immutable terminal closure.

Key architectural and platform highlights:
- **Clean Architecture & Decoupling**: Business logic resides strictly in `kortex.modules.operations`. Zero imports from `hr_payroll` or `finance`. Driver IDs and actor IDs are opaque strings (`String(64)`).
- **Security & Identity Invariant**: $\text{AUTHENTICATED IDENTITY} \equiv \text{EXECUTION IDENTITY}$. Request models strictly omit caller-controlled `tenant_id`, `principal`, and `execution_context`. Context is derived exclusively by the dispatcher and passed via `CapabilityExecutionContext`.
- **Driver Invariant & Decommissioning**: A vehicle with an assigned driver cannot be transitioned to `DECOMMISSIONED`. Explicit unassignment is required. Driver assignment is permitted only for `ACTIVE` vehicles; assignment to `MAINTENANCE` or `DECOMMISSIONED` vehicles is rejected. Re-assignment of an already-assigned vehicle fails with `OpsVehicleConflictError`.
- **VIN & Plate Integrity**: Database-backed uniqueness via composite constraints `UniqueConstraint("tenant_id", "license_plate")` and `UniqueConstraint("tenant_id", "vin")`. Empty VIN strings normalize to `NULL` to preserve correct multi-null uniqueness semantics.
- **Concurrency & Monotonicity**: Concurrency-safe incident numbering with bounded collision retry (3 attempts) and transaction rollback. Strict monotonic odometer progression ($O_{\text{new}} \ge O_{\text{current}}$) updating tracking logs and vehicle master atomically in a single transaction.
- **Terminal Immutability**: Closed incidents are terminal and sealed; mutation, note updates, resolution, or re-closure attempts raise `OpsIncidentAlreadyClosedError`.
- **Relational Persistence & Migration Parity**: 3 tenant-scoped SQLAlchemy tables (`ops_vehicles`, `ops_vehicle_tracking_records`, `ops_incidents`) managed by Alembic migration revision `4c99c2ff7376` chained from parent `c7d8e9f1a2b3`. Verified for upgrade, explicit downgrade to parent, and re-upgrade.
- **Production Boot Integration**: Registered in `kernel_bootstrap.py` via `kernel.register_engine(OperationsModule())`, reaching `ModuleState.ACTIVE` and registering 14 capabilities.
- **Domain Events**: Post-commit emission of `kortex.event.operations.vehicle.status_changed`, `kortex.event.operations.incident.reported`, and `kortex.event.operations.incident.closed` via `EventEngine`.

---

## 2. Domain Boundaries and Policies

### Vehicle Tracking Domain
- **Vehicle Master**: Stores license plate, VIN, make, model, manufacturing year, vehicle type (`SEDAN`, `SUV`, `TRUCK`, `VAN`, `MOTORCYCLE`, `HEAVY_EQUIPMENT`, `OTHER`), operational status (`ACTIVE`, `MAINTENANCE`, `DECOMMISSIONED`), current odometer, and assigned driver.
- **Uniqueness Constraints**:
  - `(tenant_id, license_plate)`: Enforced via `uq_ops_vehicle_tenant_plate`. Same plate in different tenants is fully supported.
  - `(tenant_id, vin)`: Enforced via `uq_ops_vehicle_tenant_vin`. Empty string inputs normalize to `None` (`NULL` in DB), allowing multiple vehicles without VIN while rejecting duplicates when VIN is present within a tenant.
- **Vehicle Lifecycle State Machine**:
  - `ACTIVE` $\leftrightarrow$ `MAINTENANCE`
  - `ACTIVE` $\rightarrow$ `DECOMMISSIONED` (only if `assigned_driver_id is None`)
  - `MAINTENANCE` $\rightarrow$ `DECOMMISSIONED`
  - `DECOMMISSIONED` is terminal. Transitioning out of `DECOMMISSIONED`, assigning a driver, or unassigning a driver from a decommissioned vehicle is rejected with `OpsVehicleValidationError`.
- **Driver Assignment Invariants**:
  - Assignment is permitted only to `ACTIVE` vehicles. Attempting to assign a driver to a vehicle in `MAINTENANCE` or `DECOMMISSIONED` status is rejected.
  - Attempting to assign a driver to an already-assigned vehicle raises `OpsVehicleConflictError`.
  - Decommissioning a vehicle with an assigned driver is rejected with `OpsVehicleValidationError`. The caller must explicitly unassign the driver first.
- **Odometer Progression Policy**:
  - Strict monotonicity: Any new tracking reading must satisfy $O_{\text{reading}} \ge O_{\text{current}}$. If lower, transaction aborts with `OpsTrackingRecordValidationError`.
  - Atomicity: Writing `OpsVehicleTrackingRow` and advancing `OpsVehicleRow.current_odometer` occur atomically inside `IDataStore.execute_in_transaction`.
- **Tracking History**: Reverse-chronological ordering (`recorded_at DESC, id DESC`) with limit/offset pagination.

### Incident Management Domain
- **Incident Entities**: Captures incident type (`ACCIDENT`, `BREAKDOWN`, `TRAFFIC_VIOLATION`, `THEFT_VANDALISM`, `PROPERTY_DAMAGE`, `NEAR_MISS`, `OTHER`), severity tier (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), title, narrative description, occurrence timestamp (non-future), reporting principal, involved vehicle ID (optional, verified tenant-scoped), driver ID (opaque), location, and estimated financial cost.
- **Tenant-Scoped Sequential Incident Numbering**:
  - Pattern: `INC-{YYYY}-{seq:04d}` (e.g. `INC-2026-0001`).
  - Year derivation: Deterministically derived from server-generated `reported_at` UTC timestamp (`now.year`).
  - Sequence generation queries tenant incidents for the target year and derives $\max(\text{seq}) + 1$.
  - Protected by unique constraint `uq_ops_incident_tenant_number` and wrapped in a bounded retry loop (3 attempts) with clean transaction rollback to guarantee deterministic recovery against concurrent race conditions. Exhaustion raises `OpsIncidentConflictError`.
- **Incident State Machine**:
  - `REPORTED` $\rightarrow$ `UNDER_INVESTIGATION` (via `incident.status_update`)
  - `UNDER_INVESTIGATION` $\rightarrow$ `ACTION_REQUIRED` (via `incident.status_update`)
  - `ACTION_REQUIRED` $\rightarrow$ `RESOLVED` (via `incident.resolve`)
  - `REPORTED` $\rightarrow$ `RESOLVED` (fast path via `incident.resolve` permitted strictly for `LOW` severity incidents)
  - `RESOLVED` $\rightarrow$ `CLOSED` (via `incident.close`, terminal state)
  - Direct transitions from `REPORTED` to `CLOSED` are forbidden.
  - Intermediate status updates are fail-closed: backward transitions or skipping states is rejected with `OpsIncidentValidationError`.
  - Closed incidents are sealed: any mutation, note update, resolution, or re-closure attempt raises `OpsIncidentAlreadyClosedError`.
- **Architectural Decoupling**: Incidents do not implicitly change vehicle status to `MAINTENANCE`. Fleet maintenance remains explicitly governed by `vehicle.status_update`.

---

## 3. Capability Surface

All 14 capabilities are registered in the Kernel capability registry under namespace `kortex.operations` with `provider="operations"`, `requires_authentication=True`, and `requires_execution_context=True`.

| Capability Name | Type | Required Permissions | Description |
|---|---|---|---|
| `kortex.operations.vehicle.create` | Mutation | `operations:vehicle:write` | Register new fleet vehicle master record |
| `kortex.operations.vehicle.get` | Query | `operations:vehicle:read` | Retrieve vehicle details by ID within tenant |
| `kortex.operations.vehicle.list` | Query | `operations:vehicle:read` | Query and paginate vehicles with optional status filter |
| `kortex.operations.vehicle.assign` | Mutation | `operations:vehicle:write` | Assign active driver to ACTIVE vehicle |
| `kortex.operations.vehicle.unassign` | Mutation | `operations:vehicle:write` | Release driver assignment from vehicle |
| `kortex.operations.vehicle.status_update` | Mutation | `operations:vehicle:write` | Transition vehicle operational lifecycle status |
| `kortex.operations.vehicle.tracking_record` | Mutation | `operations:vehicle:write` | Record monotonic odometer log and location check-in |
| `kortex.operations.vehicle.tracking_history` | Query | `operations:vehicle:read` | Retrieve reverse-chronological tracking logs |
| `kortex.operations.incident.report` | Mutation | `operations:incident:write` | File new operational incident report |
| `kortex.operations.incident.get` | Query | `operations:incident:read` | Retrieve incident report by ID within tenant |
| `kortex.operations.incident.list` | Query | `operations:incident:read` | Query and paginate incidents with filters |
| `kortex.operations.incident.status_update` | Mutation | `operations:incident:write` | Transition incident through intermediate lifecycle states |
| `kortex.operations.incident.resolve` | Mutation | `operations:incident:write` | Record investigation findings and mark as RESOLVED |
| `kortex.operations.incident.close` | Mutation | `operations:incident:manage` | Formally seal and close incident (terminal state) |

### RBAC Permission Catalog
- `operations:vehicle:read`: Read-only queries for vehicles and tracking logs.
- `operations:vehicle:write`: Mutations for vehicle creation, status updates, driver assignments, and tracking logs.
- `operations:incident:read`: Read-only queries for incident reports.
- `operations:incident:write`: Incident reporting, intermediate status transitions, and investigation resolution notes.
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
├── UniqueConstraint("tenant_id", "license_plate", name="uq_ops_vehicle_tenant_plate")
└── UniqueConstraint("tenant_id", "vin", name="uq_ops_vehicle_tenant_vin")

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
- **Linear Chain**: Confirmed linear head via `alembic heads`.
- **Downgrade Testing**: Explicitly tested downgrade to parent `c7d8e9f1a2b3` and re-upgrade to `4c99c2ff7376` in `test_alembic_migrations.py`.
- **Parity**: 100% schema parity with `Base.metadata.create_all()`.

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

- **Ruff Linting**: `ruff check` passed with 0 errors.
- **Mypy Strict Typing**: `mypy` passed with 0 errors across 9 source files.
- **Alembic Migration Suite**: 7/7 tests passed in `test_alembic_migrations.py`.
- **Kernel Bootstrap Integration**: `test_operations_module_registers_on_production_boot_path` passed (asserting 14 capabilities).
- **Operations Domain Test Suite**:
  - `test_operations_models.py`: 35 passed
  - `test_operations_manager.py`: 16 passed
  - `test_operations_capabilities.py`: 3 passed
  - `test_kernel_bootstrap.py`: 1 passed
  - `test_alembic_migrations.py`: 7 passed
  - Total targeted tests: 62 passed in 39.00s.
- **Baseline Failure Debt Comparison**:
  - Baseline failure count: 88 full-suite failures (38 unit + 50 integration across untouched legacy packages).
  - Current failure count: 88.
  - Resolved baseline failures: 0 (unrelated legacy code preserved per roadmap discipline).
  - Remaining baseline failures: 88.
  - Genuinely new failures introduced by Operations: 0.
  - Invariant verified: `NEW FAILURES INTRODUCED BY OPERATIONS = 0`.
