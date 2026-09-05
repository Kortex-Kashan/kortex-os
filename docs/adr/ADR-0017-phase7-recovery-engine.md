# ADR-0017: Phase 7 — Production Hardening — Recovery Engine

- **Status**: IMPLEMENTED — AWAITING REVIEW
- **Date**: 2026-09-05
- **Deciders**: Chief Architect (KASHAN), Antigravity (Implementation Engineer)
- **Target Component**: Recovery Engine (`kortex.engines.recovery`)

---

## Context and Problem Statement

KORTEX OS is an AI-powered local-first business operating system requiring enterprise-grade disaster recovery resilience. While Backup Engine (ADR-0016) established automated, encrypted, and verifiable point-in-time state capture into `.kortex-backup` containers, KORTEX required a dedicated engine to safely, deterministically, and resiliently restore accepted backup artifacts into the running installation.

Restoring system state is fundamentally destructive. Replacing a live relational database and live file storage involves inherent risks: process crashes, power interruptions, disk exhaustion, locked file handles on Windows, schema version mismatches, and data corruption.

The challenge was to design and implement the Recovery Engine adhering strictly to KORTEX Architecture v1.0.0 and the AI Engineering Constitution (`AGENTS.md`):
1. **The Core Recovery Boundary**: Recovery has exactly one mission: restore an accepted KORTEX Backup artifact into the current installation. Recovery consumes `.kortex-backup` artifacts; it never creates backup formats, never replaces Backup Engine, never acts as Sentinel or Monitoring, and never claims cross-component global ACID atomicity over independent SQLite and filesystem storage.
2. **Mandatory Pre-Recovery Safety Checkpoint**: Before ANY destructive mutation occurs, Recovery must invoke `BackupEngine` to create and fully verify a durable safety checkpoint. If checkpoint creation fails, recovery aborts immediately with zero live mutation.
3. **Durable Write-Ahead Journaling**: Destructive operations must be journaled to disk (`storage_data/.recovery/journal.json`) via atomic write-and-fsync prior to execution. An interrupted recovery must be deterministically detectable on boot, enabling automated rollback or fail-closed lockdown (`FAILED_NEEDS_OPERATOR`).
4. **Staged-Only Schema Migration**: Live databases must NEVER be directly migrated. Older compatible backups may only undergo Alembic forward migration inside isolated staging (`staged_db.db`). If staged migration or subsequent validation fails, live state remains completely untouched. Caller-controlled migration bypasses are strictly prohibited.
5. **Multi-Component Staged Replacement**: All state (database and managed storage subtrees `documents`, `buckets`, `metadata`) must be staged and validated before swapping. File swaps preserve `.rollback_<id>` snapshots to enable deterministic reverse-swap rollback.
6. **Zero Database Migrations**: Recovery introduces 0 Alembic database migrations and 0 persistent SQL tables. Recovery state is 100% filesystem-backed, ensuring operation even when the database is damaged or offline.

---

## Decision Drivers

1. **Constitutional Invariant**: "Engines are infrastructure. They never contain business rules." (AGENTS.md Art. 6)
2. **Separation of Concerns**: Backup Engine captures and verifies artifacts; Recovery Engine validates, stages, journals, and restores them.
3. **No Overclaimed Atomicity**: File systems and SQLite cannot form a single atomic distributed transaction. Crash resilience is achieved through write-ahead journaling, isolated staging, and fail-closed reverse-swap rollback.
4. **Windows Compatibility**: Handle Windows file locking semantics (`kortex_local.db` locked during active connections; directory handle locks) via explicit database pool disconnection and resilient directory swap fallbacks.
5. **Security & Sandbox Isolation**: Fail-closed execution context enforcement (`system:recovery:*` permissions, `INTERNAL`/`RESTRICTED` sensitivity). Path traversal, ZIP bombs, and symlink escapes are strictly blocked.
6. **Referential Integrity**: Staged database records must reconcile with staged storage files before any live swap is initiated.
7. **Cold-Start Resilience**: Filesystem journaling ensures boot-time recovery detection without database dependencies.

---

## Decision Outcome

Chosen Option: Implement `RecoveryEngine` as an infrastructure engine extending `BaseEngine`, `IRecoveryEngine`, and `IEngineDiagnostics`.

### Architectural Details

1. **Mission & Lifecycle**:
   - `DISCOVER → AUTHENTICATE → VALIDATE ARTIFACT → DECRYPT → STAGE → VALIDATE STAGING → CHECKPOINT → QUIESCE → SWAP → RECONNECT → VERIFY → COMPLETE`.
   - Extends `BaseEngine` lifecycle: `UNINITIALIZED → INITIALIZING → READY → RUNNING → STOPPING → STOPPED`.
   - On `initialize()`, sweeps for incomplete journal markers and triggers automated crash recovery or fail-closed quarantine (`FAILED_NEEDS_OPERATOR`).

2. **Durable Filesystem Journal (`journal.json`)**:
   - Maintained under `storage_data/.recovery/journal.json` using atomic replace (`os.replace`) with synchronous disk flush (`os.fsync`).
   - 16 discrete lifecycle phases tracking operation details, staging paths, rollback sources, checksums, and completion states.
   - Preserves completed operational history in `history.json` and quarantines corrupt journals in `quarantine/`.

3. **Pre-Recovery Safety Checkpoint**:
   - 10-condition verification via `BackupEngine.create_backup()`:
     1. Successful execution without errors.
     2. Output artifact exists and is non-empty.
     3. Metadata sidecar exists and parses cleanly.
     4. Cryptographic tag and envelope authenticate.
     5. Manifest verifies with required components.
     6. SHA-256 checksums verify across all contained files.
     7. Sidecar state is marked `COMPLETED`.
     8. Artifact is registered and discoverable.
     9. Artifact is marked protected against retention pruning.
     10. Assigned as authoritative rollback source in the recovery journal.

4. **Multi-Component Staged Replacement & Rollback**:
   - **Database Restoration**: Disconnects `DatabaseEngineManager` connection pool, validates SQLite `PRAGMA integrity_check;`, runs staged Alembic forward migration if applicable, backs up live DB to `kortex_local.db.rollback_<id>`, and swaps in the staged database.
   - **Storage Restoration**: Manages authoritative subtrees (`documents`, `buckets`, `metadata`) while strictly excluding `backups/`, `.cache/`, `.tmp/`, and `.recovery/`. Renames live directories to `.rollback_<id>` prior to swap.
   - **Reverse-Swap Rollback**: If verification or swap fails at any step, completes reverse-swap restoring `.rollback_<id>` files/directories, reconnects the database, verifies integrity, and transitions to `ROLLED_BACK`.

5. **Security & RBAC**:
   - 6 registered capabilities: `kortex.recovery.create`, `.list`, `.get`, `.verify`, `.delete`, `.diagnostics.get`.
   - Permissions: `system:recovery:manage`, `system:recovery:read`.
   - Authenticated, execution-context-aware, strictly isolated tenants, and classified as `INTERNAL`/`RESTRICTED`.

6. **Referential & Schema Validation**:
   - Cross-checks document records against document storage and bucket blob records against blob storage.
   - Validates Alembic schema versions against current codebase head; forbids unsupported newer backups or downgrades.

---

## Consequences

### Positive
- Production-grade disaster recovery with deterministic, crash-consistent restoration.
- Absolute protection against data loss via mandatory pre-recovery safety checkpoints and reverse-swap rollback.
- Zero database migrations or schema dependencies, enabling cold-start offline recovery.
- Strict isolation and verification in staging before any live mutation occurs.
- Complete Windows compatibility with connection pool draining and directory fallback handling.

### Negative / Trade-offs
- Resource preflight requires sufficient disk space for safety checkpoint + staging + rollback + live state ($2.5\times \text{payload} + 500\text{ MB}$ reserve).
- Destructive swap phase requires brief quiescence / write suspension while database connections drain.
