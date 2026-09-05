# KORTEX Update Engine

**Version**: 1.0.0  
**Phase**: Phase 7 — Production Hardening  
**Status**: Ready / Production Hardened  
**Package**: `kortex.engines.update`

---

## 1. Mission

The **Update Engine** is the authoritative infrastructure engine responsible for discovering, authenticating, verifying, staging, and safely coordinating the application of KORTEX OS software updates.

Operating across local-first Windows desktop environments (Tauri + Python sidecar) and containerized topologies, it enforces strict cryptographic verification, hostile archive defenses, disk space preflighting, pre-update safety checkpoints via `BackupEngine`, forward-only Alembic database migrations, write-ahead journaling, and automated recovery delegation to `RecoveryEngine` upon failure.

---

## 2. Inviolable Architectural Boundaries

1. **Zero Database Migrations for Update Engine**: The Update Engine introduces **0 new Alembic migrations** and **0 persistent SQL tables**. All state coordination is 100% durable filesystem journaling (`storage_data/.update/journal.json`).
2. **Pre-Update Safety Checkpoint**: No destructive update mutation occurs before a mandatory `FULL_INSTANCE` backup is captured and verified via `BackupEngine`.
3. **Strict Backup & Recovery Engine Authority**:
   - `BackupEngine` remains the sole authority for creating encrypted full-instance backups.
   - `RecoveryEngine` remains the sole authority for destructive database and storage restoration. Update Engine delegates to Recovery Engine with `confirm_destructive_restore=True`.
4. **Filesystem Swap ≠ Runtime Activation**: Replacing `.py` files on disk does not automatically reload in-memory Python modules. The Update Engine explicitly distinguishes filesystem update completion from runtime activation, requiring a controlled backend restart before claiming new code is active.
5. **Forward-Only Alembic Migrations**: In-place schema downgrades (`alembic downgrade`) are **STRICTLY FORBIDDEN**. Database rollback is performed exclusively by restoring the pre-update SQLite backup snapshot via `RecoveryEngine`.
6. **No Global Atomicity Claim**: Multi-component software update is not globally atomic. It is staged, journaled, crash-consistent, and rollback-recoverable.

---

## 3. Capabilities (Exact 6)

| Capability | Type | Required Permission | Description |
|---|---|---|---|
| `kortex.update.check` | Query | `system:update:read` | Check for available update manifests and verify Ed25519 signatures. |
| `kortex.update.stage` | Mutation | `system:update:manage` | Download, verify, and unpack update archive into isolated staging workspace. |
| `kortex.update.apply` | Mutation | `system:update:manage` | Execute checkpoint, quiescence, forward migration, file swap, and verification. |
| `kortex.update.get` | Query | `system:update:read` | Retrieve active update state, journal details, and history. |
| `kortex.update.cancel` | Mutation | `system:update:manage` | Abort unapplied staged update, purge workspace, and return to IDLE. |
| `kortex.update.diagnostics.get` | Query | `system:update:read` | Retrieve operational telemetry conforming to `IEngineDiagnostics`. |

All capabilities enforce `CapabilityExecutionContext`, fail-closed authentication, and `INTERNAL` security classification.

---

## 4. Canonical Events Contract (Exact 12)

All events belong to namespace `kortex.update.*`:

1. `kortex.update.checked`: Candidate update manifest evaluated.
2. `kortex.update.manifest.verified`: Ed25519 digital signature and authenticity verified.
3. `kortex.update.staged`: Verified package extracted into isolated staging.
4. `kortex.update.safety_checkpoint.created`: Pre-update full-instance backup verified.
5. `kortex.update.quiesced`: Maintenance lock acquired, database connections drained.
6. `kortex.update.migrated`: Forward Alembic migration completed.
7. `kortex.update.applied`: Files swapped into live paths; rollback copies preserved.
8. `kortex.update.verified`: Post-swap filesystem verification passed.
9. `kortex.update.completed`: Update transaction committed.
10. `kortex.update.failed`: Update aborted pre-mutation; live state clean.
11. `kortex.update.rolled_back`: System state restored via Recovery Engine.
12. `kortex.update.operator_intervention_required`: Catastrophic failure; halted fail-closed.

---

## 5. Rollback Hierarchy

```
Layer 1: Update-Local Rollback
  - Reverts swapped files from .rollback_<id> copies in reverse order.
  - Purges staging directory.

Layer 2: Recovery-Backed Restoration
  - Triggered if live database was migrated or file swap failed unrecoverably.
  - Invokes RecoveryEngine.create_recovery(CreateRecoveryRequest(backup_id, confirm_destructive_restore=True)).

Layer 3: Operator Intervention
  - Triggered if automated rollback fails or journal is corrupted.
  - System halts fail-closed in FAILED_NEEDS_OPERATOR under active maintenance lock.
```

---

## 6. Directory Structure

```
storage_data/.update/
  ├── journal.json              <-- Authoritative write-ahead journal
  ├── maintenance.lock          <-- Process maintenance lockfile
  ├── history.json              <-- Bounded informational history (50 entries)
  └── staging/
      └── <update_id>/          <-- Isolated extraction workspace
```
