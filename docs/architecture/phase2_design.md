# KORTEX OS — Phase 2 Architecture Design: Business Foundation

- **Document Version**: 3.0.0 (Revised Implementation Strategy & Scope Specifications)
- **Status**: Proposed (Awaiting Chief Architect Approval)
- **Author**: Senior Software Engineer (AI)
- **Authority**: Chief Architect (KASHAN)
- **Target Release**: KORTEX OS Phase 2: Business Foundation
- **Target File**: `docs/architecture/phase2_design.md`

---

## 1. Executive Summary & Scope

Phase 2 establishes the **Business Foundation Layer** of KORTEX OS. This layer provides the reusable, domain-agnostic platform infrastructure upon which all future business modules (Finance, HR & Payroll, Fleet & Operations, CRM, Procurement, Inventory, etc.) will be constructed.

### Core Architectural Mandates
1. **Zero Business Logic**: The Phase 2 engines contain strictly zero domain-specific business rules, financial calculations, or industry-specific logic. All engines are 100% reusable across any business domain.
2. **Strict Architecture Governance**: Adheres strictly to the KORTEX OS AI Engineering Constitution (`AGENTS.md`), preserving Clean Architecture, SOLID principles, Local-First execution, event-driven communication, dependency injection, and Pydantic v2 type safety.
3. **Engine Scope Boundary**: Design is strictly restricted to **exactly five System Engines**. No additional engines are introduced:
   - **Storage Engine** (`kortex.engines.storage`) — Multi-store abstraction provider (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`).
   - **Workflow Engine** (`kortex.engines.workflow`) — Sole runtime state machine and recipe execution engine.
   - **Recipe Engine** (`kortex.engines.recipe`) — Pure parser, validator, compiler, versioner, and packager (Never executes recipes).
   - **Document Engine** (`kortex.engines.document`) — Renderer registry and plugin-based document lifecycle manager.
   - **Connector Engine** (`kortex.engines.connector`) — Connector driver registry and external integration driver host.

---

## 2. Global System Architectural Conventions

### 2.1 Common Diagnostics Interface Specification

Every System Engine in KORTEX OS (Phase 1 and Phase 2) must expose a standardized diagnostics interface to enable system-wide health monitoring, telemetry, administrative inspection, and AI runtime diagnostics.

```python
from __future__ import annotations
import enum
from typing import Any, Dict, List, Protocol


class IEngineDiagnostics(Protocol):
    """Standardized Diagnostics Interface exposed by all KORTEX System Engines."""

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health status, checks, and error counters."""
        ...

    def metrics(self) -> Dict[str, Any]:
        """Return operational runtime metrics (throughput, latency, active tasks)."""
        ...

    def diagnostics(self) -> Dict[str, Any]:
        """Return complete deep diagnostic report (environment, resources, memory)."""
        ...

    def status(self) -> str:
        """Return current engine operational state (e.g. READY, RUNNING, FAILED)."""
        ...

    def version(self) -> str:
        """Return semantic version string of the engine."""
        ...

    def capabilities(self) -> List[str]:
        """Return list of registered capabilities exposed by this engine."""
        ...
```

---

### 2.2 Universal Capability Naming Convention

All system engines, modules, connectors, and recipes must register and invoke capabilities using a single unified canonical capability naming format:

$$\text{kortex}.<\text{domain}>.<\text{resource}>.<\text{action}>$$

#### Canonical Capability Table
| Capability Name | Owner Engine | Description |
| :--- | :--- | :--- |
| `kortex.storage.data.session` | Storage Engine | Acquire relational database AsyncSession (`IDataStore`) |
| `kortex.storage.file.store` | Storage Engine | Store file on sandboxed file system (`IFileStore`) |
| `kortex.storage.object.put` | Storage Engine | Store binary object blob (`IObjectStore`) |
| `kortex.storage.cache.set` | Storage Engine | Set key-value ephemeral cache entry (`ICacheStore`) |
| `kortex.workflow.instance.start` | Workflow Engine | Start executing a compiled workflow definition instance |
| `kortex.workflow.instance.approve` | Workflow Engine | Submit human decision for an approval step |
| `kortex.workflow.instance.cancel` | Workflow Engine | Abort a running workflow instance |
| `kortex.workflow.state.get` | Workflow Engine | Query the execution state of a workflow |
| `kortex.recipe.definition.register` | Recipe Engine | Parse, validate, and register a raw recipe |
| `kortex.recipe.definition.compile` | Recipe Engine | Compile a recipe into an executable Workflow Definition |
| `kortex.recipe.package.export` | Recipe Engine | Export recipe asset into `.kortex-recipe` package |
| `kortex.recipe.package.import` | Recipe Engine | Import and unpack a verified recipe package |
| `kortex.document.render.execute` | Document Engine | Render a document via the Document Renderer Registry |
| `kortex.document.preview.generate` | Document Engine | Rasterize document page preview thumbnails |
| `kortex.connector.driver.register` | Connector Engine | Register a dynamic connector driver plugin |
| `kortex.connector.action.execute` | Connector Engine | Dispatch action to configured connector driver |

---

### 2.3 Shared Generic Manifest Specification

Every KORTEX OS asset—including Recipes, Connectors, Templates, Modules, Workflows, and Marketplace Packages—must be defined with a standardized `KortexAssetManifest`.

```python
from __future__ import annotations
import enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class AssetType(str, enum.Enum):
    RECIPE = "RECIPE"
    WORKFLOW = "WORKFLOW"
    CONNECTOR = "CONNECTOR"
    TEMPLATE = "TEMPLATE"
    MODULE = "MODULE"
    MARKETPLACE_PACKAGE = "MARKETPLACE_PACKAGE"


class ManifestAuthor(BaseModel):
    name: str = Field(..., description="Author or organization name")
    email: str = Field(..., description="Contact email address")
    organization: Optional[str] = Field(None, description="Organization or vendor name")


class KortexAssetManifest(BaseModel):
    """Canonical Manifest Specification shared across all KORTEX OS assets."""

    id: str = Field(..., description="Canonical UUID asset identifier")
    name: str = Field(..., description="Human-readable asset name")
    namespace: str = Field(..., description="Reverse domain identifier e.g. kortex.hr.payroll")
    version: str = Field(..., description="Semantic Version string (e.g. 1.2.0)")
    asset_type: AssetType = Field(..., description="Type of asset defined by manifest")
    description: str = Field(..., description="Detailed technical description of asset")
    author: ManifestAuthor = Field(..., description="Author details")
    license: str = Field("MIT", description="Software or asset license model")
    
    dependencies: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of required assets and SemVer ranges e.g. {'kortex.kernel': '>=0.1.0'}"
    )
    capabilities_required: List[str] = Field(
        default_factory=list,
        description="List of capability names required in kortex.<domain>.<resource>.<action> format"
    )
    capabilities_provided: List[str] = Field(
        default_factory=list,
        description="List of capability names exposed by this asset"
    )
    permissions_required: List[str] = Field(
        default_factory=list,
        description="RBAC permissions required to execute or access asset"
    )
    kernel_compatibility: str = Field(">=0.1.0", description="Compatible KORTEX Kernel SemVer range")
    checksum: str = Field(..., description="SHA256 hash of the complete asset payload")
    signature: Optional[str] = Field(None, description="Cryptographic Ed25519 signature of checksum")
```

---

### 2.4 Semantic Versioning & Lifecycle Policy

KORTEX OS enforces Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`) across all system assets (Recipes, Workflows, Templates, Documents, Connectors, Modules, and Kernel).

- **MAJOR bump** (`1.0.0` $\rightarrow$ `2.0.0`): Breaking changes to input parameter types, removed steps, altered approval roles, or modified capability contracts.
- **MINOR bump** (`1.0.0` $\rightarrow$ `1.1.0`): Adding new optional input parameters, additional non-breaking steps, or supplementary telemetry tags.
- **PATCH bump** (`1.0.0` $\rightarrow$ `1.0.1`): Fixing step description typos, performance optimization of expression bindings, or documentation updates.

---

## 3. Engine Technical Architecture & Refined Phase 2 Scopes

---

### 3.1 Storage Engine (Step 1)

```
                               ┌──────────────────────────────────┐
                               │          Storage Engine          │
                               ├──────────────────────────────────┤
                               │ • Four Distinct Storage Stores   │
                               │   - IDataStore (Relational DB)   │
                               │   - IFileStore (Local File System)│
                               │   - IObjectStore (Blob Storage)  │
                               │   - ICacheStore (Key-Value Cache)│
                               │ • Path Sandbox Validator         │
                               │ • SHA256 Checksum Support        │
                               │ • Common Diagnostics Interface   │
                               └──────────────────────────────────┘
```

#### 1. Purpose
The Storage Engine provides four distinct, specialized storage abstractions for KORTEX OS, ensuring clean separation of concerns between relational transactional data, local file system operations, binary object blob storage, and ephemeral key-value caching.

> **Scope Refinement**: **Encryption is explicitly OUT OF SCOPE for Storage Engine in Phase 2.** Encryption at rest, key management, and secure secrets management belong exclusively to the future Security Engine. Storage Engine responsibilities in Phase 2 are limited strictly to: Read, Write, Delete, Exists, List, Metadata, Sandbox validation, and Checksum (SHA256) support.

#### 2. Responsibilities
- Provide `IDataStore` for relational database sessions and transactions (SQLite / PostgreSQL via SQLAlchemy 2.0).
- Provide `IFileStore` for local sandboxed file system operations (read, write, delete, exists, list, metadata).
- Provide `IObjectStore` for binary object storage with SHA256 checksum calculation and deduplication.
- Provide `ICacheStore` for in-memory key-value caching with expiration TTLs.
- Enforce path sandboxing to restrict file I/O inside authorized workspace directories.
- Expose the common diagnostics interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).

#### 3. Explicitly Out of Scope
- **Encryption & Key Management**: Encryption at rest belongs to the future Security Engine.
- **Business Data Validation**: Does not validate domain business rules.
- **Document Rendering & Workflows**: Handled by Document and Workflow engines respectively.

#### 4. Public API & Storage Interfaces

```python
from __future__ import annotations
from typing import Any, AsyncGenerator, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

class IDataStore:
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]: ...

class IFileStore:
    async def read_file(self, relative_path: str) -> bytes: ...
    async def write_file(self, relative_path: str, content: bytes) -> str: ...
    async def delete_file(self, relative_path: str) -> bool: ...
    async def file_exists(self, relative_path: str) -> bool: ...
    async def list_files(self, relative_path: str) -> List[str]: ...
    async def get_metadata(self, relative_path: str) -> Dict[str, Any]: ...

class ObjectMetadata(BaseModel):
    storage_key: str
    bucket_name: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    sha256_hash: str
    created_at: str

class IObjectStore:
    async def put_object(self, bucket_name: str, object_key: str, data: bytes, mime_type: str) -> ObjectMetadata: ...
    async def get_object(self, bucket_name: str, object_key: str) -> bytes: ...
    async def delete_object(self, bucket_name: str, object_key: str) -> bool: ...
    async def object_exists(self, bucket_name: str, object_key: str) -> bool: ...
    async def list_objects(self, bucket_name: str, prefix: Optional[str] = None) -> List[ObjectMetadata]: ...

class ICacheStore:
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool: ...
    async def delete(self, key: str) -> bool: ...
    async def clear(self) -> bool: ...

class StorageEngineInterface:
    @property
    def data(self) -> IDataStore: ...
    @property
    def file(self) -> IFileStore: ...
    @property
    def object(self) -> IObjectStore: ...
    @property
    def cache(self) -> ICacheStore: ...
    
    # Common Diagnostics Interface
    def health(self) -> Dict[str, Any]: ...
    def metrics(self) -> Dict[str, Any]: ...
    def diagnostics(self) -> Dict[str, Any]: ...
    def status(self) -> str: ...
    def version(self) -> str: ...
    def capabilities(self) -> List[str]: ...
```

---

### 3.2 Workflow Engine (Step 2 — Sole Execution Runtime)

```
                               ┌──────────────────────────────────┐
                               │         Workflow Engine          │
                               ├──────────────────────────────────┤
                               │ • Sole Recipe & Workflow Exec    │
                               │ • State Machine Runtime          │
                               │ • Minimal Approval Interface     │
                               │ • Retries & Exponential Backoff  │
                               │ • Rollback & Compensation        │
                               │ • Common Diagnostics Interface   │
                               └──────────────────────────────────┘
```

#### 1. Purpose
The Workflow Engine is the **sole execution runtime** for all stateful processes, compiled business recipes, multi-step tasks, background jobs, human approval states, retry backoffs, and compensation (rollback) flows across KORTEX OS.

> **Scope Refinement**: **Do NOT implement a complete Human Approval system in Phase 2.** Implement ONLY: Approval interface, Approval state, and Approval events. No UI, no reminders, no escalation, and no notifications (those belong to later phases).

#### 2. Responsibilities
- Execute compiled `WorkflowDefinition` state machines produced by the Recipe Engine or direct system registration.
- Maintain persistent execution state across system restarts via `IDataStore` and `IObjectStore`.
- Manage minimal Approval state machine transitions: pause execution at approval nodes, store decision states, emit approval events, and resume.
- Execute background steps asynchronously without blocking the main event loop.
- Enforce configurable retry policies (maximum attempts, backoff multipliers, dead-lettering).
- Execute compensation stacks (rollback flows) in reverse topological order upon terminal failure.
- Expose common diagnostics interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).

#### 3. Explicitly Out of Scope
- **Full Human Approval System**: UI, reminders, escalation schedules, and notifications are explicitly OUT OF SCOPE.
- **Recipe Parsing & Compilation**: Delegated to Recipe Engine.
- **Document Rendering**: Delegated to Document Engine.
- **Direct Protocol Networking**: Delegated to Connector Engine.

---

### 3.3 Recipe Engine (Step 3 — Compiler & Packager Only)

```
                               ┌──────────────────────────────────┐
                               │          Recipe Engine           │
                               ├──────────────────────────────────┤
                               │ • Zero Execution Responsibility  │
                               │ • Parser, Validator & Compiler   │
                               │ • Versioning & SemVer Manager    │
                               │ • Import / Export Packager       │
                               │ • Common Diagnostics Interface   │
                               └──────────────────────────────────┘
```

#### 1. Purpose
The Recipe Engine is a declarative specification parser, validator, compiler, versioner, and packager for KORTEX OS business recipes. It converts zero-code YAML/JSON business recipes into compiled `WorkflowDefinition` execution plans.

> **Core Constraint**: The Recipe Engine **NEVER executes recipes**. It compiles recipes into `WorkflowDefinition` objects and hands execution over to the Workflow Engine.

#### 2. Responsibilities
- Parse YAML and JSON recipe specification files.
- Validate recipe manifests against `KortexAssetManifest` and Pydantic v2 models.
- Verify capability requirements against Registry Engine.
- Compile declarative recipe steps into executable `WorkflowDefinition` state machine objects.
- Import and export standalone `.kortex-recipe` archives with SHA256 checksums.
- Expose common diagnostics interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).

---

### 3.4 Document Engine (Step 4 — Plugin Registry Host)

```
                               ┌──────────────────────────────────┐
                               │         Document Engine          │
                               ├──────────────────────────────────┤
                               │ • Pluggable Renderer Registry    │
                               │ • Plugin Loader & Base Plugin    │
                               │ • One Dummy Renderer for Phase 2 │
                               │ • Common Diagnostics Interface   │
                               └──────────────────────────────────┘
```

#### 1. Purpose
The Document Engine manages the document lifecycle within KORTEX OS using a **Renderer Registry** and plugin loader.

> **Scope Refinement**: **Do NOT implement production renderers in Phase 2.** Implement ONLY: Renderer Registry, `BaseDocumentRenderer` ABC, Plugin Loader, One Dummy Renderer (`DummyDocumentRenderer`), and Unit Tests. PDF, DOCX, XLSX, HTML and other production renderers belong to later feature phases.

#### 2. Responsibilities
- Maintain `DocumentRendererRegistry` for registering and looking up document renderers.
- Provide `BaseDocumentRenderer` abstract plugin interface.
- Implement `PluginLoader` for dynamic discovery of renderer plugins.
- Implement `DummyDocumentRenderer` plugin to validate the rendering pipeline.
- Route render requests to registered renderer plugins based on target format.
- Expose common diagnostics interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).

---

### 3.5 Connector Engine (Step 5 — Driver Registry Host)

```
                               ┌──────────────────────────────────┐
                               │         Connector Engine         │
                               ├──────────────────────────────────┤
                               │ • Dynamic Connector Registry     │
                               │ • Plugin Loader & Base Driver    │
                               │ • One Dummy Driver for Phase 2   │
                               │ • Common Diagnostics Interface   │
                               └──────────────────────────────────┘
```

#### 1. Purpose
The Connector Engine is the integration platform for KORTEX OS, hosting a **Connector Registry** for dynamically loaded Connector Drivers.

> **Scope Refinement**: **Do NOT implement production connectors in Phase 2.** Implement ONLY: Connector Registry, `BaseConnectorDriver` ABC, Plugin Loader, One Dummy Connector Driver (`DummyConnectorDriver`), and Unit Tests. HTTP, WhatsApp, Outlook, Gmail, GPS, Biometrics, FTP and other production connectors belong to later feature phases.

#### 2. Responsibilities
- Maintain `ConnectorDriverRegistry` for registering and looking up connector drivers.
- Provide `BaseConnectorDriver` abstract plugin class.
- Implement `PluginLoader` for dynamic discovery of connector drivers.
- Implement `DummyConnectorDriver` plugin to validate the connector action pipeline.
- Expose common diagnostics interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).

---

## 4. Standard Milestone Reporting Specification

Every implementation milestone across all Phase 2 engines must finish with a standardized execution report:

```markdown
### Milestone Completion Report: [Engine Name] — Milestone [X]: [Milestone Title]

1. **Files Created**: List of all new files created.
2. **Files Modified**: List of existing files updated.
3. **Tests Added**: Unit/integration test functions added.
4. **Coverage**: Code coverage percentage achieved.
5. **Manual Verification**: Empirical verification commands and test execution output.
6. **Architecture Compliance**: Checklist verifying SOLID, Clean Architecture, and AGENTS.md rules.
7. **Open Risks**: Technical risks or caveats identified.
8. **Next Milestone**: Pointer to the immediate next milestone.
```

---

## 5. Granular Implementation Strategy & Vertical Milestones

To ensure independent reviewability, testability, and strict risk control, implementation is divided into small, vertical milestones. **No single milestone exceeds 10–15 source files.**

---

### Step 1: Storage Engine

- **Milestone 1: Interfaces & Models**
  - Define `IDataStore`, `IFileStore`, `IObjectStore`, and `ICacheStore` abstract protocols and Pydantic models.
- **Milestone 2: Path Sandbox Validator**
  - Implement `PathSandboxValidator` to enforce strict canonical workspace directory isolation.
- **Milestone 3: Relational Data Store**
  - Implement `RelationalDataStore` (`IDataStore`) wrapping SQLAlchemy 2.0 async sessions.
- **Milestone 4: Sandboxed File Store**
  - Implement `LocalFileStore` (`IFileStore`) for local filesystem read, write, delete, exists, list, and metadata operations.
- **Milestone 5: Object Store**
  - Implement `BlobObjectStore` (`IObjectStore`) with SHA256 checksum calculation and deduplication (NO encryption).
- **Milestone 6: Cache Store**
  - Implement `MemoryCacheStore` (`ICacheStore`) with TTL expiration support.
- **Milestone 7: Storage Engine Facade & Diagnostics**
  - Implement `StorageEngine` facade inheriting `BaseEngine` and implementing the Common Diagnostics Interface (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
- **Milestone 8: Storage Engine Unit Tests**
  - Implement complete unit test suite for all storage stores and sandbox validator.
- **Milestone 9: Storage Engine Integration Verification**
  - Register Storage Engine with Kernel during boot sequence and verify capabilities via DI container.

---

### Step 2: Workflow Engine

- **Milestone 1: Workflow Models & Database Schemas**
  - Implement Pydantic v2 schemas and SQLAlchemy ORM models for workflow definitions, instances, step executions, and approvals.
- **Milestone 2: State Machine Evaluator**
  - Implement `StateMachineEvaluator` for state transitions, condition evaluation, and execution step routing.
- **Milestone 3: Basic Approval Interface & State Handling**
  - Implement approval interface, decision state tracking, and approval events (NO UI/reminders/escalation/notifications).
- **Milestone 4: Retry, Backoff & Compensation Stack Handler**
  - Implement exponential backoff retries, dead-letter queueing, and compensation rollback execution.
- **Milestone 5: Workflow Engine Core & Diagnostics**
  - Implement `WorkflowEngine` inheriting `BaseEngine` (sole execution runtime) with Common Diagnostics Interface.
- **Milestone 6: Workflow Engine Unit Tests**
  - Implement unit tests for state machine execution, retries, compensation, and approval state handling.
- **Milestone 7: Workflow Engine Integration Verification**
  - Verify Workflow Engine integration with Storage Engine (`IDataStore` / `IObjectStore`) and Kernel.

---

### Step 3: Recipe Engine

- **Milestone 1: Recipe Models & Manifest Parser**
  - Implement Recipe definition models, YAML/JSON parser, and `KortexAssetManifest` parser.
- **Milestone 2: Recipe Schema Validator & Capability Lookup**
  - Implement validation logic checking required capabilities against Registry Engine.
- **Milestone 3: Recipe Compiler**
  - Implement `RecipeCompiler` translating declarative recipes into compiled `WorkflowDefinition` state machine objects.
- **Milestone 4: Recipe Package Manager**
  - Implement `.kortex-recipe` import/export packager with SHA256 checksum verification.
- **Milestone 5: Recipe Engine Core & Diagnostics**
  - Implement `RecipeEngine` inheriting `BaseEngine` (Compiler & Packager only; delegates execution to Workflow Engine) with Common Diagnostics Interface.
- **Milestone 6: Recipe Engine Unit Tests**
  - Implement unit tests for recipe parsing, validation, compilation, and packaging.
- **Milestone 7: Recipe-to-Workflow Engine Integration Verification**
  - Verify complete pipeline: Recipe Engine compiles YAML recipe $\rightarrow$ dispatches compiled `WorkflowDefinition` to Workflow Engine $\rightarrow$ Workflow Engine executes state machine.

---

### Step 4: Document Engine

- **Milestone 1: Document Models & Renderer Interface**
  - Implement Document models, templates schemas, and `BaseDocumentRenderer` abstract plugin class.
- **Milestone 2: Renderer Registry & Plugin Loader**
  - Implement `DocumentRendererRegistry` and dynamic `PluginLoader` for renderer plugins.
- **Milestone 3: Dummy Renderer Plugin Implementation**
  - Implement `DummyDocumentRenderer` plugin to validate the rendering pipeline without production renderers.
- **Milestone 4: Template Compiler & Preview Stub**
  - Implement `Jinja2TemplateCompiler` for text/template interpolation and page preview stubs.
- **Milestone 5: Document Engine Core & Diagnostics**
  - Implement `DocumentEngine` inheriting `BaseEngine` with Common Diagnostics Interface.
- **Milestone 6: Document Engine Unit Tests**
  - Implement unit tests for renderer registry, plugin loader, dummy renderer execution, and template compilation.
- **Milestone 7: Document Engine Integration Verification**
  - Verify Document Engine registration with Kernel and capability lookup via Registry Engine.

---

### Step 5: Connector Engine

- **Milestone 1: Connector Driver Interface & Manifest Models**
  - Implement Connector config models, execution response models, and `BaseConnectorDriver` abstract plugin class.
- **Milestone 2: Connector Driver Registry & Plugin Loader**
  - Implement `ConnectorDriverRegistry` and dynamic `PluginLoader` for connector drivers.
- **Milestone 3: Dummy Connector Driver Implementation**
  - Implement `DummyConnectorDriver` plugin to validate action dispatching without production drivers.
- **Milestone 4: Rate Limiter Core**
  - Implement token-bucket rate limiter for outbound connector actions.
- **Milestone 5: Connector Engine Core & Diagnostics**
  - Implement `ConnectorEngine` inheriting `BaseEngine` with Common Diagnostics Interface.
- **Milestone 6: Connector Engine Unit Tests**
  - Implement unit tests for driver registry, plugin loader, dummy driver execution, and rate limiting.
- **Milestone 7: Connector Engine Integration Verification**
  - Verify Connector Engine registration with Kernel and capability lookup via Registry Engine.

---

## 6. Final Phase 2 Implementation Roadmap

```
Phase 2

Storage Engine
    Milestone 1: Interfaces & Models
    Milestone 2: Path Sandbox Validator
    Milestone 3: Relational Data Store (IDataStore)
    Milestone 4: Sandboxed File Store (IFileStore)
    Milestone 5: Object Store (IObjectStore)
    Milestone 6: Cache Store (ICacheStore)
    Milestone 7: Storage Engine Facade & Diagnostics
    Milestone 8: Unit Tests
    Milestone 9: Integration Verification
    Review
    Approval

Workflow Engine
    Milestone 1: Workflow Models & Database Schemas
    Milestone 2: State Machine Evaluator
    Milestone 3: Basic Approval Interface & State Handling
    Milestone 4: Retry, Backoff & Compensation Stack Handler
    Milestone 5: Workflow Engine Core & Diagnostics
    Milestone 6: Unit Tests
    Milestone 7: Integration Verification
    Review
    Approval

Recipe Engine
    Milestone 1: Recipe Models & Manifest Parser
    Milestone 2: Recipe Schema Validator & Capability Lookup
    Milestone 3: Recipe Compiler
    Milestone 4: Recipe Package Manager (.kortex-recipe)
    Milestone 5: Recipe Engine Core & Diagnostics
    Milestone 6: Unit Tests
    Milestone 7: Integration Verification
    Review
    Approval

Document Engine
    Milestone 1: Document Models & Renderer Interface
    Milestone 2: Renderer Registry & Plugin Loader
    Milestone 3: Dummy Renderer Plugin Implementation
    Milestone 4: Template Compiler & Preview Stub
    Milestone 5: Document Engine Core & Diagnostics
    Milestone 6: Unit Tests
    Milestone 7: Integration Verification
    Review
    Approval

Connector Engine
    Milestone 1: Connector Driver Interface & Manifest Models
    Milestone 2: Connector Driver Registry & Plugin Loader
    Milestone 3: Dummy Connector Driver Implementation
    Milestone 4: Rate Limiter Core
    Milestone 5: Connector Engine Core & Diagnostics
    Milestone 6: Unit Tests
    Milestone 7: Integration Verification
    Review
    Approval
```

---

## 7. Architectural Verification & Conclusion

This revised implementation strategy complies with all 10 engineering directives:
1. **Implementation Order**: Storage Engine $\rightarrow$ Workflow Engine $\rightarrow$ Recipe Engine $\rightarrow$ Document Engine $\rightarrow$ Connector Engine.
2. **Vertical Milestones**: Every engine divided into small, independently reviewable & testable milestones.
3. **Document Engine Scope**: Production renderers excluded; only Renderer Registry, Base Plugin, Plugin Loader, Dummy Renderer, and Tests.
4. **Connector Engine Scope**: Production connectors excluded; only Driver Registry, Base Driver, Plugin Loader, Dummy Driver, and Tests.
5. **Storage Engine Scope**: Encryption excluded; limited to Read, Write, Delete, Exists, List, Metadata, Sandbox validation, and Checksum support.
6. **Workflow Engine Scope**: Full Human Approval system excluded; limited to Approval interface, state, and events.
7. **Common Diagnostics Interface**: Standardized `health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, and `capabilities()` defined for all engines.
8. **Milestone Reporting Template**: Defined 8-point milestone reporting template.
9. **Milestone Rules**: Max 10–15 source files per milestone; zero forward dependencies.
10. **Final Roadmap**: Exact requested hierarchy format produced.

**Status**: IMPLEMENTATION STRATEGY REVISED — AWAITING CHIEF ARCHITECT APPROVAL BEFORE EXECUTING MILESTONE 1.
