# Browser-B4 Implementation Report — Navigation/Domain/Popup/Download/Permission Policy (V1)

**Status**: COMPLETE (V1 baseline). Not committed as of this report — commit/push authorization is separate and explicit, per this repository's milestone workflow.

## 1. Baseline

Continues directly from Browser-B3 (commit `d48651831f0c2eed0606784fbf75652bcf7983cf`, pushed to `origin/main`). The Browser-B4 architecture gate (delivered before any B4 code was written, per the task brief's explicit "architecture-audit-only first" restriction) found that `browser_security_model.md` §2's "fifth enforcement point through `CapabilityDispatcher`" framing describes a FUTURE state (B5/B6's governed `kortex.browser.*` capabilities), not B4's actual scope — a human click or page-triggered navigation never produces a capability call, and `ICoreWebView2NavigationStartingEventArgs` has no `GetDeferral` (confirmed against the pinned `webview2-com-sys-0.38.2` bindings). The owner's subsequent "B4 ARCHITECTURE GATE — APPROVED" message resolved six open decisions (local/private-network navigation: deny by default; popups: deny all in V1; native permissions: deny by default; downloads: block all in V1; policy-denied frontend event: minimal, never the URI; D27 documentation correction) and authorized B4.1 through B4.10.

## 2. Scope

**In scope (V1)**: navigation policy (scheme allowlist + local/private-network destination deny list), popup policy (deny all), download policy (deny all), native permission policy (deny all), audit logging, frontend-visible denial event, adversarial testing of the policy logic and known bypass classes, a live preflight against the real WebView2 runtime.

**Explicitly out of scope (per the approval)**: Browser-B5 (capability layer), any AI browser capability, provider authentication, workflow integration, backend policy round-trips, new Tauri commands, popup-to-tab conversion, download save-path confirmation, native-permission confirmation UI, domain allowlisting beyond the fixed local/private-network deny list.

## 3. Architecture

See `browser_architecture.md` §2.8 (new) for the full account. Summary: `browser_policy.rs`'s `evaluate_navigation` is a pure, synchronous, deterministic function with zero I/O — required, not preferred, because `NavigationStarting` cannot defer. `browser_runtime.rs`'s WebView2 COM event handlers (`NavigationStarting`, `NewWindowRequested`, `DownloadStarting`, `PermissionRequested`) are the actual enforcement points; three of the four (popup/download/permission) are unconditional-deny in V1 and do not call into `browser_policy.rs`'s decision logic at all, only its shared audit/event plumbing.

`browser_policy.rs` remains deliberately profile/tenant-agnostic (see its own module doc for why), matching `browser_runtime.rs`'s existing Browser-B3 boundary. The policy audit log reuses the exact same local JSON-Lines file Browser-B3 established (`<profiles_root>/audit.log`), threaded into `WebView2RuntimeAdapter` as a plain `PathBuf` rather than a `BrowserProfileStore` dependency.

## 4. Security Model

See `browser_security_model.md` §2 (corrected), §3, §5, §12 (all updated for B4). Summary of the implemented V1 policy:

- **Navigation**: only `http`/`https` potentially allowed. Local/private-network destinations denied via the first-class `DenyReason::PrivateNetworkAccess` (loopback, RFC1918, link-local, "this network", carrier-grade NAT IPv4; unique-local/link-local IPv6; IPv4-mapped IPv6 reclassified by embedded address; `localhost` including trailing-dot FQDN notation). No DNS resolution — disclosed gap (OD-B16).
- **Popup**: `NewWindowRequested` → `SetHandled(true)`, `SetNewWindow` never called.
- **Download**: `DownloadStarting` → `SetCancel(true)`.
- **Permission**: `PermissionRequested` → `SetState(COREWEBVIEW2_PERMISSION_STATE_DENY)`.
- **Frontend/audit event**: `{surfaceId, action, reason}` only — never the URI, host, path, query, or fragment, for any of the four action kinds.

## 5. Implementation

New: `apps/desktop/src-tauri/src/browser_policy.rs` (821 lines, including 35 unit tests). Modified: `browser_runtime.rs` (+306/-net, new imports, `WebView2RuntimeAdapter.policy_audit_log_path` field, `evaluate_navigation_args`, `record_and_emit_policy_denial`/`deny_navigation`/`deny_popup_download_or_permission`, three new event-handler registrations in `register_navigation_loading_handlers`), `lib.rs` (+42, `mod browser_policy;`, `profiles_root` computed earlier, `policy_audit_log_path` threaded into the adapter), `Cargo.toml`/`Cargo.lock` (`url = "2"` promoted from transitive to direct — 1-line lockfile diff, no new package/version). Frontend: `features/browser/api.ts` (`NetworkClassification`, `PolicyDenyReason`, `PolicyAction`, `PolicyDeniedEvent`, `onBrowserPolicyDenied`, `policyDenyReasonMessage`), `hooks/useBrowserTabs.ts` (policy-denied error-banner subscription), `components/BrowserApp.test.tsx` (mock for `onBrowserPolicyDenied` — see §8).

`docs/architecture/browser_decision_log.md`'s D27 entry was also corrected in this phase (a documentation-only fix carried over from the architecture gate, not new B4 work): the `BrowserRuntimeError` field-casing fix it discusses was already present in the B3 commit itself; the entry previously (incorrectly) said otherwise.

## 6. Threat Model (adversarial, applied during B4.9)

| Threat | Mitigation | Verified |
|---|---|---|
| Page navigates to `file:`/`javascript:`/`data:`/`about:`/`blob:`/custom scheme | `SchemeNotAllowed` deny | Unit test per scheme, plus case-varied |
| Page/redirect navigates to loopback/RFC1918/link-local/CGN/`localhost` | `PrivateNetworkAccess` deny | Unit tests, full-range + boundary-precision |
| IPv6 loopback/private/link-local literal | Same, via `Url::host()` not `host_str()` (D30) | Unit tests |
| IPv4-mapped IPv6 literal embedding a private address | Reclassified by embedded IPv4 (`to_ipv4_mapped()`) | Unit tests |
| **Trailing-dot `localhost.` FQDN notation** | **Found as a real bypass, fixed** (D33) | Regression test added |
| **Decimal/hex/octal/short-form numeric IPv4 encodings of a private address** (classic SSRF-filter-bypass class) | Confirmed already safe — classification operates on the parsed `Ipv4Addr`, never the raw string | New regression test added (no fix needed) |
| **Userinfo-based host confusion** (`user@host` where the "real" host is disguised) | Confirmed already safe — `Url::host()` reads the structurally-parsed host, never the userinfo | New regression test added (no fix needed) |
| Malformed/empty/unparseable URI | Fails closed (`Malformed`) | Unit tests |
| Page calls `window.open()` | `NewWindowRequested` denied | Unit test (event wiring) + live preflight (D32) |
| Page/link triggers a download | `DownloadStarting` denied | Live preflight, external filesystem check (D32) |
| Page requests geolocation/camera/mic/etc. | `PermissionRequested` denied | Live preflight, audit-log confirmation (D32) |
| Policy-denied event/audit log leaking the actual URI | Structurally impossible — `DenyReason`/`PolicyDeniedEvent` never carry a URI field at all | Unit tests assert absence of the test URI's substrings |

## 7. Adversarial Findings

- **CONFIRMED, fixed**: `http://localhost./` (and case-varied, and double-trailing-dot variants) bypassed the `localhost` deny check — DNS's own root-anchored FQDN notation was not recognized by the exact-string comparison. Fixed by stripping trailing dots before comparing (`classify_host`). Regression-tested.
- No other findings required a code change. Two additional bypass classes worth naming were tested and confirmed ALREADY safe by construction (numeric IPv4 encodings, userinfo host confusion) — see §6.

## 8. Tests

Rust `cargo test --lib`: **142 passed**, 0 failed, 2 ignored (pre-existing) — was 107 after B3; +35 net new, entirely in `browser_policy.rs`. `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing, `sidecar.rs`, unrelated). `cargo fmt --check`: clean for every B4-touched Rust file (`browser_policy.rs`, `browser_runtime.rs`, `lib.rs`); pre-existing drift in `secure_keys.rs` (untouched by this phase) left alone, out of scope. Frontend `pnpm typecheck`: clean. `pnpm test` (full suite): **839 passed** across 101 files, 0 failed — required adding a mock for `onBrowserPolicyDenied` in `BrowserApp.test.tsx`, which had none; without it, the real `listen()` call (reaching for `window.__TAURI_INTERNALS__`, absent in jsdom) threw an unhandled rejection on every test in that file once B4.4's subscription effect actually ran during tests. This is a pre-existing gap from B4.4 (the previous session), surfaced and fixed as part of this phase's full-regression pass, per this repository's dependency-chain rule.

## 9. Observed Results (live preflight, D32)

A temporary, disposable harness (gated behind `KORTEX_BROWSER_B4_LIVE_PREFLIGHT`, spawned on its own thread from `.setup()` per `add_child`'s documented main-thread-deadlock warning, removed after this run) exercised the real compiled app binary against the real pinned WebView2 runtime on this machine:

```
[B4-PREFLIGHT] baseline: initial allowed navigation loaded = true
[B4-PREFLIGHT] navigation-block: url unchanged (still example.com) = true (actual url: https://example.com/); audit has NAVIGATION_BLOCKED = true
[B4-PREFLIGHT] popup-block: eval dispatched = true; audit has POPUP_BLOCKED = true
[B4-PREFLIGHT] download-block: no file reached Downloads = true; audit has DOWNLOAD_BLOCKED = true
[B4-PREFLIGHT] permission-deny: audit has PERMISSION_DENIED = true
```

All four checks answer the real question ("does the action actually get blocked", not "does an audit entry get written while the action still occurs"): the surface's live URL never changed away from the last-allowed page after a denied-navigation attempt; the download's uniquely-named marker file never appeared in the real `%USERPROFILE%\Downloads` folder; the audit log recorded every one of the four block/deny events. Disclosed limitation: the permission check's page-side promise-rejection outcome was not independently re-observed (no JS-to-Rust bridge exists in this crate, by design — see §12).

All temporary preflight code (`run_b4_live_preflight`, `eval_for_preflight`, the `lib.rs` `.setup()` hook) and its on-disk WebView2 profile directory were removed/deleted after this run; none of it is present in the working tree as of this report.

## 10. Documentation

Updated: `browser_architecture.md` (new §2.8), `browser_security_model.md` (§2 corrected, §3/§5/§12 filled in), `browser_decision_log.md` (D28–D33 new; D27 correction preserved; OD-B5 annotated; OD-B13–B16 new), `browser_known_limitations.md` (new "As of Browser-B4" section), `browser_roadmap_b0_b10.md` (B4 marked COMPLETE with full evidence; B3's own stale D27 reference also corrected to match D27's already-fixed text), this report.

## 11. Git Diff (summary)

```
 apps/desktop/src-tauri/Cargo.lock                                |   1 +
 apps/desktop/src-tauri/Cargo.toml                                |   7 +
 apps/desktop/src-tauri/src/browser_policy.rs                     | 821 (new file)
 apps/desktop/src-tauri/src/browser_runtime.rs                    | 306 +++++++++++++++++++--
 apps/desktop/src-tauri/src/lib.rs                                |  42 ++-
 apps/desktop/src/features/browser/api.ts                        |  67 +++++
 apps/desktop/src/features/browser/components/BrowserApp.test.tsx |  11 +
 apps/desktop/src/features/browser/hooks/useBrowserTabs.ts        |  19 ++
 docs/architecture/browser_architecture.md                        |  13 +
 docs/architecture/browser_decision_log.md                        |  50 +++-
 docs/architecture/browser_known_limitations.md                   |  13 +
 docs/architecture/browser_roadmap_b0_b10.md                      |  20 +-
 docs/architecture/browser_security_model.md                      |  10 +-
```

`git diff --check`: clean (no whitespace/CRLF issues). Not staged or committed as of this report. Confirmed untouched: `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md` (pre-existing, unrelated uncommitted changes from before this phase began), `scratch/`.

## 12. CI-Readiness

Full local regression (Rust build/test/clippy/fmt, frontend typecheck/test) is clean, matching what `desktop-ci.yml`'s jobs check. Not yet pushed, so no real CI run exists for this phase yet. `OD-B6`'s pre-existing Linux-cross-compile-check gap (this Windows machine cannot `cargo check --target x86_64-unknown-linux-gnu` due to a missing system-library toolchain) continues to apply to B4's new `#[cfg(windows)]`/`#[cfg(not(windows))]`-paired code exactly as it did for B2/B3's.

## 13. Known Limitations

See `browser_known_limitations.md`'s new "As of Browser-B4" section for the full, disclosed list: no DNS-rebinding protection (OD-B16); popups/downloads/permissions all unconditionally denied with no confirmation UI (OD-B13/B14/B15); the live preflight's permission check did not independently re-observe the page-side outcome; `DownloadStarting` registration best-effort-degrades on a pre-`ICoreWebView2_4` runtime; OD-B5's automated-live-lifecycle-test gap continues (worked around via a live preflight against the real binary this phase, not resolved).

## 14. Deferred Risks

OD-B13 (popup-to-tab conversion), OD-B14 (download save-path confirmation/`browser.download` capability), OD-B15 (permission confirmation UI — architecture already supports it via `GetDeferral`), OD-B16 (DNS-rebinding bypass — no existing KORTEX precedent to build a fix on). None of these block B4 V1's own stated goal (deny-by-default baseline for all four action kinds); all are explicitly the responsibility of a later milestone.

## 15. Commit Recommendation

**READY.** No open BLOCKER/CRITICAL/HIGH findings. Full regression clean (Rust: 142/142 tests, 0 clippy warnings introduced, fmt clean for every touched file; frontend: typecheck clean, 839/839 tests). One real adversarial finding (trailing-dot `localhost.` bypass) was found and fixed with regression coverage, not merely noted. Live preflight evidence, not just unit tests, backs every one of the four denial mechanisms. Awaiting explicit user GO/NO-GO, commit authorization, and (separately) push authorization per this repository's milestone workflow.
