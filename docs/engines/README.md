# KORTEX System Engines Documentation

Detailed technical documentation for the KORTEX OS system engines.

## Phase 2 Engine Implementation Status

| Engine | Namespace | Implementation Status | Coverage | Capabilities Registered |
| :--- | :--- | :--- | :--- | :--- |
| **Storage Engine** | `kortex.engines.storage` | **Completed** | 100% | `kortex.storage.data.session`, `kortex.storage.file.store`, `kortex.storage.object.put`, `kortex.storage.cache.set` |
| **Workflow Engine** | `kortex.engines.workflow` | **Completed** | 100% | `kortex.workflow.instance.start`, `kortex.workflow.instance.approve`, `kortex.workflow.instance.cancel`, `kortex.workflow.state.get` |
| **Recipe Engine** | `kortex.engines.recipe` | **Completed** | 97% | `kortex.recipe.load`, `kortex.recipe.validate`, `kortex.recipe.compile`, `kortex.recipe.install`, `kortex.recipe.remove`, `kortex.recipe.upgrade`, `kortex.recipe.package`, `kortex.recipe.search`, `kortex.recipe.list`, `kortex.recipe.info` |
| **Document Engine** | `kortex.engines.document` | **Completed** | 99% | `kortex.document.render.execute`, `kortex.document.preview.generate` |
| **Connector Engine** | `kortex.engines.connector` | **Completed** | 99% | `kortex.connector.driver.register`, `kortex.connector.action.execute` |

---

## System Engine Technical Summary

### 1. Recipe Engine (`kortex.engines.recipe`) — Completed (97% Coverage)
- **Purpose**: Declarative parser, validator, compiler, versioner, packager, installer, and catalog registry for Business Recipes.
- **Key Architectural Directive**: **Zero Execution Logic**. The Recipe Engine NEVER executes recipes. It compiles zero-code YAML/JSON recipes into `WorkflowDefinition` state machine execution plans and hands execution over to the Workflow Engine.
- **Compiler Determinism**: The `RecipeCompiler` is a pure deterministic compiler (same recipe + same inputs = identical `WorkflowDefinition`). It has zero filesystem, network, timestamp, or random state dependencies.
- **Security Rule Enforcement**: Bans executable file types (`.py`, `.js`, `.sql`, `.sh`, `.ps1`, `.exe`, `.dll`) within recipe packages.
- **Persistence**: All recipe archive storage operations use `StorageEngine` (`IFileStore`).

### 2. Workflow Engine (`kortex.engines.workflow`) — Completed (100% Coverage)
- **Purpose**: Sole runtime state machine and recipe execution engine for stateful workflows, retry backoffs, compensation rollbacks, and approval checkpoints.

### 3. Storage Engine (`kortex.engines.storage`) — Completed (100% Coverage)
- **Purpose**: Unified multi-store abstraction provider (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) with workspace sandboxing and SHA256 checksum support.
