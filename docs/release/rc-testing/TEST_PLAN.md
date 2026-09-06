# KORTEX RC Test Plan

## 1. Objective

Determine whether the KORTEX Release Candidate can be installed, configured, used, deliberately broken, and recovered — and whether any failure encountered is a genuine release defect.

**This plan does not certify KORTEX.** It produces evidence; the Chief Architect certifies or rejects.

## 2. Scope

**In scope:** Windows x64 desktop (MSI/NSIS) and Docker/headless production container, covering Phases 1–7 and the M7.1–M7.5 application-completion workstreams.

**Out of scope** (must not be filed as RC defects): macOS/Linux desktop, bare/server topology, code signing, public distribution, PostgreSQL, real customer data.

## 3. Defect classification

Every finding gets **both** a severity and a type.

**Severity**

| Level | Meaning |
|---|---|
| **P0** | Critical release blocker — data loss, security boundary broken, unrecoverable state |
| **P1** | High-severity release blocker — a major accepted feature is unusable |
| **P2** | Significant bug — real impact, workaround exists |
| **P3** | Minor bug — cosmetic or low-impact |
| **P4** | Enhancement / documentation |

**Type** (severity alone does not decide RC disposition)

| Type | Meaning |
|---|---|
| **REGRESSION** | Worked before; broken now. Weighs heaviest against the RC |
| **PRE-EXISTING** | Long-standing; the RC did not introduce it |
| **ENVIRONMENTAL** | Tester's machine/network/config, not KORTEX |
| **EXPECTED BEHAVIOR** | KORTEX is correct; the expectation was wrong (fail-closed denials usually land here) |
| **DOCUMENTATION** | Behavior is right, docs are wrong |
| **UX / PRODUCT** | Works as designed; the design is questionable |
| **ARCHITECTURE** | Requires an architectural decision, not a patch |

A P0 that is **PRE-EXISTING** and a P0 that is a **REGRESSION** are very different RC decisions. Always record both.

## 4. Rules of engagement

1. **Do not fix defects you find.** Capture, reproduce, classify, report. This is what stops RC testing becoming uncontrolled development.
2. **Reproduce before filing.** State attempts and outcome (e.g. "3/3 reproduced", "1/5 — intermittent"). Intermittent is a valid, important finding — say so rather than forcing a clean story.
3. **Never invent capability names.** A 404 means it does not exist; record that.
4. **Never put real secrets in a report.** Redact tokens and keys. Key material appearing in output is itself a P0.
5. **Check `KNOWN_FINDINGS.md` first.**

## 5. RC test matrix

| Area | Test | Environment | Expected | Status |
|---|---|---|---|---|
| **System** | Boot / kernel start | Both | Process starts, engines initialize | |
| System | `/health` | Both | 200; `bootstrap_required` accurate | |
| System | Authentication (GP-05) | Both | Token issued | |
| System | Authorization (SEC-04) | Both | Ungranted → 403 | |
| System | Tenant isolation (SEC-01/02) | Both | Cross-tenant denied | |
| **Desktop** | Install (GP-01) | Windows | App + bundled backend present | |
| Desktop | Launch (GP-02) | Windows | Window + healthy backend | |
| Desktop | Backend spawn | Windows | Sidecar spawned by Tauri | |
| Desktop | Restart (FAIL-01) | Windows | New PID; health recovers | |
| Desktop | Persistence (GP-30) | Windows | Data survives restart | |
| Desktop | Reinstall (LIFE-01) | Windows | DB byte-identical | |
| Desktop | Uninstall (LIFE-01) | Windows | Binaries gone, data kept | |
| **AI** | Provider list (GP-15) | Both | Ollama listed | |
| AI | Conversation (GP-16) | Both | Response returned | |
| AI | Document context (GP-09→16) | Both | Document reachable via AI tools | |
| AI | Knowledge context (GP-12→16) | Both | Knowledge reachable via AI tools | |
| AI | Provider failure (FAIL-05) | Both | Clean error; KORTEX stays up | |
| **Documents** | Ingestion (GP-09) | Both | Parsed | |
| Documents | Extraction (GP-10) | Both | OCR text returned | |
| Documents | Invalid document (FAIL-07) | Both | Clean error | |
| **Knowledge** | Index (GP-12) | Both | Indexed | |
| Knowledge | Retrieval (GP-13) | Both | Relevant results | |
| Knowledge | Tenant isolation (SEC-01) | Both | No cross-tenant leakage | |
| **Connectors** | Driver list (GP-18) | Both | http + dummy only | |
| Connectors | Operation (GP-19) | Both | Executes | |
| Connectors | Failure (FAIL-06) | Both | Clean error | |
| **Workflow** | Definitions (GP-20) | Both | 200 | |
| Workflow | Approval create (GP-21) | Both | Ticket created | |
| Workflow | Approval decide (GP-22) | Both | Terminal state; no self-approval | |
| Workflow | Failure (FAIL-08) | Both | Validation error | |
| **Business** | Finance (GP-23) | Both | Invoice round-trips | |
| Business | HR (GP-24) | Both | Employee + attendance | |
| Business | Payroll (GP-25) | Both | Run + payslip | |
| Business | Operations (GP-26) | Both | Vehicle + incident lifecycle | |
| **License** | Status (GP-29) | Both | Status returned | |
| License | Degraded/fallback | Both | Community fallback valid | |
| **Sentinel** | Health (GP-28) | Both | Subsystems reported | |
| Sentinel | Failure detection (FAIL-01) | Both | Backend failure observed | |
| **Monitoring** | Metrics (GP-27) | Both | Metrics reflect activity | |
| Monitoring | Diagnostics (GP-27) | Both | 200 | |
| **Backup** | Create (BR-02) | Both | Artifact produced | ⚠ DEFECT-001 on desktop |
| Backup | Verify (BR-03) | Both | Valid | |
| Backup | Encryption/fail-closed | Both | No key → refuses | |
| Backup | Tampered artifact (BR-04) | Both | Rejected | |
| **Recovery** | Authorization (BR-05) | Both | Unprivileged denied | |
| Recovery | Preflight verify (BR-05) | Both | 200 | |
| Recovery | Restore (BR-06) | Disposable only | Data restored; safe on failure | |
| **Update** | Check (UPD-01) | Both | 200 | |
| Update | Trust (UPD-02) | Both | Invalid rejected | ⚠ BLOCKED — artifact required |
| Update | Authorization (UPD-03) | Both | Unprivileged denied | |
| **Docker** | Build | Docker | Image builds | |
| Docker | Startup | Docker | Container healthy | |
| Docker | Migration | Docker | Runs before serving | |
| Docker | Health | Docker | `/health` 200 | |
| Docker | Persistence | Docker | Volume survives restart | |
| **Security** | Impersonation (SEC-03) | Both | Caller identity ignored | |
| Security | Unauthenticated (SEC-06) | Both | 401 | |
| Security | Bootstrap re-entry (SEC-07) | Both | 401 | |
| Security | Secrets on disk (SEC-08) | Windows | No plaintext | |
| Security | Keyring persistence (SEC-09) | Windows | Survives restart | |

## 6. Reset procedures

> ⚠️ **Every command marked DESTRUCTIVE permanently deletes data.** Read the annotation before running.

### Windows — full reset to clean-machine state

```powershell
# 1. Stop the application (non-destructive)
Get-Process kortex-desktop,kortex-backend -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Uninstall (removes binaries only; app data is intentionally PRESERVED)
Start-Process -FilePath "$env:LOCALAPPDATA\KORTEX Desktop\uninstall.exe" -ArgumentList "/S" -Wait

# 3. DESTRUCTIVE — deletes ALL application data: database, storage, backups.
#    Everything created during testing is lost. Skip to keep data across a reinstall test.
Remove-Item -Recurse -Force "$env:APPDATA\com.kortex.desktop"

# 4. DESTRUCTIVE — removes stored master/signing keys from Windows Credential Manager.
#    Any previously-encrypted secrets become permanently unreadable.
#    Inspect first (names only, never values):
cmdkey /list | Select-String "kortex-desktop"
#    Then delete each listed target:
#    cmdkey /delete:<exact target name from the listing>
```

To reset **test data only** while keeping the install: perform steps 1 and 3, then relaunch — the app will report `bootstrap_required: true` again.

### Docker — full reset

```bash
cd docker

# Stop containers (non-destructive; named volumes survive)
docker compose -f docker-compose.prod.yml down

# DESTRUCTIVE — `-v` also deletes the named volumes: database, storage, backups.
docker compose -f docker-compose.prod.yml down -v

# Recreate: migrations re-run automatically at entrypoint
docker compose -f docker-compose.prod.yml up -d
curl -s http://localhost:8000/health
```

`docker/.env` is **not** deleted by any of the above. Keep your keys — losing `KORTEX_MASTER_KEY` makes existing encrypted data and backups permanently unreadable.

## 7. Exit criteria

Testing is complete for a cycle when: the Golden Path has been run end-to-end on both environments; all SEC-* cases have been executed and recorded; the desktop lifecycle test is complete; backup/recovery is executed or explicitly recorded as blocked; every finding is filed with severity **and** type; and `RESULTS_TEMPLATE.md` is filled in per run.

**A clean run is not the goal — an honest, evidenced run is.**
