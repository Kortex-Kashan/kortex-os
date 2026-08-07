# KORTEX OS — Business Module Development Guide (SDK)

Status: Approved Guide
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/module_development_guide.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Business Module Architecture (`docs/architecture/business_module_architecture.md`)
- Business Entity Model (`docs/architecture/business_entity_model.md`)

---

## 1. Architecture Overview

This SDK guide explains how software engineers build enterprise-grade business modules (such as **HR**, **Payroll**, **Inventory**, and **CRM**) for KORTEX OS **without modifying the Kernel or system engines**.

All modules extend KORTEX OS by registering capabilities (`kortex.<module>.<resource>.<action>`), subscribing to system events, defining declarative recipes/templates, and interacting with `StorageEngine` abstractions.

```
┌────────────────────────────────────────────────────────────────────────┐
│                          Business Module                               │
│  (Domain Aggregates -> Services -> Use Cases -> Capability Handlers)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Kernel IoC Container                            │
│           (Registers capabilities, authorization & routing)            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                           System Engines                               │
│  (Storage, Workflow, Recipe, Document, Connector, AI, Security)        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Standard Module Folder Structure

Every module MUST adhere to Clean Architecture folder conventions:

```
kortex.hr.payroll/
├── manifest.yaml               # KortexAssetManifest module specification
├── module.py                   # Main Module Entrypoint class inheriting BaseModule
├── domain/                     # Domain Layer (Pure Python, Zero External Imports)
│   ├── aggregates/             # Domain Aggregates & Entities
│   ├── events/                 # Domain Event definitions
│   └── policies/               # Invariant validation policies
├── application/                # Application Layer (Use Cases)
│   ├── commands/               # Mutative Command handlers
│   └── queries/                # Read Query handlers & Projections
├── infrastructure/             # Infrastructure Layer (Capability Mappers)
│   ├── persistence/            # Storage Engine ORM mappings (IDataStore)
│   └── capabilities/           # Capability handler handlers registered with Kernel
├── recipes/                    # Pre-packaged business recipes (.kortex-recipe)
├── templates/                  # Pre-packaged document templates (.kortex-template)
└── tests/                      # Comprehensive Unit and Integration test suite
```

---

## 3. Required Files & Interfaces

1. `manifest.yaml`: Module specification conforming to `KortexAssetManifest`.
2. `module.py`: Implements `BaseModule` abstract base class:
   - `on_load()`: Initializes IoC dependency injection bindings.
   - `on_register_capabilities()`: Registers canonical capability handlers with Kernel Registry.
   - `on_subscribe_events()`: Connects module handlers to Event Engine topics.
   - `on_unload()`: Cleans up transient resources upon module deactivation.

---

## 4. Capability Registration

Capability handlers are registered during `on_register_capabilities()` using canonical capability strings:

```python
# Specification example for capability registration in module.py
class PayrollModule(BaseModule):
    def on_register_capabilities(self, registry: ICapabilityRegistry) -> None:
        registry.register_capability(
            name="kortex.payroll.process.execute",
            handler=self.payroll_service.process_monthly_payroll,
            required_permissions=["payroll:process:execute"],
            description="Process monthly payroll calculations for an organization branch"
        )
```

---

## 5. Event Integration

Modules subscribe to system events and publish domain events via Event Engine:

```python
# Event subscription & publication contract specification
async def on_employee_created_event(self, event: KortexEvent) -> None:
    # Handle employee created event from HR module automatically in Payroll module
    await self.payroll_service.initialize_salary_record(event.payload["employee_id"])
```

---

## 6. Storage Integration

Modules persistence flows strictly through `StorageEngine`:
- `IDataStore`: Relational transactional sessions (`AsyncSession`).
- `IFileStore`: Module resource files.
- `IObjectStore`: Binary attachments.
- `ICacheStore`: Read projections and query caches.

Direct database connections (`sqlite3`, `asyncpg`) or direct file operations (`open()`) are forbidden.

---

## 7. How to Build Core Business Modules (Step-by-Step)

### 7.1 HR Module (`kortex.hr`)
- **Aggregates**: `EmployeeAggregate`, `AttendanceAggregate`, `LeaveAggregate`.
- **Capabilities**: `kortex.hr.employee.create`, `kortex.hr.attendance.clock_in`, `kortex.hr.leave.apply`.
- **Flow**: Recovers employee input $\rightarrow$ Validates invariant policies $\rightarrow$ Persists via `IDataStore` $\rightarrow$ Emits `EmployeeCreatedEvent`.

### 7.2 Payroll Module (`kortex.payroll`)
- **Aggregates**: `PayrollProcessAggregate`, `SalaryAggregate`, `LoanAggregate`.
- **Capabilities**: `kortex.payroll.salary.calculate`, `kortex.payroll.process.execute`.
- **Document Integration**: Dispatches payslip rendering requests to Document Engine via `kortex.document.operation.execute` (using `payslip.declarative.v1` template).

### 7.3 Inventory Module (`kortex.inventory`)
- **Aggregates**: `StockItemAggregate`, `ProductAggregate`, `WarehouseAggregate`.
- **Capabilities**: `kortex.inventory.stock.adjust`, `kortex.inventory.product.create`.
- **Event Integration**: Publishes `StockLowEvent` when inventory falls below reorder thresholds.

### 7.4 CRM Module (`kortex.crm`)
- **Aggregates**: `CustomerAggregate`, `SalesOrderAggregate`, `QuotationAggregate`.
- **Capabilities**: `kortex.crm.customer.register`, `kortex.sales.order.create`.
- **Workflow Integration**: Triggers approval workflow via Workflow Engine (`kortex.workflow.instance.start`).

---

## 8. Versioning, Packaging & Marketplace Deployment

1. **Versioning**: Enforce SemVer 2.0.0 (`MAJOR.MINOR.PATCH`).
2. **Packaging**: Assemble module directory into `.kortex-module` ZIP archive containing SHA256 checksum and Ed25519 digital signature.
3. **Deployment**: Deploy via Asset System pipeline (`AssetInstaller.install()`).

---

## 9. Best Practices

- **Keep Domain Pure**: Zero framework or database dependencies inside `domain/`.
- **Single Responsibility**: One use case per command/query handler.
- **Idempotent Handlers**: Ensure mutative capability handlers support `idempotency_key`.

---

## 10. Anti-Patterns to Avoid

- ❌ **Direct Cross-Module Imports**: Importing sibling module python code directly instead of invoking capabilities.
- ❌ **Direct Database Access**: Bypassing `StorageEngine` to run raw SQL.
- ❌ **Infrastructure Logic in Modules**: Writing custom PDF renderers or HTTP clients inside module source code instead of invoking Document or Connector engines.

---

## 11. Acceptance Criteria

- ✓ **Kernel Preservation**: Zero modifications to Kernel or System Engines.
- ✓ **Clean Architecture**: Domain layer remains 100% pure Python.
- ✓ **Capability Compliant**: All public use cases exposed as canonical capabilities.
- ✓ **Storage Engine Only**: All storage flows through `StorageEngine` abstractions.
