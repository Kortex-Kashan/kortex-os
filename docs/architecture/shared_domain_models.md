# KORTEX OS — Universal Shared Domain Models Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Phase 2 Architecture Design
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/shared_domain_models.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- KORTEX OS Phase 2 Architecture Design (`docs/architecture/phase2_design.md`)

---

## 1. Purpose

This document defines the canonical, engine-agnostic **Universal Shared Domain Models** of KORTEX OS.

As KORTEX OS evolves into a Local-First Business Operating System, all system engines (Storage, Workflow, Recipe, Document, Connector, AI, Security, etc.), business modules (Finance, HR & Payroll, Operations, CRM, Procurement), recipes, templates, connectors, and Marketplace packages must utilize a single unified domain vocabulary.

To prevent architectural drift, duplicated type definitions, or engine-specific coupling, this specification establishes the universal domain models that every KORTEX OS asset must inherit from or implement.

This document is strictly **engine-agnostic, framework-agnostic, and technology-agnostic**. No engine-specific, database-specific, or format-specific terminology is permitted. All future engines, modules, and extensions MUST reference this specification instead of redefining shared domain models.

---

## 2. Design Principles

Every Universal Shared Domain Model defined in this specification complies with the core principles of KORTEX OS:

1. **Engine-Agnostic Isolation**: Models describe fundamental domain concepts independent of any single engine, module, framework, database, or external library.
2. **Local-First & Offline-First**: Models function 100% locally without requiring internet connectivity, cloud synchronization, or remote validation servers.
3. **Clean Architecture & SOLID**: Model definitions maintain explicit boundaries, single responsibilities, and immutable data contracts. Dependencies always point inward toward abstract domain contracts.
4. **Strict Immutability**: Domain models representing published states, audit logs, version snapshots, or system events are strictly immutable once created.
5. **Universal Type Safety**: All models enforce strong typing, explicit default values, and structural validation specifications.
6. **Marketplace-Ready**: Models include metadata fields for asset identification, Semantic Versioning (`SemVer 2.0.0`), checksum integrity, and Ed25519 digital signature verification.
7. **Enterprise & Multi-Tenant Ready**: Models support multi-tenant organization isolation (`tenant_id`), security classification levels, compliance retention tags, and audit trails.
8. **AI-Ready Abstraction**: Models provide declarative structural metadata, relationship links, and ontology references consumable by AI orchestrators without embedding AI execution dependencies inside the domain layer.

---

## 3. Universal Identity Model (`UniversalIdentity`)

The `UniversalIdentity` model provides a standardized, globally unique identification mechanism across all assets, entities, and processes in KORTEX OS.

### Structural Fields Specification:

- `id`: Canonical UUID string (v4/v7 format) guaranteeing global uniqueness.
- `namespace`: Reverse-domain namespace string (e.g. `kortex.system.kernel`, `kortex.hr.payroll`).
- `canonical_name`: Human-readable canonical identifier string (e.g. `payroll.payslip.generate`).
- `tenant_id`: Multi-tenant organization identifier string (defaults to `default` for single-tenant local execution).
- `scope`: Access scope string (`SYSTEM`, `TENANT`, `USER`, `SESSION`).
- `urn`: Uniform Resource Name string resolving the complete entity path (format: `urn:kortex:<tenant_id>:<namespace>:<id>`).

---

## 4. Universal Metadata Model (`UniversalMetadata`)

The `UniversalMetadata` model provides common descriptive metadata, operational titles, and content hash references for all platform entities.

### Structural Fields Specification:

- `identity`: `UniversalIdentity` object defining the unique entity identity.
- `name`: Technical entity name string.
- `display_name`: Human-readable display label string.
- `description`: Detailed technical description string.
- `created_at`: ISO 8601 UTC timestamp string of entity creation.
- `updated_at`: ISO 8601 UTC timestamp string of last modification.
- `created_by`: Actor identity string of creator (User ID, Agent ID, or System Process).
- `updated_by`: Actor identity string of last modifier.
- `attributes`: Key-value dictionary of custom domain metadata attributes.
- `content_hash`: SHA256 hex digest calculated over entity structural content.

---

## 5. Universal Lifecycle Model (`UniversalLifecycleState`)

The `UniversalLifecycleState` enum defines the standardized state machine governing the operational lifecycle of all system assets, entities, and definitions across KORTEX OS.

### Universal Lifecycle State Catalog:

| Lifecycle State Code | Description | Immutability Status |
| :--- | :--- | :--- |
| `DRAFT` | Initial working state; asset or entity is editable | Mutable |
| `PENDING_REVIEW` | Locked state undergoing validation, approval, or verification | Mutable (Restricted) |
| `ACTIVE` / `PUBLISHED` | Formally issued, validated, and operational state | **Immutable** |
| `SUPERSEEDED` | Replaced by a newer active version in lineage chain | **Immutable** |
| `DEPRECATED` | Active but scheduled for retirement; generates warnings | **Immutable** |
| `ARCHIVED` | Retained exclusively for audit, legal, or historical compliance | **Immutable** |
| `LOGICAL_DELETE` | Soft-deleted state preserving complete audit trails | **Immutable** |

---

## 6. Universal Versioning Model (`UniversalVersion`)

The `UniversalVersion` model enforces Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`) and tracks lineage trees for versioned assets.

### Structural Fields Specification:

- `version_id`: Unique UUID string identifying this specific version snapshot.
- `parent_version_id`: Optional UUID string referencing the immediate predecessor version.
- `major`: Non-negative integer representing breaking architectural or structural changes.
- `minor`: Non-negative integer representing backward-compatible feature additions.
- `patch`: Non-negative integer representing backward-compatible bug fixes or doc updates.
- `semver`: Formatted SemVer 2.0.0 string (`MAJOR.MINOR.PATCH`).
- `prerelease`: Optional pre-release identifier string (e.g. `rc.1`, `beta`).
- `build_metadata`: Optional build metadata string.
- `lineage_path`: List of version IDs tracing historical lineage from root version to current version.
- `is_immutable`: Boolean flag enforcing immutability (`True` if state is `PUBLISHED`, `SUPERSEEDED`, `ARCHIVED`).

---

## 7. Universal Asset Model (`UniversalAsset`)

The `UniversalAsset` model defines standard packaging, storage, and cryptographic integrity parameters for all installable or managed platform assets (Recipes, Templates, Adapters, Modules, Connectors, Profiles).

### Structural Fields Specification:

- `asset_id`: Universal UUID string identifying asset.
- `asset_type`: Asset type string (`RECIPE`, `TEMPLATE`, `CONNECTOR`, `ADAPTER`, `PROFILE`, `MODULE`, `PACKAGE`).
- `manifest`: `KortexAssetManifest` compatible manifest definition.
- `checksum_sha256`: SHA256 hex digest calculated over complete asset payload.
- `digital_signature`: Optional Ed25519 cryptographic signature string verifying publisher.
- `size_bytes`: Integer size of asset binary payload in bytes.
- `mime_type`: Content MIME type string.
- `storage_key`: Object storage key reference in Storage Engine (`IObjectStore`).
- `bucket_name`: Storage bucket reference (defaults to `assets`).

---

## 8. Universal Ownership Model (`UniversalOwnership`)

The `UniversalOwnership` model establishes ownership chains, access controls, and organization boundaries.

### Structural Fields Specification:

- `owner_id`: Primary owner identifier string.
- `owner_type`: Type of owner (`USER`, `ORGANIZATION`, `SYSTEM`, `AGENT`, `SERVICE`).
- `organization_id`: Organization boundary identifier string.
- `tenant_id`: Multi-tenant isolation identifier string.
- `created_by_user_id`: Optional User ID of human creator.
- `created_by_agent_id`: Optional Agent ID of AI creator.
- `created_by_process_id`: Optional Process ID of system engine creator.

---

## 9. Universal Classification Model (`UniversalClassification`)

The `UniversalClassification` model specifies security boundaries, confidentiality levels, and compliance retention rules.

### Structural Fields Specification:

- `classification_level`: Security classification rating (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`).
- `compliance_flags`: List of compliance governance flags (e.g. `GDPR`, `HIPAA`, `FINANCIAL_AUDIT`).
- `retention_policy_id`: Optional retention policy identifier string.
- `encryption_required`: Boolean flag indicating encryption requirement.
- `export_restricted`: Boolean flag indicating regulatory export constraints.

---

## 10. Universal Labels & Tags (`UniversalTagging`)

The `UniversalTagging` model provides consistent indexing, filtering, and categorization primitives across all platform assets.

### Structural Fields Specification:

- `tags`: Key-value dictionary of structured domain attributes (e.g. `{"department": "finance", "year": "2026"}`).
- `labels`: List of unstructured metadata strings (e.g. `["monthly", "payroll", "approved"]`).
- `categories`: List of broad domain taxonomy strings (e.g. `["financial_report", "tax"]`).
- `system_tags`: List of internal engine-managed operational tags (e.g. `["_indexed", "_verified"]`).

---

## 11. Universal Audit Model (`UniversalAuditEntry`)

The `UniversalAuditEntry` model defines immutable audit log records for tracking every operation across KORTEX OS.

### Structural Fields Specification:

- `audit_id`: Unique UUID string identifying audit log record.
- `timestamp_utc`: ISO 8601 UTC timestamp string of action.
- `action`: Canonical capability or action name executed (`kortex.<domain>.<resource>.<action>`).
- `actor_id`: Identifier of actor performing action (User ID, Agent ID, System Engine ID).
- `actor_type`: Type of actor (`HUMAN`, `AI_AGENT`, `SYSTEM_ENGINE`, `CONNECTOR`).
- `tenant_id`: Multi-tenant organization identifier.
- `resource_id`: Identifier of target resource acted upon.
- `previous_state_hash`: SHA256 content hash of resource prior to action.
- `new_state_hash`: SHA256 content hash of resource after action.
- `client_ip`: Optional client IP address or execution node location.
- `context`: Structured dictionary of execution context data and parameters.

---

## 12. Universal Reference Model (`UniversalReference`)

The `UniversalReference` model provides a standardized mechanism for cross-entity references without direct object instantiation or tight circular coupling.

### Structural Fields Specification:

- `ref_id`: Target entity identifier string.
- `ref_type`: Target entity type code.
- `canonical_urn`: Complete URN string (`urn:kortex:<tenant_id>:<namespace>:<ref_id>`).
- `entity_type`: High-level domain entity category.
- `tenant_id`: Target tenant identifier.
- `version_constraint`: Optional SemVer range constraint string (e.g. `>=1.0.0`).

---

## 13. Universal Relationship Model (`UniversalRelationship`)

The `UniversalRelationship` model defines semantic directed graph relationships between platform assets and entities.

### Structural Fields Specification:

- `relationship_id`: Unique UUID string identifying relationship.
- `source_ref`: `UniversalReference` object targeting source entity.
- `target_ref`: `UniversalReference` object targeting target entity.
- `relationship_type`: Semantic relationship type (`DEPENDS_ON`, `PARENTS`, `DERIVED_FROM`, `SUPERSEDES`, `LINKS_TO`, `ATTACHED_TO`, `VALIDATES`).
- `weight`: Optional float weight or priority rating (defaults to `1.0`).
- `metadata`: Key-value dictionary of relationship attributes.

---

## 14. Universal Time Model (`UniversalTimestamp`)

The `UniversalTimestamp` model provides standard date, time, and temporal boundary tracking across all operations.

### Structural Fields Specification:

- `created_at_utc`: Mandatory ISO 8601 UTC timestamp string of creation.
- `updated_at_utc`: Mandatory ISO 8601 UTC timestamp string of modification.
- `expires_at_utc`: Optional ISO 8601 UTC timestamp string of expiration.
- `effective_from_utc`: Optional ISO 8601 UTC timestamp string when entity becomes active.
- `effective_to_utc`: Optional ISO 8601 UTC timestamp string when entity deactivates.
- `timezone`: Timezone identifier string (defaults to `UTC`).

---

## 15. Universal Result Model (`UniversalResult`)

The `UniversalResult` model provides a unified response structure for capability execution, engine operations, pipeline tasks, and system queries.

### Structural Fields Specification:

- `request_id`: Unique UUID string matching operational request.
- `status`: Result status code (`SUCCESS`, `FAILURE`, `PARTIAL_SUCCESS`, `CANCELLED`).
- `payload`: Optional result data object or dictionary.
- `errors`: List of `UniversalError` objects explaining failures.
- `warnings`: List of `UniversalError` objects detailing non-fatal warnings.
- `execution_duration_ms`: Float execution duration in milliseconds.
- `timestamp_utc`: ISO 8601 UTC timestamp string of completion.

---

## 16. Universal Error Model (`UniversalError`)

The `UniversalError` model provides structured exception reporting, machine-readable error codes, and contextual diagnostic details.

### Structural Fields Specification:

- `error_code`: Standardized machine-readable error code string (e.g. `ENTITY_NOT_FOUND`, `VALIDATION_FAILED`, `PERMISSION_DENIED`).
- `message`: Human-readable error message string.
- `domain`: Domain or engine originating error (e.g. `kortex.storage`, `kortex.workflow`).
- `severity`: Error severity level (`INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- `details`: Structured dictionary of diagnostic error context.
- `timestamp_utc`: ISO 8601 UTC timestamp string of occurrence.
- `trace_id`: Correlation trace ID for distributed diagnostic tracking.

---

## 17. Universal Validation Model (`UniversalValidationReport`)

The `UniversalValidationReport` model encapsulates schema verification results, field validation checks, and constraint reports.

### Structural Fields Specification:

- `is_valid`: Boolean flag indicating whether validation succeeded without fatal errors.
- `errors`: List of fatal `UniversalError` objects.
- `warnings`: List of non-fatal `UniversalError` warning objects.
- `missing_fields`: List of mandatory field names missing from input data.
- `invalid_fields`: List of field names violating type or constraint rules.
- `validated_at_utc`: ISO 8601 UTC timestamp string of validation execution.

---

## 18. Universal Capability Metadata (`UniversalCapabilityMetadata`)

The `UniversalCapabilityMetadata` model describes registered capabilities in the Kernel IoC container and Registry Engine using the canonical capability naming format:

$$\text{kortex}.<\text{domain}>.<\text{resource}>.<\text{action}>$$

### Structural Fields Specification:

- `capability_name`: Canonical capability string (e.g. `kortex.storage.file.store`).
- `owner_domain`: Owning engine or module domain string.
- `resource_type`: Target resource category string.
- `action`: Specific action code string.
- `description`: Technical description of capability purpose.
- `required_permissions`: List of RBAC permission keys required for execution.
- `is_idempotent`: Boolean flag indicating whether capability execution is idempotent.
- `is_read_only`: Boolean flag indicating whether capability modifies state.

---

## 19. Universal Search Metadata (`UniversalSearchMetadata`)

The `UniversalSearchMetadata` model provides standardized indexing structures for search, filtering, and indexing across engines.

### Structural Fields Specification:

- `index_id`: Unique search index entry identifier string.
- `searchable_text`: Consolidated text string for full-text search indexing.
- `indexed_fields`: Dictionary of key fields structured for field-level filtering.
- `ranking_weight`: Float weight rating for search result relevance ordering.
- `filters`: Key-value map of exact-match filter categories.
- `indexed_at_utc`: ISO 8601 UTC timestamp string of last index update.

---

## 20. Inheritance Rules

To guarantee consistency while preserving Clean Architecture, all specialized domain models across engines, modules, and extensions MUST follow these strict inheritance rules:

1. **Composition Over Inheritance**: Specialized models should prefer embedding universal models (`UniversalIdentity`, `UniversalMetadata`, `UniversalTimestamp`) as sub-objects rather than relying on deep class inheritance hierarchies.
2. **Single Primary Identity**: Specialized models MUST contain exactly one `UniversalIdentity` instance. Re-defining custom `id` fields with conflicting types is forbidden.
3. **Immutable Lifecycle Preservation**: Subclasses inheriting `UniversalLifecycleState` MUST enforce immutability when state transitions to `ACTIVE`/`PUBLISHED`, `SUPERSEEDED`, `ARCHIVED`, or `LOGICAL_DELETE`.
4. **No Structural Overriding**: Subclasses MUST NOT override fields defined in universal models with incompatible types. Subclasses may extend attributes via specialized dictionaries or nested sub-models.
5. **Clean Domain Boundaries**: Universal models MUST NOT import or reference engine-specific classes, database ORM models, or web framework schemas.

---

## 21. Architecture Rules

1. **Engine-Agnostic Core**: Shared domain models belong strictly inside the core shared domain abstractions (`kortex.shared.domain`). No engine-specific dependencies (e.g., SQLAlchemy, Jinja2, Pydantic plugins, HTTP frameworks) may be required by core shared models.
2. **Zero Framework Pollution**: Models are defined using pure language primitives or standard typing protocols to ensure portability across local offline execution environments, CLI tools, server runtimes, and worker nodes.
3. **Storage Independence**: Shared domain models describe pure domain concepts. Persistence mapping (ORM definitions, database tables, object store keys) belongs to Storage Engine adapters.
4. **Dependency Direction**: Engines and business modules depend inward on `shared_domain_models.md`. Universal domain models NEVER depend on outer engines or modules.
5. **Backward Compatibility**: Any modifications to universal shared domain models MUST preserve backward compatibility. Field additions must provide explicit default values. Breaking changes require major SemVer increments.

---

## 22. Acceptance Criteria

The Universal Shared Domain Models specification shall be considered complete and authoritative when all of the following criteria are met:

- ✓ **Canonical Authority**: Serves as the single reference specification for shared domain models across KORTEX OS.
- ✓ **Engine-Agnostic**: Contains zero engine-specific, technology-specific, or database-specific terminology.
- ✓ **Comprehensive Sections**: Fully specifies all 22 required sections (Identity, Metadata, Lifecycle, Versioning, Asset, Ownership, Classification, Labels/Tags, Audit, Reference, Relationship, Time, Result, Error, Validation, Capability Metadata, Search Metadata, Inheritance Rules, Architecture Rules, Acceptance Criteria).
- ✓ **Clean Architecture Compliant**: Dependency rules strictly enforced; shared models have zero external infrastructure dependencies.
- ✓ **SOLID Compliant**: Models satisfy Single Responsibility, Open/Closed, and Interface Segregation principles.
- ✓ **Offline & Local First**: Designed to function 100% offline without cloud dependencies.
- ✓ **Enterprise & Marketplace Ready**: Fully supports multi-tenancy (`tenant_id`), security classifications, SemVer versioning, SHA256 checksums, and Ed25519 digital signatures.
