# KORTEX OS — Document Engine Implementation Specification

Status: Approved for Implementation
Version: 3.0.0
Authority: KORTEX OS Engineering Constitution & Phase 2 Architecture Design
Target Release: KORTEX OS Phase 2: Business Foundation
Target File: `docs/architecture/document_engine_implementation_spec.md`

Depends On:
- Phase 1 Foundation (Kernel Engine, Event Engine, Registry Engine, Configuration Engine)
- Storage Engine (`kortex.engines.storage`)

---

## 1. Executive Summary & Scope

The Document Engine (`kortex.engines.document`) is an enterprise-grade, adapter-driven system engine responsible for managing document lifecycles, declarative template libraries, hybrid data binding, version chains, document lineage, Document Operation Profiles, Adapter Pipelines, document ontology, preview generation stubs, document intelligence provider abstractions, AI recommendation interfaces, execution recovery hooks, and persistence across KORTEX OS.

As defined in the KORTEX OS Engineering Constitution (Article 10) and Phase 2 Architecture Design (`docs/architecture/phase2_design.md`), the Document Engine functions exclusively as an infrastructure adapter host, pipeline coordinator, lifecycle manager, and capability orchestrator. It contains zero business logic and zero technology-specific implementations.

The Phase 2 implementation scope of the Document Engine comprises:

1. **Document Operation Profiles (`DocumentOperationProfile`)**: Technology-independent process specifications defining business operations, required templates, adapter pipelines, validation rules, permissions, output rules, post-processing, and lifecycle rules.
2. **Adapter Pipelines (`AdapterPipeline`)**: Stage-based pipeline execution framework supporting sequential, conditional, optional, and parallel adapter execution stages (e.g. Normalization $\rightarrow$ Macro Processing $\rightarrow$ Transformation $\rightarrow$ Verification $\rightarrow$ Persistence).
3. **Document Adapter Registry & Metadata (`DocumentAdapterRegistry`)**: Centralized, thread-safe registry for registering, discovering, and resolving document adapters based on immutable Marketplace-ready adapter metadata (`KortexAssetManifest` compatible).
4. **Adapter Capability Engine (`AdapterCapability`)**: Fine-grained capability declaration framework (`Preview`, `Generate`, `Convert`, `Transform`, `Merge`, `Split`, `Extract`, `OCR`, `Charts`, `Pivot Tables`, `Macros`, `Digital Signature`, `QR Code`, `Barcode`, `Compression`, `Encryption`, `Validation`, `Printing`).
5. **Adapter Sandbox Architecture (`AdapterSandbox`)**: Sandboxed execution isolation defining permission boundaries, capability allowances, sandboxed workspace paths, memory limits, timeouts, structured logging, and audit metadata.
6. **Local-First Template Library (`TemplateLibrary`)**: Storage, indexing, search, versioning, and installation framework for reusable, technology-independent business templates (Invoices, Payslips, Salary Certificates, Quotations, Purchase Orders, Employment Letters, Loan Letters, Leave Forms, Warning Letters, Contracts, Certificates).
7. **Marketplace Compatibility Architecture**: Asset manifest verification, SemVer resolution, SHA256 checksum validation, Ed25519 digital signature validation, and dependency resolution for installable Templates, Adapters, and Operation Profiles.
8. **Declarative Document Ontology & Hybrid Data Binding**: Declarative document structural schemas (e.g. Payslip ontology defining Employee, Allowance, Deduction, Net Salary relationships) and hybrid data binder for strong type validation, placeholder validation, computed field resolution, and auto-complete metadata.
9. **Document Lifecycle, Versioning & Lineage Manager**: Immutable state machine managing transitions (`Draft`, `Review`, `Published`, `Superseded`, `Archived`, `Logical Delete`), version chains (`Document ID`, `Version ID`, `Parent Version`), document lineage graphs, and immutable published documents.
10. **Document Intelligence & AI Recommendation Interfaces**: Declarative ontology consumption, `IDocumentIntelligenceProvider` protocol, `IDocumentRecommendationProvider` protocol for intelligent template/pipeline recommendations, Knowledge References, and incremental intelligence updates (Provider interfaces only; AI optional design).
11. **Document Execution Recovery Architecture (`IDocumentRecoveryProvider`)**: Execution state checkpointing, failure metadata collection, retry hooks, rollback stacks, and resume interfaces for Workflow Engine coordination.
12. **Document Engine Core Facade & Diagnostics**: `DocumentEngine` facade inheriting `BaseEngine`, implementing capability handlers, lifecycle hooks, and the standardized `IEngineDiagnostics` interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
13. **Storage Engine Integration**: Exclusive use of `StorageEngine` abstractions (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) for template retrieval, schema caching, binary output persistence, version lineage tracking, and audit trails.
14. **Security Integration Points**: Classification, labels, verification metadata, verification service integration (`IVerificationService`), capability checks, security metadata, and audit events.

---

## 2. Architectural Hierarchy & Technology Independence

To guarantee long-term maintainability, enterprise readiness, and modularity, the Document Engine strictly enforces technology independence. Business modules, recipes, and capabilities never interact directly with underlying formatting technologies, file formats, or third-party libraries. All document operations flow through a six-layer architectural hierarchy:

```
                      ┌──────────────────────────────────────────┐
                      │             Business Module              │
                      │  (Finance, HR & Payroll, Operations)     │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │            Business Operation            │
                      │  (e.g., Generate Monthly Payroll Slips)  │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │        Document Operation Profile        │
                      │  (Declarative rules, template & pipeline)│
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │             Adapter Pipeline             │
                      │  (Stage 1 -> Stage 2 -> Stage 3 -> Storage)│
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │             Document Adapter             │
                      │  (Sandboxed plugin executing capability) │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │         Technology Implementation        │
                      │  (Hidden behind adapter abstraction)     │
                      └──────────────────────────────────────────┘
```

### Hierarchy Rules:
1. **Business Modules** invoke high-level **Business Operations** via canonical capabilities (`kortex.document.operation.execute`).
2. **Business Operations** execute declared **Document Operation Profiles**.
3. **Document Operation Profiles** assemble and execute **Adapter Pipelines**.
4. **Adapter Pipelines** dispatch data to one or more **Document Adapters** inside isolated sandboxes.
5. **Document Adapters** interface with concrete **Technology Implementations**.
6. **Technology Implementations** remain 100% encapsulated behind Document Adapters and are never exposed to business modules or public APIs.

---

## 3. Out of Scope

To preserve strict roadmap discipline, maintain zero unnecessary dependencies, and adhere to Clean Architecture, the following items are explicitly **OUT OF SCOPE** for the Document Engine core:

1. **Direct Technology Exposure**: Exposing technology-specific file format parameters (e.g. PDF, DOCX, XLSX, HTML, PNG, etc.) in public engine APIs is strictly prohibited. All technologies remain hidden behind adapters.
2. **Core Macro Execution**: Executing macros, scripts, or embedded code inside the engine core is forbidden. Macros are recognized as adapter-level capabilities executed exclusively inside isolated sandboxed adapters.
3. **Business Domain Calculations**: Business rules, financial calculations, payroll formulas, tax calculations, and invoice totals belong strictly inside business modules or declarative computed field bindings.
4. **Database Entity Editing**: As mandated by Article 10 of the Engineering Constitution, the Document Engine shall never edit, mutate, or modify underlying business database entities or records.
5. **Workflow & Recipe Orchestration**: Multi-step business workflows and state machine scheduling belong exclusively to the Workflow Engine.
6. **Direct Protocol Networking & Messaging**: Transmitting output documents via email, SMS, WhatsApp, printer hardware, or external webhooks belongs exclusively to the Connector Engine.
7. **Direct Storage Access**: Direct filesystem I/O (`open()`, `os.path`, `shutil`), direct database driver calls (`sqlite3`, `asyncpg`), or cloud SDKs are forbidden. All persistence flows through the Storage Engine.
8. **Cryptographic & RBAC Implementations**: Key management, certificate generation, and RBAC policy evaluation logic belong to the Security Engine and Kernel middleware.
9. **AI Model Execution**: Machine learning inference, LLM model hosting, and OCR vision engines belong to external AI providers. Document Engine exposes `IDocumentIntelligenceProvider` and `IDocumentRecommendationProvider` protocols only.
10. **Front-End User Interfaces**: Document view widgets, canvas editors, or web preview UI components.

---

## 4. Document Operation Profiles

A Document Operation Profile (`DocumentOperationProfile`) represents an executable, technology-independent specification of a business document process. Operation profiles allow business modules to trigger complex document generation, verification, and transformation workflows without knowing which adapters or technologies are handling the execution.

### Profile Attributes Specification:

- `id`: Canonical UUID string identifying the operation profile.
- `name`: Human-readable profile name (e.g. `EmployeePayslipGenerationProfile`).
- `namespace`: Reverse-domain identifier (e.g. `kortex.hr.payroll.payslip`).
- `version`: Semantic Version string of the profile (e.g. `1.2.0`).
- `description`: Detailed operational description.
- `business_operation`: High-level business operation name (e.g. `GENERATE_PAYROLL_SLIP`).
- `required_template`: `TemplateReference` specifying required declarative template ID and SemVer constraint.
- `adapter_pipeline`: `AdapterPipelineDefinition` defining the list of adapter execution stages.
- `validation_rules`: `ValidationRuleSet` specifying pre-execution schema and data constraint rules.
- `permissions`: List of RBAC permission keys required to execute the profile.
- `output_rules`: `OutputRuleSpecification` defining storage bucket, naming format, and indexing metadata.
- `post_processing`: List of post-execution pipeline actions (e.g. thumbnail preview generation, verification hash creation).
- `lifecycle_rules`: `LifecycleRuleSpecification` defining initial lifecycle state (`Draft`, `Review`, `Published`) and retention policies.

---

## 5. Adapter Pipelines & Macro Adapter Integration

Rather than assuming a rigid one-to-one mapping between operations and adapters, the Document Engine utilizes **Adapter Pipelines** (`AdapterPipeline`). An Adapter Pipeline coordinates a sequence of sandboxed document adapters to perform complex multi-step document operations.

### 5.1 Pipeline Execution Modes

1. **Sequential Execution**: Stages execute in strict topological order, passing output payloads from one adapter to the next.
2. **Conditional Execution**: Stages evaluate execution conditions against binding context before dispatching payload to an adapter.
3. **Optional Adapters**: Non-critical stages (e.g. watermark application, preview thumbnail generation) can be flagged as optional, allowing the pipeline to continue if an optional adapter fails.
4. **Parallel Execution Preparation**: Independent pipeline stages (e.g. dual verification and thumbnail extraction) are structured to support future non-blocking parallel execution.

### 5.2 Example Adapter Pipeline Flow (Macro & Verification Integration)

```
┌──────────────┐     ┌────────────────────┐     ┌─────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│ Input Data   │ ──> │ Normalizer Adapter │ ──> │  Macro Adapter  │ ──> │ Spreadsheet Adapter │ ──> │ Verification Adapter │ ──> │ Storage Engine  │
│ (Raw Context)│     │ (Format Cleaning)  │     │ (Rule Execution)│     │ (Layout Generation) │     │ (Integrity Check)    │     │ (IObjectStore)  │
└──────────────┘     └────────────────────┘     └─────────────────┘     └─────────────────────┘     └──────────────────────┘     └─────────────────┘
```

### 5.3 Macro Adapter Architecture

Macros are officially recognized as first-class, adapter-level technology capabilities in KORTEX OS. 

- **Isolation Constraint**: Macros are executed **only through isolated Document Adapters** operating inside an `AdapterSandbox`.
- **Core Engine Insulation**: Macros never enter, contaminate, or execute inside the Document Engine core or Kernel.
- **Workflow Pattern**: Raw data or structured tables are passed into a sandboxed Macro Adapter (e.g. applying formatting rules, auto-calculating layout bounds), passed to a Spreadsheet/Layout Adapter, verified by a Verification Adapter, and persisted to `IObjectStore`.

---

## 6. Adapter Capabilities & Immutable Metadata

Document adapters advertise their capabilities to the engine. Operation profiles resolve and select appropriate adapters based on advertised capabilities rather than technology names.

### 6.1 Adapter Capability Catalog (`AdapterCapability` Enum)

| Capability Code | Description |
| :--- | :--- |
| `PREVIEW` | Rasterize or render page preview thumbnails |
| `GENERATE` | Generate new document output from declarative schema and binding context |
| `CONVERT` | Convert document payload between structural representations |
| `TRANSFORM` | Apply structural transformations, rotation, scaling, or page re-ordering |
| `MERGE` | Combine multiple document payloads into a single multi-page asset |
| `SPLIT` | Extract specific pages or sections from a document payload |
| `EXTRACT` | Extract raw text, tabular data, or embedded metadata from document payload |
| `OCR` | Perform optical character recognition on image/scanned document payloads |
| `CHARTS` | Render visual data charts, graphs, and trend visualizers |
| `PIVOT_TABLES` | Compute and render dynamic pivot tables and analytical summaries |
| `MACROS` | Execute sandboxed automation macros and formatting scripts |
| `DIGITAL_SIGNATURE` | Apply cryptographic signature metadata and verification blocks |
| `QR_CODE` | Generate and embed dynamic 2D QR Code visual blocks |
| `BARCODE` | Generate and embed 1D/2D linear barcode visual blocks |
| `COMPRESSION` | Compress document payload size using lossless algorithms |
| `ENCRYPTION` | Apply structural encryption and security payload wrappers |
| `VALIDATION` | Verify structural compliance, visual layout bounds, and integrity |
| `PRINTING` | Format payload for physical spooling or print layout optimization |

### 6.2 Immutable Adapter Metadata (`AdapterMetadata`)

Every document adapter plugin must expose an immutable `AdapterMetadata` object compliant with `KortexAssetManifest` for seamless Marketplace integration:

- `adapter_id`: Unique canonical UUID string of adapter plugin.
- `display_name`: Human-readable adapter display name.
- `vendor`: Vendor or author organization string.
- `author`: Author contact details.
- `version`: Semantic Version string (e.g. `1.4.0`).
- `license`: Software license model string (e.g. `MIT`).
- `description`: Technical description of adapter capabilities.
- `homepage`: Documentation or vendor web URL.
- `supported_capabilities`: List of `AdapterCapability` enum values advertised by adapter.
- `supported_operations`: List of `DocumentOperationType` enum values supported by adapter.
- `supports_preview`: Boolean flag indicating native page preview capability.
- `supports_streaming`: Boolean flag indicating binary payload streaming support.
- `supports_macros`: Boolean flag indicating sandboxed macro execution capability.
- `supports_security`: Boolean flag indicating security classification handling capability.
- `supports_versioning`: Boolean flag indicating version metadata preservation capability.

---

## 7. Document Template Library & Marketplace Compatibility

### 7.1 Local-First Document Template Library (`TemplateLibrary`)

The Document Engine includes a local-first **Template Library** (`TemplateLibrary`) for storing, indexing, versioning, searching, and installing reusable business templates.

- **Pre-installed Standard Templates**: Includes standard, technology-independent declarative templates for:
  - Invoices (`invoice.declarative.v1`)
  - Payslips (`payslip.declarative.v1`)
  - Salary Certificates (`salary_certificate.declarative.v1`)
  - Quotations (`quotation.declarative.v1`)
  - Purchase Orders (`purchase_order.declarative.v1`)
  - Employment Letters (`employment_letter.declarative.v1`)
  - Loan Letters (`loan_letter.declarative.v1`)
  - Leave Forms (`leave_form.declarative.v1`)
  - Warning Letters (`warning_letter.declarative.v1`)
  - Contracts (`contract.declarative.v1`)
  - Certificates (`certificate.declarative.v1`)
- **Local-First Storage**: Templates are stored in `IFileStore` and indexed in `IDataStore` relational tables. Templates function 100% offline without cloud dependencies.
- **Searchable Index**: Searchable by category, tags, namespace, version, and required capabilities.
- **Technology Independence**: Templates define declarative schemas, layout regions, and placeholder constraints without referencing underlying rendering tools or file extensions.

### 7.2 Marketplace Compatibility Architecture

All Document Engine assets—including **Templates**, **Adapters**, and **Operation Profiles**—are designed as installable Marketplace assets (`MarketplaceAsset`).

Each Marketplace asset package (`.kortex-template`, `.kortex-adapter`, `.kortex-profile`) defines:

1. **Manifest (`KortexAssetManifest`)**: Asset identification, namespace, version, author, and description.
2. **Version (SemVer 2.0.0)**: Strict semantic versioning for compatibility resolution.
3. **Compatibility Declaration**: Kernel compatibility (`kernel_compatibility: ">=0.1.0"`) and engine dependency requirements.
4. **Checksum (SHA256)**: SHA256 cryptographic hash calculated over the complete package payload.
5. **Digital Signature (Ed25519)**: Cryptographic signature for verifying asset publisher authenticity.
6. **Dependencies Map**: Map of required asset packages and compatible SemVer ranges.

---

## 8. Declarative Document Ontology & Hybrid Data Binding

### 8.1 Document Ontology System (`DocumentOntology`)

The Document Engine introduces a declarative **Document Ontology** system. Document Ontology allows KORTEX OS to understand the semantic structure, entities, and field relationships of business documents **without requiring active AI models or LLM calls**.

#### Declarative Ontology Examples:

1. **Payslip Ontology**:
   - Entity: `Payslip`
   - Child Structures: `EmployeeInfo` (ID, Name, Designation, Department), `EarningsBreakdown` (Basic Salary, Allowances, Overtime), `DeductionsBreakdown` (Tax, Insurance, Pension), `Summary` (Gross Salary, Total Deductions, Net Salary).
   - Invariant Rule: $\text{Net Salary} = \text{Gross Salary} - \text{Total Deductions}$.
2. **Invoice Ontology**:
   - Entity: `Invoice`
   - Child Structures: `CustomerInfo` (ID, Name, TaxID, Address), `LineItems` (ItemCode, Description, Quantity, UnitPrice, LineTotal), `TaxSummary` (TaxableAmount, TaxRate, TaxAmount), `GrandTotal`.
   - Invariant Rule: $\text{Grand Total} = \sum(\text{Line Totals}) + \text{Tax Amount}$.

Document Intelligence providers consume Document Ontology schemas to extract and validate structured document metadata.

### 8.2 Hybrid Data Binding System (`TemplateBinder`)

Templates combine declarative HTML/JSON layout structural schemas with data contexts using the `TemplateBinder`:

- **Template Schemas**: Declarative specification of placeholders, data types, required fields, and layout blocks.
- **Placeholder Validation**: Verifies that all mandatory placeholders declared in `TemplateSchema` exist within the supplied `BindingContext`.
- **Strong Type Validation**: Ensures bound data values match expected field types (`string`, `number`, `boolean`, `array`, `object`).
- **Computed Field Resolution**: Evaluates declarative arithmetic and logical expressions (e.g. calculating line item totals, tax amounts, or formatted dates) without executing arbitrary code scripts.
- **Auto-complete Metadata**: Exposes structural field metadata to UI template builders for intelligent placeholder auto-completion.
- **Validation Reports (`ValidationReport`)**: Produces structured validation reports detailing validity status, missing placeholders, type mismatches, and resolved computed field counts.

---

## 9. Document Lifecycle, Versioning & Lineage Management

The Document Engine owns the lifecycle state transitions, version integrity, and lineage tracking for all document assets in KORTEX OS.

```
                  ┌─────────┐
                  │  Draft  │
                  └────┬────┘
                       │
                       ▼
                  ┌─────────┐
                  │ Review  │
                  └────┬────┘
                       │
                       ▼
                 ┌───────────┐         ┌────────────┐
                 │ Published │ ──────> │ Superseded │
                 └─────┬─────┘         └─────┬──────┘
                       │                     │
                       ▼                     ▼
                 ┌───────────┐         ┌────────────┐
                 │ Archived  │         │ Logical    │
                 └───────────┘         │ Delete     │
                                       └────────────┘
```

### 9.1 Lifecycle States (`DocumentLifecycleState` Enum)

1. **`DRAFT`**: Initial working version of a document asset. Data values and templates may be updated.
2. **`REVIEW`**: Document version is locked for formal review and human approval.
3. **`PUBLISHED`**: Formally approved, active document version. **Published documents are strictly IMMUTABLE**.
4. **`SUPERSEEDED`**: A previously published document version that has been replaced by a newer published version in the lineage chain. Remains **immutable**.
5. **`ARCHIVED`**: Retained for historical audit or legal compliance requirements. Remains **immutable**.
6. **`LOGICAL_DELETE`**: Soft-deleted state preserving complete audit trails while hiding asset from active operational lookups. Remains **immutable**.

### 9.2 Version Chains & Lineage Tracking

- **Version Identifiers**: Every document version is tracked using three distinct keys:
  - `document_id`: Canonical UUID string representing the root document entity.
  - `version_id`: Unique UUID string representing a specific version instance.
  - `parent_version_id`: Optional UUID string referencing the immediate predecessor version.
- **Document Lineage Graph**: The engine maintains parent-child relationship graphs enabling full historical traversal, diff inspection, and lineage visualization.
- **Immutable Published Documents**: Once a document version transitions to `PUBLISHED`, its binary payload, SHA256 checksum, metadata, and binding context are locked permanently against edits. Any subsequent modifications require spawning a new `DRAFT` version with an updated `version_id` and a link to the `parent_version_id`.

---

## 10. Document Intelligence & AI Recommendation Provider Abstractions

The Document Engine includes formal interfaces for Document Intelligence and AI Recommendations while maintaining an **AI Optional Design** (the engine functions 100% deterministically without active AI models).

### 10.1 Document Intelligence Provider Interface (`IDocumentIntelligenceProvider`)

Defines abstract contract for document analysis engines:

- `analyze_document()`: Consumes document binary payload and `DocumentOntology` to extract domain concepts, structured field values, and confidence scores.
- `update_intelligence_incrementally()`: Processes delta context changes to update document intelligence metadata without full re-analysis.
- `extract_knowledge_references()`: Identifies and links entity references between the document asset and the KORTEX Knowledge Engine.

### 10.2 AI Recommendation Provider Interface (`IDocumentRecommendationProvider`)

Defines abstract contract for intelligent process recommendation providers:

- `recommend_template()`: Analyzes user intent, input data schema, and installed templates to recommend the optimal `TemplateDefinition`.
- `recommend_operation_profile()`: Recommends the appropriate `DocumentOperationProfile` based on business context and available permissions.
- `recommend_adapter_pipeline()`: Analyzes input data characteristics, output requirements, and installed adapters to compose an optimal `AdapterPipeline`.

---

## 11. Adapter Sandbox & Execution Recovery Architecture

### 11.1 Adapter Sandbox Architecture (`AdapterSandbox`)

Every document adapter executes within an isolated **Adapter Sandbox** (`AdapterSandbox`) to ensure platform security, fault isolation, and resource protection.

The Adapter Sandbox defines and enforces:
- `permissions`: Explicit list of RBAC permissions granted to the sandboxed execution context.
- `allowed_capabilities`: Strict list of `AdapterCapability` enum values the adapter is permitted to invoke.
- `temporary_workspace`: Sandboxed, isolated workspace path in `IFileStore` automatically cleaned up post-execution.
- `timeout_seconds`: Hard execution time limit (default 30s) after which adapter execution is terminated.
- `memory_limit_mb`: Hard memory threshold allocated to sandboxed adapter process.
- `structured_logging`: Isolated logger intercepting adapter outputs and tagging logs with adapter execution context.
- `audit_metadata`: Metadata recording execution start time, duration, resource usage, and exit codes.

### 11.2 Document Execution Recovery Architecture (`IDocumentRecoveryProvider`)

Document operation failures must never leave document assets in corrupted or inconsistent states. The Document Engine exposes interfaces for execution recovery, coordinated by the Workflow Engine:

- **Retry**: Re-dispatches failed pipeline stages with exponential backoff multipliers.
- **Rollback**: Executes reverse compensation actions, removing partial binary outputs from `IObjectStore` upon unrecoverable pipeline failure.
- **Checkpoint**: Persists intermediate stage payloads to `ICacheStore` after each successful pipeline stage.
- **Resume**: Resumes pipeline execution from the last valid stage checkpoint following system restarts or transient failures.
- **Failure Metadata**: Captures detailed failure context (`stage_id`, `adapter_id`, `error_code`, `stack_trace_snippet`) for telemetry and administrative inspection.

---

## 12. Folder Structure & Module Responsibilities

All Document Engine source code resides inside `backend/src/kortex/engines/document/`.

```
backend/src/kortex/engines/document/
├── __init__.py                # Package exports (DocumentEngine, models, interfaces)
├── engine.py                  # DocumentEngine core facade inheriting BaseEngine
├── interfaces.py              # Abstract interfaces (IDocumentEngine, IBaseDocumentAdapter, etc.)
├── models.py                  # Pydantic v2 domain models, lifecycle enums, and schemas
├── exceptions.py              # Strongly-typed hierarchy of document engine exceptions
├── registry.py                # DocumentAdapterRegistry for managing document adapters
├── loader.py                  # DocumentAdapterLoader for dynamic adapter discovery
├── base_adapter.py            # BaseDocumentAdapter abstract base class
├── sandbox.py                 # AdapterSandbox for isolated adapter execution
├── pipeline.py                # AdapterPipeline coordinator for stage execution
├── profiles.py                # DocumentOperationProfile manager & capability mapping
├── library.py                 # Local-first TemplateLibrary (Invoice, Payslip, etc.)
├── ontology.py                # Declarative DocumentOntology schemas & validator
├── template.py                # Template Schema, Validator, Binder, & Computed Fields
├── lifecycle.py               # Document Lifecycle, Versioning & Lineage Manager
├── intelligence.py            # IDocumentIntelligenceProvider & IDocumentRecommendationProvider
├── recovery.py                # Execution recovery interfaces (retry, rollback, checkpoint)
├── preview.py                 # DocumentPreviewGenerator stub for thumbnail creation
├── security.py                # Security Integration metadata & verification interfaces
├── diagnostics.py             # Common Diagnostics Interface (IEngineDiagnostics)
├── events.py                  # Immutable event payload definitions
└── adapters/
    ├── __init__.py            # Document adapter package marker
    └── dummy_adapter.py       # DummyDocumentAdapter reference implementation

backend/tests/unit/
├── test_document_models.py           # Unit tests for Pydantic models and enum validations
├── test_document_lifecycle.py        # Unit tests for versioning, lineage, lifecycle states
├── test_document_template.py         # Unit tests for template schema, binding, validation
├── test_document_ontology.py         # Unit tests for declarative document ontology
├── test_template_library.py          # Unit tests for local-first template library
├── test_document_adapter_registry.py # Unit tests for adapter registration & metadata
├── test_document_adapter_loader.py   # Unit tests for adapter loading & sandbox isolation
├── test_adapter_pipeline.py          # Unit tests for pipeline stages and execution modes
├── test_dummy_adapter.py             # Unit tests for DummyDocumentAdapter execution
├── test_document_profiles.py         # Unit tests for operation profiles
├── test_document_intelligence.py     # Unit tests for intelligence & recommendation protocols
├── test_document_recovery.py         # Unit tests for recovery hooks and checkpointing
├── test_document_security.py         # Unit tests for security metadata & verification
├── test_document_diagnostics.py      # Unit tests for IEngineDiagnostics methods
└── test_document_engine.py           # Unit tests for core DocumentEngine facade

backend/tests/integration/
└── test_document_engine_integration.py # Integration tests with Kernel, Storage & Event Engine
```

---

## 13. Implementation Milestones

Implementation is divided into ten sequential vertical milestones. Each milestone is independently testable, reviewable, and limited to a focused set of source files.

```
Step 4: Document Engine (Phase 2 Roadmap)
├── Milestone 1: Interfaces, Protocols, Models
├── Milestone 2: Document Lifecycle, Versioning, Lineage
├── Milestone 3: Template System (Library, Schema, Ontology, Hybrid Binding)
├── Milestone 4: Document Adapter Architecture (Registry, Sandbox, Loader, Dummy Adapter)
├── Milestone 5: Document Operation Profiles, Pipeline Architecture & Capabilities
├── Milestone 6: Document Intelligence, AI Recommendations & Recovery Interfaces
├── Milestone 7: Storage Integration
├── Milestone 8: Engine Facade, Kernel Integration, Capability Registration
├── Milestone 9: Unit Tests
└── Milestone 10: Integration Tests
```

### Milestone 1: Interfaces, Protocols, Models
- **Goal**: Establish core type definitions, domain models, custom exception classes, and abstract interfaces.
- **Files Created**:
  - `backend/src/kortex/engines/document/models.py`
  - `backend/src/kortex/engines/document/interfaces.py`
  - `backend/src/kortex/engines/document/exceptions.py`
  - `backend/src/kortex/engines/document/base_adapter.py`
  - `backend/tests/unit/test_document_models.py`
- **Deliverables**:
  - Enums: `DocumentLifecycleState`, `DocumentOperationType`, `AdapterCapability`, `SecurityClassification`.
  - Pydantic v2 models: `DocumentMetadata`, `DocumentVersion`, `AdapterMetadata`, `BindingContext`, `ValidationReport`, `OperationRequest`, `OperationResult`, `PreviewOptions`, `PreviewResult`, `SecurityMetadata`.
  - Abstract base class `BaseDocumentAdapter` with methods `execute()`, `validate_schema()`, and properties `metadata`, `supported_capabilities`.
  - Unit tests verifying model validation, serialization, and default values.

### Milestone 2: Document Lifecycle, Versioning, Lineage
- **Goal**: Implement document lifecycle state machine, version chain, and lineage tracking.
- **Files Created**:
  - `backend/src/kortex/engines/document/lifecycle.py`
  - `backend/tests/unit/test_document_lifecycle.py`
- **Deliverables**:
  - `DocumentLifecycleManager` handling state transitions: `Draft` $\rightarrow$ `Review` $\rightarrow$ `Published` $\rightarrow$ `Superseded` $\rightarrow$ `Archived` / `Logical Delete`.
  - Version chain enforcement (`Document ID`, `Version ID`, `Parent Version`).
  - Immutable Published Documents protection (published versions locked against edits).
  - Document lineage tracking (parent-child relationship graph).
  - Unit tests verifying transition rules, version increments, and immutability enforcement.

### Milestone 3: Template System (Library, Schema, Ontology, Hybrid Binding)
- **Goal**: Implement Template Library, declarative Document Ontology, placeholder validation, computed fields, and hybrid binder.
- **Files Created**:
  - `backend/src/kortex/engines/document/library.py`
  - `backend/src/kortex/engines/document/ontology.py`
  - `backend/src/kortex/engines/document/template.py`
  - `backend/tests/unit/test_template_library.py`
  - `backend/tests/unit/test_document_ontology.py`
  - `backend/tests/unit/test_document_template.py`
- **Deliverables**:
  - `TemplateLibrary` indexing standard templates (Invoices, Payslips, Contracts, Certificates, etc.).
  - `DocumentOntology` schemas defining document structure without AI dependencies.
  - `TemplateBinder` executing hybrid data binding with `BindingContext`.
  - Placeholder validation, strong type validation, computed field resolution, auto-complete metadata, and `ValidationReport`.
  - Unit tests verifying template indexing, ontology parsing, type checking, and computed field evaluation.

### Milestone 4: Document Adapter Architecture (Registry, Sandbox, Loader, Dummy Adapter)
- **Goal**: Implement adapter metadata registry, sandboxed execution environment, dynamic loader, and reference dummy adapter.
- **Files Created**:
  - `backend/src/kortex/engines/document/registry.py`
  - `backend/src/kortex/engines/document/sandbox.py`
  - `backend/src/kortex/engines/document/loader.py`
  - `backend/src/kortex/engines/document/adapters/__init__.py`
  - `backend/src/kortex/engines/document/adapters/dummy_adapter.py`
  - `backend/tests/unit/test_document_adapter_registry.py`
  - `backend/tests/unit/test_document_adapter_loader.py`
  - `backend/tests/unit/test_dummy_adapter.py`
- **Deliverables**:
  - `DocumentAdapterRegistry` supporting registration and lookup by `AdapterMetadata` and `AdapterCapability`.
  - `AdapterSandbox` isolating adapter execution (permissions, memory thresholds, timeouts, temporary workspace).
  - `DocumentAdapterLoader` supporting dynamic discovery of `BaseDocumentAdapter` subclasses.
  - `DummyDocumentAdapter` reference plugin supporting mock operation execution.
  - Unit tests verifying registration, capability lookup, sandboxed isolation, and dummy adapter execution.

### Milestone 5: Document Operation Profiles, Pipeline Architecture & Capabilities
- **Goal**: Implement Document Operation Profiles and Adapter Pipeline execution engine.
- **Files Created**:
  - `backend/src/kortex/engines/document/profiles.py`
  - `backend/src/kortex/engines/document/pipeline.py`
  - `backend/tests/unit/test_document_profiles.py`
  - `backend/tests/unit/test_adapter_pipeline.py`
- **Deliverables**:
  - `DocumentOperationProfile` manager mapping Business Operations $\rightarrow$ Operation Profiles $\rightarrow$ Adapter Pipelines $\rightarrow$ Document Adapters.
  - `AdapterPipeline` stage execution coordinator supporting sequential, conditional, optional, and parallel stages.
  - Integration for macro processing adapters inside pipeline stages.
  - Unit tests verifying profile resolution, pipeline stage execution, and macro adapter workflows.

### Milestone 6: Document Intelligence, AI Recommendations & Recovery Interfaces
- **Goal**: Implement Document Intelligence models, AI recommendation protocols, and execution recovery interfaces.
- **Files Created**:
  - `backend/src/kortex/engines/document/intelligence.py`
  - `backend/src/kortex/engines/document/recovery.py`
  - `backend/tests/unit/test_document_intelligence.py`
  - `backend/tests/unit/test_document_recovery.py`
- **Deliverables**:
  - `DocumentIntelligenceModel` and `IDocumentIntelligenceProvider` protocol.
  - `IDocumentRecommendationProvider` protocol (recommending templates, profiles, pipelines).
  - `IDocumentRecoveryProvider` protocol (retry, rollback, checkpointing, resume).
  - AI optional design (engine functions fully without active AI providers).
  - Unit tests verifying models, protocol interfaces, and recovery checkpointing.

### Milestone 7: Storage Integration
- **Goal**: Connect Document Engine services to Storage Engine abstractions (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`).
- **Files Created**:
  - `backend/src/kortex/engines/document/security.py`
  - `backend/tests/unit/test_document_security.py`
- **Deliverables**:
  - Storage Engine binding for metadata records, version chains, and audit logs (`IDataStore`).
  - Storage Engine binding for declarative template schemas and libraries (`IFileStore`).
  - Storage Engine binding for immutable published outputs and preview thumbnails (`IObjectStore`).
  - Caching bindings for discovery, schemas, metadata, preview, and capability lookups (`ICacheStore`).
  - Security metadata schemas and `IVerificationService` integration interface.
  - Unit tests verifying persistence and caching through Storage Engine mocks.

### Milestone 8: Engine Facade, Kernel Integration, Capability Registration
- **Goal**: Implement main engine facade, capability handlers, and diagnostic telemetry.
- **Files Created**:
  - `backend/src/kortex/engines/document/engine.py`
  - `backend/src/kortex/engines/document/diagnostics.py`
  - `backend/src/kortex/engines/document/events.py`
  - `backend/src/kortex/engines/document/__init__.py`
  - `backend/tests/unit/test_document_diagnostics.py`
- **Deliverables**:
  - `DocumentEngine` inheriting `BaseEngine`.
  - Capability handler methods for `kortex.document.operation.execute` and `kortex.document.lifecycle.transition`.
  - Standardized `IEngineDiagnostics` implementation (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
  - Immutable system events definition.
  - Unit tests verifying engine initialization, diagnostic metrics, and event emission.

### Milestone 9: Unit Tests
- **Goal**: Complete comprehensive unit test coverage across all engine files.
- **Files Created**:
  - `backend/tests/unit/test_document_engine.py`
- **Deliverables**:
  - Complete edge case testing (missing templates, invalid context, pipeline stage failures, storage errors, invalid state transitions).
  - Verification of 100% test pass rate.
  - Verification of ≥90% code coverage across all core files in `backend/src/kortex/engines/document/`.

### Milestone 10: Integration Tests
- **Goal**: End-to-end integration testing with Kernel, Storage Engine, and Event Engine.
- **Files Created**:
  - `backend/tests/integration/test_document_engine_integration.py`
- **Deliverables**:
  - Boot sequence registration test with Kernel IoC container.
  - Capability resolution test via Registry Engine.
  - Complete flow test: Operation Profile invocation $\rightarrow$ Template Library lookup $\rightarrow$ Declarative template binding $\rightarrow$ Adapter Pipeline execution $\rightarrow$ Sandboxed Dummy Adapter execution $\rightarrow$ Lifecycle transition $\rightarrow$ Storage Engine persistence $\rightarrow$ Event Engine event publication.
  - Architecture Compliance Audit verification.

---

## 14. Public Interfaces & Protocols

All public contracts are defined using Python `Protocol` and Abstract Base Classes (ABC) in `interfaces.py` and `base_adapter.py`.

### 14.1 IDocumentEngine Protocol

```python
# Specification declaration for IDocumentEngine interface
class IDocumentEngine(Protocol):
    """Primary facade interface exposed by the Document Engine."""

    async def execute_profile(self, profile_id: str, request: OperationRequest) -> OperationResult:
        """Execute a Document Operation Profile via configured Adapter Pipeline."""
        ...

    async def transition_lifecycle(
        self,
        document_id: str,
        version_id: str,
        target_state: DocumentLifecycleState
    ) -> DocumentMetadata:
        """Transition document version to new lifecycle state."""
        ...

    async def bind_template(
        self,
        template_id: str,
        context: BindingContext
    ) -> ValidationReport:
        """Validate and bind context data against a declarative Template Schema."""
        ...

    async def generate_preview(self, request_id: str, options: PreviewOptions) -> PreviewResult:
        """Generate a preview thumbnail for a document operation page."""
        ...

    def register_adapter(self, adapter: BaseDocumentAdapter) -> None:
        """Register a document adapter instance."""
        ...

    def unregister_adapter(self, adapter_id: str) -> bool:
        """Unregister a document adapter by ID."""
        ...

    def get_adapter(self, capability: AdapterCapability) -> BaseDocumentAdapter:
        """Retrieve registered document adapter advertising specified capability."""
        ...

    def list_adapters(self) -> List[AdapterMetadata]:
        """Return list of metadata objects for all registered document adapters."""
        ...
```

### 14.2 BaseDocumentAdapter Abstract Base Class

```python
# Specification declaration for BaseDocumentAdapter ABC
class BaseDocumentAdapter(ABC):
    """Abstract base class for all sandboxed document adapter plugins."""

    @property
    @abstractmethod
    def metadata(self) -> AdapterMetadata:
        """Return immutable Marketplace-ready adapter metadata object."""
        ...

    @property
    def adapter_id(self) -> str:
        return self.metadata.adapter_id

    @property
    def supported_capabilities(self) -> List[AdapterCapability]:
        return self.metadata.supported_capabilities

    @abstractmethod
    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: Dict[str, Any]
    ) -> bytes:
        """Execute document operation into binary output payload."""
        ...

    @abstractmethod
    def validate_schema(self, schema: TemplateSchema) -> bool:
        """Validate whether declarative template schema is compatible with this adapter."""
        ...
```

### 14.3 IDocumentRecommendationProvider Protocol

```python
# Specification declaration for IDocumentRecommendationProvider
class IDocumentRecommendationProvider(Protocol):
    """Interface for AI-driven template, profile, and pipeline recommendations."""

    async def recommend_template(
        self,
        user_intent: str,
        data_schema: Dict[str, Any]
    ) -> List[str]:
        """Recommend template schema IDs based on intent and available data."""
        ...

    async def recommend_operation_profile(
        self,
        business_operation: str,
        user_context: Dict[str, Any]
    ) -> str:
        """Recommend optimal DocumentOperationProfile ID."""
        ...

    async def recommend_adapter_pipeline(
        self,
        profile_id: str,
        installed_adapters: List[AdapterMetadata]
    ) -> List[str]:
        """Recommend optimal adapter pipeline stage configuration."""
        ...
```

---

## 15. Data Models & Schemas

Defined using Pydantic v2 (`BaseModel`) with strict type validation, default values, and field documentation.

### Key Data Models Summary:

- **`DocumentOperationProfile`**: `id`, `name`, `namespace`, `version`, `description`, `business_operation`, `required_template`, `adapter_pipeline`, `validation_rules`, `permissions`, `output_rules`, `post_processing`, `lifecycle_rules`.
- **`AdapterPipeline`**: `pipeline_id`, `profile_id`, `stages` (List of `PipelineStage`), `execution_mode` (`SEQUENTIAL`, `CONDITIONAL`, `PARALLEL_PREP`), `allow_fallback`.
- **`PipelineStage`**: `stage_id`, `adapter_id`, `required_capability`, `execution_condition`, `is_optional`, `stage_options`.
- **`AdapterMetadata`**: `adapter_id`, `display_name`, `vendor`, `author`, `version`, `license`, `description`, `homepage`, `supported_capabilities`, `supported_operations`, `supports_preview`, `supports_streaming`, `supports_macros`, `supports_security`, `supports_versioning`.
- **`DocumentMetadata`**: `document_id`, `version_id`, `parent_version_id`, `lifecycle_state`, `lineage_path`, `title`, `author_id`, `is_immutable`, `security_classification`, `security_labels`, `file_size_bytes`, `sha256_hash`, `storage_key`, `bucket_name`, `created_at`, `published_at`.
- **`DocumentOntology`**: `ontology_id`, `entity_name`, `version`, `child_structures`, `invariant_rules`, `relationship_mappings`.
- **`ValidationReport`**: `is_valid`, `errors`, `warnings`, `missing_placeholders`, `type_mismatches`, `computed_fields_resolved`.
- **`DocumentIntelligenceModel`**: `document_id`, `version_id`, `extracted_concepts`, `knowledge_references`, `relationship_metadata`, `confidence_score`, `last_updated_at`.
- **`AdapterSandboxConfig`**: `permissions`, `allowed_capabilities`, `temporary_workspace`, `timeout_seconds`, `memory_limit_mb`.

---

## 16. System Events

Immutable event catalog published to Event Engine (`kortex.events`):

| Event Name | Topic / Routing Key | Trigger Condition |
| :--- | :--- | :--- |
| `DocumentCreatedEvent` | `document.created` | Dispatched when a new root document entity is registered |
| `DocumentLifecycleTransitionedEvent` | `document.lifecycle.transitioned` | Dispatched when document state transitions (`Draft` $\rightarrow$ `Review`, etc.) |
| `DocumentPublishedEvent` | `document.published` | Dispatched when document version transitions to `Published` (Locks immutability) |
| `DocumentSupersededEvent` | `document.superseded` | Dispatched when a published version is replaced by a newer version |
| `DocumentArchivedEvent` | `document.archived` | Dispatched when document version transitions to `Archived` |
| `DocumentOperationStartedEvent` | `document.operation.started` | Dispatched immediately upon receiving valid `OperationRequest` |
| `DocumentOperationCompletedEvent` | `document.operation.completed` | Dispatched when operation output is written to `IObjectStore` |
| `DocumentOperationFailedEvent` | `document.operation.failed` | Dispatched when operation execution or pipeline stage fails |
| `DocumentIntelligenceUpdatedEvent` | `document.intelligence.updated` | Dispatched when intelligence metadata model is updated |
| `DocumentAdapterRegisteredEvent` | `document.adapter.registered` | Dispatched when new document adapter is registered |

---

## 17. Capability Registration

Canonical capability names (`kortex.<domain>.<resource>.<action>`):

1. `kortex.document.operation.execute`: Execute document operation profile via adapter pipeline.
2. `kortex.document.lifecycle.transition`: Transition document version lifecycle state.
3. `kortex.document.template.bind`: Bind context data against declarative Template Schema.
4. `kortex.document.preview.generate`: Rasterize page preview thumbnail stub.
5. `kortex.document.intelligence.analyze`: Trigger intelligence analysis via `IDocumentIntelligenceProvider`.
6. `kortex.document.recommendation.get`: Query AI recommendations via `IDocumentRecommendationProvider`.
7. `kortex.document.adapter.register`: Register a new document adapter into `DocumentAdapterRegistry`.
8. `kortex.document.adapter.list`: List all registered document adapters and advertised capabilities.

---

## 18. Storage & Multi-Level Caching Requirements

### Storage Interaction Rules:
1. **Template Library & Declarative Schemas (`IFileStore`)**: Template schemas and ontology files stored in sandboxed paths via `IFileStore`. Path sandboxing enforced by `PathSandboxValidator`.
2. **Immutable Output Persistence (`IObjectStore`)**: Rendered assets, published versions, and thumbnails stored in `IObjectStore` buckets. Storage Engine computes and returns SHA256 checksums (`ObjectMetadata.sha256_hash`).
3. **Multi-Level Caching (`ICacheStore`)**:
   - **Adapter Discovery Cache**: Caches resolved adapter pipelines by profile ID.
   - **Template Schema Cache**: Caches parsed declarative `TemplateSchema` models.
   - **Metadata Cache**: Caches `DocumentMetadata` and lifecycle state records.
   - **Preview Cache**: Caches preview thumbnail storage references.
   - **Capability Lookup Cache**: Caches capability registration paths.
4. **Lifecycle, Versioning & Audit History (`IDataStore`)**: Document entities, version chains, lineage graphs, and operation profiles persisted in relational tables via `IDataStore.get_session()`.

---

## 19. Security Integration & Audit Requirements

1. **Security Classification & Labels**: Security metadata (`SecurityMetadata`) attached to every document version (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`).
2. **Verification Metadata**: SHA256 cryptographic hashes computed upon publication and verified via `IVerificationService`.
3. **Capability Permission Checks**: Capability execution intercepted by Kernel authorization middleware to enforce permission checks.
4. **Security Audit Events**: Sensitive operations trigger immutable audit events published to Event Engine.

---

## 20. Performance Requirements

1. **Lazy Adapter Loading**: Adapters instantiated lazily upon first invocation.
2. **Multi-Level Caching**: Five caching layers via `ICacheStore` ensuring sub-millisecond metadata lookups.
3. **Async Non-Blocking Execution**: `async`/`await` primitives throughout execution paths.
4. **Low Orchestration Overhead**: Engine orchestration overhead $\le$ 50ms per request.
5. **Thread-Safe Operations**: `DocumentAdapterRegistry` protected by thread synchronization locks.

---

## 21. Testing & Quality Requirements

- Unit tests across all 10 milestones in `backend/tests/unit/`.
- Integration tests in `backend/tests/integration/` verifying boot registration, capability lookup, pipeline execution, and event dispatching.
- Quality gates: 100% passing tests, $\ge$ 90% code coverage across all core engine files.

---

## 22. Acceptance Criteria & Final Validation

The Document Engine implementation shall be considered complete and ready for pull request merge only when all of the following criteria are met:

- ✓ **Local-First**: Operates 100% offline without cloud dependencies.
- ✓ **Offline-First**: All templates, profiles, and pipelines execute locally.
- ✓ **Plugin-Ready**: Dynamic discovery and loading of sandboxed adapters via `DocumentAdapterLoader`.
- ✓ **AI-Ready**: Exposes `IDocumentIntelligenceProvider` and `IDocumentRecommendationProvider` protocols.
- ✓ **Marketplace-Ready**: Adapters, Templates, and Profiles expose `KortexAssetManifest` compatible metadata, versioning, and signatures.
- ✓ **Enterprise-Ready**: Complete audit trails, security labels, verification metadata, and multi-tenant scoping.
- ✓ **Multi-Tenant Ready**: All storage keys, templates, and profiles scoped by tenant identifier.
- ✓ **Capability-Based**: Adapters advertise `AdapterCapability` enum values resolved by operation profiles.
- ✓ **Adapter-Driven**: 100% adapter-driven architecture using `BaseDocumentAdapter` and `AdapterPipeline`.
- ✓ **Technology-Independent**: Technology details hidden behind adapters; hierarchy enforced (Business Module $\rightarrow$ Business Operation $\rightarrow$ Operation Profile $\rightarrow$ Adapter Pipeline $\rightarrow$ Document Adapter $\rightarrow$ Technology).
- ✓ **Zero Business Logic**: Infrastructure contains zero domain rules or financial calculations.
- ✓ **Clean Architecture**: System boundaries, dependency direction, and interfaces strictly preserved.
- ✓ **SOLID**: Single responsibility, open/closed, Liskov substitution, interface segregation, and dependency inversion verified.
- ✓ **Dependency Injection**: IoC container used for all service construction; zero global state.
- ✓ **Event-Driven**: Immutable system events emitted via Event Engine.
- ✓ **Unit Tests $\ge$ 90%**: Code coverage threshold met across all core files in `backend/src/kortex/engines/document/`.
- ✓ **Integration Tests Pass**: 100% end-to-end integration tests passing.
- ✓ **Architecture Audit Passes**: Architecture review checklist verified with zero violations.