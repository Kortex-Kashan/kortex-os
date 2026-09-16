# Known Findings — discovered while building the RC test environment

Read this before filing a defect. These were found while preparing and validating this environment. Per RC process, findings are captured, reproduced, classified, and left for Chief Architect review before any fix — each entry's own `Status` field is authoritative for whether it has since been resolved (DEFECT-001, DEFECT-002, and DEFECT-003 are, as of this reconciliation pass, all **RESOLVED**; see each entry below for its own Resolution record).

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

---

## DEFECT-002 — Workflow durability restart recovery optimistic-lock race condition

| Field | Value |
|---|---|
| **ID** | DEFECT-002 |
| **Severity** | **P2 — Medium** (deterministic CI blocker for backend full suite) |
| **Classification** | **PRE-EXISTING BASELINE DEFECT** (not an M1 regression) |
| **Type** | Concurrency / Optimistic-Lock Race / State Machine Recovery Conflict |
| **Exact Test** | `tests/unit/test_workflow_durability.py::test_restart_recovery_ready_and_approved_workflows` |
| **Exact Failure** | `WorkflowStateConflictError` followed by assertion failure `assert WorkflowState.RUNNING == WorkflowState.COMPLETED` |
| **Baseline Commit** | `63460cb792222ab2796615afbb9498dd6f389ed` (reproduced on M1 parent baseline) |
| **M1 Commit** | `79040e9270cdf87b0bc616b6f84f2cd8679d0f72` (also reproduces on current M1 commit) |
| **Scope Ownership** | Originally attributed to Workflow Engine (`kortex.engines.workflow`) / Durability & Persistence layer; root-caused on resolution to `DatabaseEngineManager` (`backend/src/kortex/core/db.py`) |
| **Status** | **RESOLVED** — commit `6cc223be7ebcfe17269408791ae532b6dd5d039e` (`fix(storage): serialize concurrent SQLite sessions`) |

### Resolution

**Root cause confirmed**: the defect was one layer lower than originally scoped. SQLite allows only one writer transaction at a time, but `DatabaseEngineManager` never enforced that at the application level, so two concurrent `get_session()` callers could have their transactions interleave against the shared connection — making one session's own just-committed write not yet visible to a different session's read-modify-write cycle a moment later. The Workflow Engine's restart-recovery path was the reachable symptom (an optimistic-lock version conflict leaving an instance `RUNNING` instead of `COMPLETED`), not the fault's origin.

**Correction**: `DatabaseEngineManager` now serializes the entire SQLite session lifetime behind a per-event-loop-bound `asyncio.Lock`, leaving PostgreSQL's genuine per-connection concurrency untouched. The lock is lazily rebuilt if the running event loop changes, since `asyncio.Lock` binds to whichever loop first acquires it and a `DatabaseEngineManager` can legitimately outlive one loop (e.g. an application restart that reuses the same manager).

**Adjacent defect surfaced and fixed in the same commit**: a pre-existing test-design race in `test_workflow_definition_version_safety.py` — the test mutated a `WorkflowInstance` object immediately after `start_workflow()` returned, racing that same call's own unawaited background execution task — was newly exposed once this defect stopped masking it, and was fixed alongside it.

**Regression coverage added**: a storage-layer concurrent-write stress test, a Workflow Engine restart-recovery stress test, and two tests documenting the lock's re-entrancy boundary (sequential acquisition on one task never hangs; genuine nested acquisition on one task times out by design rather than deadlocking silently).

**Governance**: per the original acceptance record, this was correctly never treated as an M1 regression and M1 acceptance was not withheld pending its resolution. It is resolved as a dedicated, separate fix, consistent with the original deferral.

### Summary

During execution of the backend test suite, `tests/unit/test_workflow_durability.py::test_restart_recovery_ready_and_approved_workflows` fails when attempting restart recovery of ready and approved workflows. An optimistic-lock concurrency conflict (`WorkflowStateConflictError`) occurs in the workflow engine durability recovery path, leaving the instance in state `RUNNING` rather than transitioning cleanly to `COMPLETED`.

### Observed Behavior

```
WorkflowStateConflictError: Workflow instance <id> version conflict: expected version X, found version Y
...
assert engine.get_instance(inst_ready.id).state == WorkflowState.COMPLETED
where WorkflowState.RUNNING = <WorkflowInstance ...>.state
```

### Root Cause / Race Analysis

An optimistic-locking race condition exists in the workflow durability recovery mechanism (`kortex.engines.workflow.persistence` and `WorkflowEngine.recover_workflows_on_boot` / execution state transitions). When ready and approved workflows are swept during engine restart recovery, concurrent or interleaved updates to workflow instance state cause a version mismatch / optimistic lock conflict (`WorkflowStateConflictError`). As a consequence, the workflow instance does not complete its lifecycle transition, and its persisted state remains `RUNNING` instead of reaching `COMPLETED`.

### Baseline Reproduction Evidence

This defect is formally classified as a **PRE-EXISTING BASELINE DEFECT**:
1. **M1 Parent Baseline (`63460cb792222ab2796615afbb9498dd6f389ed`)**: The exact failure reproduces deterministically on the baseline commit prior to any Integration Hub M1 changes.
2. **Current M1 Commit (`79040e9270cdf87b0bc616b6f84f2cd8679d0f72`)**: The failure reproduces identically on GitHub Actions CI and local testing.
3. **Not an M1 Regression**: Integration Hub M1 scope was strictly additive (`connector-mcp` driver, MCP client transport, tenant-scoped capability projection metadata in `RegistryEngine`, and desktop Connections UI / Palette). Zero lines of code in `WorkflowEngine`, `StorageEngine`, `persistence.py`, `executor.py`, or `test_workflow_durability.py` were modified.
4. **Scope Ownership**: Owned exclusively by the Workflow Engine infrastructure team. It is outside the architectural boundary of Integration Hub M1.

### Governance & Acceptance Rules

Per Chief Architect decision:
- **Do NOT fix this defect under Integration Hub M1.**
- This defect was **DEFERRED** for a separate, dedicated workflow-engine durability investigation.
- It was correctly **NOT** silently treated as an M1 failure.
- It was correctly **NOT** silently treated as resolved prior to an actual fix landing.
- (Historical, at M1 time) It remained an active, known CI failure until separately corrected and verified.
- Integration Hub M1 milestone status at the time was formally: **M1 — ACCEPTED / COMPLETE with PRE-EXISTING CI DEFECT DEFERRED**.
- (Historical, at M1 time) Overall repository CI status: NOT GREEN due to this pre-existing workflow durability defect (Desktop CI: GREEN, M1 tests: PASS, Backend full suite: 1 pre-existing failure).

**Update — resolved**: the separate, dedicated investigation this defect was deferred for concluded with commit `6cc223be7ebcfe17269408791ae532b6dd5d039e` (see Resolution above). Integration Hub M1 status is now **M1 — ACCEPTED / COMPLETE (PRE-EXISTING CI DEFECT RESOLVED)**. This defect is no longer an active CI failure.

---

## DEFECT-003 — Hardcoded, now-expired update manifest fixture fails 3 tests in `test_update_integration.py`

| Field | Value |
|---|---|
| **ID** | DEFECT-003 |
| **Severity** | **P3 — Low** (test-fixture data staleness, not an engine defect) |
| **Classification** | **PRE-EXISTING BASELINE DEFECT** (not an Integration Hub M2 regression) |
| **Type** | Test-fixture data staleness (hardcoded expiry date now in the past) |
| **Distinct from** | `DEFECT-002` (unrelated engine/component: Update Engine test fixtures vs. Workflow Engine durability recovery) |
| **Exact Tests** | `tests/integration/test_update_integration.py::test_end_to_end_successful_update_lifecycle`, `tests/integration/test_update_integration.py::test_recovery_delegation_on_post_mutation_failure`, `tests/integration/test_update_integration.py::test_checkpoint_failure_aborts_before_any_destructive_mutation` |
| **Exact Failure** | `kortex.engines.update.exceptions.UpdateManifestError: Update manifest 'mf-0.2.0' expired at 2026-09-12T00:00:00Z (current: <real wall-clock time>)` |
| **Baseline Commit** | `f5d57cb65b882bd6fe87f8527474804320c0a624` (reproduced on the unmodified `main` HEAD immediately prior to Integration Hub M2, via `git stash`) |
| **Scope Ownership** | Update Engine (`kortex.engines.update`) test suite / fixture maintenance |
| **Status** | **RESOLVED** — commit `283cf87fab6ffe2624bbd8fef5d0bc7826c4620a` (`test(update): fix DEFECT-003 stale Update Engine manifest test fixture timestamps`) |

### Resolution

**Fix**: `create_signed_package` (`backend/tests/integration/test_update_integration.py`) now computes timezone-aware UTC `created_at`/`expires_at` timestamps relative to the time the test actually runs, instead of the previous fixed, hardcoded ISO-8601 calendar date. All three affected tests (`test_end_to_end_successful_update_lifecycle`, `test_recovery_delegation_on_post_mutation_failure`, `test_checkpoint_failure_aborts_before_any_destructive_mutation`) pass again. No change to `UpdateManifest.parse_dict`'s expiry-checking logic itself, which was behaving exactly as designed — this was purely a test-fixture correction, not an Update Engine behavior change.

**Governance**: per the original deferral, this was not fixed under Integration Hub M2 as an in-scope change — it landed as its own dedicated, separately-authored commit, consistent with "deferred for separate fixture-date correction."

### Summary

Three tests in `test_update_integration.py` construct (or reuse a shared helper that constructs)
an `UpdateManifest` fixture with a hardcoded `expires_at: "2026-09-12T00:00:00Z"`. `UpdateManifest.
parse_dict` (`backend/src/kortex/engines/update/manifest.py`) checks this timestamp against real
wall-clock time and raises `UpdateManifestError` once it has passed — which it now has. This is a
test-data staleness bug, not a defect in the Update Engine's expiry-checking logic itself (which
is behaving exactly as designed: rejecting an expired manifest).

### Observed Behavior

```
kortex.engines.update.exceptions.UpdateManifestError: Update manifest 'mf-0.2.0' expired at
2026-09-12T00:00:00Z (current: 2026-09-15T11:35:08.579730+00:00)
```

### Root Cause

The fixture manifest's `expires_at` field is a fixed, hardcoded ISO-8601 timestamp rather than
one computed relative to the time the test runs (e.g. `now + timedelta(days=N)`). Any test run
after `2026-09-12T00:00:00Z` fails closed at manifest-parse time, before any of the actual
lifecycle/recovery/checkpoint logic each test intends to exercise ever runs.

### Baseline Reproduction Evidence

Formally classified as a **PRE-EXISTING BASELINE DEFECT**, confirmed via `git stash` isolation
during Integration Hub M2 work:

1. **Unmodified `main` baseline (`f5d57cb65b882bd6fe87f8527474804320c0a624`)**: all three tests
   fail identically with zero Integration Hub M2 code changes present — verified directly by
   stashing every M2 change and re-running the three tests in isolation.
2. **Not an M2 Regression**: Integration Hub M2's scope (`kortex.engines.security.*`,
   `kortex.engines.connector.*`, desktop Connectors UI) touches no file under
   `kortex.engines.update`, `test_update_integration.py`, or `manifest.py`.
3. **Scope Ownership**: Owned by the Update Engine test suite's fixture maintenance, unrelated to
   Integration Hub M2's architectural boundary.

### Governance & Acceptance Rules

Per Chief Architect decision:
- **Do NOT fix this defect (or the fixture) under Integration Hub M2.**
- This defect was **DEFERRED** for separate fixture-date correction (e.g. computing `expires_at`
  relative to test run time rather than a fixed calendar date).
- It was correctly **NOT** silently treated as an M2 failure.
- It was correctly **NOT** silently treated as resolved prior to an actual fix landing.
- (Historical, at M2 time) It remained an active, known CI failure until separately corrected.
- Distinct from `DEFECT-002` — the two are unrelated engines/components and were not
  conflated or resolved together.

**Update — resolved**: the separate fixture-date correction this defect was deferred for landed as commit `283cf87fab6ffe2624bbd8fef5d0bc7826c4620a` (see Resolution above). This defect is no longer an active CI failure.
