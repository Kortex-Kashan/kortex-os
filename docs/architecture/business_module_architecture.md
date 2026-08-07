# KORTEX OS — Business Module Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/business_module_architecture.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Universal Asset System (`docs/architecture/asset_system.md`)

---

## 1. Purpose

This document defines the canonical **Business Module Architecture** for KORTEX OS.

Business modules (e.g. Finance, HR & Payroll, Operations, CRM, Procurement, Inventory) represent the domain-specific application layer of KORTEX OS. While system engines (Storage, Workflow, Recipe, Document, Connector, AI, Security, Knowledge) provide reusable infrastructure, business modules encapsulate domain business rules, entities, aggregates, commands, queries, policies, and workflows.

This specification establishes the modular architecture, lifecycle, isolation boundaries, packaging, dependency resolution, extension points, and communication standards governing all business modules.

---

## 2. Philosophy

1. **Zero Infrastructure Logic**: Business modules contain zero infrastructure orchestration, database driver logic, file rendering engines, or protocol networking code. All infrastructure functionality is accessed through canonical Kernel capabilities.
2. **Strict Domain Isolation**: Business modules never directly import or invoke code from other business modules. Module-to-module communication occurs exclusively through registered capabilities and published events.
3. **Local-First & Offline-First**: Business modules execute 100% locally on local storage without requiring internet connectivity or remote cloud API calls.
4. **Clean Architecture & Domain-Driven Design (DDD)**: Modules are structured around DDD Bounded Contexts, Business Aggregates, Domain Events, Commands, and Queries.
5. **Marketplace-Ready Packaging**: Modules are packaged as `.kortex-module` archives containing manifests (`KortexAssetManifest`), schemas, permissions, and cryptographic signatures.

---

## 3. Module Lifecycle (`BusinessModuleLifecycle`)

Every business module transitions through a managed state machine orchestrated by the Kernel:

```
┌──────────┐     ┌───────────┐     ┌───────────┐     ┌──────────┐     ┌───────────┐
│ Unloaded │ ──> │ Installed │ ──> │   Loaded  │ ──> │ Active   │ ──> │ Disabled  │
└──────────┘     └───────────┘     └───────────┘     └──────────┘     └───────────┘
                                                           │                │
                                                           ▼                ▼
                                                    ┌────────────┐   ┌────────────┐
                                                    │ Superseded │   │ Uninstalled│
                                                    └────────────┘   └────────────┘
```

- **UNLOADED**: Module package exists in storage but is not registered.
- **INSTALLED**: Package verified, unpacked into `IFileStore`, metadata registered in `IDataStore`.
- **LOADED**: Module classes discovered and IoC dependency injections configured.
- **ACTIVE**: Capabilities registered in Kernel Registry, handling business commands/queries.
- **DISABLED**: Capabilities temporarily suspended from active routing.
- **SUPERSEEDED**: Replaced by a newer version following an upgrade.
- **UNINSTALLED**: Capabilities unregistered and local resources archived.

---

## 4. Module Anatomy

A Business Module comprises the following architectural building blocks:

- **Business Module**: Bounded context root packaging all domain logic for a specific business domain.
- **Business Service**: Domain service executing business use cases and enforcing business policies.
- **Business Capability**: Public entrypoint registered in Kernel Registry (`kortex.<module>.<resource>.<action>`).
- **Business Aggregate**: Domain aggregate root maintaining consistency boundaries over business entities.
- **Business Context**: Contextual execution state passing tenant, user, organization, and session parameters.
- **Business Policy**: Rule set defining domain constraints, authorization rules, and invariant conditions.
- **Business Event**: Immutable domain event emitted to Event Engine upon state changes.
- **Business Command**: Mutative request encapsulating domain actions.
- **Business Query**: Non-mutative read request returning projections or snapshots.
- **Business Validation**: Domain constraint validator ensuring business invariants are satisfied.
- **Business Projection**: Read-optimized view model generated from domain events or aggregate states.
- **Business Snapshot**: Point-in-time state snapshot of an aggregate for performance optimization.
- **Business Configuration**: Declarative configuration settings stored in Configuration Engine.
- **Business Feature Flag**: Dynamic toggle governing experimental or tenant-specific module features.
- **Business Module Manifest**: Manifest definition (`KortexAssetManifest`) describing module metadata.
- **Business Module Package**: Zip archive (`.kortex-module`) containing module assets.
- **Business Module Asset**: Installable resource (recipes, templates, schemas) belonging to the module.
- **Business Module SDK**: Platform interface library used by developers to construct modules.
- **Business Module Dependencies**: Declared list of required platform capabilities and module versions.
- **Business Module Version**: SemVer 2.0.0 string tracking module release versions.
- **Business Module Ownership**: Security metadata establishing module publisher and maintainer.
- **Business Module Isolation**: Security sandbox boundaries isolating module data and execution.

---

## 5. Folder Structure

All business modules reside inside `apps/` or `backend/src/kortex/modules/<module_name>/`:

```
<module_name>/
├── manifest.yaml               # Business Module Manifest (KortexAssetManifest)
├── module.py                   # BusinessModule entrypoint inheriting BaseModule
├── domain/                     # Domain Layer (DDD Core)
│   ├── aggregates/             # Business Aggregates & Entities
│   ├── events/                 # Business Events
│   ├── policies/               # Business Policies & Invariant Rules
│   └── services/               # Business Services
├── application/                # Application Layer (Use Cases)
│   ├── commands/               # Business Commands & Handlers
│   ├── queries/                # Business Queries & Projections
│   └── validators/             # Business Validation rules
├── infrastructure/             # Infrastructure Layer (Adapters)
│   ├── persistence/            # Storage Engine mappers (IDataStore)
│   └── capabilities/           # Capability handler registrations
├── recipes/                    # Module business recipes (.kortex-recipe)
├── templates/                  # Module document templates (.kortex-template)
└── tests/                      # Module unit and integration test suite
```

---

## 6. Module Manifest (`BusinessModuleManifest`)

Every module contains a `manifest.yaml` adhering to `KortexAssetManifest` defined in `asset_system.md`:

```yaml
id: "b47a98e2-51c3-4d61-893f-912a3e47b901"
name: "HR & Payroll Module"
namespace: "kortex.hr.payroll"
version: "1.0.0"
asset_type: "MODULE"
description: "Enterprise HR, Employee Management, Shift Scheduling, and Payroll Processing"
author:
  name: "KORTEX Core Team"
  email: "engineering@kortex.os"
  organization: "KORTEX"
license: "MIT"
kernel_compatibility: ">=0.1.0"
dependencies:
  "kortex.engines.storage": ">=1.0.0"
  "kortex.engines.workflow": ">=1.0.0"
  "kortex.engines.document": ">=1.0.0"
capabilities_required:
  - "kortex.storage.data.session"
  - "kortex.workflow.instance.start"
  - "kortex.document.operation.execute"
capabilities_provided:
  - "kortex.hr.employee.create"
  - "kortex.hr.payroll.process"
permissions_required:
  - "hr:employee:write"
  - "payroll:process:execute"
checksum: "a8f5c...3e"
signature: "ed25519:9b2f...81"
```

---

## 7. Module Metadata (`BusinessModuleMetadata`)

Module metadata extends `UniversalMetadata` to track module status, tenant bindings, installed capabilities, and active feature flags.

---

## 8. Module Registration

1. **Discovery**: Kernel scans module directories or unpacked archive paths.
2. **Manifest Parsing**: Loads and validates `manifest.yaml`.
3. **IoC Container Registration**: Registers Business Services in Kernel IoC container (`DependencyInjection`).
4. **Capability Mapping**: Registers provided capabilities (`kortex.<module>.<resource>.<action>`) in Kernel Registry.
5. **Event Subscription**: Subscribes module event handlers to target Event Engine topics.

---

## 9. Module Dependencies (`BusinessModuleDependencies`)

Module dependencies are resolved using directed acyclic graph (DAG) topological sorting. A module will fail to load if declared module dependencies, capabilities, or minimum Kernel versions are absent.

---

## 10. Module Isolation (`BusinessModuleIsolation`)

1. **Logical Data Isolation**: Module database tables are scoped by module prefix or schema inside `IDataStore`.
2. **No Code Imports**: Modules MUST NOT import classes or methods from sibling modules.
3. **Sandboxed Memory**: Module execution runs in isolated task scopes preventing cross-module memory mutation.

---

## 11. Module Communication

- **Synchronous Invocations**: Modules invoke capability requests through Kernel Capability Dispatcher (`Platform Service Contracts`).
- **Asynchronous Events**: Modules broadcast state changes by publishing immutable `BusinessEvent` objects to Event Engine.

---

## 12. Module Capabilities (`BusinessCapability`)

Capabilities follow canonical format: $\text{kortex}.<\text{module}>.<\text{resource}>.<\text{action}>$.

Examples:
- `kortex.hr.employee.create`
- `kortex.payroll.salary.calculate`
- `kortex.inventory.product.adjust`
- `kortex.crm.customer.register`

---

## 13. Module Events (`BusinessEvent`)

Domain events published to Event Engine inherit from `UniversalMetadata`:
- `kortex.event.hr.employee_created`
- `kortex.event.payroll.processed`
- `kortex.event.inventory.stock_low`

---

## 14. Module Permissions

Modules define granular RBAC/ABAC permissions in `permissions.yaml`. Capability execution is intercepted by Kernel authorization middleware to enforce permission checks.

---

## 15. Module Configuration (`BusinessConfiguration`)

Module settings are declared in schema format and stored in Configuration Engine, supporting tenant-specific overrides and `BusinessFeatureFlag` toggles.

---

## 16. Module Storage (`BusinessModuleStorage`)

All persistence flows through `StorageEngine`:
- `IDataStore`: Relational entity storage via SQLAlchemy 2.0 AsyncSession.
- `IFileStore`: Module asset files and template storage.
- `IObjectStore`: Binary attachments and document snapshots.
- `ICacheStore`: Read projections and snapshot caching.

---

## 17. Module Services (`BusinessService`)

Domain services encapsulate business logic, orchestrating aggregates and executing commands without exposing infrastructure details.

---

## 18. Module APIs

Modules expose capability interfaces to Kernel IoC. External HTTP/GraphQL APIs map directly to capability dispatchers via API Gateway middleware.

---

## 19. Module Extension Points

Modules expose extension hooks via recipes, document templates, custom events, and plugin adapters without requiring source modifications.

---

## 20. Module Versioning (`BusinessModuleVersion`)

Strict SemVer 2.0.0 versioning (`MAJOR.MINOR.PATCH`). Schema migrations are defined in declarative version migration steps.

---

## 21. Module Installation

Handled via `AssetSystem` pipeline: Archive Unpacking $\rightarrow$ Verification Pipeline $\rightarrow$ Database Schema Migration $\rightarrow$ Kernel Capability Registration.

---

## 22. Module Upgrade

Atomic upgrade pipeline: Installs new version $\rightarrow$ Runs declarative data migration $\rightarrow$ Swaps Kernel capability routes $\rightarrow$ Marks previous version `SUPERSEEDED`.

---

## 23. Module Rollback

Restores previous stable version snapshot from `IDataStore` and restores previous capability routes in Kernel Registry.

---

## 24. Module Packaging (`BusinessModulePackage`)

Assembled into `.kortex-module` archives containing manifest, domain code, recipes, templates, SHA256 checksums, and Ed25519 signatures.

---

## 25. Marketplace Compatibility

Fully compliant with KORTEX Marketplace distribution requirements, asset manifest verification, licensing checks, and digital signatures.

---

## 26. Multi-Tenant Rules

All module queries, entities, and events MUST include `tenant_id` filtering to guarantee multi-tenant data isolation.

---

## 27. Performance Requirements

- Capability dispatch overhead $\le$ 10ms.
- Read queries utilize cached `BusinessProjection` objects in `ICacheStore`.
- Non-blocking `async`/`await` primitives throughout execution.

---

## 28. Security Requirements

- Strict RBAC/ABAC permission checks on all capabilities.
- Immutable audit entries (`UniversalAuditEntry`) recorded for mutative commands.
- Zero plaintext secret storage.

---

## 29. Testing Requirements

- Unit tests for domain aggregates, policies, and services.
- Integration tests for command/query capability handlers.
- Quality gates: 100% passing tests, $\ge$90% code coverage.

---

## 30. Acceptance Criteria

- ✓ **Zero Infrastructure Code**: Modules contain no direct DB, filesystem, or network code.
- ✓ **Clean Architecture**: Domain logic strictly separated into Aggregates, Services, Commands, and Queries.
- ✓ **Capability-Driven**: All module operations exposed via canonical capabilities.
- ✓ **Decoupled**: Zero direct imports between business modules.
- ✓ **Marketplace Ready**: Packaged as verified `.kortex-module` archives.
