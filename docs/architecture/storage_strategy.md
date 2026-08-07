# KORTEX OS — Storage Strategy Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/storage_strategy.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- KORTEX OS Phase 2 Architecture Design (`docs/architecture/phase2_design.md`)

---

## 1. Purpose

This document defines the canonical **Storage Strategy Architecture** for KORTEX OS.

In accordance with Article 12 of the KORTEX OS Engineering Constitution, the **Storage Engine** (`kortex.engines.storage`) is the sole gateway to persistence in KORTEX OS. No engine, module, service, or script may access SQLite, PostgreSQL, local filesystems, object stores, or in-memory caches directly. All data access flows through Storage Engine abstractions.

---

## 2. Four Universal Storage Layers

The Storage Engine abstracts persistence into four distinct, specialized stores:

```
                               ┌──────────────────────────────────┐
                               │          Storage Engine          │
                               ├──────────────────────────────────┤
                               │ • IDataStore   (Relational DB)   │
                               │ • IFileStore   (Local File Sys)  │
                               │ • IObjectStore (Blob Storage)    │
                               │ • ICacheStore  (Key-Value Cache) │
                               └──────────────────────────────────┘
```

---

## 3. Relational Data Store (`IDataStore`)

Provides relational transactional database sessions (`AsyncSession` wrapping SQLAlchemy 2.0).
- **SQLite**: Default relational backend for offline local execution.
- **PostgreSQL**: Supported enterprise relational backend.
- **Rules**: Business modules use `IDataStore.get_session()` without knowing the underlying SQL provider. Changing SQL providers requires zero business logic changes.

---

## 4. Binary Object Store (`IObjectStore`)

Provides immutable binary blob storage for large assets (rendered documents, preview images, marketplace packages).
- Calculates SHA256 checksums automatically (`ObjectMetadata.sha256_hash`).
- Provides content-addressable deduplication.
- Supports bucket isolation (e.g. `documents`, `packages`, `backups`).

---

## 5. Sandboxed File Store (`IFileStore`)

Provides file system read, write, list, metadata, and delete operations inside sandboxed workspace directories.
- Path sandboxing enforced by `PathSandboxValidator`.
- Directory traversal attacks (`../`) automatically blocked.

---

## 6. Ephemeral Cache Store (`ICacheStore`)

Provides high-performance key-value caching with TTL expiration support.
- Caches read projections, compiled template ASTs, capability lookups, and session tokens.
- Supports LRU eviction and memory threshold limits.

---

## 7. Database Indices

Relational tables in `IDataStore` maintain compound indices on (`tenant_id`, `id`), (`tenant_id`, `version_id`), and (`tenant_id`, `created_at_utc`) for sub-10ms query responses.

---

## 8. Search Architecture

Full-text and metadata search are indexed via `UniversalSearchMetadata`, decoupling business search queries from specific database search engines.

---

## 9. Storage Versioning

Entities track version lineage via `UniversalVersion`. Mutating an active entity creates a new database record linked to `parent_version_id`, preserving historical versions.

---

## 10. Snapshots

Read projections and workflow states periodically create point-in-time state snapshots in `ICacheStore` or `IObjectStore` to accelerate recovery.

---

## 11. Backups

Supports zero-downtime, incremental backup snapshots of `IDataStore` relational databases and `IObjectStore` binary buckets, exportable to encrypted local archives.

---

## 12. Encryption Strategy

- **In Phase 2**: Storage Engine handles plain persistence, checksums, and sandboxing.
- **Security Integration**: Key management and payload encryption at rest are delegated to Security Engine integration wrappers (`SecretStore`).

---

## 13. Compression

Large object blobs (packages, raw document outputs) are compressed using lossless algorithms prior to writing to `IObjectStore`.

---

## 14. Schema Migration Strategy

Database schema migrations are defined in declarative Alembic/SQLAlchemy migration scripts executed automatically during system startup or module installation.

---

## 15. Performance Benchmarks

- Relational Session Retrieval: $\le$ 5ms.
- Cache Key Lookup: $\le$ 1ms.
- Binary Object Streaming: Fixed 64KB chunk buffers streaming up to 100MB/s locally.

---

## 16. Acceptance Criteria

- ✓ **Single Persistence Gateway**: 100% of data, file, blob, and cache I/O flows through Storage Engine.
- ✓ **Zero Hardcoded Paths**: Direct `open()`, `os.path`, or raw SQL calls prohibited.
- ✓ **Sandboxed Filesystem**: `PathSandboxValidator` isolates file I/O within authorized workspaces.
- ✓ **Database Interchangeable**: SQLite and PostgreSQL backends work without code changes.
