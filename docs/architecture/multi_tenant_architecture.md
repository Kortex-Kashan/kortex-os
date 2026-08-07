# KORTEX OS — Multi-Tenant Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/multi_tenant_architecture.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Business Entity Model (`docs/architecture/business_entity_model.md`)

---

## 1. Multi-Tenant Organizational Hierarchy

KORTEX OS enforces a 3-tier organizational hierarchy across all platform entities and storage operations:

```
┌──────────────────────────────────────────────────────────┐
│                      Tenant (ID)                         │
│       (Complete enterprise boundary isolation)           │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                    Organization (ID)                     │
│         (Legal corporate entity within Tenant)           │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                       Branch (ID)                        │
│         (Physical location / Operational Unit)           │
└──────────────────────────────────────────────────────────┘
```

- **Tenant**: Top-level multi-tenant boundary (`tenant_id`). Default local installation uses `tenant_id = "default"`.
- **Organization**: Corporate entity within a tenant (`organization_id`).
- **Branch**: Operational unit or physical office within an organization (`branch_id`).

---

## 2. Multi-Tenant Isolation Strategy

1. **Logical Data Isolation**: Every database table, index, file path, object bucket key, and cache key includes `tenant_id` scoping.
2. **Zero Cross-Tenant Leakage**: All database queries automatically append `WHERE tenant_id = :tenant_id` filters via Storage Engine middleware.
3. **Execution Sandbox**: Memory task contexts and temporary workspace paths in `IFileStore` are isolated per tenant.

---

## 3. Storage Layer Scoping

- **`IDataStore`**: Every relational entity model includes `tenant_id` as a indexed column.
- **`IObjectStore`**: Object keys formatted as `<tenant_id>/<bucket_name>/<object_key>`.
- **`IFileStore`**: Paths sandboxed inside `<workspace_root>/tenants/<tenant_id>/`.
- **`ICacheStore`**: Cache keys prefixed with `kortex:<tenant_id>:`.

---

## 4. Identity & Authentication Scoping

Sessions, user principal tokens, and service principals carry `tenant_id` metadata inside `UniversalIdentity`.

---

## 5. Permissions & Authorization

ABAC rules evaluate `tenant_id` match conditions. Users with `tenant_id = "A"` cannot execute capabilities on resources belonging to `tenant_id = "B"`.

---

## 6. Event Bus Scoping

Events carry `tenant_id` in `KortexEvent` metadata. Event subscriptions can filter by `tenant_id` to prevent cross-tenant event processing.

---

## 7. Capability Scoping

Capability execution requests carry `tenant_id` inside `CapabilityRequest`. Dispatcher enforces tenant authorization prior to handler invocation.

---

## 8. Knowledge Engine Scoping

Knowledge graph nodes, entity relationships, and vector search indices are partitioned by `tenant_id`. Graph traversal queries cannot cross tenant boundaries.

---

## 9. AI Orchestration Engine Scoping

RAG context retrieval, prompt assembly, and agent memory instances are strictly scoped by `tenant_id`. Restricted tenant data (`CONFIDENTIAL`, `RESTRICTED`) is never sent to cloud providers.

---

## 10. Document Engine Scoping

Document templates, metadata, version lineage chains, and rendered binary outputs are isolated by `tenant_id`.

---

## 11. Recipe Engine Scoping

Installed recipes, compiled workflow definitions, and trigger registrations are scoped by `tenant_id`.

---

## 12. Business Module Scoping

Modules inherit tenant context automatically via `BusinessContext`. All commands, queries, aggregates, and domain events process data strictly within the caller's `tenant_id`.

---

## 13. Marketplace Scoping

Private Enterprise Marketplaces scope custom modules and templates to authorized `tenant_id` organization accounts.

---

## 14. Tenant Backup & Restore

Supports zero-downtime backup and restore for individual tenants by exporting tenant-scoped `IDataStore` records and `IObjectStore` buckets into encrypted archive archives.

---

## 15. Tenant Migration

Supports tenant data migration between local offline single-tenant nodes and enterprise multi-tenant server environments without code changes.

---

## 16. Acceptance Criteria

- ✓ **Complete Data Isolation**: 100% of entities, queries, file paths, and object keys carry `tenant_id` scoping.
- ✓ **Zero Leakage**: Cross-tenant data access attempts rejected by Kernel authorization middleware.
- ✓ **Independent Backup/Restore**: Individual tenant data exportable and restorable independently.
- ✓ **Local & Cloud Compatible**: Seamless operation on single-tenant local nodes and multi-tenant enterprise servers.
