# KORTEX RC Test Environment — Start Here

**Purpose.** You have never used KORTEX before. This directory tells you how to stand up a clean, reproducible Release Candidate test environment, use KORTEX as a real product, deliberately try to break it, recover it, and determine whether something you hit is a genuine release defect.

**This directory does not certify KORTEX.** It is the controlled environment and procedure through which the Chief Architect certifies or rejects the RC.

| File | What it is for |
|---|---|
| `README.md` | This file — setup, entry point, orientation |
| `TEST_PLAN.md` | Strategy, the RC test matrix, what is and is not in scope |
| `TEST_CASES.md` | The Golden Path, security attack tests, failure/resilience tests, lifecycle tests |
| `TEST_DATA.md` | The deterministic test tenants, users, permissions and business data |
| `KNOWN_FINDINGS.md` | Defects already found while building this environment — **read before filing** |
| `TROUBLESHOOTING.md` | "Is this a KORTEX bug or my environment?" |
| `DEFECT_REPORT_TEMPLATE.md` | Copy this per defect |
| `RESULTS_TEMPLATE.md` | Copy this per test run |
| `seed_rc_dataset.py` | Test-only seeder for tenants/users/RBAC (see §8 — you will need it) |

---

## 1. Supported RC environments

Exactly two. Anything else is out of scope and must not be reported as an RC defect.

| Environment | Status |
|---|---|
| **Windows x64 desktop** (MSI or NSIS installer) | Supported RC target |
| **Docker / headless production container** | Supported RC target |
| macOS desktop, Linux desktop | **NOT in this RC** — not built, not tested |
| Bare/server (non-Docker, non-desktop) install | **NOT in this RC** |
| Publicly signed / publicly distributed artifacts | **NOT in this RC** — artifacts are unsigned |

## 2. Prerequisites

**Windows desktop testing**
- Windows 10/11 x64. A clean machine or VM is strongly preferred — prior KORTEX state invalidates fresh-install results.
- ~1.5 GB free disk (installer ≈103–135 MB; installed app plus the frozen backend is larger).
- Administrator rights to install/uninstall.
- Windows Credential Manager available (default on Windows).

**Docker testing**
- Docker Engine + Compose v2.
- ~2 GB free disk for the image.

**Optional, for AI tests**
- A local [Ollama](https://ollama.com) instance. KORTEX's only wired AI provider is Ollama (see §9). Without it, AI capabilities fail — that is expected, not a defect.

**Optional, for the seeder and source-level checks**
- Python 3.12 and a checkout of this repository.

## 3. Repository preparation

Only needed if you are running the seeder or building from source. Testing an already-built installer needs no checkout.

```bash
git clone https://github.com/Kortex-Kashan/kortex-os.git
cd kortex-os
py -m venv .venv
.venv/Scripts/python -m pip install -e "backend/[dev,ai]"
```

## 4. Windows desktop setup

**Build the installers** (skip if you were handed them):
```bash
.venv/Scripts/pyinstaller installer/pyinstaller/kortex_backend.spec --distpath dist --workpath build/pyinstaller --noconfirm
cd apps/desktop && pnpm install --frozen-lockfile && pnpm tauri build
```
Artifacts land in:
```
apps/desktop/src-tauri/target/release/bundle/msi/KORTEX Desktop_<version>_x64_en-US.msi
apps/desktop/src-tauri/target/release/bundle/nsis/KORTEX Desktop_<version>_x64-setup.exe
```

**Install.** Prefer the MSI for RC testing; the NSIS `-setup.exe` is the same application and is what CI smoke-tests. Both install per-user.

Silent install (NSIS), useful for scripted resets:
```powershell
Start-Process -FilePath ".\KORTEX Desktop_0.1.0_x64-setup.exe" -ArgumentList "/S" -Wait
```

**Where things land** — verify these; they are part of the test:

| Thing | Path |
|---|---|
| Installed application | `%LOCALAPPDATA%\KORTEX Desktop\` |
| Installed executable | `%LOCALAPPDATA%\KORTEX Desktop\kortex-desktop.exe` |
| Bundled frozen backend | `%LOCALAPPDATA%\KORTEX Desktop\kortex-backend\kortex-backend.exe` |
| Uninstaller (NSIS) | `%LOCALAPPDATA%\KORTEX Desktop\uninstall.exe` |
| **Persistent app data** | `%APPDATA%\com.kortex.desktop\` |
| Database | `%APPDATA%\com.kortex.desktop\storage_data\kortex_local.db` |
| Storage root | `%APPDATA%\com.kortex.desktop\storage_data\` |
| Backups | `%APPDATA%\com.kortex.desktop\storage_data\backups\` |
| Master/signing keys | Windows Credential Manager, service `kortex-desktop` |

The install directory must stay read-mostly. **Any persistent application data appearing under `%LOCALAPPDATA%\KORTEX Desktop\` is a defect.**

**Verify it came up:**
```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing | Select-Object -Expand Content
```
Expect HTTP 200 and a JSON body containing `"bootstrap_required": true` on a genuinely fresh install.

**Capturing desktop logs.** The release build is a `windows_subsystem = "windows"` GUI app with no file logger, so its diagnostics only exist on its stdout/stderr. To capture them, launch it redirected:
```powershell
Start-Process -FilePath "$env:LOCALAPPDATA\KORTEX Desktop\kortex-desktop.exe" `
  -RedirectStandardOutput "$env:TEMP\kortex-stdout.log" `
  -RedirectStandardError  "$env:TEMP\kortex-stderr.log"
```
`kortex-stderr.log` is where backend-spawn failures and supervisor restarts appear. **Do this for every test run** — without it, a failure is far harder to diagnose. Note this captures the *Tauri* process only; the frozen backend's own stdout/stderr are piped internally and are not visible here.

## 5. Docker setup

```bash
cd docker
cp .env.example .env
```

Now edit `docker/.env`. Both keys are **required** — the entrypoint refuses to start without them, deliberately, rather than falling back to an ephemeral key:

```bash
openssl rand -hex 32     # run twice; paste one value into each variable
```

```
KORTEX_MASTER_KEY=<64 hex chars>
KORTEX_AUTH_SIGNING_PRIVATE_KEY=<64 hex chars>
```

> **Use the bare 64-hex form (no `0x` prefix)** for `KORTEX_MASTER_KEY`. See `KNOWN_FINDINGS.md` DEFECT-001 — a `0x`-prefixed key is accepted by the platform generally but **not** by the Backup Engine, which then fails closed.

`docker/.env` is git-ignored. **Never commit it. Never paste a real key into a defect report.**

Start, migrate, and verify — migrations run automatically at entrypoint, ahead of serving traffic:
```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f     # watch migration + startup
curl -s http://localhost:8000/health
```

Shutdown (**non-destructive** — the volume survives):
```bash
docker compose -f docker-compose.prod.yml down
```

## 6. How you interact with KORTEX

This matters more than anything else in this document, and it surprises most first-time testers.

**The backend exposes exactly three HTTP endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /health` | Health + first-run detection |
| `POST /capabilities/invoke` | **Everything else.** All functionality is capability dispatch |
| `WS /events/stream` | Authenticated event relay |

There is no REST resource API. Every operation — creating an invoice, running payroll, taking a backup — is a named capability invoked through one endpoint.

**Request shape** (camelCase on the wire):
```json
{
  "requestId": "any-unique-string",
  "capabilityName": "kortex.monitoring.metrics.get",
  "parameters": {}
}
```
with `Authorization: Bearer <sessionToken>` once you have authenticated.

**Response shape**: an envelope with `status` (`SUCCESS`/`FAILURE`), `payload`, `errors[]` (each with a `category`), and `executionDurationMs`. A successful authentication additionally returns `sessionToken` at the top level.

Worked example, PowerShell:
```powershell
$body = @{ requestId="t1"; capabilityName="kortex.security.auth.authenticate"; parameters=@{
  credentials=@{ principal_type="USER"; tenant_id="tenant-alpha"; principal_id="alpha-admin"; password="rc-test-alpha-admin" } } } | ConvertTo-Json -Depth 6
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/capabilities/invoke" -Method Post -Body $body -ContentType "application/json"
$token = $r.sessionToken
```

An unknown capability name returns **404** — useful for confirming you are really hitting capability dispatch.

## 7. First-run bootstrap

On a fresh install, `/health` reports `"bootstrap_required": true`. Create the first administrator — **no `Authorization` header**:

```json
{
  "requestId": "bootstrap-1",
  "capabilityName": "kortex.security.bootstrap.create_admin",
  "parameters": { "tenant_id": "tenant-alpha", "principal_id": "alpha-admin", "password": "rc-test-alpha-admin" }
}
```

Then `/health` flips to `"bootstrap_required": false`, and **bootstrap closes permanently**. A second attempt — even with different valid credentials — must return **401 / `PERMISSION_DENIED`**. That rejection is a security test, not a bug.

## 8. Test accounts and dataset — read this

**KORTEX ships no capability to create a second tenant, additional users, or RBAC grants.** This is explicit, documented architecture, not an oversight (`security/models.py::RolePermissionRecord`: *"There is no provisioning capability in M4 ... RBAC fails closed (denies) for every role until explicitly granted"*). Bootstrap creates exactly one administrator and then closes.

Consequently the required tenant-isolation and RBAC-denial tests **cannot be set up through the product**. Use the supplied seeder, which uses the same direct-insert mechanism the repository's own integration tests use:

```powershell
# Windows desktop — run with KORTEX CLOSED
$env:KORTEX_DATABASE_URL = "sqlite+aiosqlite:///$env:APPDATA/com.kortex.desktop/storage_data/kortex_local.db"
python docs/release/rc-testing/seed_rc_dataset.py
```
```bash
# Docker — run against the mounted volume with the container stopped
KORTEX_DATABASE_URL="sqlite+aiosqlite:///./data/storage_data/kortex_local.db" \
  python docs/release/rc-testing/seed_rc_dataset.py
```

It is idempotent — re-running creates nothing and changes nothing. See `TEST_DATA.md` for exactly what it creates and why.

**The seeded credentials are deliberately artificial, published, and test-only.** They are not secrets. Never use them anywhere real.

## 9. AI configuration

The only AI provider wired into the production boot path is **Ollama**. There is no OpenAI/Anthropic/Azure provider registered at boot — do not assume one exists.

Configuration is via `KORTEX_`-prefixed environment variables (`SystemSettings`, `env_prefix="KORTEX_"`):

| Variable | Default |
|---|---|
| `KORTEX_OLLAMA_URL` | `http://localhost:11434` |
| `KORTEX_OLLAMA_DEFAULT_MODEL` | `llama3` |

No API key or credential is required for local Ollama. If you test a provider that does require one, it belongs in an environment variable or the Security Engine's secret store — **never in a document, never in a commit**. Use placeholders like `YOUR_AI_API_KEY` in any notes.

**Expected behavior with no Ollama running:** AI capabilities fail with a provider/connection error while `/health` stays healthy and every non-AI capability keeps working. That is *AI service unavailable*, not *KORTEX backend failure* — distinguish these in any defect report.

## 10. Connectors

Two connector drivers exist in the repository: `http_driver` and `dummy_driver`. Only these. Do not test against invented integrations.

- `dummy_driver` is the safe local path — use it for connector tests that must not touch the network.
- `http_driver` targets a real HTTP endpoint; point it at something disposable you control.

Connection management is via `kortex.connector.profile.register` / `.list` / `.get` / `.delete`, and execution via `kortex.connector.action.execute`. Credentials go through `kortex.security.secret.put`, never into a profile in plaintext.

## 11–14. Test procedures

The actual test procedures live in dedicated files so this page stays a setup guide:

- **Golden Path** (30 numbered steps, install → shutdown): `TEST_CASES.md` §1
- **Security attack tests** (tenant isolation, impersonation, RBAC, secrets, backup/update/recovery trust): `TEST_CASES.md` §2
- **Failure / resilience tests** (kill backend, DB gone, AI down, malformed input): `TEST_CASES.md` §3
- **Desktop lifecycle test** (install → reinstall → uninstall, data retention): `TEST_CASES.md` §4
- **Backup / recovery**: `TEST_CASES.md` §5
- **Update**: `TEST_CASES.md` §6

## 15. Reset procedures

⚠️ **Read `TEST_PLAN.md` §6 before running any reset.** Several steps permanently delete data. Each is labelled there with exactly what it destroys.

## 16. Evidence collection

Copy `RESULTS_TEMPLATE.md` per run, into a directory you control (do **not** commit run artifacts). Record for every test: Test ID, date, environment, build/commit, tester, preconditions, steps, expected, actual, PASS/FAIL, severity, evidence, logs/screenshots, reproduction status.

Always capture, at minimum: the `/health` response, the desktop `kortex-stderr.log` (or `docker compose logs`), and the full response envelope of any failing capability call — the `correlationId` in it is how a failure is traced.

**Scrub before attaching.** Logs and envelopes can contain tenant data. They must never contain keys — if you ever see key material in output, that is itself a P0 defect.

## 17. Defect reporting

Use `DEFECT_REPORT_TEMPLATE.md`. Check `KNOWN_FINDINGS.md` first — one real defect (DEFECT-001) is already filed and you will hit it during backup testing.

**Do not fix defects you find, and do not expect them to be fixed mid-run.** Capture, reproduce, classify, report. The Chief Architect decides what gets corrected. This is what keeps RC testing from turning into uncontrolled development.

## 18. What is explicitly NOT tested or supported

- macOS desktop; Linux desktop (not built)
- Bare/server (non-Docker, non-desktop) fresh-machine topology
- Public code signing — **all artifacts are unsigned**; SmartScreen will warn, and that is expected, not a defect
- Public distribution
- Production or real customer data — use only the artificial dataset
- PostgreSQL (`backend/README.md` mentions it; the RC topologies are **SQLite-only**)
