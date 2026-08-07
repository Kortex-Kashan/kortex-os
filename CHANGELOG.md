# Changelog

All notable changes to KORTEX OS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-07

### Added

- **Phase 2 — Recipe Engine (`kortex.engines.recipe`)**:
  - Central `RecipeEngine` facade implementing `BaseEngine` and `IEngineDiagnostics`.
  - Pure deterministic `RecipeCompiler` translating declarative Recipe DSL into `WorkflowDefinition` state machines without execution logic.
  - `RecipeParser` for loading `recipe.yaml`, `manifest.yaml`, `schema.yaml`, and `permissions.yaml`.
  - `RecipeValidator` enforcing schema validation, capability checks, and security rules (banning code files like `.py`, `.js`, `.sql`, `.sh`, `.exe`, `.dll`).
  - `RecipeManifestManager` managing `manifest.yaml` structure and SHA256 checksum calculation.
  - `RecipeRegistry` for cataloging, finding, searching, and listing registered recipes by ID, namespace, or version.
  - `RecipeInstaller` managing recipe installation, upgrade, removal, and rollback via `StorageEngine` (`IFileStore`).
  - `RecipePackager` creating and verifying standalone `.kortex-recipe` ZIP archives with SHA256 payload checksums.
  - `RecipeLoader` reading recipe assets from directories, ZIPs, or `.kortex-recipe` files.
  - `VersionResolver` implementing SemVer 2.0.0 comparison, range matching, and dependency resolution.
  - `PermissionValidator` enforcing least privilege and capability authorization checks.
  - `CompatibilityValidator` checking system engine and Kernel version constraints.
  - Registered 10 canonical capabilities: `kortex.recipe.load`, `kortex.recipe.validate`, `kortex.recipe.compile`, `kortex.recipe.install`, `kortex.recipe.remove`, `kortex.recipe.upgrade`, `kortex.recipe.package`, `kortex.recipe.search`, `kortex.recipe.list`, `kortex.recipe.info`.
  - Comprehensive test suite (131 tests passing, 97% overall coverage across `kortex.engines.recipe`).

## [0.1.0] - 2026-08-06

### Added

- Project scaffolding tool (`tools/create_project.py`)
- Python package configuration (`backend/pyproject.toml`)
- Development tooling (`.editorconfig`, `.pre-commit-config.yaml`, Ruff, mypy)
- Full-stack `.gitignore` (Python, Node.js, Tauri, Docker)
- Project documentation skeleton (`.kortex/`, `docs/`)
- Contribution guidelines (`CONTRIBUTING.md`)
- Phase 1 Kernel Foundation & Boot/Configuration/Registry/Event engines
- Phase 2 Storage Engine & Workflow Engine implementations
