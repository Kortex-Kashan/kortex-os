# Troubleshooting — "Is this a KORTEX defect or my environment?"

Work through this before filing. Environmental issues filed as defects cost more than they save.

## Diagnostic order

1. `GET /health` — is the backend up at all?
2. `kortex-stderr.log` (desktop) or `docker compose logs` — did the backend spawn/start?
3. The full failing response envelope — `errors[0].category` and `correlationId`.
4. `KNOWN_FINDINGS.md` — already filed?

## The application launches but never becomes healthy

Check `kortex-stderr.log` (you must launch redirected — `README.md` §4).

| Log signal | Meaning |
|---|---|
| `backend sidecar spawned.` then nothing | Backend started; still initializing. Allow ~30s on first run (migrations). |
| `sidecar exited unexpectedly; restart attempt 1…` | Backend is crashing on startup. **Real defect** — capture the whole log. |
| `exhausted its restart attempts` | Backend failed repeatedly; supervisor gave up. **Real defect.** |
| `could not resolve a backend command to spawn` | Bundled backend missing/unreachable — packaging defect. |

The frozen backend's own stdout/stderr are piped internally and not visible here — a known observability limitation, not something you are missing.

## Port 8000 already in use

KORTEX binds `127.0.0.1:8000`. Another process (including a stale `kortex-backend.exe`) will block it.

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
Get-Process kortex-backend -ErrorAction SilentlyContinue | Stop-Process -Force
```
**Environmental**, unless a stale backend persists after a clean shutdown — that is a defect.

## Everything returns 401

- Missing/expired session token → re-authenticate.
- Wrong `tenant_id` in credentials → 401 by design (SEC-09 covers this).
- After a credential-store reset, previously-encrypted secrets are unreadable — **expected**, that data is gone.

## Everything returns 403

Almost always correct fail-closed behavior:
- The principal's role has no grant for that permission.
- Clearance too low (`PUBLIC` < required `INTERNAL`) → `ABAC_INSUFFICIENT_CLEARANCE`.
- You are `alpha-restricted`, which has **zero grants by design**.

Re-check `TEST_DATA.md` §4 before filing. A 403 is only a defect if a principal that *should* be granted the permission is denied.

## A capability returns 404

That capability name does not exist. KORTEX 404s unknown names — this is real dispatch routing, not an outage. Verify the exact name; **do not guess variants**. Record it.

## AI calls fail

Ollama is the only wired provider. No Ollama → AI capabilities fail while everything else works.

```bash
curl http://localhost:11434/api/tags
```
Reachable but failing → possibly a defect. Unreachable → **environmental**, and the correct expected behavior is a clean error with `/health` still healthy (FAIL-05).

## Backup fails with `BackupEncryptionError`

**This is DEFECT-001.** On desktop, `KORTEX_MASTER_KEY` is `0x`-prefixed and the Backup Engine does not accept that format. Use the bare-hex `KORTEX_BACKUP_KEY` workaround in `KNOWN_FINDINGS.md`, and **record that you used it**. Do not file a duplicate.

## Sentinel reports the `backup` subsystem unhealthy

Same root cause — DEFECT-001. Sentinel is correctly reporting a real condition; that part is working as designed.

## The seeder fails

| Error | Cause |
|---|---|
| `KORTEX_DATABASE_URL is not set` | Set it (`TEST_DATA.md` §6). |
| `no such table: security_principals` | Schema not created yet — launch the app once so migrations run, then seed. |
| `ImportError … circular import` | Import order; the shipped script already handles it (OBSERVATION-002). Run it unmodified. |
| `database is locked` | KORTEX is running. **Stop it and re-run.** |

## Docker won't start

- Entrypoint exits immediately → a required key is missing or still the placeholder in `docker/.env`. This refusal is deliberate fail-closed behavior, **not** a defect.
- Migration errors in logs → capture them; likely a real defect.
- `/health` refuses → check `docker compose ps` and the logs before concluding anything.

## Windows SmartScreen warns on the installer

**Expected.** RC artifacts are unsigned. Not a defect (`README.md` §18).

## After uninstall, my data is still there

**Correct and intended.** Uninstall removes binaries and preserves application data. Data *disappearing* would be the defect.

## Reinstall lost my data

**That is a defect** — file it. The accepted behavior is a byte-identical database across reinstall (LIFE-01).

## Something is intermittent

Do not force it into a clean pass or fail. Record the attempt ratio (e.g. "2/10"). Intermittency is itself a finding, and a real one is more valuable reported honestly than smoothed over.
