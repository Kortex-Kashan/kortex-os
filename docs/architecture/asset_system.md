# KORTEX OS — Universal Asset System Architecture

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/asset_system.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- KORTEX OS Phase 2 Architecture Design (`docs/architecture/phase2_design.md`)

---

## 1. Purpose

This document defines the **Universal Asset System** governing all extensible, installable, and versioned assets in KORTEX OS.

KORTEX OS assets comprise:
- **Recipes** (`.kortex-recipe`)
- **Templates** (`.kortex-template`)
- **Adapters** (`.kortex-adapter`)
- **Modules** (`.kortex-module`)
- **Connectors** (`.kortex-connector`)
- **Knowledge Packs** (`.kortex-knowledge`)
- **Business Packs** (`.kortex-business`)
- **Themes** (`.kortex-theme`)
- **Marketplace Packages** (`.kortex-package`)

This architecture establishes a local-first, offline-first, cryptographic, SemVer-compliant asset management lifecycle ensuring safe installation, dependency resolution, atomic updates, rollbacks, and Marketplace distribution.

---

## 2. Asset Philosophy

1. **Local-First & Offline-First**: Assets function 100% locally without cloud verification dependencies.
2. **Unified Packaging Specification**: All asset types share a standard ZIP archive structure, manifest schema (`KortexAssetManifest`), and cryptographic verification pipeline.
3. **Strict Immutability**: Installed asset versions are immutable. Updates or edits produce new SemVer versions.
4. **Zero Code Injection**: Non-adapter asset packages (Recipes, Templates, Profiles, Knowledge Packs) contain zero executable code (Python, JS, binaries) and are strictly declarative.
5. **Decoupled Extensions**: Extensions add capabilities via Kernel IoC registration rather than modifying engine source code.

---

## 3. Universal Asset Model (`UniversalAsset`)

Every asset implements `UniversalAsset` defined in `shared_domain_models.md`:

- `asset_id`: Universal UUID string identifying asset instance.
- `namespace`: Reverse domain identifier (e.g. `kortex.hr.payroll`).
- `asset_type`: Enum type string (`RECIPE`, `TEMPLATE`, `ADAPTER`, `MODULE`, `CONNECTOR`, `KNOWLEDGE`, `BUSINESS`, `THEME`, `PACKAGE`).
- `manifest`: Embedded `KortexAssetManifest` object.
- `checksum_sha256`: SHA256 hex digest of complete archive payload.
- `digital_signature`: Ed25519 cryptographic signature string.
- `size_bytes`: Integer file byte size.
- `storage_key`: Reference key in Storage Engine (`IObjectStore`).

---

## 4. Asset Manifest (`KortexAssetManifest`)

All asset packages MUST contain a valid `manifest.yaml` adhering to `KortexAssetManifest`:

- `id`: Canonical UUID asset identifier.
- `name`: Human-readable name.
- `namespace`: Reverse-domain namespace string.
- `version`: Semantic Version string (`MAJOR.MINOR.PATCH`).
- `asset_type`: `AssetType` enum value.
- `description`: Detailed technical summary.
- `author`: `ManifestAuthor` object (name, email, organization).
- `license`: Software license model string (e.g. `MIT`).
- `dependencies`: Map of required assets and SemVer constraints.
- `capabilities_required`: List of required capability names.
- `capabilities_provided`: List of capability names exposed by asset.
- `permissions_required`: List of RBAC permissions required.
- `kernel_compatibility`: Compatible KORTEX Kernel SemVer range (`>=0.1.0`).
- `checksum`: SHA256 payload digest.
- `signature`: Ed25519 signature string.

---

## 5. Folder Structure

Unpacked asset packages strictly follow Clean Architecture:

```
<asset_name>/
├── manifest.yaml           # Asset manifest (KortexAssetManifest)
├── asset.yaml              # Declarative asset definition (Recipe, Template, Profile)
├── schema.yaml             # Input/Output validation schema definitions
├── permissions.yaml        # Declared RBAC permissions & capability dependencies
├── checksum.sha256         # SHA256 package checksum
├── signature.sig           # Cryptographic Ed25519 digital signature
└── resources/              # Static resources, documentation & schemas
```

---

## 6. Installation Pipeline

1. **Extraction**: Unpacks archive into sandboxed temporary workspace in `IFileStore`.
2. **Verification**: Executes 6-stage Verification Pipeline (Checksum, Signature, Static Code, Schema, Capabilities, Dependencies).
3. **Persistence**: Copies asset archive to `IObjectStore` and unpacked files to `IFileStore`.
4. **Registration**: Persists asset metadata in `IDataStore` and registers capabilities in Kernel Registry.
5. **State Lock**: Marks asset state as `ACTIVE`/`PUBLISHED`.

---

## 7. Upgrade Pipeline

1. **SemVer Validation**: Verifies upgrade package SemVer range against installed version.
2. **Compatibility Verification**: Verifies backward compatibility and checks for breaking schema changes.
3. **Atomic Swap**: Installs new version alongside old version, updates active registry references, and marks old version as `SUPERSEEDED`.
4. **Audit Log**: Records `AssetUpgradedEvent` in audit history.

---

## 8. Downgrade Policy

Direct downgrades to older asset versions are **forbidden by default** to prevent schema corruption. Downgrades MUST be executed through explicit **Rollback** operations using historical backups.

---

## 9. Rollback Pipeline

1. **Trigger**: Triggered automatically upon upgrade verification failure or manually by admin command.
2. **Restore Registry**: Restores previous stable version references in Kernel Registry from `IDataStore`.
3. **Purge Failed Snapshot**: Unregisters failed upgrade version and marks metadata as `FAILED`.
4. **State Lock**: Re-activates previous stable asset version.

---

## 10. Dependency Resolution

1. **Topological Sort**: Asset dependencies are resolved using directed acyclic graph (DAG) topological sorting.
2. **Conflict Resolution**: Verifies that all declared dependency SemVer ranges overlap compatibly without version collisions.
3. **Missing Dependency Guard**: Rejects installation if any required dependency or system capability is absent.

---

## 11. Compatibility Framework

- **Kernel Compatibility**: Checks `kernel_compatibility` against active Kernel SemVer.
- **Engine Compatibility**: Checks asset compatibility against Storage, Workflow, Recipe, Document, Connector, and Security engine versions.
- **Breaking Change Guard**: Rejects installation if breaking `MAJOR` version mismatches exist.

---

## 12. Digital Signatures

- **Algorithm**: Ed25519 public-key signature scheme.
- **Verification**: Verifies package `signature.sig` against trusted publisher public keys stored in Security Engine.
- **Unsigned Packages**: Unsigned packages are rejected in production mode; developer override flags permit installation strictly in sandbox dev environments.

---

## 13. Checksums

- **Algorithm**: SHA256 cryptographic digest.
- **Validation**: Re-calculates payload digest post-extraction and verifies against `checksum.sha256`. Mismatches cause immediate installation abort.

---

## 14. Asset Repository (`AssetRepository`)

The `AssetRepository` provides centralized access to installed assets via `IDataStore` relational indices and `IObjectStore` archives, supporting search, filter, lookup, and lineage inspection.

---

## 15. Local Repository

Local assets reside in sandboxed system workspace paths managed by `StorageEngine`. The system operates 100% offline without requiring internet access to list or load installed assets.

---

## 16. Marketplace Compatibility

Marketplace asset packages (`.kortex-package`) adhere to `KortexAssetManifest`. The platform supports third-party extension distribution through private enterprise repos or public Marketplace registries without core architecture modifications.

---

## 17. Verification Pipeline

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ Stage 1: File    │ ──> │ Stage 2: SHA256  │ ──> │ Stage 3: Ed25519 │ ──> │ Stage 4: Static  │
│ Structure Check  │     │ Checksum Check   │     │ Signature Check  │     │ Code Analysis    │
└──────────────────┘     └──────────────────┘     └──────────────────┘     └─────────┬────────┘
                                                                                     │
                                                                                     ▼
                                                       ┌──────────────────┐     ┌────┴─────────────┐
                                                       │ Stage 6: Depen-  │ <── │ Stage 5: Schema  │
                                                       │ dency Graph      │     │ Validation       │
                                                       └──────────────────┘     └──────────────────┘
```

---

## 18. Asset Lifecycle States

State machine transitions: `DRAFT` $\rightarrow$ `PENDING_REVIEW` $\rightarrow$ `ACTIVE` $\rightarrow$ `SUPERSEEDED` $\rightarrow$ `ARCHIVED` / `LOGICAL_DELETE`. Published states are strictly **immutable**.

---

## 19. Acceptance Criteria

- ✓ **Universal Coverage**: Covers all 9 asset types (Recipes, Templates, Adapters, Modules, Connectors, Knowledge, Business, Themes, Packages).
- ✓ **Local-First**: Complete local installation, verification, and execution without remote dependencies.
- ✓ **Cryptographic Security**: Ed25519 signature and SHA256 checksum validation specified.
- ✓ **Safe Upgrades**: SemVer resolution, DAG dependency checks, and atomic rollbacks defined.
