# RC Test Run Results — <DATE> / <ENVIRONMENT>

> Copy per run. Keep completed runs outside the repository unless the Chief Architect asks for them — they can contain tenant data.

## Run metadata

| Field | Value |
|---|---|
| Date | |
| Tester | |
| Environment | Windows desktop (MSI / NSIS) / Docker |
| OS / version | |
| Build under test | installer filename or image tag |
| Commit SHA | |
| Machine state | clean machine / reused / VM snapshot |
| Ollama available | yes / no |

## Summary

| | Count |
|---|---|
| Executed | |
| PASS | |
| FAIL | |
| BLOCKED | |
| NOT RUN | |

**Defects filed this run:** DEFECT-XXX, …

**Overall impression:** *one honest paragraph — not a verdict, an observation.*

## Golden Path

| Test | Result | Notes / Defect |
|---|---|---|
| GP-01 Install | | |
| GP-02 Launch | | |
| GP-03 First-run detection | | |
| GP-04 Bootstrap admin | | |
| GP-05 Authenticate | | |
| GP-06 Seed dataset | | |
| GP-07 Authenticate personas | | |
| GP-08 Permissions effective | | |
| GP-09 Document parse | | |
| GP-10 OCR | | |
| GP-11 Templates / profiles | | |
| GP-12 Knowledge index | | |
| GP-13 Knowledge search | | |
| GP-14 Knowledge graph | | |
| GP-15 AI providers | | |
| GP-16 AI conversation | | |
| GP-17 AI history | | |
| GP-18 Connector profile | | |
| GP-19 Connector execute | | |
| GP-20 Workflow definitions | | |
| GP-21 Approval create | | |
| GP-22 Approval decide | | |
| GP-23 Finance | | |
| GP-24 HR | | |
| GP-25 Payroll | | |
| GP-26 Operations | | |
| GP-27 Monitoring | | |
| GP-28 Sentinel | | |
| GP-29 License | | |
| GP-30 Restart persistence | | |

## Security tests

| Test | Result | Notes / Defect |
|---|---|---|
| SEC-01 Cross-tenant read | | |
| SEC-02 Cross-tenant write | | |
| SEC-03 Identity impersonation | | |
| SEC-04 RBAC denial | | |
| SEC-05 Insufficient clearance | | |
| SEC-06 Unauthenticated access | | |
| SEC-07 Bootstrap re-entry | | |
| SEC-08 No plaintext secrets on disk | | |
| SEC-09 Keyring persistence | | |
| SEC-10 Event stream leakage | | |

## Failure / resilience tests

| Test | Result | Notes / Defect |
|---|---|---|
| FAIL-01 Kill backend | | |
| FAIL-02 Repeated kills | | |
| FAIL-03 Database unavailable | | |
| FAIL-04 Storage removed | | |
| FAIL-05 AI unavailable | | |
| FAIL-06 Connector unavailable | | |
| FAIL-07 Malformed document | | |
| FAIL-08 Malformed input | | |
| FAIL-09 Invalid authentication | | |
| FAIL-10 Unknown capability | | |

## Lifecycle / backup / recovery / update

| Test | Result | Notes / Defect |
|---|---|---|
| LIFE-01 Full desktop lifecycle | | |
| BR-01 Known dataset | | |
| BR-02 Create backup | | |
| BR-03 Verify backup | | |
| BR-04 Tampered backup rejected | | |
| BR-05 Recovery authorization | | |
| BR-06 Recovery restore | | |
| UPD-01 Update check | | |
| UPD-02 Update trust rejection | | BLOCKED — artifact required |
| UPD-03 Update authorization | | |

## Verification depth

State honestly, per area — do not claim more than was done:

| Area | UNIT TESTED | INTEGRATION TESTED | MANUALLY VERIFIED | NOT VERIFIED |
|---|---|---|---|---|
| Backup | | | | |
| Recovery | | | | |
| Update | | | | |
| Tenant isolation | | | | |
| Desktop lifecycle | | | | |

## Not run, and why

| Test | Reason |
|---|---|
| | |

## Evidence location

*Where logs, screenshots, and envelopes for this run are stored. Confirm they were scrubbed of secrets.*
