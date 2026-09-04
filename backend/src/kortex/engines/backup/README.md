# KORTEX Backup Engine

Phase 7 — Production Hardening — Backup Engine

The Backup Engine is a core production-hardening infrastructure subsystem responsible for capturing, packaging, protecting, validating, retaining, indexing, and reporting durable, consistent, and encrypted point-in-time operational snapshots of KORTEX OS.

---

## Mission

```text
BACKUP → CAPTURE → PACKAGE → PROTECT → VALIDATE → RETAIN → INDEX → REPORT
```

The Backup Engine creates tamper-evident, self-describing `.kortex-backup` archives representing an authoritative snapshot of operational state (SQLite relational data, sandboxed files, and object blobs).

---

## Architectural Boundaries

### Hard Boundary with Recovery
- Backup **captures and validates** backup artifacts.
- Backup **never restores, rolls back, or mutates** live state.
- Restoration belongs exclusively to the future Recovery Engine.
- Zero runtime dependencies on Recovery.

### Storage Boundary
- Backup consumes the existing Storage Engine abstractions and filesystems.
- Backup does not compete with or reinvent persistence.
- Relational database state is captured via native SQLite online backup APIs (`sqlite3.Connection.backup` executed asynchronously in worker threads), guaranteeing page-level transaction consistency without blocking the live server.

### Migration Boundary
- **Zero Database Migrations**: Backup artifacts and their sidecar `.meta.json` records are stored in the filesystem (`storage_data/backups/`).
- This ensures discovery and cold-start inspection work even when the primary database is completely offline or uninitialized.

### Cryptographic Boundary
- Encryption is **REQUIRED BY DEFAULT** via AES-256-GCM authenticated encryption using `LocalCrypto`.
- If key material is missing or invalid, the engine **FAILS CLOSED**.
- There is **NO PLAINTEXT FALLBACK**.

---

## Core Components

| Component | Module | Responsibility |
|-----------|--------|----------------|
| `BackupEngine` | `engine.py` | Main facade coordinating lifecycle, background scheduler, and capability dispatch. |
| `BackupCryptoManager` | `crypto.py` | AES-256-GCM envelope encryption, decryption, and SHA-256 integrity verification. |
| `DatabaseSnapshotCapture` | `capture.py` | Asynchronous SQLite online backup in 100-page increments with integrity checks. |
| `StoragePayloadCapture` | `capture.py` | Sandboxed scanner and streamer for file and object blobs from storage data. |
| `BackupPackager` | `packager.py` | ZIP container assembly, deterministic timestamp normalization, and atomic rename. |
| `BackupRepository` | `repository.py` | Filesystem repository enforcing path sandboxing and atomic metadata persistence. |
| `BackupVerifier` | `verifier.py` | Structural, cryptographic, checksum, and database verification for `.kortex-backup` files. |
| `RetentionEngine` | `retention.py` | Count, age, and size pruning enforcing the invariant: **NEVER DELETE THE LAST VALID BACKUP**. |
| `BackupEventPublisher` | `events.py` | Non-blocking lifecycle event publishing to the Kernel event engine. |
| `BackupDiagnosticsAdapter` | `diagnostics.py` | Standardized self-observability conforming to `IEngineDiagnostics`. |

---

## Capability Surface

All capabilities are authenticated, context-aware, and classified as `INTERNAL`:

| Capability | Permission | Purpose |
|------------|------------|---------|
| `kortex.backup.create` | `system:backup:manage` | Initiate and finalize an atomic full-instance operational backup. |
| `kortex.backup.list` | `system:backup:read` | List discovered backups sorted chronologically descending. |
| `kortex.backup.get` | `system:backup:read` | Retrieve metadata and manifest for a specific backup. |
| `kortex.backup.verify` | `system:backup:read` | Verify integrity, checksums, and envelope authentication of a backup. |
| `kortex.backup.delete` | `system:backup:manage` | Delete an artifact and its sidecar metadata. |
| `kortex.backup.diagnostics.get` | `system:backup:read` | Retrieve technical self-diagnostics for the engine. |

---

## Safety Invariants

1. **Inviolable Retention Safety**: $\text{count}(\text{valid\_backups}) \le 1 \implies \text{ABORT}$. The engine will never delete the sole surviving valid backup.
2. **Crash-Safe Assembly**: Archives are assembled in `.tmp` workspaces, fsynced, and self-verified before atomic rename (`os.replace`).
3. **Fail-Closed Cryptography**: If `KORTEX_BACKUP_KEY` or `KORTEX_MASTER_KEY` is not resolved, creation is aborted immediately with `BackupEncryptionError`.
4. **Path Containment**: All artifact and retention operations are strictly contained within `storage_data/backups/` via `PathSandboxValidator`.
