# KORTEX OS — Capability Registry Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/capability_registry.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)

---

## 1. Capability Naming Conventions

All system capabilities across engines and business modules MUST adhere to the canonical capability naming standard:

$$\text{kortex}.<\text{domain}>.<\text{resource}>.<\text{action}>$$

### Examples:
- `kortex.storage.file.store`
- `kortex.workflow.instance.start`
- `kortex.recipe.compile`
- `kortex.document.operation.execute`
- `kortex.connector.action.execute`
- `kortex.hr.employee.create`
- `kortex.payroll.process.execute`

---

## 2. Capability Metadata (`UniversalCapabilityMetadata`)

Every capability registered in KORTEX OS exposes immutable metadata implementing `UniversalCapabilityMetadata` defined in `shared_domain_models.md`:

- `capability_name`: Canonical capability string.
- `owner_domain`: Owning engine or module domain string.
- `resource_type`: Target resource category string.
- `action`: Specific action code string.
- `description`: Technical description of capability purpose.
- `required_permissions`: List of RBAC permission keys required for execution.
- `is_idempotent`: Boolean flag indicating idempotent execution behavior.
- `is_read_only`: Boolean flag indicating whether capability mutates state.
- `input_schema`: JSON/Pydantic schema defining required input parameters.
- `output_schema`: JSON/Pydantic schema defining returned output structure.

---

## 3. Capability Discovery

The Registry Engine (`kortex.engines.registry`) maintains an in-memory and persistent catalog of all available capabilities. Engines, modules, recipes, and AI agents discover capabilities dynamically via `search_capabilities()` or `get_capability_info()`.

---

## 4. Capability Registration

Capability registration occurs during engine/module startup via Kernel IoC:

```python
# Specification example for capability registration
registry.register_capability(
    metadata=UniversalCapabilityMetadata(
        capability_name="kortex.hr.employee.create",
        owner_domain="kortex.hr",
        resource_type="employee",
        action="create",
        description="Create a new employee record",
        required_permissions=["hr:employee:write"],
        is_idempotent=True,
        is_read_only=False
    ),
    handler=employee_service.create_employee
)
```

---

## 5. Capability Resolution

Kernel Capability Dispatcher resolves requests by matching capability string names to registered handlers, checking active diagnostic status (`IEngineDiagnostics.status() == "READY"`) prior to dispatch.

---

## 6. Capability Invocation

All invocations flow through `CapabilityRequest` wrappers and return `UniversalResult` payloads as specified in `platform_service_contracts.md`. Direct method calls bypassing Kernel routing are strictly prohibited.

---

## 7. Capability Versioning

Capabilities support Semantic Versioning (`SemVer 2.0.0`). Breaking changes to input/output schemas require introducing a new capability name or major namespace version (e.g. `kortex.hr.employee.v2.create`).

---

## 8. Capability Permissions

Execution is intercepted by Kernel authorization middleware (`SecurityEngine`), verifying caller permissions against `required_permissions` before executing capability handlers.

---

## 9. Capability Dependencies

Modules declare required capabilities in `manifest.yaml` under `capabilities_required`. Kernel verifies that all required capabilities exist during module startup.

---

## 10. Capability Deprecation

Deprecated capabilities are marked with `is_deprecated = True` and output non-fatal `UniversalError` warnings in `UniversalResult.warnings` during execution without breaking workflows.

---

## 11. Capability Aliases

The registry supports alias mappings to maintain backward compatibility during namespace updates (e.g. mapping legacy alias `kortex.hr.create_employee` $\rightarrow$ `kortex.hr.employee.create`).

---

## 12. Capability Documentation

Every capability automatically exports self-documenting OpenAPI/JSON-Schema metadata for developer tooling, CLI help, and AI agent tool definitions.

---

## 13. Capability Search

Searchable by domain, resource, action, keyword, permissions, or read-only/idempotence flags.

---

## 14. Capability Marketplace Integration

Marketplace packages (`.kortex-*`) declare `capabilities_provided` and `capabilities_required` in their manifests, allowing automated compatibility checking prior to package installation.

---

## 15. Acceptance Criteria

- ✓ **Canonical Naming Enforced**: 100% of capabilities adhere to `kortex.<domain>.<resource>.<action>`.
- ✓ **Metadata Self-Documenting**: All capabilities provide schema definitions and descriptions.
- ✓ **Security Guarded**: Kernel authorization middleware validates permissions on every invocation.
- ✓ **Dynamic Resolution**: Services discover and invoke capabilities via Kernel Registry without direct imports.
