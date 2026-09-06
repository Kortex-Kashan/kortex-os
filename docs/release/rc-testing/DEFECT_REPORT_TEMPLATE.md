# Defect Report — DEFECT-XXX

> Copy this file per defect. Check `KNOWN_FINDINGS.md` first — do not duplicate DEFECT-001.
> **Never paste real key material, tokens, or passwords.** Redact as `[REDACTED]`.
> Key material appearing anywhere in KORTEX output is itself a **P0** — report that fact, never the value.

| Field | Value |
|---|---|
| **ID** | DEFECT-XXX |
| **Title** | *One line: what is broken, where* |
| **Date** | YYYY-MM-DD |
| **Tester** | |
| **Severity** | P0 / P1 / P2 / P3 / P4 |
| **Classification** | REGRESSION / PRE-EXISTING / ENVIRONMENTAL / EXPECTED BEHAVIOR / DOCUMENTATION / UX-PRODUCT / ARCHITECTURE |
| **Environment** | Windows desktop (MSI/NSIS) / Docker |
| **Build / commit** | |
| **Test ID** | e.g. GP-23, SEC-04, FAIL-01 |
| **Reproduction** | e.g. 3/3, or 2/10 intermittent |
| **Status** | Open |

## Summary

*Two or three sentences. What did KORTEX do that it should not have, or fail to do that it should have?*

## Preconditions

*Environment state, dataset, which principal, whether the DEFECT-001 backup workaround was in use.*

## Steps to reproduce

1.
2.
3.

*Exact capability names and parameters. Redact tokens.*

## Expected result

*What should have happened, and why you believe that — cite the runbook case, the reconciliation document, or observed prior behavior.*

## Actual result

*What happened. Paste the full response envelope, including `errors[0].category` and `correlationId`.*

```json

```

## Evidence

- [ ] `/health` response
- [ ] `kortex-stderr.log` / `docker compose logs`
- [ ] Full failing response envelope (with `correlationId`)
- [ ] Screenshot (if UI)
- [ ] Filesystem state (if persistence/lifecycle related)

*Attach files alongside this report. **Scrub secrets before attaching.***

## Regression assessment

*Did this work in an earlier build? If unknown, say unknown — do not guess.*

- Previously working build:
- Evidence for/against regression:

## Impact

*What can a user not do? Is there a workaround? Does it fail closed (safe) or open (unsafe)?*

## Notes

*Anything that would help diagnosis: timing, load, ordering, whether it survives a restart.*

---

**Do not attempt a fix.** Capture, reproduce, classify, report. The Chief Architect decides what gets corrected and when.
