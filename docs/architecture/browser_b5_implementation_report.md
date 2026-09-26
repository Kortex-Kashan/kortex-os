# Browser-B5.0-B5.4 Implementation Report — Browser Capability Layer Foundation

**Status**: COMPLETE for B5.0-B5.4 scope only. Not committed as of this report — commit/push authorization is separate and explicit, per this repository's milestone workflow and the B5.0-B5.4 authorization's own "DO NOT COMMIT OR PUSH YET."

## 1. Baseline

Continues directly from Browser-B4 (commit `10f1b4ccf2488b64aadbc2efc632dcd82bf9698a`, on `origin/main`, CLOSED). Implements the approved `KORTEX OS — Browser B5 Architecture Gate` (`browser_b5_architecture_gate.md`, verdict READY WITH CONDITIONS, conditions B5-C01 through B5-C07) under the explicit "KORTEX OS — B5 FOUNDATION IMPLEMENTATION, B5.0 → B5.4 ONLY" authorization.

## 2. Scope

**In scope (delivered)**: B5.0 (decision lock, recorded in `browser_decision_log.md` D34-D39), B5.1 (typed capability contracts), B5.2 (registry/governance integration — all eight `kortex.browser.*` capabilities registered, authorized, audited, tool-bridged), B5.3 (Capability Execution Grant — mint, sign, verify, sensitive-input gate), B5.4 (desktop redeem command — signature/expiry/single-use/live-binding verification, per-surface serialization, navigation-generation counter).

**Explicitly out of scope, not implemented**: real execution of any capability (`browser.navigate` through `.screenshot` all mint a Grant and stop; `.download` never mints one); B5.5+; B6/B7/B8/B9/B10; graded autonomy tiers; a persisted Grant-signing key.

## 3. Architecture

See `browser_architecture.md` §2.9 (new) and `browser_b5_architecture_gate.md` for the full design. Summary: `kortex.browser.*` registers into the existing, unmodified `RegistryEngine`/`CapabilityDispatcher`/RBAC-ABAC/`ToolGovernanceEvaluator`/`DurableAIApprovalPolicy`/`AuditManager` framework (D35) — confirmed sufficient, no second dispatcher/authorization/approval engine created or found necessary. The one new abstraction, the Capability Execution Grant, exists because a backend capability handler has no channel to a live WebView2 surface; it mints a signed, short-lived authorization artifact instead of executing, which the desktop's new, AI-only `browser_execute_granted_action` command independently re-verifies before doing anything.

## 4. Security Model

- Every capability: `requires_authentication=True`, `requires_execution_context=True`, `security_classification="CONFIDENTIAL"` (`"INTERNAL"` for `grant_verification_key`, a public key).
- Mutation classification exactly per the locked table: `navigate`/`click`/`type`/`download` mutating (approval-gated by the existing, unmodified `ToolGovernanceEvaluator`); `read`/`extract`/`screenshot`/`grant_verification_key` read-only.
- `browser.type` refuses outright (never merely logs) if the target field is labeled like a credential input or the value is shaped like a secret (`grant.py::is_sensitive_type_target`/`looks_like_secret_value`) — before a Grant is ever minted.
- `browser.download`'s handler has no reference to grant-minting at all — verified structurally by inspecting its own source (`test_download_handler_has_no_reference_to_grant_minting`), not merely by its current return value.
- The redeem command independently re-verifies, in order, signature (against a cached, separately-fetched public key — never trusting the Grant's own claim), expiry, single-use, live surface existence, live tenant/profile binding, and live navigation-generation binding, all under a per-surface lock.
- B4's `BrowserPolicyEngine` is structurally unreachable to bypass: this phase never calls any `BrowserRuntime` mutating method at all, and when a later phase does, it will do so through the exact same trait methods a human click already uses.

## 5. Implementation

**Backend** (`backend/src/kortex/engines/browser/`, new package): `models.py` (typed contracts — `BrowserActionErrorCode`, `BrowserElementSelector`, `BrowserCapabilityTarget`, per-capability params, `BrowserCapabilityExecutionGrant`, `BrowserGrantVerificationKey`), `exceptions.py` (one exception type per error code, mirroring `desktop_automation.exceptions`'s convention), `grant.py` (canonicalization/hashing, mint/verify via the *existing* `SecurityEngine`-adjacent `VerificationService`/`ICryptoProvider`/`LocalCrypto`, sensitive-input detection), `audit.py` (shared six-event vocabulary, `BROWSER_GRANT_MINTED` wired to the real `AuditManager`), `engine.py` (`BrowserCapabilityEngine`, registering all eight capabilities and their handlers). `api/kernel_bootstrap.py` wired (pre-boot engine registration alongside `DesktopAutomationEngine`; post-boot `register_browser_ai_tools`, mirroring `register_connector_action_ai_tools`'s exact pattern).

**Desktop** (Rust): new `browser_grant.rs` (`CapabilityExecutionGrant`, `verify_signature`/`verify_expiry` via `ed25519-dalek`/`time`, `RedeemedGrantTracker`, `GrantVerificationKeyCache`, `SurfaceRedeemLocks`, the `browser_execute_granted_action` command). `browser_runtime.rs` extended: `SurfaceEntry.navigation_generation: Arc<AtomicU64>` (bumped on every *allowed* navigation, including the surface's own initial load); `surface_exists`/`navigation_generation` added to the `BrowserRuntime` trait. `browser_profile_store.rs` extended: `ActiveProfileSurfaces::lookup` (non-destructive counterpart to the existing `take`). New `Cargo.toml` dependencies: `ed25519-dalek = "2"` (Ed25519 signature verification — no existing dependency exposes this), `time = { version = "0.3", features = ["parsing", "formatting"] }` (RFC3339 expiry parsing only — never used to reconstruct the signed payload, which stays byte-for-byte identical to what the backend sent). New capability/permission files: `capabilities/browser-ai-execution.json`, `permissions/browser-ai-execution.toml` — deliberately separate from `browser-runtime.json`'s human-facing commands.

## 6. Threat Model / Security Invariant Verification

| Invariant (from the B5 gate, §24) | How B5.0-B5.4 satisfies it |
|---|---|
| INV-B5-01 (no raw WebView2/JS/IPC to AI) | No capability contract accepts a script/selector-free command; the new Tauri command is one narrow, typed command |
| INV-B5-02 (authenticated principal) | `requires_authentication=True` on all eight, no exception |
| INV-B5-03 (tenant-bound) | `CapabilityExecutionContext.tenant_id`, never caller-supplied |
| INV-B5-04 (explicit surface/profile binding, live re-resolved) | Redeem command re-checks `ActiveProfileSurfaces::lookup` against the *live* binding, never the Grant's own claim alone |
| INV-B5-05 (BrowserPolicyEngine unbypassable) | This phase never calls `BrowserRuntime` at all; the architecture is shaped so it never can without going through the same trait |
| INV-B5-06 (page content has no capability authority) | No contract accepts arbitrary page-originated input as an instruction |
| INV-B5-07/08 (no secrets in extraction/audit) | `ui_input_text` never appears in `_mint_and_audit`'s audit context; `BROWSER_GRANT_MINTED`'s context carries only a hash, ids, and expiry |
| INV-B5-09 (profile isolation, live re-checked) | Same as INV-B5-04 |
| INV-B5-10 (fail-closed) | Every check (signature/expiry/single-use/binding/generation) fails closed; unknown tool → `ToolGovernanceEvaluator`'s own existing fail-closed default |

## 7. Adversarial Findings

- **CONFIRMED, fixed (D37)**: a real, would-have-shipped cross-language signature bug — `datetime.isoformat()` (`+00:00`) vs. Pydantic's JSON serialization of the same field (`Z`) — found by a genuine cross-language fixture test (a real Python-minted grant, verified in Rust), not by reasoning about the two languages' conventions in the abstract. Fixed by storing pre-formatted strings instead of `datetime` fields.
- **CONFIRMED, fixed (D39)**: registering 8 new production capabilities correctly triggered an existing, independent completeness guard (`test_capability_metadata_completeness.py`'s `_EXPECTED_RISK` table) — updated with the correct values, all taken directly from the registration call sites.
- Grant tamper tests (Rust and Python, parametrized): every single-field tamper (tenant, principal, capability, target, generation, parameter hash) independently fails signature verification — proven, not assumed.
- Live preflight (D38) confirmed the two new `BrowserRuntime` trait methods behave correctly against a real WebView2 surface, including a subtlety a pure unit test could not have surfaced: the initial page load itself is a real, allowed navigation and correctly bumps the generation counter from 0 to 1 (not 0), which is the more semantically correct behavior.

## 8. Tests

**Backend**: `test_browser_grant.py` (unit, 40 tests) — canonicalization, mint/verify roundtrip, tamper detection (parametrized across every field), sensitive-input detection (target-name and value-shape, both positive and negative cases). `test_browser_capability_dispatch.py` (integration vertical slice, 49 tests) — real `CapabilityDispatcher`/`SecurityEngine` boundary: registration completeness, authentication/authorization/tenant-isolation denial, all seven capabilities minting grants correctly, `browser.type` refusal, `browser.download` never executing (structurally), `ToolGovernanceEvaluator` correctly gating mutation vs. read-only, `action_fingerprint` changing on every relevant field change. **89/89 passed.** `ruff check`: clean.

Full backend suite (`pytest tests/`) was run twice. First run, before two fixes below: 27 failed / 4383 passed — investigated individually: 1 was a stale pre-fix snapshot, 1 was the real, expected `_EXPECTED_RISK` completeness gap, 24 were pre-existing Windows AppContainer sandbox tests in the completely unrelated `python_exec` engine (confirmed unrelated: different subsystem, zero code overlap, reproducible in isolation, unaffected by toggling this session's own sandbox setting), and 1 was a timing-flaky circuit-breaker test. Both real issues were fixed (D39, and reverting an accidental unrelated file change — see §11). Final run, after both fixes: **4386 passed, 24 failed, 4 skipped** — the delta (+3 passed / −3 failed) is exactly the stale snapshot, the completeness fix, and the flaky test now passing; the same 24 pre-existing/unrelated `python_exec` failures remain, unchanged. **The full backend suite is not "clean" and this report does not claim otherwise** — see §12.

**Desktop (Rust)**: `browser_grant.rs`'s own test module, 13 tests — mint/verify roundtrip, every tamper case, expiry boundary conditions, single-use enforcement, and a real cross-language fixture test (D37) using an actual Python-minted grant. `cargo test --lib`: **155/155 passed** (was 142 after B4). `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing, `sidecar.rs`, unchanged). `cargo fmt --check`: clean for every B5-touched file (`browser_grant.rs`, `browser_runtime.rs`, `browser_profile_store.rs`, `lib.rs`); `secure_keys.rs`'s pre-existing, out-of-scope drift confirmed untouched (an accidental reformat of it during this session was caught and reverted before it could enter any commit — see §11).

## 9. Live Verification (D38)

A temporary, disposable preflight (`run_b5_live_preflight`, gated behind `KORTEX_BROWSER_B5_LIVE_PREFLIGHT`, spawned on its own thread per `add_child`'s documented main-thread-deadlock warning, same convention as Browser-B4.8) confirmed, against the real pinned WebView2 runtime:

```
[B5-PREFLIGHT] baseline: initial allowed navigation loaded = true
[B5-PREFLIGHT] surface_exists after create = true; navigation_generation after create = Ok(1)
[B5-PREFLIGHT] second allowed navigation completed = true; navigation_generation after navigate = Ok(2)
[B5-PREFLIGHT] after destroy: surface_exists = false; navigation_generation = Err(SurfaceNotFound { .. })
```

All observed values are correct (the initial load counting as generation 1, not 0, is the more semantically correct behavior — a `.read()` immediately after creation correctly sees "this specific loaded page," not a pre-navigation placeholder). All temporary preflight code and its on-disk WebView2 profile directory were removed after this run; confirmed absent from the current working tree (`grep` for its markers returns nothing).

## 10. Documentation

Updated: `browser_architecture.md` (new §2.9), `browser_capability_model.md` (marked implemented, Grant mechanism added as the one thing the B0-era sketch didn't anticipate, §8 updated), `browser_decision_log.md` (D34-D39 new; §29 Q1 marked resolved; OD-B17/B18/B19 new), `browser_known_limitations.md` (new "As of Browser-B5.0-B5.4" section), `browser_roadmap_b0_b10.md` (B5 marked complete for this scope with full evidence), this report.

## 11. Git Diff (summary)

```
 apps/desktop/src-tauri/Cargo.lock                                 |   ~
 apps/desktop/src-tauri/Cargo.toml                                 |  ~20 +
 apps/desktop/src-tauri/capabilities/browser-ai-execution.json     |   6 (new file)
 apps/desktop/src-tauri/permissions/browser-ai-execution.toml      |  15 (new file)
 apps/desktop/src-tauri/src/browser_grant.rs                       | ~600 (new file)
 apps/desktop/src-tauri/src/browser_profile_store.rs               |  13 +
 apps/desktop/src-tauri/src/browser_runtime.rs                     |  40 +
 apps/desktop/src-tauri/src/lib.rs                                 |   1 +
 backend/src/kortex/api/kernel_bootstrap.py                       |  20 +
 backend/src/kortex/engines/browser/                               | ~900 (new package: __init__, models, exceptions, grant, audit, engine)
 backend/tests/integration/test_browser_capability_dispatch.py     | ~410 (new file)
 backend/tests/integration/test_capability_metadata_completeness.py|   9 +
 backend/tests/unit/test_browser_grant.py                          | ~180 (new file)
 docs/architecture/browser_architecture.md                        |  13 +
 docs/architecture/browser_b5_architecture_gate.md                 | ~330 (new file, from the prior gate)
 docs/architecture/browser_b5_implementation_report.md             | (this file, new)
 docs/architecture/browser_capability_model.md                     |  10 +
 docs/architecture/browser_decision_log.md                        |  ~55 +
 docs/architecture/browser_known_limitations.md                   |  10 +
 docs/architecture/browser_roadmap_b0_b10.md                       |  20 ~
```

`git diff --check`: clean. Not staged or committed as of this report. Confirmed untouched: `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md`, `scratch/`. **One accidental, out-of-scope change was caught and reverted before finalizing this report**: an unrelated formatting pass touched `apps/desktop/src-tauri/src/secure_keys.rs` (pre-existing `cargo fmt` drift this and the prior B4 session both deliberately left alone as out-of-scope) — reverted via `git checkout --`, confirmed absent from the current diff. A stray, empty, accidentally-created `backend/file` was also found and deleted before finalizing.

## 12. CI-Readiness

**B5-specific regression**: clean. Rust `cargo test --lib`: 155/155 passed. `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing warning in `sidecar.rs`, unrelated, unchanged). `cargo fmt --check`: clean for every B5-touched file. Backend, the two new Browser test files (`test_browser_grant.py`, `test_browser_capability_dispatch.py`): 89/89 passed. `git diff --check`: clean.

**Full backend suite**: NOT clean, and this report does not claim otherwise. Final run: **4386 passed, 24 failed, 4 skipped**. The 24 failures are the already-investigated Windows AppContainer `python_exec` sandbox tests (`test_python_execution_boundary.py`, `test_python_execution_vertical_slice.py`) and are pre-existing/unrelated to Browser-B5.0-B5.4 — individually investigated and confirmed to have no code overlap with anything this phase touched (different engine, different subsystem, reproducible in isolation, unaffected by this session's own sandbox setting). None of the 24 involve Browser code, and none were introduced by this phase.

Not yet pushed, so no real CI run exists for this phase yet.

## 13. Known Limitations

See `browser_known_limitations.md`'s new "As of Browser-B5.0-B5.4" section: no capability executes yet (deliberate); B5 V1 is desktop-session-scoped only, no autonomous backend-initiated execution path (D34); non-durable Grant-signing key (OD-B18); pattern-based (not exhaustive) sensitive-input detection (OD-B19); inherited, disclosed, not-fixed cross-cutting gaps (`action_fingerprint` optional-check skip condition, AI-path idempotency).

## 14. Deferred Risks

OD-B17 (wiring real execution, B5.5+), OD-B18 (Grant-signing key persistence, future hardening if ever required), OD-B19 (sensitive-input detection completeness), OD-B13/B14/B15/B16 (all inherited unchanged from Browser-B4, untouched by this phase).

## 15. Commit Recommendation

No open BLOCKER/CRITICAL/HIGH findings against Browser-B5.0-B5.4 itself. B5-specific regression is clean: Rust 155/155, 0 new clippy warnings, fmt clean; backend 89/89 in the two new Browser test files; `git diff --check` clean.

Full backend suite: 4386 passed, 24 pre-existing/unrelated Windows AppContainer `python_exec` failures, 4 skipped. The 24 failures were individually investigated and confirmed to have no code overlap with B5.0-B5.4.

One real, would-have-shipped security-relevant bug (D37) was found and fixed by genuine cross-language verification, not assumed away. One accidental, out-of-scope file change was caught and reverted before it could enter any commit.

**B5.0-B5.4 is READY for commit.**

Awaiting explicit user GO/NO-GO, commit authorization, and (separately) push authorization, per this repository's milestone workflow and the B5.0-B5.4 authorization's own explicit "DO NOT COMMIT OR PUSH YET."
