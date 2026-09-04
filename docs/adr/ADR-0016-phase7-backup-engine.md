# ADR-0016: Phase 7 — Production Hardening — Backup Engine

- **Status**: IMPLEMENTED — AWAITING REVIEW
- **Date**: 2026-09-04
- **Deciders**: Chief Architect (KASHAN), Antigravity (Implementation Engineer)
- **Target Component**: Backup Engine (`kortex.engines.backup`)

---

## Context and Problem Statement

KORTEX OS is an AI-powered local-first business operating system requiring enterprise-grade production reliability and disaster-recovery readiness. While Sentinel Engine (ADR-0014) established health observability and Monitoring Engine (ADR-0015) established real-time operational telemetry, KORTEX required a dedicated engine to capture, package, protect, validate, retain, index, and report consistent, durable, and encrypted point-in-time operational snapshots of the entire application state.

The challenge was to design and implement the Backup Engine adhering strictly to KORTEX Architecture v1.0.0 and the AI Engineering Constitution (`AGENTS.md`):
1. **The Backup/Recovery Boundary**: Backup creates and validates backup artifacts; it must NEVER restore, roll back, replace live state, restart services, or execute recovery. Restoration belongs exclusively to the future Recovery Engine.
2. **Database Consistency**: Relational database snapshotting must use native SQLite online backup APIs (`sqlite3.Connection.backup` executed asynchronously in worker threads), guaranteeing page-level transaction consistency without blocking the live server or creating dirty reads.
3. **Encryption Required by Default**: Authenticated AES-256-GCM symmetric encryption is mandatory. If key material is missing or invalid, the engine FAILS CLOSED. Zero plaintext fallback.
4. **Zero Database Migrations**: Backup metadata is self-describing and stored in the filesystem (`storage_data/backups/`). This guarantees backup discovery and cold-start inspection even if the primary database is completely offline or uninitialized.
5. **Inviolable Retention Safety Invariant**: Never delete the last valid backup. If $\text{count}(\text{valid\_backups}) \le 1$, pruning is aborted.

---

## Decision Drivers

1. **Constitutional Invariant**: "Engines are infrastructure. They never contain business rules." (AGENTS.md Art. 6)
2. **Separation of Concerns**: Backup Engine generates and verifies artifacts; Recovery Engine consumes and restores artifacts.
3. **Crash Safety**: Backups assemble in `.tmp` workspaces, fsync, and self-verify before atomic rename (`os.replace`).
4. **Security & Sandbox Isolation**: All backup storage and retention operations are strictly contained within `storage_data/backups/` using `PathSandboxValidator`.
5. **Fail-Closed Cryptography**: Encryption uses `LocalCrypto` (AES-256-GCM). Key failure results in immediate abortion with `BackupEncryptionError`.
6. **Decoupled Observability**: Diagnostics conform to `IEngineDiagnostics`; lifecycle events emit asynchronously without blocking callers.

---

## Decision Outcome

Chosen Option: Implement `BackupEngine` as an infrastructure engine extending `BaseEngine`, `IBackupEngine`, and `IEngineDiagnostics`.

### Architectural Details

1. **Mission & Lifecycle**:
   - `BACKUP → CAPTURE → PACKAGE → PROTECT → VALIDATE → RETAIN → INDEX → REPORT`.
   - Extends `BaseEngine` lifecycle: `UNINITIALIZED → INITIALIZING → READY → RUNNING → STOPPING → STOPPED`.
   - Owned background scheduler loop executing automated periodic backups.

2. **Artifact Container (`.kortex-backup`)**:
   - Standalone ZIP container (`zipfile.ZIP_DEFLATED`, level 6) with deterministic date normalization (`2026-01-01`).
   - Standard internal layout: `manifest.json`, `checksums.json`, `database/kortex_snapshot.db`, `storage/files/...`, `storage/objects/...`.
   - Sidecar filesystem index: `{backup_id}.meta.json` containing top-level status, size, SHA-256 digest, and schema revision.

3. **Database Consistency**:
   - SQLite online backup executed asynchronously via `asyncio.to_thread` with 100-page increments.
   - Post-capture SQLite `PRAGMA integrity_check;` validation.
   - Discovers and embeds Alembic schema version (`alembic_version`).

4. **Storage & Blob Capture**:
   - Recursively captures regular files from `storage_data/files` and `storage_data/objects`.
   - Explicitly excludes `backups/`, `.tmp`, and `.cache` directories.
   - Sandboxed via `PathSandboxValidator` to block traversal and symlink escapes.

5. **Retention Engine**:
   - Evaluates composite policies (count, age, size).
   - Inviolable safety invariant: $\text{count}(\text{valid\_backups}) \le 1 \implies \text{ABORT}$.
   - Protects active and newly created backups from concurrent deletion.

6. **Security & RBAC**:
   - 6 registered capabilities: `kortex.backup.create`, `.list`, `.get`, `.verify`, `.delete`, `.diagnostics.get`.
   - Permissions: `system:backup:read`, `system:backup:manage`.
   - Authenticated, execution-context-aware, and classified as `INTERNAL`.

---

## Consequences

### Positive
- Authoritative, tamper-evident, encrypted, and verifiable full-instance backups.
- Non-blocking online database snapshots without write lock contention.
- Zero database migrations; resilient cold-start disaster recovery indexing.
- Clean architectural isolation preserving the future Recovery Engine boundary.

### Negative / Trade-offs
- Envelope encryption requires adequate memory buffer for full artifact cipher blocks.
- Preflight disk space requires at least $2\times \text{payload} + 100\text{ MB}$ free space.
