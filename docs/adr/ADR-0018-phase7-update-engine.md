# ADR-0018: Phase 7 — Production Hardening — Update Engine

- **Status**: IMPLEMENTED — AWAITING REVIEW
- **Date**: 2026-09-05
- **Deciders**: Chief Architect (KASHAN), Antigravity (Implementation Engineer)
- **Target Component**: Update Engine (`kortex.engines.update`)

---

## Context and Problem Statement

KORTEX OS is an AI-powered local-first business operating system requiring reliable, offline-capable, and enterprise-grade software update management. Updating a running application across desktop containers (Tauri + Python sidecar on Windows) and containerized servers carries critical operational risks: file-lock contention on Windows, power interruptions during file replacement, schema drift, corrupted downloads, and malicious update injection.

Following the formal completion of Sentinel (ADR-0014), Monitoring (ADR-0015), Backup (ADR-0016), and Recovery (ADR-0017), the Update Engine was required to orchestrate software updates while strictly preserving existing architectural boundaries.

The core engineering requirements established for Update Engine:
1. **Zero Database Migrations**: Update Engine introduces 0 new Alembic migrations and 0 persistent SQL tables. State coordination is 100% durable filesystem journaling (`storage_data/.update/journal.json`).
2. **Strict Authority Separation**:
   - `BackupEngine` remains the sole authority for creating point-in-time full-instance backups.
   - `RecoveryEngine` remains the sole authority for destructive database and storage restoration.
   - Update Engine owns update discovery, package verification, staging, forward schema migration orchestration, filesystem component swapping, and disaster recovery delegation.
3. **Mandatory Pre-Update Safety Checkpoint**: Before any destructive mutation occurs, Update Engine must invoke `BackupEngine` to create and verify a `FULL_INSTANCE` safety checkpoint. If checkpoint creation fails, update aborts immediately with zero live mutation.
4. **Recovery Delegation Contract**: The Recovery Engine model default `confirm_destructive_restore: bool = False` remains untouched. Update Engine explicitly supplies `confirm_destructive_restore=True` when delegating disaster restoration to Recovery.
5. **Filesystem Swap ≠ Runtime Activation**: Replacing `.py` files on disk does not automatically reload in-memory Python modules. The Update Engine explicitly distinguishes filesystem update completion from runtime activation, requiring a controlled backend restart before claiming that new runtime code is active.
6. **Live Alembic Migration Compatibility Model (Cases A–J)**: Live forward migrations are permitted under quiescence, but in-place live schema downgrades (`alembic downgrade`) are **STRICTLY FORBIDDEN**. If an update aborts after schema migration has touched the live database, database restoration is performed exclusively by restoring the pre-update SQLite backup snapshot via `RecoveryEngine`.
7. **No Global Atomicity Claim**: Multi-component software update is not globally atomic. It is staged, journaled, crash-consistent, and rollback-recoverable.
8. **Frozen 12-Event Contract**: Exactly 12 canonical events on namespace `kortex.update.*` with zero additions, removals, or aliases.

---

## Decision Drivers

1. **Constitutional Invariant**: "Engines are infrastructure. They never contain business rules." (AGENTS.md Art. 6)
2. **Cryptographic Authenticity**: Only packages signed with authoritative vendor Ed25519 keys matching compiled root keys (`COMPILED_VENDOR_UPDATE_KEYS`) may be staged or applied.
3. **Hostile Input Defenses**: Path traversal (`..`), absolute/UNC paths, symlinks, hardlinks, and ZIP bombs (10:1 expansion ratio, 500 MB compressed, 2 GB uncompressed) are rejected before disk extraction.
4. **Disk Space Preflighting**: Formula-based deterministic space check prior to download or staging: $(1.0 \times \text{Package}) + (1.5 \times \text{Extracted}) + (1.0 \times \text{DB}) + (1.0 \times \text{Backup}) + 500\text{ MB reserve}$.
5. **Cross-Engine Mutual Exclusion**: Prevents concurrent execution with active Backup or Recovery operations.

---

## Decision Outcome

Chosen Option: Implement `UpdateEngine` as an infrastructure engine extending `BaseEngine`, `IUpdateEngine`, and `IEngineDiagnostics`.

### Architectural Details

1. **Mission & Lifecycle**:
   - `CHECK → STAGE → CHECKPOINT → QUIESCE → MIGRATE → SWAP → VERIFY → REPORT`.
   - Extends `BaseEngine` lifecycle: `UNINITIALIZED → INITIALIZING → READY → STOPPED`.
   - On `initialize()`, sweeps for unresolved journal state and executes deterministic crash recovery.

2. **Durable Filesystem Journal (`journal.json`)**:
   - Maintained under `storage_data/.update/journal.json` using atomic replace (`write -> flush -> fsync -> os.replace`).
   - 14 discrete lifecycle phases tracking operation details, staging paths, rollback sources, checksums, and completion states.
   - Preserves completed operational history in `history.json` (bounded to 50 entries).

3. **Pre-Update Safety Checkpoint**:
   - Captured via `BackupEngine.create_backup(CreateBackupRequest(scope=BackupScope.FULL_INSTANCE))`.
   - Destructive mutation begins only after the checkpoint is accepted.

4. **3-Layer Rollback Hierarchy**:
   - **Layer 1 (Update-Local)**: Reverts swapped code files from `.rollback_<update_id>` copies and purges staging.
   - **Layer 2 (Recovery-Backed)**: Invokes `RecoveryEngine.create_recovery(CreateRecoveryRequest(backup_id, confirm_destructive_restore=True))` to restore database and storage state.
   - **Layer 3 (Operator Intervention)**: Halts fail-closed in `FAILED_NEEDS_OPERATOR` under active maintenance lock if automated recovery cannot complete safely.

5. **Canonical 6-Capability Surface**:
   - `kortex.update.check` (`system:update:read`)
   - `kortex.update.stage` (`system:update:manage`)
   - `kortex.update.apply` (`system:update:manage`)
   - `kortex.update.get` (`system:update:read`)
   - `kortex.update.cancel` (`system:update:manage`)
   - `kortex.update.diagnostics.get` (`system:update:read`)

6. **Frozen 12-Event Surface**:
   - `kortex.update.checked`
   - `kortex.update.manifest.verified`
   - `kortex.update.staged`
   - `kortex.update.safety_checkpoint.created`
   - `kortex.update.quiesced`
   - `kortex.update.migrated`
   - `kortex.update.applied`
   - `kortex.update.verified`
   - `kortex.update.completed`
   - `kortex.update.failed`
   - `kortex.update.rolled_back`
   - `kortex.update.operator_intervention_required`
