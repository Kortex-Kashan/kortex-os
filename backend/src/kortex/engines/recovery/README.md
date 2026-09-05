# KORTEX Recovery Engine

Phase 7 — Production Hardening — Recovery Engine

The Recovery Engine is the authoritative production-hardening infrastructure subsystem responsible for discovering, validating, staging, quiescing, swapping, reconnecting, verifying, and reporting durable, consistent, and cryptographically authenticated restorations from `.kortex-backup` artifacts into a live KORTEX OS instance.

---

## Mission

```text
DISCOVER → PRECHECK → CHECKPOINT → VALIDATE → STAGE → QUIESCE → SWAP → RECONNECT → VERIFY → REPORT
```

The Recovery Engine consumes tamper-evident `.kortex-backup` archives, creates a durable pre-recovery safety checkpoint of live state, stages and validates artifacts in isolation, and safely swaps live database and storage subtrees with full write-ahead journaling and automated reverse-swap rollback.

---

## Architectural Boundaries

### Hard Boundary with Backup
- Backup **creates and packages** backup artifacts.
- Recovery **consumes and restores** backup artifacts.
- Recovery does not invent backup formats or duplicate capture logic.
- Recovery invokes `BackupEngine.create_backup()` to establish mandatory pre-recovery safety checkpoints.

### Storage Boundary
- Recovery operates against the repository's verified Storage Engine topology:
  - `storage_data/documents/`: Restored from `storage/documents/`.
  - `storage_data/buckets/`: Restored from `storage/buckets/`.
  - `storage_data/metadata/`: Restored from `storage/metadata/`.
  - Strictly excluded: `storage_data/backups/`, `.cache/`, `.tmp/`, `.recovery/`, and `.recovery_staging/`.
- Directory swaps on Windows handle file handle locks with copy/file-level fallback.

### Database & Migration Boundary
- **Zero Database Migrations for Recovery Tracking**: All recovery operations, state machines, and journals are persisted at `storage_data/.recovery/journal.json`.
- **Deterministic Schema Compatibility**: Arbitrary caller-controlled forward migration bypasses are strictly rejected. Forward migrations are executed strictly in isolated staging against `staged_db.db` via Alembic and verified prior to any live swap. Live databases are NEVER directly migrated.

### Cryptographic Boundary
- Consumes AES-256-GCM sealed envelopes (`Nonce || Ciphertext || Tag`) matching `BackupCryptoManager`.
- Fails closed on any tag mismatch, corrupted payload, or missing key.
- Zero plaintext fallback.

---

## Core Components

| Component | Module | Responsibility |
|---|---|---|
| `RecoveryEngine` | `engine.py` | Central facade coordinating lifecycle, 26-step pipeline, and capabilities. |
| `RecoveryCryptoManager` | `crypto.py` | AES-256-GCM envelope decryption and SHA-256 integrity verification. |
| `RecoveryStagingManager` | `staging.py` | Sandboxed staging workspace, traversal defense, and capacity preflight. |
| `RecoveryJournalManager` | `journal.py` | Durable write-ahead crash-recovery journal with atomic replacement and fsync. |
| `RecoveryQuiescenceManager`| `quiescence.py` | Maintenance lock, workload drain, and database connection pool disposal. |
| `DatabaseRestorer` | `database_restorer.py`| SQLite snapshot validation, staged migration, and atomic file swap. |
| `StorageRestorer` | `storage_restorer.py` | Storage subtree replacement, referential consistency checks, and reverse swap. |
| `RecoveryValidator` | `validator.py` | Multi-tier artifact, envelope, checksum, and preflight verification. |
| `RecoveryEventPublisher` | `events.py` | Non-blocking lifecycle event emission to Kernel event bus. |
| `RecoveryDiagnosticsAdapter`| `diagnostics.py` | Conforms to standardized `IEngineDiagnostics` protocol. |

---

## Capability Surface

| Capability Name | Permissions | Classification | Description |
|---|---|---|---|
| `kortex.recovery.create` | `system:recovery:manage` | `INTERNAL` | Execute staged, journaled live recovery from backup. |
| `kortex.recovery.list` | `system:recovery:read` | `INTERNAL` | List recovery history and active status. |
| `kortex.recovery.get` | `system:recovery:read` | `INTERNAL` | Retrieve detailed status and journal metadata. |
| `kortex.recovery.verify` | `system:recovery:manage` | `INTERNAL` | Non-destructive preflight validation and capacity check. |
| `kortex.recovery.delete` | `system:recovery:manage` | `INTERNAL` | Clean completed journal or cancel pre-swap recovery. |
| `kortex.recovery.diagnostics.get` | `system:recovery:read` | `INTERNAL` | Retrieve operational telemetry and health metrics. |

---

## Lifecycle Events (Canonical 12-Event Contract)

| # | Event Topic | Lifecycle Phase / Boundary |
|---|---|---|
| 1 | `kortex.recovery.requested` | Request received and validated; prior to staging |
| 2 | `kortex.recovery.precheck.passed` | Envelope and disk preflight verification passed |
| 3 | `kortex.recovery.safety_checkpoint.created` | Mandatory safety backup captured and verified |
| 4 | `kortex.recovery.validated` | Backup archive extraction and checksum verification passed |
| 5 | `kortex.recovery.staged` | Staged DB and storage consistency verification passed |
| 6 | `kortex.recovery.quiesced` | Workload drained and database connections disconnected |
| 7 | `kortex.recovery.swapped` | Destructive database and storage file swaps completed |
| 8 | `kortex.recovery.verified` | Post-swap SQLite and storage referential verification passed |
| 9 | `kortex.recovery.completed` | Recovery successfully completed; maintenance lock released |
| 10 | `kortex.recovery.failed` | Unrecoverable error occurred; pre-mutation failure |
| 11 | `kortex.recovery.rolled_back` | Post-swap failure detected; reverse-swap successfully restored |
| 12 | `kortex.recovery.operator_intervention_required` | Fatal failure during rollback; system halted in maintenance |
