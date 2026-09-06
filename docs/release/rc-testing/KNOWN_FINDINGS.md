# Known Findings — discovered while building the RC test environment

Read this before filing a defect. These were found while preparing and validating this environment. **None has been fixed** — per RC process, findings are captured, reproduced, classified, and left for Chief Architect review.

---

## DEFECT-001 — Backup Engine cannot resolve a `0x`-prefixed master key; backup fails closed on Windows desktop

| Field | Value |
|---|---|
| **ID** | DEFECT-001 |
| **Severity** | **P1 — High** |
| **Classification** | **PRE-EXISTING** (not a regression from any recent commit) |
| **Type** | Correctness / configuration-contract mismatch |
| **Environment** | Windows desktop RC build; any deployment where `KORTEX_MASTER_KEY` carries the `0x` prefix |
| **Status** | **RESOLVED** — commit `<see git log for the exact SHA of "fix: align desktop backup key format with backup crypto">` |

### Resolution

**Root cause confirmed**: `BackupCryptoManager._resolve_key_from_env` (`backend/src/kortex/engines/backup/crypto.py`) was the *only* consumer of `KORTEX_MASTER_KEY`/`KORTEX_BACKUP_KEY` on the platform that did not implement the `0x`-prefixed hex representation. That representation is not a Backup-specific or desktop-specific quirk — it is the platform's own established, triply-documented canonical format:
- `kernel_bootstrap.py::_resolve_key` already decodes it identically.
- `docker/entrypoint.sh::require_key` already validates it at the container boundary.
- `docker/.env.example` already documents it explicitly: *"either a '0x'-prefixed 64-hex-character string or a UTF-8 value of at least 32 bytes."*

The Windows desktop sidecar (`secure_keys.rs::hex_encode`, `format!("0x{}", ...)`) was producing exactly this already-canonical form. **The producer was correct; the one consumer was not.**

**Canonical representation**: unchanged — `0x` + 64 lowercase hex characters, decoding to exactly 32 bytes. No new format was introduced.

**Correction**: added the missing `0x`-prefixed case to `_resolve_key_from_env`, mirroring `kernel_bootstrap.py`'s own decoding exactly. The two pre-existing formats (bare 64-char hex, base64) are untouched and still resolve identically. No change to `secure_keys.rs`, Docker's key model, AES-256-GCM behavior, or any accepted architecture.

**Backward compatibility**: no migration was needed. Because this exact defect made backup fail closed on every prior desktop attempt, **no encrypted backup was ever successfully produced with a `0x`-prefixed key** — there is nothing pre-existing to migrate or silently invalidate.

**Regression tests added** (`backend/tests/unit/test_backup_crypto.py`): acceptance of the exact desktop-generated format; acceptance via both `KORTEX_MASTER_KEY` and `KORTEX_BACKUP_KEY`; confirmation the two pre-existing formats are undisturbed; fail-closed rejection of malformed near-matches (bad hex, wrong decoded length); confirmation no failure path leaks key material; and a real AES-256-GCM encrypt/decrypt round trip using the exact desktop-generated key representation.

**Real desktop verification**: executed against a live backend (real Alembic-migrated database, real `kortex.api.main:app`), with `KORTEX_MASTER_KEY` set to a freshly generated `0x`-prefixed key exactly as `secure_keys.rs` would produce, and **`KORTEX_BACKUP_KEY` deliberately unset — no workaround**:

| Step | Result |
|---|---|
| `kortex.backup.create` | **200** — real backup ID returned |
| `kortex.backup.verify` | **200** — `is_valid`, `checksum_verified`, `encryption_verified`, `schema_compatible` all present |

**Recovery/Update prerequisite**: Recovery and Update both take their mandatory pre-mutation checkpoint via the exact same `BackupEngine.create_backup()` path just verified above, so the dependency each has on Backup is satisfied. Their own capability endpoints (`kortex.recovery.verify`, `kortex.update.check`) are not reachable through the current desktop API surface for a **separate, pre-existing reason unrelated to this defect**: `kernel_bootstrap.py` does not register `RecoveryEngine`/`UpdateEngine` at all (confirmed directly — neither appears anywhere in that file), a gap already tracked as Owner Decision OD-1 in the Docker Production Builds reconciliation record, predating this fix and explicitly out of its authorized scope. The full Recovery (62 tests) and Update (85 tests) suites were re-run after this fix with zero regressions.

**The bare-hex `KORTEX_BACKUP_KEY` workaround previously documented here is no longer required or referenced anywhere in this test environment.**

### Summary

`BackupCryptoManager._resolve_key_from_env` (`backend/src/kortex/engines/backup/crypto.py`) does not accept the `0x`-prefixed 64-hex key format that the Windows desktop generates and injects. The Backup Engine therefore finds no valid key and fails closed on every operation, making backup non-functional in the primary RC desktop topology unless the operator separately supplies `KORTEX_BACKUP_KEY` in bare-hex form.

### Root cause

The platform has two key parsers that disagree on format:

- `kernel_bootstrap.py::_resolve_key` — **accepts** the `0x`-prefixed form.
- `apps/desktop/src-tauri/src/secure_keys.rs` — **generates** the `0x`-prefixed form: `format!("0x{}", hex_encode(&bytes))`, and injects it as `KORTEX_MASTER_KEY` for the sidecar.
- `backup/crypto.py::_resolve_key_from_env` — accepts only: exactly 64 hex chars (**no prefix**), base64 decoding to 32 bytes, or exactly 32 raw UTF-8 bytes. A 66-character `0x`-prefixed value matches none of these.

### Reproduction

Directly, against the real parser logic:

```
bare 64-hex (openssl rand -hex 32):        HEX-OK
0x-prefixed 66-char (desktop secure_keys): UNRESOLVED
32-char utf8:                              RAW-OK
```

End-to-end, on a real backend with a correctly-formed `0x`-prefixed `KORTEX_MASTER_KEY` set:

```
Subsystem 'backup' health check error: [BackupEncryptionError] Backup encryption is
required by default, but no valid 32-byte key was provided or found in environment
('KORTEX_BACKUP_KEY' or 'KORTEX_MASTER_KEY'). Fail-closed policy enforced; backup aborted.
```

Reproduced independently twice: during Desktop Installer lifecycle testing, and again while validating this test environment.

### Impact

- Backup — an accepted Phase 7 engine — is **non-functional on the Windows desktop RC build** out of the box.
- Recovery and Update both take a mandatory backup checkpoint before mutating anything, so both are also blocked on the desktop by extension.
- Sentinel/Monitoring correctly report the `backup` subsystem as unhealthy, so it is visible, not silent.

### Mitigating factors (why P1, not P0)

- It **fails closed**. No unencrypted, corrupt, or silently-skipped backup is ever produced — the safe direction.
- The application boots, runs, and is otherwise fully usable; `/health` stays healthy.
- There is an operator workaround: set `KORTEX_BACKUP_KEY` to a bare 64-hex value.
- Docker is unaffected when `.env` uses the documented `openssl rand -hex 32` output (bare hex).

### Workaround for testing

To exercise backup tests on the desktop, set a bare-hex `KORTEX_BACKUP_KEY` in the environment that launches the app:

```powershell
$env:KORTEX_BACKUP_KEY = (openssl rand -hex 32)   # 64 hex chars, NO 0x prefix
Start-Process -FilePath "$env:LOCALAPPDATA\KORTEX Desktop\kortex-desktop.exe" ...
```

**Record clearly in your results whether backup tests were run with this workaround.** Backup passing *with* the workaround does not mean DEFECT-001 is resolved.

### Not fixed here

Deliberately. Which parser is authoritative — and therefore whether the fix belongs in `crypto.py`, in `secure_keys.rs`, or in a shared key-parsing helper — is an architectural decision for the Chief Architect, not a test-environment decision.

---

## OBSERVATION-001 — `backend/README.md` states PostgreSQL; RC topologies are SQLite-only

| Field | Value |
|---|---|
| **Severity** | **P4 — Documentation** |
| **Classification** | PRE-EXISTING / DOCUMENTATION |

`backend/README.md` lists "**Database**: PostgreSQL (via asyncpg)". Both accepted RC topologies are SQLite-only, and the reconciliation record states PostgreSQL is aspirational documentation with zero PostgreSQL tests in the repository. Harmless to the RC, but it will mislead a first-time tester. Not corrected here — documentation change, out of this task's scope.

---

## OBSERVATION-002 — Security models must be imported after `kortex.core`

| Field | Value |
|---|---|
| **Severity** | **P4 — Minor / developer-experience** |
| **Classification** | PRE-EXISTING |

Importing `kortex.engines.security.models` as the first KORTEX import raises `ImportError: cannot import name 'AccessDecision' ... (most likely due to a circular import)`. Importing `kortex.core` first resolves it. The repository's own tests never hit this because they import the app/kernel first. `seed_rc_dataset.py` documents and works around it. No product impact — the running application is unaffected. Not corrected here.
