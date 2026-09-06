# KORTEX RC Test Cases

Every case uses the format: **Preconditions → Action → Expected → Evidence → Failure indicators.**

Conventions used throughout:
- `INVOKE <capability> {params}` means `POST /capabilities/invoke` with that `capabilityName`/`parameters` and a `Bearer` session token (see `README.md` §6).
- "DENIED" means HTTP 403 with `errors[0].category == "PERMISSION_DENIED"`, unless stated otherwise.
- Capability names below were verified against the repository. If a call returns **404**, the capability does not exist under that name — record it rather than guessing at a variant.

---

## 1. Golden Path (GP-01 … GP-30)

Run in order on a genuinely fresh environment. Stop and file a defect at the first hard failure; note-and-continue for cosmetic issues.

### GP-01 Install
**Pre:** Clean Windows machine, no prior KORTEX. **Action:** Install the MSI. **Expected:** Completes without error; `%LOCALAPPDATA%\KORTEX Desktop\kortex-desktop.exe` and `...\kortex-backend\kortex-backend.exe` both exist. **Evidence:** Directory listing. **Failure:** Installer error; missing bundled backend (that means resource packaging broke).

### GP-02 Launch
**Pre:** GP-01. **Action:** Launch with stdout/stderr redirected (`README.md` §4). **Expected:** Window appears; process stays alive. **Evidence:** `kortex-stderr.log`. **Failure:** Immediate exit; `sidecar exited and exhausted its restart attempts` in stderr.

### GP-03 Initialize / first-run detection
**Action:** `GET /health`. **Expected:** 200, `"bootstrap_required": true`. **Evidence:** Full JSON body. **Failure:** 503; connection refused after 30s; `bootstrap_required` absent.

### GP-04 Bootstrap the first administrator
**Action:** `INVOKE kortex.security.bootstrap.create_admin {"tenant_id":"tenant-alpha","principal_id":"alpha-admin","password":"rc-test-alpha-admin"}` with **no** auth header. **Expected:** 200, `payload.created == true`; `/health` now `"bootstrap_required": false`. **Evidence:** Both responses. **Failure:** Non-200; `bootstrap_required` stays true.

### GP-05 Authenticate
**Action:** `INVOKE kortex.security.auth.authenticate {"credentials":{"principal_type":"USER","tenant_id":"tenant-alpha","principal_id":"alpha-admin","password":"rc-test-alpha-admin"}}`. **Expected:** 200; top-level `sessionToken` present; `payload.result.principal_id == "alpha-admin"`. **Evidence:** Response with the **token redacted**. **Failure:** 401 with correct credentials.

### GP-06 Seed the test dataset
**Pre:** Close KORTEX first. **Action:** Run `seed_rc_dataset.py` (`README.md` §8), then relaunch. **Expected:** 5 principals + 50 grants created; re-run reports 0 created. **Evidence:** Seeder output. **Failure:** Import error; non-idempotent behavior.

### GP-07 Authenticate as each seeded persona
**Action:** Authenticate `alpha-manager`, `alpha-employee`, `alpha-restricted`, `beta-admin` (passwords in `TEST_DATA.md`). **Expected:** All 200 with tokens. **Evidence:** Status codes only. **Failure:** Any 401 — the seeder or password hashing is broken.

### GP-08 Verify permissions took effect
**Action:** As `alpha-admin`, `INVOKE kortex.monitoring.metrics.get {}`. **Expected:** 200. **Evidence:** Response. **Failure:** 403 — grants did not apply.

### GP-09 Upload / analyze a document
**Action:** `INVOKE kortex.document_intelligence.pdf.parse` with a small artificial PDF. **Expected:** 200 with extracted structure. **Evidence:** Response payload. **Failure:** 5xx; unhandled exception.

### GP-10 OCR extraction
**Action:** `INVOKE kortex.document_intelligence.ocr.extract` with a small artificial image. **Expected:** 200 with recognized text. **Evidence:** Response. **Failure:** ONNXRuntime/model-loading error — indicates a packaging defect in the frozen backend.

### GP-11 Document templates / profiles
**Action:** `INVOKE kortex.document.template.list {}`, then `kortex.document.profile.list {}`. **Expected:** 200 (possibly empty lists). **Evidence:** Responses.

### GP-12 Index a knowledge source
**Action:** `INVOKE kortex.knowledge.source.index` with artificial content. **Expected:** 200. **Evidence:** Response.

### GP-13 Knowledge search
**Action:** `INVOKE kortex.knowledge.query.search {"query":"<term from GP-12>"}`. **Expected:** 200; results reference the indexed source. **Evidence:** Response. **Failure:** Content from another tenant appears → **P0**, file immediately.

### GP-14 Knowledge graph
**Action:** `INVOKE kortex.knowledge.graph.list {}` and `kortex.knowledge.graph.traverse {...}`. **Expected:** 200, bounded results.

### GP-15 AI provider availability
**Action:** `INVOKE kortex.ai.provider.list {}` and `kortex.ai.model.list {}`. **Expected:** 200; Ollama provider listed. **Evidence:** Response. **Note:** if Ollama is not running, subsequent AI calls fail — expected, not a defect (`README.md` §9).

### GP-16 AI conversation
**Pre:** Ollama running. **Action:** `INVOKE kortex.ai.agent.orchestrate` with a simple prompt. **Expected:** 200 with a response. **Evidence:** Response. **Failure:** Backend 5xx (as opposed to a clean provider-unavailable error).

### GP-17 AI conversation history
**Action:** `INVOKE kortex.ai.conversation.history.get {...}`. **Expected:** 200; the GP-16 turn is present — proves durable recording.

### GP-18 Connector profile
**Action:** `INVOKE kortex.connector.driver.list {}`, then `kortex.connector.profile.register` for the **dummy** driver. **Expected:** 200; profile appears in `kortex.connector.profile.list`. **Evidence:** Responses.

### GP-19 Connector execution
**Action:** `INVOKE kortex.connector.action.execute` against the dummy profile. **Expected:** 200. **Failure:** Unhandled exception.

### GP-20 Workflow definitions
**Action:** `INVOKE kortex.workflow.definition.list {}`. **Expected:** 200.

### GP-21 Approval queue
**Action:** `INVOKE kortex.workflow.approval.list {}`, then `kortex.workflow.approval.create` / `.get`. **Expected:** 200; ticket retrievable. **Evidence:** Ticket ID + responses.

### GP-22 Approval decision
**Action:** `INVOKE kortex.workflow.approval.decide {...}` as an authorized approver. **Expected:** 200; state transitions to a terminal value. **Failure:** A user may approve their own request → security defect.

### GP-23 Finance
**Action:** `INVOKE kortex.finance.invoice.create {...}` then `kortex.finance.invoice.get {...}`. **Expected:** 200; the invoice round-trips. **Evidence:** Invoice ID.

### GP-24 HR
**Action:** `kortex.hr_payroll.employee.create`, `.get`, `.list`; `kortex.hr_payroll.attendance.check_in` / `.check_out`. **Expected:** 200 each. **Evidence:** Employee ID.

### GP-25 Payroll
**Action:** `kortex.hr_payroll.leave.request` → `.decide`; `kortex.hr_payroll.payroll.calculate` → `.run_get` → `kortex.hr_payroll.payslip.get`. **Expected:** 200 each; payslip reflects the run. **Evidence:** Run ID + payslip.

### GP-26 Operations
**Action:** `kortex.operations.vehicle.create`, `.assign`, `.status_update`, `.tracking_record`, `.tracking_history`; `kortex.operations.incident.report` → `.status_update` → `.resolve` → `.close`. **Expected:** 200 each; odometer is monotonic; a closed incident is immutable. **Failure:** A closed incident accepts mutation.

### GP-27 Monitoring
**Action:** `kortex.monitoring.metrics.get`, `.timeseries.get`, `.dashboard.get`, `.diagnostics.get`. **Expected:** 200; metrics reflect the activity above.

### GP-28 Sentinel
**Action:** `kortex.sentinel.health.get`, `.status.get`, `.diagnostics.get`. **Expected:** 200. **Note:** the `backup` subsystem will report unhealthy on desktop — that is **DEFECT-001**, already filed.

### GP-29 License
**Action:** `kortex.license.status.get {}`. **Expected:** 200; a status (Canonical Community fallback is valid on an unlicensed install).

### GP-30 Restart and verify persistence
**Action:** Close the app; relaunch; re-authenticate; re-fetch the GP-23 invoice and GP-24 employee. **Expected:** All data intact; `bootstrap_required` remains false. **Evidence:** Before/after responses. **Failure:** Data loss → **P0**.

---

## 2. Security Attack Tests (SEC-01 … SEC-10)

These are meant to fail safely. A "pass" means KORTEX **refused**.

### SEC-01 Tenant isolation — cross-tenant read
**Action:** Authenticate as `beta-admin`; attempt to read the invoice/employee IDs created under `tenant-alpha`. **Expected:** Not found or DENIED — never Beta seeing Alpha's data. **Evidence:** Both responses side by side. **Failure:** Any Alpha data returned → **P0**.

### SEC-02 Tenant isolation — cross-tenant write
**Action:** As `beta-admin`, attempt to update/delete an Alpha-owned record by ID. **Expected:** DENIED / not found. **Failure:** Mutation succeeds → **P0**.

### SEC-03 Identity impersonation via parameters
**Action:** As `alpha-employee`, invoke a capability while passing `tenant_id`/`principal_id` in `parameters` naming someone else (e.g. `tenant-beta` / `alpha-admin`). **Expected:** Caller-supplied identity is **ignored**; the Kernel-verified principal remains authoritative; the call is scoped to `alpha-employee`/`tenant-alpha` or DENIED. **Evidence:** Response + what data came back. **Failure:** The supplied identity takes effect → **P0**.

### SEC-04 RBAC denial
**Action:** As `alpha-restricted` (zero grants), invoke `kortex.monitoring.metrics.get`, `kortex.backup.create`, `kortex.finance.invoice.create`. **Expected:** All **403 / PERMISSION_DENIED**. **Evidence:** All three responses. **Failure:** Any success → **P0**. *(Validated while building this environment: `alpha-restricted` → `kortex.monitoring.metrics.get` → 403 PERMISSION_DENIED.)*

### SEC-05 Insufficient clearance
**Action:** As `alpha-restricted` (clearance `PUBLIC`), invoke a capability requiring `INTERNAL`. **Expected:** DENIED, decision code `ABAC_INSUFFICIENT_CLEARANCE`. **Note:** clearance ranks are `PUBLIC(0) < INTERNAL(1) < CONFIDENTIAL(2) < RESTRICTED(3)` — `RESTRICTED` is the *highest*.

### SEC-06 Unauthenticated access
**Action:** Invoke a privileged capability with no `Authorization` header, and again with a garbage token. **Expected:** 401 both times. **Failure:** Any 200 → **P0**.

### SEC-07 Bootstrap re-entry
**Action:** After GP-04, call `kortex.security.bootstrap.create_admin` again with different valid credentials. **Expected:** **401 / PERMISSION_DENIED** — bootstrap is permanently closed. **Failure:** A second administrator is created → **P0**.

### SEC-08 Secret storage — no plaintext on disk
**Action:** After using the app, search `%APPDATA%\com.kortex.desktop\` and `%LOCALAPPDATA%\KORTEX Desktop\` for the master key value and any secret you stored via `kortex.security.secret.put`. **Expected:** No plaintext key material anywhere on disk. **Evidence:** Search command + "no matches". **Failure:** Any hit → **P0**.

### SEC-09 Keyring persistence across restarts
**Action:** Note that keys live in Windows Credential Manager under service `kortex-desktop` (`cmdkey /list` shows entry names only — **never dump values**). Close the app fully, relaunch, and confirm your session/encrypted data still works. **Expected:** Identity survives process and application restart. **Failure:** Sessions/secrets invalidated on every restart → the keyring is not persisting.

### SEC-10 Event stream leakage
**Action:** Connect to `WS /events/stream` as an Alpha user while performing Beta activity. **Expected:** No Beta tenant data relayed. **Known limitation:** events published without a `tenant_id` in their payload are forwarded to all authenticated subscribers — documented in `main.py`. Record what you observe; classify against that documented limitation rather than assuming a new defect.

---

## 3. Failure / Resilience Tests (FAIL-01 … FAIL-10)

### FAIL-01 Kill the backend process
**Action:** With the app running, `Stop-Process -Name kortex-backend -Force`. **Expected:** Tauri's supervisor restarts it under a **new PID**; `/health` recovers within seconds; `kortex-stderr.log` shows `restart attempt 1`. **Evidence:** Old/new PIDs, stderr, health before/after. **Failure:** No restart, or `exhausted its restart attempts`.

### FAIL-02 Repeated backend kills
**Action:** Kill it 4+ times in quick succession. **Expected:** The bounded restart policy eventually stops retrying and reports failure rather than looping forever; the UI surfaces a backend-unavailable state. **Failure:** Infinite restart loop; UI hangs with no explanation.

### FAIL-03 Database unavailable
**Action:** Close the app; make the database unreadable (rename `kortex_local.db`); launch. **Expected:** Clean, diagnosable failure — not silent data loss and not a fresh empty database masking the problem. **Evidence:** stderr. **Restore the file afterwards.**

### FAIL-04 Storage directory removed
**Action:** Close the app; rename `storage_data\`; launch. **Expected:** Either a clean recreate or a clear error — never a crash loop with no explanation. *(Note: a related fresh-install defect in this area was found and fixed pre-RC; regression here is worth close attention.)*

### FAIL-05 AI provider unavailable
**Action:** Stop Ollama; invoke `kortex.ai.agent.orchestrate`. **Expected:** A clean provider-unavailable error; `/health` stays healthy; non-AI capabilities unaffected. **Failure:** Backend 5xx or process death → AI outage must not take down KORTEX.

### FAIL-06 Connector unavailable
**Action:** Register an `http_driver` profile pointing at an unreachable host; execute an action. **Expected:** A clean connector error, correctly attributed. **Failure:** Unhandled exception.

### FAIL-07 Malformed document
**Action:** Feed `kortex.document_intelligence.pdf.parse` a renamed text file and a truncated PDF. **Expected:** Clean validation/parse error. **Failure:** Crash, hang, or unbounded memory growth.

### FAIL-08 Malformed capability input
**Action:** Invoke capabilities with missing required parameters, wrong types, `null`, and a very large string. **Expected:** Clean `VALIDATION`-category errors. **Failure:** 5xx or process death.

### FAIL-09 Invalid authentication
**Action:** Wrong password; unknown principal; correct password but wrong `tenant_id`. **Expected:** 401 each; error messages must not reveal whether the principal exists. **Evidence:** All three. *(Validated: wrong password → 401.)*

### FAIL-10 Unknown capability
**Action:** Invoke `kortex.does.not.exist`. **Expected:** **404**. Confirms real dispatch routing.

---

## 4. Desktop Lifecycle Test (LIFE-01)

Run end-to-end, in order, recording each arrow:

```
INSTALL → LAUNCH → CREATE DATA → CLOSE → REOPEN → VERIFY DATA
  → KILL BACKEND → VERIFY RESTART → VERIFY DATA
  → REINSTALL → VERIFY DATA → UNINSTALL → VERIFY DATA RETENTION
```

| Stage | Expected |
|---|---|
| Install | App + bundled backend present |
| Launch | `/health` 200 |
| Create data | Invoice + employee created (record IDs) |
| Close | Both `kortex-desktop` and `kortex-backend` processes exit |
| Reopen | `/health` 200; `bootstrap_required` false |
| Verify data | Both records return identically |
| Kill backend | Supervisor restarts under a new PID; `/health` recovers |
| Verify data | Records still intact |
| **Reinstall over existing** | App reinstalls; **database byte-identical** (compare SHA-256 with the app closed) |
| Verify data | Records still intact |
| **Uninstall** | `kortex-desktop.exe` and `kortex-backend\` **removed** |
| **Verify retention** | `%APPDATA%\com.kortex.desktop\` and the database **still exist, byte-identical** |

**Accepted policy:** uninstall removes binaries and **preserves** application data. Data disappearing on uninstall is a defect. So is application data appearing inside the install directory.

---

## 5. Backup / Recovery (BR-01 … BR-06)

⚠️ **Disposable data only.** Never run recovery against anything you care about.
⚠️ **DEFECT-001 applies** — on desktop you need the bare-hex `KORTEX_BACKUP_KEY` workaround, and you must record that you used it.

### BR-01 Create a known dataset
Create clearly identifiable records (e.g. invoice `RC-BACKUP-001`). Record IDs.

### BR-02 Create a backup
`INVOKE kortex.backup.create {...}` → expect 200 and a backup ID. Confirm the artifact appears under `storage_data\backups\`. **Failure:** `BackupEncryptionError` → DEFECT-001.

### BR-03 Verify the backup
`INVOKE kortex.backup.verify {"backup_id":"..."}` → expect 200, valid. Then `kortex.backup.list` / `.get`. Record the ID and any checksum returned.

### BR-04 Tampered / invalid backup
Copy the artifact, flip bytes in the copy, and verify **the copy**. **Expected:** verification fails (AES-GCM authentication or checksum). **Failure:** a tampered archive verifies clean → **P0**.

### BR-05 Recovery — authorization
As `alpha-restricted`, attempt `kortex.recovery.create`. **Expected:** DENIED. Then `kortex.recovery.verify` as admin for a preflight check without mutating anything.

### BR-06 Recovery — restore *(destructive; disposable environment only)*
Delete/modify the BR-01 records, then run the supported recovery procedure from the BR-02 backup. **Expected:** a mandatory safety checkpoint is taken first; staged verification precedes any swap; records return; a failed recovery leaves the system safe and reports `FAILED_NEEDS_OPERATOR` rather than a half-restored state.

**Report honestly** which of these you actually executed. Distinguish **UNIT TESTED** / **INTEGRATION TESTED** / **MANUALLY VERIFIED** — do not claim recovery was manually verified if you only ran the preflight.

---

## 6. Update (UPD-01 … UPD-03)

### UPD-01 Check
`INVOKE kortex.update.check {}` → expect 200 (reporting no update available is a valid result). `kortex.update.diagnostics.get` → 200.

### UPD-02 Trust rejection *(requires an artifact — see below)*
Attempt to stage an archive with an invalid/absent Ed25519 signature, a tampered manifest, and an incompatible source revision. **Expected:** each rejected before any staging or mutation.

### UPD-03 Authorization
As `alpha-restricted`, attempt `kortex.update.apply`. **Expected:** DENIED.

> **No signed test update artifact exists in this repository.** UPD-02 cannot be executed as written without one. Producing it requires the vendor Ed25519 signing key that `LocalCrypto` validates against — which is exactly the key material that must never be committed. **Do not fabricate one, and do not run an untrusted update on the RC machine.** Required to enable UPD-02: an officially-produced, disposable `.kortex-update` archive plus a deliberately-corrupted variant, supplied out-of-band by the Chief Architect. Until then, record UPD-02 as **BLOCKED — artifact required**, not as passed or failed.
