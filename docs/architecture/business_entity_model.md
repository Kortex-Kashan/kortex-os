# KORTEX OS — Business Entity Model Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/business_entity_model.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Business Module Architecture (`docs/architecture/business_module_architecture.md`)

---

## 1. Purpose

This document defines the canonical **Business Entity Model** for KORTEX OS.

It establishes the authoritative specification for all business domain entities and platform assets across KORTEX OS. Every entity used in business modules (HR, Payroll, Inventory, CRM, Procurement, Operations) or system engines MUST adhere to the universal structural rules defined herein.

---

## 2. Entity Philosophy

1. **Universal Model Inheritance**: All business entities inherit from `UniversalMetadata`, `UniversalIdentity`, `UniversalOwnership`, `UniversalLifecycleState`, and `UniversalClassification`.
2. **Domain-Driven Aggregates**: Entities are grouped into consistency boundaries managed by Aggregate Roots.
3. **Database Independence**: Entity definitions describe pure domain concepts decoupled from ORM schemas or storage drivers.
4. **Strict Immutability**: Historical, financial, and published entity states are immutable once finalized.

---

## 3. Universal Entity Rules

Every business entity in KORTEX OS MUST implement the following 10 mandatory facets:
1. **Identity**: `UniversalIdentity` (UUID, namespace, URN).
2. **Metadata**: `UniversalMetadata` (name, display name, timestamps).
3. **Relationships**: List of `UniversalRelationship` directed graph connections.
4. **Ownership**: `UniversalOwnership` (tenant ID, organization ID, creator ID).
5. **Lifecycle**: `UniversalLifecycleState` (`Draft`, `Active`, `Archived`, etc.).
6. **Versioning**: `UniversalVersion` (SemVer 2.0.0, version chain).
7. **Classification**: `UniversalClassification` (security level, compliance flags).
8. **Audit**: Complete `UniversalAuditEntry` history tracking.
9. **Search**: `UniversalSearchMetadata` for multi-modal indexing.
10. **Validation**: `UniversalValidationReport` rules enforcing domain invariants.

---

## 4. Aggregate Rules

- Aggregates enforce transactional consistency boundaries.
- External entities refer to aggregates strictly by Aggregate Root `UniversalIdentity`.
- Entities inside an aggregate cannot be mutated without routing through the Aggregate Root.

---

## 5. Identity Rules

Entity IDs use canonical UUID v4/v7 strings embedded in `UniversalIdentity` with unique URNs (`urn:kortex:<tenant_id>:<namespace>:<entity_name>:<id>`).

---

## 6. Version Rules

Entities track modifications using `UniversalVersion`. Mutating a published or immutable entity state produces a new version instance linked to `parent_version_id`.

---

## 7. Lifecycle Rules

Transitions follow `UniversalLifecycleState`: `DRAFT` $\rightarrow$ `PENDING_REVIEW` $\rightarrow$ `ACTIVE` $\rightarrow$ `SUPERSEEDED` $\rightarrow$ `ARCHIVED` / `LOGICAL_DELETE`.

---

## 8. Ownership Rules

Entities belong to an explicit tenant (`tenant_id`) and organization (`organization_id`) defined in `UniversalOwnership`.

---

## 9. Relationships

Managed via `UniversalRelationship` graph connections (`DEPENDS_ON`, `PARENTS`, `DERIVED_FROM`, `SUPERSEDES`, `LINKS_TO`, `ATTACHED_TO`, `VALIDATES`).

---

## 10. References

Cross-aggregate and cross-module references use `UniversalReference` URN pointers to eliminate tight code coupling.

---

## 11. Events

Every mutative action emits a corresponding `BusinessEvent` to Event Engine (e.g. `kortex.event.<module>.<entity>_<action>`).

---

## 12. Validation

Validated via `UniversalValidationReport` checking required fields, strong types, and business invariant policies.

---

## 13. Search Metadata

Indexed via `UniversalSearchMetadata` for full-text, keyword, and field-level search filtering.

---

## 14. Audit Metadata

Generates immutable `UniversalAuditEntry` logs capturing actor ID, timestamp, previous state hash, new state hash, and correlation trace ID.

---

## 15. Classification

Security classification rating (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`) specified via `UniversalClassification`.

---

## 16. Multi Tenant Rules

Every entity query and persistence operation is strictly filtered by `tenant_id`. Cross-tenant data leakage is impossible.

---

## 17. Storage Rules

Persistence flows exclusively through `StorageEngine` (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`).

---

## 18. Security Rules

Entity operations require explicit capability permission checks verified by Kernel authorization middleware.

---

## 19. Performance Rules

Cached read projections (`BusinessProjection`) stored in `ICacheStore` for sub-10ms query responses.

---

## 20. Canonical Business Entities Catalog

The table below defines the 36 canonical entities of KORTEX OS. Each entity implements all 10 universal facets (Identity, Metadata, Relationships, Ownership, Lifecycle, Versioning, Classification, Audit, Search, Validation):

| Entity Name | Domain / Namespace | Description & Key Facets |
| :--- | :--- | :--- |
| **Organization** | `kortex.system.org` | Root enterprise organization entity; owns global tenant boundaries |
| **Branch** | `kortex.system.org` | Physical or operational branch location within an Organization |
| **Department** | `kortex.system.org` | Organizational division (e.g. HR, Finance, Operations) |
| **Employee** | `kortex.hr.employee` | Person employed by Organization; linked to User, Department, Branch |
| **User** | `kortex.security.user` | Authenticated system user account; holds Roles and Security Credentials |
| **Role** | `kortex.security.role` | Security role aggregate grouping system Permissions |
| **Permission** | `kortex.security.permission` | Fine-grained RBAC capability permission key |
| **Attendance** | `kortex.hr.attendance` | Daily attendance log entity tracking clock-in/out timestamps |
| **Shift** | `kortex.hr.shift` | Work shift schedule definition defining hours and break rules |
| **Payroll** | `kortex.payroll.process` | Monthly or periodic payroll processing aggregate root |
| **Salary** | `kortex.payroll.salary` | Employee compensation breakdown (basic, allowances, deductions) |
| **Loan** | `kortex.payroll.loan` | Employee financial advance or loan record with repayment schedule |
| **Leave** | `kortex.hr.leave` | Employee leave application entity tracking approval lifecycle |
| **Overtime** | `kortex.hr.overtime` | Overtime work authorization record with rate multipliers |
| **Project** | `kortex.ops.project` | Operational project aggregate tracking tasks, milestones, budgets |
| **Task** | `kortex.ops.task` | Work task unit assigned to Employees within a Project |
| **Asset** | `kortex.ops.asset` | Physical company asset tracking depreciation, location, assignment |
| **Vehicle** | `kortex.ops.vehicle` | Fleet vehicle entity tracking maintenance, fuel, driver assignment |
| **Inventory** | `kortex.inventory.stock` | Warehouse inventory stock item tracking quantity and location |
| **Product** | `kortex.inventory.product` | Product catalog item definition with SKU, pricing, tax rates |
| **Customer** | `kortex.crm.customer` | External client or customer profile with contact and billing metadata |
| **Vendor** | `kortex.procurement.vendor` | Supplier entity tracking purchasing agreements and lead times |
| **Purchase Order** | `kortex.procurement.po` | Procurement order issued to Vendor for products/services |
| **Sales Order** | `kortex.sales.order` | Commercial sales order placed by Customer |
| **Invoice** | `kortex.finance.invoice` | Commercial billing invoice entity (Immutable when Published) |
| **Quotation** | `kortex.sales.quotation` | Price quotation or estimate issued to Customer |
| **Contract** | `kortex.legal.contract` | Legal agreement entity tracking terms, dates, and signatures |
| **Incident** | `kortex.ops.incident` | Operational incident or ticket record tracking resolution workflow |
| **Visitor** | `kortex.security.visitor` | Facility visitor log entity tracking check-in/out credentials |
| **Knowledge Item** | `kortex.knowledge.item` | Indexed knowledge graph node entity with semantic connections |
| **Document** | `kortex.document.item` | Rendered business document version asset with lineage history |
| **Workflow** | `kortex.workflow.def` | Compiled workflow definition state machine AST entity |
| **Recipe** | `kortex.recipe.item` | Installed zero-code automation recipe package asset |
| **Connector** | `kortex.connector.item` | Integration channel profile entity with rate limits and secret handles |
| **Template** | `kortex.template.item` | Declarative document template definition in Template Library |
| **Business Module** | `kortex.module.item` | Installed business module package asset in system registry |

---

## 21. Acceptance Criteria

- ✓ **Universal Structure**: 100% of defined entities implement all 10 mandatory facets.
- ✓ **Clean Boundaries**: Entities are database-agnostic and contain no ORM imports.
- ✓ **Complete Catalog**: Defines all 36 canonical system entities.
- ✓ **Local-First & Multi-Tenant**: Tenant isolation (`tenant_id`) and local execution enforced.
