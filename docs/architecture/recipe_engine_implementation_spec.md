# KORTEX OS — Recipe Engine Implementation Specification

Status: Approved for Implementation
Version: 3.0.0
Authority: KORTEX OS Engineering Constitution & Phase 2 Architecture Design
Target Release: KORTEX OS Phase 2: Business Foundation
Target File: `docs/architecture/recipe_engine_implementation_spec.md`

Depends On:
- Phase 1 Foundation (Kernel Engine, Event Engine, Registry Engine, Configuration Engine)
- Storage Engine (`kortex.engines.storage`)
- Workflow Engine (`kortex.engines.workflow`)

---

## 1. Executive Summary & Scope

The Recipe Engine (`kortex.engines.recipe`) is an enterprise-grade, declarative specification parser, validator, compiler, versioner, installer, packager, and lifecycle manager for zero-code KORTEX Business Recipes (`.kortex-recipe`).

As mandated by the KORTEX OS Engineering Constitution (Article 9) and Phase 2 Architecture Design (`docs/architecture/phase2_design.md`), the Recipe Engine functions **exclusively as a parser, compiler, and packager**. The Recipe Engine **NEVER executes recipes or workflows**. Execution belongs exclusively to the Workflow Engine.

The Phase 2 implementation scope of the Recipe Engine comprises:

1. **Declarative Recipe Parser (`RecipeParser`)**: YAML and JSON parser responsible for loading and structural parsing of `recipe.yaml`, `manifest.yaml`, `schema.yaml`, and `permissions.yaml`.
2. **Multi-Stage Recipe Validator (`RecipeValidator`)**: Validation engine for structural schemas, permissions, capability requirements, dependency graphs, SemVer compatibility, security rules, SHA256 checksums, and Ed25519 digital signatures.
3. **Deterministic Recipe Compiler (`RecipeCompiler`)**: Pure, deterministic compilation engine translating zero-code Recipe DSL representations into executable `WorkflowDefinition` state machine objects.
4. **Package Manager & Installer (`RecipeInstaller`, `RecipePackager`)**: Packaging, installation, upgrading, removal, rollback, and dependency resolution for standalone `.kortex-recipe` archives and developer folders.
5. **Recipe Asset Registry (`RecipeRegistry`)**: In-memory and persistent registry exposing lookup, search, and capability inspection for installed business recipes inside the Kernel Registry.
6. **Semantic Versioning & Compatibility Manager (`RecipeVersioning`, `RecipeCompatibility`)**: Strict SemVer 2.0.0 resolution, dependency graph verification, and backward compatibility checking across Kernel, Workflow Engine, Document Engine, Connector Engine, and business modules.
7. **AI Recommendation & Generation Interfaces (`IRecipeRecommendationProvider`)**: Abstract protocols for natural language recipe recommendation, intent mapping, and declarative YAML recipe generation (Provider interfaces only; AI optional design).
8. **Recipe Engine Core Facade (`RecipeEngine`)**: Engine entry point inheriting `BaseEngine`, implementing central orchestration, capability handlers, and lifecycle hooks.
9. **Common Diagnostics Interface (`IEngineDiagnostics`)**: Complete implementation of standard diagnostics methods (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
10. **Storage Engine Integration**: Exclusive use of `StorageEngine` abstractions (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) for recipe package loading, compiled workflow AST caching, package persistence, and version history.
11. **Security Integration & Static Code Rejection**: Strict static analysis rejecting executable code (Python, JavaScript, SQL, PowerShell, Shell, DLLs, binaries) and enforcing least-privilege capability permissions.

---

## 2. Architectural Hierarchy & Zero Execution Mandate

To preserve Clean Architecture and system stability, the Recipe Engine operates strictly as a declarative asset compilation engine. It maintains a clean separation between automation description (Recipe Engine) and automation execution (Workflow Engine).

```
                      ┌──────────────────────────────────────────┐
                      │    Marketplace / Developer Workspace     │
                      │        (.kortex-recipe archive)          │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │          Recipe Package Loader           │
                      │      (Extracts YAML & Manifest)          │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │       Multi-Stage Recipe Validator       │
                      │  (Schema, Security, Capability & Sig)    │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │       Deterministic Recipe Compiler      │
                      │   (Translates Recipe DSL -> Workflow AST)│
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │    Storage Engine (IDataStore / Cache)   │
                      │   (Persists Compiled WorkflowDefinition) │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │             Workflow Engine              │
                      │  (Sole State Machine Runtime Execution)  │
                      └──────────────────────────────────────────┘
```

### Architectural Rules:

1. **Zero Execution Mandate**: The Recipe Engine SHALL NOT execute workflows, state machines, background tasks, document operations, or connector requests.
2. **Deterministic Output**: For any given Recipe DSL input, the Recipe Compiler MUST produce an identical, byte-for-byte deterministic `WorkflowDefinition` object.
3. **Storage Engine Isolation**: All persistence operations (reading recipe archives, storing compiled workflows, caching ASTs) flow exclusively through the Storage Engine. Direct filesystem or database access is forbidden.
4. **Decoupled Workflow Handoff**: The Recipe Engine outputs compiled `WorkflowDefinition` objects to `IDataStore`. The Workflow Engine loads and executes these workflow definitions independently via event triggers or capability calls (`kortex.workflow.instance.start`).

---

## 3. Out of Scope

The following items are explicitly **OUT OF SCOPE** for the Recipe Engine:

1. **Workflow Execution & Runtime State Machines**: Managing step execution, loop iteration, approval pause states, retry backoffs, or compensation stack execution belongs exclusively to the Workflow Engine.
2. **Document Rendering & Generation**: Layout parsing, format adaptation, and binary output generation belong exclusively to the Document Engine.
3. **External Connector Protocol Execution**: HTTP requests, email transmission, printer spooling, or external API drivers belong exclusively to the Connector Engine.
4. **Dynamic Code Execution**: Executing arbitrary scripts (Python, JavaScript, Lua, SQL, PowerShell, Shell) or dynamic binary payloads (DLLs, shared objects) is strictly prohibited. Recipes are 100% declarative YAML/JSON specifications.
5. **Direct Storage & Database Access**: Direct calls to Python `open()`, `pathlib`, `os.path`, `sqlite3`, `asyncpg`, or AWS S3 SDKs are forbidden. All I/O flows through the Storage Engine.
6. **Marketplace Server Hosting**: Hosting remote package repositories or authentication servers is handled by external Marketplace infrastructure. The Recipe Engine manages local package installation, validation, and unpacking only.
7. **User Interface Components**: Recipe visual builders, drag-and-drop workflow canvas editors, or GUI forms.

---

## 4. Recipe Specification & DSL Architecture

KORTEX Recipes are zero-code, declarative automation specifications defined in YAML or JSON format. A recipe describes a multi-step business process by composing registered system capabilities (`kortex.<domain>.<resource>.<action>`).

### 4.1 Recipe Archive Anatomy (`.kortex-recipe`)

A standalone `.kortex-recipe` package is a zip archive containing the following standardized assets:

- `manifest.yaml`: Asset manifest specification compliant with `KortexAssetManifest`.
- `recipe.yaml`: Primary declarative recipe DSL definition.
- `schema.yaml`: Declarative input and output variable schemas.
- `permissions.yaml`: Declarative permission requirements and required capability access lists.
- `checksum.sha256`: SHA256 digest calculated over package contents.
- `signature.sig`: Cryptographic Ed25519 digital signature of the publisher.

### 4.2 Declarative Recipe DSL Structure

The Recipe DSL (`recipe.yaml`) contains zero executable code. It defines:

1. **Metadata**: ID, name, namespace, version, author, description, category.
2. **Trigger**: Event pattern (`kortex.event.<domain>.<event>`), schedule cron expression, or manual API trigger.
3. **Inputs**: Named input parameters, data types, default values, and validation constraints.
4. **Steps**: Sequential or conditional execution steps. Each step specifies:
   - `step_id`: Unique string identifier within the recipe.
   - `capability`: Target system capability name (`kortex.<domain>.<resource>.<action>`).
   - `parameters`: Mapping of input parameters using declarative expression bindings (e.g. `${inputs.employee_id}`).
   - `condition`: Optional declarative boolean expression governing step execution.
   - `on_failure`: Failure strategy (`ABORT`, `RETRY`, `CONTINUE`, `COMPENSATE`).
5. **Compensation Stack**: Reverse rollback actions associated with steps to execute upon terminal workflow failure.
6. **Outputs**: Mapping of output variables returned upon completion.
7. **Permissions**: Required RBAC permissions and capabilities declared in `permissions.yaml`.

---

## 5. Recipe Compiler Architecture (Deterministic Compilation)

The `RecipeCompiler` transforms declarative Recipe DSL objects into compiled, executable `WorkflowDefinition` state machine ASTs for the Workflow Engine.

```
┌─────────────────────────┐                               ┌───────────────────────────┐
│       Recipe DSL        │                               │    Workflow Definition    │
├─────────────────────────┤                               ├───────────────────────────┤
│ • manifest.yaml         │    Deterministic Compiler     │ • workflow_id / version   │
│ • recipe.yaml           │ ───────────────────────────>  │ • state_nodes (Topology)  │
│ • schema.yaml           │                               │ • transition_edges        │
│ • permissions.yaml      │                               │ • compensation_stack      │
└─────────────────────────┘                               └───────────────────────────┘
```

### Compiler Design Guarantees:

1. **Pure Functional Transformation**: The compiler receives parsed and validated `RecipeDefinition` objects and returns a compiled `WorkflowDefinition` without mutating global state or performing network I/O.
2. **Deterministic Step Mapping**:
   - Recipe steps translate directly to workflow state nodes (`WorkflowStateNode`).
   - Declarative parameter bindings translate to deterministic runtime expression nodes.
   - Triggers map to workflow entrypoint state triggers.
   - Failure and compensation definitions map to workflow exception handling nodes and rollback stacks.
3. **Schema Invariant Verification**: The compiler verifies that all outputs produced by step $N$ satisfy the input parameter schemas required by step $N+1$ prior to generating the workflow object.

---

## 6. Recipe Validation & Dependency Resolution Engine

The `RecipeValidator` enforces strict security, structural, capability, and dependency checks before any recipe can be compiled or installed.

### 6.1 Multi-Stage Validation Pipeline

```
┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐     ┌───────────────────┐
│ Stage 1: Manifest │ ──> │ Stage 2: Security │ ──> │ Stage 3: Schema   │ ──> │ Stage 4: Capa-    │
│ & Signature Check │     │ Static Analysis   │     │ & DSL Validation  │     │ bility Lookup     │
└───────────────────┘     └───────────────────┘     └───────────────────┘     └─────────┬─────────┘
                                                                                        │
                                                                                        ▼
                                                          ┌───────────────────┐     ┌───┴───────────────┐
                                                          │ Stage 6: Compilers│ <── │ Stage 5: Depen-   │
                                                          │ Output Check      │     │ dency & SemVer    │
                                                          └───────────────────┘     └───────────────────┘
```

1. **Stage 1: Manifest & Digital Signature Validation**: Verifies `KortexAssetManifest`, SHA256 checksums, and Ed25519 digital signatures.
2. **Stage 2: Static Code Rejection**: Scans all files inside the recipe package to verify **zero executable code** exists. Rejects packages containing Python (`.py`), JavaScript (`.js`), SQL (`.sql`), PowerShell (`.ps1`), Shell (`.sh`), executables (`.exe`), or libraries (`.dll`, `.so`).
3. **Stage 3: Schema & DSL Validation**: Validates `recipe.yaml` structure against Pydantic models (`RecipeDefinition`, `RecipeStep`, `RecipeInput`).
4. **Stage 4: Capability Lookup Validation**: Queries the Registry Engine to verify that every capability referenced in recipe steps (`kortex.<domain>.<resource>.<action>`) exists and is currently registered in the platform.
5. **Stage 5: Dependency Graph & SemVer Validation**: Verifies dependencies declared in `manifest.yaml` against installed modules, engines, and minimum Kernel versions using `RecipeCompatibility`.
6. **Stage 6: Compiler Pre-Flight Check**: Performs dry-run compilation to verify deterministic AST generation without errors.

---

## 7. Package Management, Installers & Marketplace Compatibility

The Recipe Engine includes package management services for installing, upgrading, removing, and packaging business recipes.

### 7.1 Package Operations (`RecipeInstaller`, `RecipePackager`)

- **Installation (`install()`)**:
  1. Loads package via `RecipeLoader` (from folder or `.kortex-recipe` archive).
  2. Runs complete multi-stage `RecipeValidator` pipeline.
  3. Invokes `RecipeCompiler` to generate compiled `WorkflowDefinition`.
  4. Stores recipe files in `IFileStore`, package archives in `IObjectStore`, and recipe registration metadata in `IDataStore`.
  5. Registers recipe capabilities inside Kernel Registry via `RecipeRegistry`.
- **Upgrade (`upgrade()`)**:
  1. Validates SemVer range of upgrade package against installed version.
  2. Verifies backward compatibility and checks for breaking input/output schema changes.
  3. Executes atomic state update: updates recipe files, compiles new `WorkflowDefinition`, marks old version as superseded, and updates registry references.
- **Rollback (`rollback()`)**: Restores previous stable recipe version from `IDataStore` if an upgrade fails validation or deployment.
- **Removal (`remove()`)**: Unregisters recipe capabilities from Kernel Registry, marks recipe metadata as removed in `IDataStore`, and archives recipe package files.
- **Packaging (`package()`)**: Assembles recipe directory into a verified `.kortex-recipe` archive, calculates SHA256 checksums, updates `KortexAssetManifest`, and applies cryptographic signatures.

---

## 8. Semantic Versioning, Lineage & Migration Management

The Recipe Engine enforces Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`) across all recipe assets.

### 8.1 SemVer Rules for Business Recipes:

- **MAJOR Version Bump** (`1.0.0` $\rightarrow$ `2.0.0`): Breaking changes to recipe input parameter types, removed steps, altered output structures, or changed capability requirements.
- **MINOR Version Bump** (`1.0.0` $\rightarrow$ `1.1.0`): Adding new optional input parameters, additional non-breaking steps, or supplementary telemetry output tags.
- **PATCH Version Bump** (`1.0.0` $\rightarrow$ `1.0.1`): Fixing step description typos, performance optimization of expression bindings, or documentation updates.

### 8.2 Recipe Lineage & Version History

- Every installed recipe tracks its version chain (`recipe_id`, `version`, `parent_version`).
- `IDataStore` maintains recipe lineage trees allowing historical inspection of recipe evolution.
- Old recipe versions remain stored in `IDataStore` to support active workflow instances executing on legacy recipe versions.

---

## 9. AI Readiness & Natural Language Recipe Recommendation Interfaces

The Recipe Engine includes formal interface protocols for AI-assisted recipe selection and generation while maintaining an **AI Optional Design** (the engine operates 100% deterministically without active AI models).

### 9.1 Recipe Recommendation Provider Interface (`IRecipeRecommendationProvider`)

Defines abstract contract for AI recommendation providers:

- `recommend_recipe()`: Analyzes natural language user intent and context data to recommend the most relevant installed recipe ID.
- `generate_declarative_recipe()`: Generates a candidate zero-code declarative Recipe DSL (`recipe.yaml`) string from natural language process descriptions.
- `validate_generated_recipe()`: Passes AI-generated recipe strings through `RecipeValidator` to ensure strict structural compliance before user review.

---

## 10. Folder Structure & Module Responsibilities

All Recipe Engine source code strictly resides inside `backend/src/kortex/engines/recipe/`.

```
backend/src/kortex/engines/recipe/
├── __init__.py                # Package exports (RecipeEngine, models, interfaces)
├── engine.py                  # RecipeEngine core facade inheriting BaseEngine
├── interfaces.py              # Abstract interfaces (IRecipeEngine, IRecipeCompiler, etc.)
├── models.py                  # Pydantic v2 domain models, schemas, and enums
├── exceptions.py              # Strongly-typed hierarchy of recipe engine exceptions
├── compiler.py                # Deterministic RecipeCompiler (Recipe DSL -> WorkflowDefinition)
├── parser.py                  # RecipeParser for YAML/JSON files (recipe, manifest, schema)
├── validator.py               # Multi-Stage RecipeValidator & static code rejection
├── registry.py                # RecipeRegistry for managing registered business recipes
├── installer.py               # RecipeInstaller for install, upgrade, remove, rollback
├── packager.py                # RecipePackager for creating .kortex-recipe archives
├── loader.py                  # RecipeLoader for package inspection (Folder, ZIP, Archive)
├── versioning.py              # Semantic Versioning 2.0.0 & dependency graph resolver
├── permissions.py             # IPermissionValidator for least-privilege checks
├── compatibility.py           # ICompatibilityValidator across modules and engines
├── diagnostics.py             # Common Diagnostics Interface (IEngineDiagnostics)
├── manifest.py                # KortexAssetManifest parser and validator wrappers
├── dsl.py                     # Recipe DSL Pydantic representation models
└── events.py                  # Immutable event payload definitions

backend/tests/unit/
├── test_recipe_models.py           # Unit tests for Pydantic models and enum validations
├── test_recipe_parser.py           # Unit tests for YAML/JSON parser
├── test_recipe_validator.py        # Unit tests for multi-stage validator & code rejection
├── test_recipe_compiler.py        # Unit tests for deterministic compilation
├── test_recipe_registry.py        # Unit tests for recipe registration and search
├── test_recipe_loader.py          # Unit tests for package loader
├── test_recipe_installer.py       # Unit tests for install, upgrade, remove, rollback
├── test_recipe_packager.py        # Unit tests for .kortex-recipe archive creation
├── test_recipe_permissions.py     # Unit tests for permission validation
├── test_recipe_compatibility.py   # Unit tests for SemVer compatibility checks
├── test_recipe_diagnostics.py     # Unit tests for IEngineDiagnostics methods
└── test_recipe_engine.py          # Unit tests for core RecipeEngine facade

backend/tests/integration/
└── test_recipe_engine_integration.py # Integration tests with Kernel, Workflow & Storage Engine
```

---

## 11. Implementation Milestones

Implementation is divided into ten sequential vertical milestones. Each milestone is independently testable, reviewable, and limited to a focused set of source files.

```
Step 3: Recipe Engine (Phase 2 Roadmap)
├── Milestone 1: Models, Manifest & Recipe DSL Definition
├── Milestone 2: Recipe Parser & YAML/JSON Deserializer
├── Milestone 3: Multi-Stage Recipe Validator & Static Code Rejection Engine
├── Milestone 4: Deterministic Recipe Compiler (Recipe DSL -> Workflow Definition)
├── Milestone 5: Recipe Registry, Loader & Package Manager (.kortex-recipe)
├── Milestone 6: Recipe Installer, Upgrade, Rollback & Dependency Resolver
├── Milestone 7: Versioning, Compatibility & Lineage Manager
├── Milestone 8: Storage Integration & Multi-Level Caching
├── Milestone 9: Engine Facade, Kernel Integration, Diagnostics & Capability Registration
└── Milestone 10: Unit & Integration Test Suite & Architecture Audit
```

### Milestone 1: Models, Manifest & Recipe DSL Definition
- **Goal**: Establish core type definitions, domain models, custom exception classes, and Recipe DSL representations.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/models.py`
  - `backend/src/kortex/engines/recipe/interfaces.py`
  - `backend/src/kortex/engines/recipe/exceptions.py`
  - `backend/src/kortex/engines/recipe/manifest.py`
  - `backend/src/kortex/engines/recipe/dsl.py`
  - `backend/tests/unit/test_recipe_models.py`
- **Deliverables**:
  - Pydantic v2 models: `RecipeManifest`, `RecipeDefinition`, `RecipeMetadata`, `RecipeInput`, `RecipeStep`, `RecipeOutput`, `RecipeSettings`, `RecipePermission`, `RecipeCompatibility`, `RecipeDependency`.
  - Abstract interface `IRecipeEngine` and supporting core protocols.
  - Unit tests verifying model validation, serialization, and default values.

### Milestone 2: Recipe Parser & YAML/JSON Deserializer
- **Goal**: Build YAML and JSON parsing service for recipe files.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/parser.py`
  - `backend/tests/unit/test_recipe_parser.py`
- **Deliverables**:
  - `RecipeParser` supporting loading of `recipe.yaml`, `manifest.yaml`, `schema.yaml`, and `permissions.yaml`.
  - Syntax error handling and detailed line-number error reporting.
  - Unit tests verifying file deserialization and syntax validation.

### Milestone 3: Multi-Stage Recipe Validator & Static Code Rejection Engine
- **Goal**: Build security, capability, schema, and static code rejection validator.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/validator.py`
  - `backend/src/kortex/engines/recipe/permissions.py`
  - `backend/tests/unit/test_recipe_validator.py`
  - `backend/tests/unit/test_recipe_permissions.py`
- **Deliverables**:
  - Multi-stage `RecipeValidator` checking manifest, schema, capability availability, permissions, checksums, and signatures.
  - Static code scanner explicitly rejecting Python, JS, SQL, Shell, and binary files.
  - Unit tests verifying validation rules and security rejection.

### Milestone 4: Deterministic Recipe Compiler (Recipe DSL -> Workflow Definition)
- **Goal**: Build deterministic compilation engine translating recipes to workflow definitions.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/compiler.py`
  - `backend/tests/unit/test_recipe_compiler.py`
- **Deliverables**:
  - `RecipeCompiler` producing pure, deterministic `WorkflowDefinition` state machine objects.
  - Mapping step parameters, triggers, compensation nodes, and failure strategies.
  - Unit tests verifying compilation determinism and AST structural validity.

### Milestone 5: Recipe Registry, Loader & Package Manager (.kortex-recipe)
- **Goal**: Implement package loader, archive packager, and Kernel registry manager.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/registry.py`
  - `backend/src/kortex/engines/recipe/loader.py`
  - `backend/src/kortex/engines/recipe/packager.py`
  - `backend/tests/unit/test_recipe_registry.py`
  - `backend/tests/unit/test_recipe_loader.py`
  - `backend/tests/unit/test_recipe_packager.py`
- **Deliverables**:
  - `RecipeRegistry` supporting in-memory registration, search, and capability lookup.
  - `RecipeLoader` supporting Folder, ZIP, and `.kortex-recipe` archives.
  - `RecipePackager` creating `.kortex-recipe` archives with SHA256 checksums and digital signatures.
  - Unit tests verifying loader, packager, and registry functionality.

### Milestone 6: Recipe Installer, Upgrade, Rollback & Dependency Resolver
- **Goal**: Build installer service managing lifecycle operations for recipe packages.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/installer.py`
  - `backend/tests/unit/test_recipe_installer.py`
- **Deliverables**:
  - `RecipeInstaller` handling `install()`, `upgrade()`, `remove()`, and `rollback()`.
  - Atomic transaction handling and dependency resolution.
  - Unit tests verifying install pipelines, upgrade migrations, and rollback execution.

### Milestone 7: Versioning, Compatibility & Lineage Manager
- **Goal**: Implement SemVer 2.0.0 version resolution, module compatibility checks, and lineage tracking.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/versioning.py`
  - `backend/src/kortex/engines/recipe/compatibility.py`
  - `backend/tests/unit/test_recipe_compatibility.py`
- **Deliverables**:
  - `RecipeVersioning` resolving SemVer ranges and version graphs.
  - `RecipeCompatibility` checking compatibility across Kernel, Workflow, Document, Connector, and Storage engines.
  - Unit tests verifying version comparison, breaking change detection, and compatibility checks.

### Milestone 8: Storage Integration & Multi-Level Caching
- **Goal**: Connect Recipe Engine services to Storage Engine abstractions (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`).
- **Files Created**:
  - `backend/tests/unit/test_recipe_storage.py`
- **Deliverables**:
  - Storage Engine binding for metadata and version history (`IDataStore`).
  - Storage Engine binding for recipe package files (`IFileStore`).
  - Storage Engine binding for `.kortex-recipe` binary archives (`IObjectStore`).
  - Caching bindings for compiled workflow ASTs and parsed DSL objects (`ICacheStore`).
  - Unit tests verifying persistence and cache interactions.

### Milestone 9: Engine Facade, Kernel Integration, Diagnostics & Capability Registration
- **Goal**: Implement main engine facade, capability handlers, and diagnostic telemetry.
- **Files Created**:
  - `backend/src/kortex/engines/recipe/engine.py`
  - `backend/src/kortex/engines/recipe/diagnostics.py`
  - `backend/src/kortex/engines/recipe/events.py`
  - `backend/src/kortex/engines/recipe/__init__.py`
  - `backend/tests/unit/test_recipe_diagnostics.py`
- **Deliverables**:
  - `RecipeEngine` inheriting `BaseEngine`.
  - Capability handler methods (`kortex.recipe.load`, `kortex.recipe.compile`, `kortex.recipe.install`, etc.).
  - Standardized `IEngineDiagnostics` implementation (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
  - System events catalog definition.
  - Unit tests verifying engine initialization, telemetry metrics, and capability dispatching.

### Milestone 10: Unit & Integration Test Suite & Architecture Audit
- **Goal**: Complete comprehensive unit and integration test coverage across all engine files.
- **Files Created**:
  - `backend/tests/unit/test_recipe_engine.py`
  - `backend/tests/integration/test_recipe_engine_integration.py`
- **Deliverables**:
  - Complete edge case testing (invalid YAML, missing capabilities, version conflicts, corrupted archives).
  - Verification of 100% test pass rate.
  - Verification of ≥90% code coverage across all core files in `backend/src/kortex/engines/recipe/`.
  - Architecture Compliance Audit verification.

---

## 12. Public Interfaces & Protocols

All public contracts are defined using Python `Protocol` and Abstract Base Classes (ABC) in `interfaces.py`.

### 12.1 IRecipeEngine Protocol

```python
# Specification declaration for IRecipeEngine interface
class IRecipeEngine(Protocol):
    """Primary facade interface exposed by the Recipe Engine."""

    async def load_recipe(self, package_path: str) -> RecipePackage:
        """Load and parse a recipe package from folder or .kortex-recipe archive."""
        ...

    async def validate_recipe(self, package: RecipePackage) -> RecipeValidationResult:
        """Run multi-stage validation pipeline on loaded recipe package."""
        ...

    async def compile_recipe(self, recipe: RecipeDefinition) -> RecipeCompilationResult:
        """Compile a RecipeDefinition into an executable WorkflowDefinition AST deterministically."""
        ...

    async def install_recipe(self, package_path: str) -> RecipeInstallationResult:
        """Validate, compile, persist, and register a recipe package."""
        ...

    async def upgrade_recipe(self, package_path: str) -> RecipeUpgradeResult:
        """Upgrade an installed recipe package with SemVer compatibility checks."""
        ...

    async def remove_recipe(self, recipe_id: str, version: str) -> RecipeRemovalResult:
        """Unregister and archive an installed recipe."""
        ...

    async def package_recipe(self, recipe_dir: str, output_path: str) -> str:
        """Assemble a recipe directory into a signed .kortex-recipe archive."""
        ...

    def search_recipes(self, query: str) -> List[RecipeMetadata]:
        """Search registered recipes by keyword, category, or capability requirement."""
        ...
```

### 12.2 Supporting Engine Protocols

- `IRecipeParser`: Interface for loading and deserializing YAML/JSON recipe assets.
- `IRecipeValidator`: Interface for executing multi-stage validation checks.
- `IRecipeCompiler`: Interface for pure deterministic compilation into `WorkflowDefinition` ASTs.
- `IRecipeRegistry`: Interface for recipe registration, search, and capability lookup.
- `IRecipeInstaller`: Interface for package lifecycle operations (install, upgrade, remove, rollback).
- `IRecipePackager`: Interface for archiving and digitally signing `.kortex-recipe` packages.
- `IRecipeRecommendationProvider`: Interface for AI natural language recipe recommendations.

---

## 13. Data Models & Schemas

Defined using Pydantic v2 (`BaseModel`) with strict type validation, default values, and field documentation.

### Core Data Models:

- `RecipeManifest`: Manifest specification inheriting from `KortexAssetManifest`.
- `RecipeDefinition`: Top-level recipe DSL model (`recipe_id`, `name`, `version`, `trigger`, `inputs`, `steps`, `outputs`, `compensation`).
- `RecipeStep`: Individual recipe execution step (`step_id`, `capability`, `parameters`, `condition`, `on_failure`).
- `RecipeInput`: Input parameter definition (`name`, `type`, `default`, `required`, `description`).
- `RecipeOutput`: Output parameter binding mapping.
- `RecipePackage`: Loaded recipe asset package containing manifest, definition, schemas, permissions, and checksums.
- `RecipeCompilationResult`: Result model containing compiled `WorkflowDefinition`, compilation duration, and checksum.
- `RecipeValidationResult`: Result model containing validation status, error messages, and capability check results.
- `RecipeInstallationResult`: Result model containing installed recipe ID, version, and registration status.

---

## 14. System Events Catalog

Immutable event catalog published to Event Engine (`kortex.events`):

| Event Name | Topic / Routing Key | Trigger Condition |
| :--- | :--- | :--- |
| `RecipeRegisteredEvent` | `recipe.registered` | Dispatched when a recipe is registered in Kernel Registry |
| `RecipeCompiledEvent` | `recipe.compiled` | Dispatched when a recipe is successfully compiled into a `WorkflowDefinition` |
| `RecipeInstalledEvent` | `recipe.installed` | Dispatched when a recipe package is fully installed and stored |
| `RecipeUpgradedEvent` | `recipe.upgraded` | Dispatched when an installed recipe is upgraded to a newer version |
| `RecipeRemovedEvent` | `recipe.removed` | Dispatched when a recipe is removed or archived |
| `RecipePackagedEvent` | `recipe.packaged` | Dispatched when a `.kortex-recipe` package archive is generated |
| `RecipeValidationFailedEvent` | `recipe.validation.failed` | Dispatched when a recipe package fails validation checks |
| `RecipeCompilationFailedEvent` | `recipe.compilation.failed` | Dispatched when recipe compilation fails |

---

## 15. Capability Registration

Canonical capability names (`kortex.<domain>.<resource>.<action>`):

1. `kortex.recipe.load`: Load and inspect recipe packages from storage.
2. `kortex.recipe.validate`: Execute multi-stage validation checks on a recipe payload.
3. `kortex.recipe.compile`: Compile declarative Recipe DSL into an executable `WorkflowDefinition`.
4. `kortex.recipe.install`: Install, compile, and register a business recipe package.
5. `kortex.recipe.remove`: Remove and unregister an installed business recipe.
6. `kortex.recipe.upgrade`: Upgrade an installed recipe with compatibility checks.
7. `kortex.recipe.package`: Assemble and sign a `.kortex-recipe` archive.
8. `kortex.recipe.search`: Search installed recipes by keyword or capability requirement.
9. `kortex.recipe.list`: List all installed business recipes and versions.
10. `kortex.recipe.info`: Retrieve detailed metadata and capability requirements for a recipe.

---

## 16. Storage & Multi-Level Caching Requirements

### Storage Interaction Rules:
1. **Recipe Files (`IFileStore`)**: Unpacked recipe YAML definitions, schemas, and permissions are stored in sandboxed paths via `IFileStore`.
2. **Binary Package Persistence (`IObjectStore`)**: Standalone `.kortex-recipe` archives are persisted in `IObjectStore` buckets. Storage Engine computes and returns SHA256 checksums (`ObjectMetadata.sha256_hash`).
3. **Relational Metadata (`IDataStore`)**: Recipe registrations, version chains, lineage trees, and capability requirements are stored in relational tables via `IDataStore.get_session()`.
4. **Multi-Level Caching (`ICacheStore`)**:
   - **Compiled Workflow AST Cache**: Caches compiled `WorkflowDefinition` objects by recipe ID and SemVer hash to eliminate compilation overhead on repeated executions.
   - **Parsed Recipe DSL Cache**: Caches parsed `RecipeDefinition` Pydantic models.
   - **Capability Validation Cache**: Caches capability lookup resolution paths.

---

## 17. Security Integration & Static Analysis Protection

1. **Zero Executable Code Policy**: The `RecipeValidator` enforces static code analysis scanning, rejecting any recipe package containing executable scripts (Python, JS, SQL, Shell) or binaries.
2. **Package Integrity & Signature Verification**: All `.kortex-recipe` packages must include valid SHA256 checksums and Ed25519 digital signatures verified against trusted publisher keys before installation.
3. **Least-Privilege Capability Checks**: `permissions.yaml` must explicitly declare all required capabilities (`capabilities_required`). The Recipe Engine verifies that the caller possesses sufficient permissions before compiling or installing a recipe.
4. **Sandboxed Evaluation**: Parameter expression evaluation is strictly restricted to declarative variable lookups (`${inputs.var_name}`). Arbitrary expression evaluation or code reflection is blocked.

---

## 18. Performance Requirements

1. **Deterministic Sub-50ms Compilation**: Recipe compilation for standard recipes (up to 50 steps) must complete in $\le$ 50ms.
2. **Compiled AST Caching**: Compiled `WorkflowDefinition` ASTs are cached in `ICacheStore` to achieve sub-millisecond retrieval times for Workflow Engine execution requests.
3. **Asynchronous Non-Blocking Execution**: All engine operations use `async`/`await` primitives to prevent blocking the Python asyncio main loop.
4. **Low Memory Footprint**: Recipe parsing and compilation operate on streaming buffers to minimize memory allocation per compilation task.

---

## 19. Testing & Quality Gates Requirements

1. **Unit Tests**: Comprehensive unit tests covering all ten implementation milestones in `backend/tests/unit/`.
2. **Integration Tests**: End-to-end integration tests in `backend/tests/integration/` verifying Kernel boot registration, capability lookup, recipe compilation handoff to Workflow Engine, and Event Engine event dispatching.
3. **Quality Gates**:
   - 100% passing unit and integration tests.
   - $\ge$ 90% code coverage across every core file in `backend/src/kortex/engines/recipe/`.
   - Zero architectural violations.

---

## 20. Acceptance Criteria & Final Architectural Audit Checklist

The Recipe Engine implementation shall be considered complete and ready for pull request merge only when all of the following criteria are met:

- ✓ **Architecture Compliant**: Fully complies with `AGENTS.md`, `engineering_constitution.md`, `platform_principles.md`, and `phase2_design.md`.
- ✓ **Zero Execution Mandate**: The engine ONLY parses, validates, compiles, and packages recipes. Execution belongs exclusively to the Workflow Engine.
- ✓ **Zero Executable Code**: Static code analysis scanner verifies zero executable scripts (Python, JS, SQL, Shell) or binaries exist in recipe packages.
- ✓ **Storage Engine Only**: 100% of file, data, object, and cache operations flow strictly through `StorageEngine` abstractions (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`).
- ✓ **Capability Registration Completed**: Canonical capabilities (`kortex.recipe.load`, `kortex.recipe.compile`, `kortex.recipe.install`, etc.) registered in Kernel IoC container and Registry Engine.
- ✓ **Kernel Integration Verified**: Engine facade inherits `BaseEngine`, implements `IEngineDiagnostics`, and registers cleanly during Kernel boot sequence.
- ✓ **Deterministic Compilation**: `RecipeCompiler` produces 100% deterministic `WorkflowDefinition` state machine objects.
- ✓ **Package Management Verified**: `.kortex-recipe` packaging, installation, upgrade, removal, and rollback operations verified.
- ✓ **SemVer Compatibility Verified**: SemVer 2.0.0 version resolution, dependency graph verification, and lineage tracking verified.
- ✓ **Marketplace-Ready**: Packages include `KortexAssetManifest`, SHA256 checksums, and Ed25519 digital signatures.
- ✓ **AI-Ready**: Exposes `IRecipeRecommendationProvider` protocol interface for AI-assisted recipe selection and generation.
- ✓ **Unit Tests $\ge$ 90%**: Code coverage threshold met across all core files in `backend/src/kortex/engines/recipe/`.
- ✓ **Integration Tests Pass**: 100% end-to-end integration tests passing.
- ✓ **Architecture Audit Passes**: Architecture review checklist verified with zero violations.