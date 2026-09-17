# KORTEX Python Execution Engine — Supported Deployment Modes

This document states exactly what the KORTEX Python execution boundary
enforces, on which platforms, and what it deliberately does **not** enforce.
It is operational documentation, not an architecture specification.

Security claims below use the vocabulary this milestone was specified with:

| Term | Meaning |
|---|---|
| **IMPLEMENTED** | The mechanism is applied to the real process in code. |
| **TESTED** | An automated test observes the real OS behaviour. |
| **NOT TESTED** | Implemented, but no automated test observes enforcement. |
| **ENVIRONMENT DEPENDENT** | Cannot be verified without a specific host. |

---

## 1. What this boundary is

The Windows boundary is a **governed trusted-Python execution boundary**. It
contains code KORTEX already trusts — administrator-authored or explicitly
trusted versioned Python Actions — so that a defect in that code cannot reach
unrelated tenant data, host credentials, or the network.

It is **not** a malicious-code sandbox and is **not** equivalent to
kernel-level isolation. Do not describe it as one.

Untrusted Python is supported on **Linux only**, via nsjail.

## 2. Trust model

| Trust level | Linux | Windows |
|---|---|---|
| `TRUSTED` | Supported (nsjail) | Supported (AppContainer + restricted token + Job Object) |
| `UNTRUSTED` | Supported (nsjail, stricter seccomp) | **Not supported** — refused before any process starts |

Windows Untrusted Python is an MVP non-goal. It is rejected in
`policy.resolve_policy()`, before a workspace is created or a process is
spawned, so the Windows boundary is unreachable with untrusted code.

`TRUSTED` is an **administrative grant**, never a property an author asserts
about their own code: publishing a trusted Action additionally requires the
`python:trust` permission, evaluated by `SecurityEngine.authorize()`.

## 3. Windows: what enforces what

The four mechanisms are layered and are **not interchangeable**. Attributing
the wrong property to the wrong mechanism is the most likely way this document
becomes misleading, so each is stated separately.

| Mechanism | What it actually enforces | Status |
|---|---|---|
| **AppContainer identity** (zero capability SIDs) | Host filesystem denial (including the KORTEX database file itself, tested directly); network denial (including loopback) | IMPLEMENTED, TESTED |
| **Restricted token** (`DISABLE_MAX_PRIVILEGE`, deny-only admin SIDs) | Privilege removal; no administrative execution | IMPLEMENTED, TESTED |
| **Job Object** | Process-tree containment; active-process limit; memory limit; deterministic timeout termination; full UI restrictions | IMPLEMENTED, TESTED |
| **Job Object -- CPU time limit** | `JOB_OBJECT_LIMIT_PROCESS_TIME`/`JOB_OBJECT_LIMIT_JOB_TIME` are set whenever `cpu_seconds` is configured | IMPLEMENTED, **NOT TESTED** -- no test spins a CPU-bound busy-loop long enough to observe the Job Object terminate it for exceeding CPU time, as distinct from wall-clock timeout (which is tested) |
| **Explicit ACLs** | Runtime readable+executable, workspace writable, nothing else reachable by the sandbox SID | IMPLEMENTED, TESTED |

`WaitForSingleObject` failure (`WAIT_FAILED`) is handled as its own fail-closed
path, separate from both "the process finished" and "the process timed out":
the boundary captures the real Win32 error immediately (before any cleanup
call can overwrite it via a later `GetLastError()`), still attempts real Job
Object containment, and raises rather than reporting an unconfirmed process
state as a successful result. Verified by
`test_wait_failed_is_never_reported_as_a_successful_or_timed_out_execution`,
which fakes only the `WaitForSingleObject` return value against the otherwise
real boundary (real token, real AppContainer, real Job Object, real process)
-- forcing a genuine `WAIT_FAILED` requires racing a handle close against a
live wait, which is not reliably reproducible as an automated test.

### 3.1 A measured limitation of the restricted token

Verified empirically on Windows 11 (26100) during implementation: a restricted
token whose restricting-SID list **excludes the interactive user SID cannot
initialize a process at all** — `STATUS_DLL_INIT_FAILED` (0xC0000142). This was
confirmed across window-station, working-directory, and SID-set variations, and
is not fixed by granting the window station or desktop (both already grant
`RC` / `S-1-5-12` full access on a default Windows 11 install).

The user SID is therefore present in the restricting list. **Consequence: the
restricted token alone imposes no filesystem denial.** That denial comes
entirely from the AppContainer identity.

This is why AppContainer is layered on: without it, the "denied: unrelated host
data" requirement would not be met on this platform.

### 3.2 The KORTEX-owned runtime image

The sandbox identity needs read+execute access to a Python runtime. KORTEX does
**not** re-ACL the system or user-profile Python installation. Instead it
provisions a private copy it owns and ACLs itself:

- Location: `%LOCALAPPDATA%\KORTEX\python-exec\runtime-<fingerprint>\`
  (override with `KORTEX_PYTHON_EXEC_ROOT`).
- Contents: the interpreter, its DLLs, and the standard library.
- **Excluded**: `Lib/site-packages`. Host third-party packages are not part of
  a reproducible execution contract, and shipping them would silently widen
  every Action's dependency surface.
- Size: ~40 MB. Provisioned lazily on first execution, idempotent thereafter
  (~1.3 s cold, ~0 s warm).
- No directory outside the KORTEX execution root is created, modified, or
  re-ACL'd. The shared interpreter installation is only ever **read**.

### 3.3 Required environment variable

`LOCALAPPDATA` must be present in the KORTEX process environment. An
AppContainer profile lives under `%LOCALAPPDATA%\Packages\<container>`, and
creating a process under an AppContainer identity without it fails with
`ERROR_ENVVAR_NOT_FOUND` (203) before the process starts.

## 4. Linux: nsjail

**ENVIRONMENT DEPENDENT.** The Linux boundary requires the `nsjail` binary on
`PATH` (or `KORTEX_NSJAIL_BINARY`).

The Gateway **fails closed**: if nsjail is absent or not executable,
`IsolationUnavailableError` is raised and **no Python runs**. There is no
degraded path that falls back to an unguarded subprocess — a fallback that
silently executes unisolated code is worse than an outright failure, because
the caller believes it was contained.

Configured isolation: mount/pid/ipc/uts/user namespaces, read-only bind of the
runtime image, read-write bind of the execution workspace only, `rlimit`
address-space / CPU / file-size / nofile / nproc limits, `--time_limit`,
non-root uid/gid, network denial by default, and a stricter seccomp policy for
untrusted executions (no `ptrace`, `mount`, or socket syscalls).

**Verification status on this milestone's development host:** the argument
vector KORTEX builds is unit-tested via `LinuxExecutionBoundary.build_arguments`,
but **enforcement is NOT TESTED** — the development host is Windows and has no
nsjail. Required CI environment: Linux with `nsjail` installed and unprivileged
user namespaces enabled (`kernel.unprivileged_userns_clone=1`).

## 5. Environment sanitization

The execution environment is built **additively from an allowlist**, never by
copying the parent environment and pruning it. A KORTEX secret that nobody
anticipated is therefore absent by construction rather than by successful
filtering.

Passed through: `SystemRoot`, `windir`, `LOCALAPPDATA`,
`NUMBER_OF_PROCESSORS`, `PROCESSOR_ARCHITECTURE` (Windows); `LANG`, `LC_ALL`
(POSIX). Set by KORTEX: a `PATH` pointing **only** at the provisioned runtime,
`TEMP`/`TMP`/`TMPDIR` inside the workspace, `PYTHONNOUSERSITE`,
`PYTHONDONTWRITEBYTECODE`, and the workspace/input paths.

Never passed: database URLs, SecretStore material, connector or MCP
credentials, agent private keys, or any other parent-environment variable.

## 6. Network policy

Default-deny on both platforms.

- **Windows**: kernel-enforced by the AppContainer identity having zero
  capability SIDs — no `internetClient`, no `privateNetworkClientServer`. Both
  outbound and loopback connections are denied. TESTED.
- **Linux**: network namespace denial via nsjail. ENVIRONMENT DEPENDENT.

A Trusted Action may declare `network_policy=ALLOW_LIST`. An Untrusted Action's
declaration is ignored and forced back to `DENY`, so an untrusted author cannot
grant itself network access by editing its own Action metadata.

> **Known limitation.** `ALLOW_LIST` is currently recorded and carried through
> policy, but the Windows boundary does not yet implement per-host allow-list
> enforcement — a Trusted Action requesting `ALLOW_LIST` on Windows still
> receives **no** network access, because the AppContainer has no network
> capability. This is fail-closed (stricter than declared), never fail-open.

## 7. Subprocess policy

Neither trust level receives unrestricted child-process creation.

- **Windows**: enforced by the Job Object `ActiveProcessLimit`
  (default 1 — the Python process itself). Attempting to spawn a child raises
  `OSError`. TESTED.
- **Linux**: `--rlimit_nproc`, plus seccomp denial of process-creation-adjacent
  syscalls for untrusted code. ENVIRONMENT DEPENDENT.

## 8. Python Actions

Production Python is always a **versioned, immutable, tenant-owned Action**.
Inline Python is not a supported production asset.

- A workflow pins `(action_id, version)`. Publishing a new version never
  changes what an existing pinned workflow executes.
- Immutability is enforced three independent ways: `publish_version` only ever
  INSERTs; `(tenant_id, action_id, version)` is uniquely constrained; and the
  stored `source_sha256` is recomputed on every load, so a row modified
  out-of-band fails to resolve rather than executing.
- Dependencies follow a recorded `requirements_lock`. **There is no runtime
  `pip install`** — arbitrary package installation from the network is not
  implemented and is an explicit non-goal.

## 9. Governed capability IPC (Trusted Python only)

Trusted Python may invoke **explicitly allowlisted** KORTEX capabilities over a
local channel. It can name a capability and pass parameters; it **cannot** name
a tenant, principal, workflow, execution, or session token.

- **Transport**: Windows named pipe whose DACL names only SYSTEM, the KORTEX
  account, and the execution's AppContainer SID. (A loopback TCP socket is not
  an option — the AppContainer cannot reach 127.0.0.1.) Unix domain socket
  inside the 0700 workspace on Linux.
- **Authentication**: a 256-bit CSPRNG execution token, execution-scoped,
  tenant-scoped, workflow/execution-scoped, short-lived, and revoked
  unconditionally when the execution terminates.
- Only the SHA-256 digest is stored; comparison is constant-time; refusals are
  indistinguishable from one another (no oracle).
- The token is **removed from the Action's environment** before any Action code
  runs, so an Action that dumps `os.environ` cannot exfiltrate a live
  credential.
- The nested call re-enters `Kernel.invoke_capability`, so the real
  `CapabilityDispatcher` re-verifies the session token and builds the
  authoritative `CapabilityExecutionContext`. **There is no second execution
  authority.**
- The token appears in no log line, audit context, exception message, or result
  payload.

Untrusted Python receives **no bridge at all** — the address and token are
absent from its environment, so there is nothing for it to attempt.

## 10. Stdio contract

```
stdin  -> structured JSON input
stdout -> structured JSON result   (reserved exclusively for the result document)
stderr -> diagnostics
```

An Action's own `print()` is redirected to stderr for the duration of the call.
This is structural, not conventional: it is what lets the Gateway treat an
unparseable stdout as a genuine protocol failure rather than as "the Action
printed something".

## 11. Failure and retry

Idempotency belongs to the **capability**, not the language.
`kortex.python.execute` is registered fail-closed as mutating and
non-idempotent, because what a Python Action does is opaque to KORTEX. KORTEX
does **not** blanket-retry Python executions.

A timeout is reported as `TIMEOUT` regardless of what the process wrote before
termination: partial output from a killed execution is never presented to a
workflow as a completed result.

Governed refusals (unknown action, cross-tenant miss, failed integrity check,
refused trust level, unavailable isolation) return a structured `REJECTED`
result rather than propagating an exception, so WorkflowEngine's authoritative
state model is preserved. A `REJECTED` result reports **no boundary**, because
none was entered.

## 12. Running the tests

```bash
cd backend
python -m pytest tests/unit/test_python_execution_policy.py tests/unit/test_python_action_versioning.py -q
python -m pytest tests/unit/test_python_linux_boundary.py -q                # config/fail-closed decisions, any platform
python -m pytest tests/integration/test_python_execution_boundary.py -q      # OS-level, Windows only
python -m pytest tests/integration/test_python_capability_bridge.py -q
python -m pytest tests/integration/test_python_execution_vertical_slice.py -q
```

OS-level enforcement tests are marked `os_security` and skip off Windows:

```bash
python -m pytest -m os_security -q
```

**A green run on a platform where these skip is not evidence that the boundary
was verified.** Check for skips explicitly.

### Test-fixture AppContainer identities

`test_python_execution_boundary.py` and `test_python_execution_vertical_slice.py`
each create their own named AppContainer profile (`KortexPythonExecTest`,
`KortexPythonSliceTest`) -- distinct from `create_sandbox_identity`'s
production default (`KortexPythonExec`), so a test run can never touch or
delete a production identity. Both fixtures delete their own profile in
teardown via `workspace.delete_sandbox_identity`, which always runs (even on
test failure) and raises rather than swallowing a genuine deletion failure, so
repeated test runs do not accumulate AppContainer profile residue on the host.
`delete_sandbox_identity` is test/teardown-oriented only; production code never
calls it, matching `create_sandbox_identity`'s own contract of one stable,
reused identity for the lifetime of a KORTEX installation.
