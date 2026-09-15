# KORTEX OS — Integration Hub M2 Implementation Report
## GitHub OAuth Connector

---

### 1. Executive Summary

**IMPLEMENTATION COMPLETE — PENDING FULL REGRESSION SIGN-OFF AND CHIEF ARCHITECT GO/NO-GO.**

Implements the flow `GitHub OAuth → IntegrationOAuthManager → SecretStore → GitHub curated
descriptor → ConnectorEngine → RegistryEngine → F6 CapabilityProjection → CapabilityDispatcher →
HttpRestConnectorDriver → GitHub REST API`, following M1's dynamic per-profile capability
pattern (`kortex.mcp.<profile_id>.<tool_name>`) rather than F5's boot-time-fixed one: a GitHub
`ConnectorProfile` registers/unregisters its own capabilities (`kortex.connector.<profile_id>.
<action>`, `owner_id = profile_id`) on connect/disconnect, exactly like an MCP profile does.

Every existing hop in that chain — `ConnectorPipeline`, `RegistryEngine`, `CapabilityProjection`,
`CapabilityDispatcher`, `HttpRestConnectorDriver` — is reused unmodified except one additive,
backward-compatible signature change (`secret_resolver` grows a third `profile_id` parameter).
No new registry, dispatcher, or HTTP execution path was introduced.

The plan for this milestone went through **three rounds of explicit architecture correction**
from the Chief Architect before implementation began — see §3. The corrected design is
materially different from, and safer than, the first draft on every point that was corrected.

### 2. Scope

- GitHub OAuth App (Authorization Code flow, `offline_access` requested for expiring-token
  support), not a GitHub App installation.
- Semantic actions: `user_get`, `repo_get`, `issues_list`, `issue_create`.
- New: `IntegrationOAuthManager` (SecurityEngine-owned), GitHub curated descriptor catalog
  (ConnectorEngine-owned), one new ConnectorEngine capability
  (`kortex.connector.integration.disconnect`), three new SecurityEngine capabilities
  (`kortex.security.integration_oauth.begin/complete/status`), desktop "Connect with
  GitHub"/"Disconnect" UI.
- Explicitly out of scope (per the corrections): boot-time-fixed global GitHub capabilities, a
  second registry/dispatcher/HTTP execution path, GitHub App installation tokens, any change to
  M1's MCP surface.

### 3. Architecture corrections (three rounds, before any code was written)

**Round 1** — my first draft named capabilities `kortex.connector.github.<resource>.<action>`
(provider-global) and placed `IntegrationOAuthManager` under `ConnectorEngine`. Corrected to:
runtime capability identity is per-profile (`kortex.connector.<profile_id>.<action>`, `owner_id
= profile_id`) — structurally identical to M1's MCP pattern; static descriptor catalog and
dynamic runtime registration are kept separate; `IntegrationOAuthManager` belongs under
`SecurityEngine` (owns OAuth state security, credential metadata, `SecretStore` coordination,
token lifecycle) — `ConnectorEngine` keeps capability lifecycle; OAuth `state` must be
signed + expiring + tenant/profile/provider-bound + a cryptographically strong nonce +
**atomically single-use**, never a plain signed-but-reusable token; `SecretStore`'s handle must
be opaque, never a client-derivable string like `connector/<profileId>` — `(tenant_id,
profile_id)` is the identity boundary, the handle only a lookup key.

**Round 2** — three remaining gaps in the corrected draft:
1. *Disconnect wasn't backend-authoritative.* Splitting it across two independently
   frontend-sequenced capability calls (`integration_oauth.disconnect` then
   `deleteConnectorProfile()`) couldn't guarantee "successful disconnect → no executable
   capabilities remain registered" under a crash or a dropped second request. Fixed: a single
   `kortex.connector.integration.disconnect` capability, owned by `ConnectorEngine`, that
   unregisters capabilities **first, unconditionally**, before ever touching the credential.
2. *Credential resolution didn't bind to the profile.* A `(secret_handle, tenant_id)` resolver
   only proves "this tenant owns some secret at this handle," not "this profile is authorized to
   use it" — a forged/misassigned `ConnectorProfile.secret_handle` could otherwise hand one
   profile another's GitHub token within the same tenant. Fixed: `secret_resolver` grows a third
   `profile_id` parameter; `IntegrationOAuthManager.resolve_access_token` looks up the
   authoritative `OAuthIntegrationCredentialRecord` by `(tenant_id, profile_id)` first and only
   returns a token if the record's own handle matches what was passed.
3. *OAuth principal/session binding wasn't explicitly determined.* Resolved by finding that
   `SecurityEngine.oauth_link_complete_capability` (Phase A) already hard-enforces an identical
   principal/tenant match over the *same* desktop deep-link transport — direct, already-shipped
   precedent that session identity survives that round-trip reliably. `complete_authorization`
   therefore hard-requires the match (not the softer, logged-only design first proposed), and the
   underlying CSRF/replay protection (signed + nonce + tenant/profile/provider-bound + atomic
   single-use) does not depend on it either way.

**Round 3** — GitHub token-lifecycle amendment: explicit `offline_access` scope request;
`IntegrationTokenSet` carries every field GitHub's token endpoint may return
(`access_token`/`refresh_token`/both expiry timestamps/`scope`/`token_type`), each mapped to
`None` rather than guessed when GitHub omits it; mandatory refresh-token **rotation** (the old
refresh token is discarded and never reused after a successful rotation); per-`(tenant_id,
profile_id)` refresh synchronization (an `asyncio.Lock` plus an optimistic compare-and-swap on
the stored refresh-token fingerprint as multi-process defense-in-depth); explicit
`REAUTHORIZATION_REQUIRED` status transition on a missing/expired/provider-rejected refresh
token, rather than ever silently returning an unusable token.

Full plan text (all three rounds): `C:\Users\KOBRA\.claude\plans\calm-coalescing-moon.md`
(session-local; not part of this repository).

### 4. New components

| Component | File | Owner |
|---|---|---|
| `IntegrationOAuthManager` | `kortex/engines/security/integration_oauth_manager.py` | SecurityEngine |
| `GitHubIntegrationOAuthProvider` | `kortex/engines/security/oauth/github_provider.py` | SecurityEngine |
| `IIntegrationOAuthProvider` protocol | `kortex/engines/security/oauth/base.py` | SecurityEngine |
| `oauth_state_codec` (shared with Phase A) | `kortex/engines/security/oauth_state_codec.py` | SecurityEngine |
| `OAuthIntegrationCredentialRecord`, `OAuthStateNonceRecord`, `IntegrationTokenSet`, `IntegrationCredentialStatus` | `kortex/engines/security/models.py` | SecurityEngine |
| GitHub curated descriptor catalog + dynamic register/unregister | `kortex/engines/connector/github_actions.py` | ConnectorEngine |
| `ConnectorEngine.disconnect_integration()` (`kortex.connector.integration.disconnect`) | `kortex/engines/connector/engine.py` | ConnectorEngine |
| Desktop "Connect with GitHub" flow | `apps/desktop/src/auth/githubConnectorOAuth.ts`, `useConnectorOAuthDeepLink.ts`, `apps/desktop/src/features/connectors/components/GitHubConnectionForm.tsx` | Desktop |

`AuthenticationManager.issue_oauth_state`/`verify_oauth_state` (Phase A, unmodified in behavior)
gained one new optional field round-tripped through the signed payload: `profile_id`, set only
for `intent="connector_link"`. `OAuthStatePayload` and the codec were both updated to carry it;
every pre-existing intent (`"login"`, `"link"`) leaves it `None` and is unaffected.

### 5. Backend wiring changes (all additive)

- `ConnectorEngine.__init__`/`initialize()`: `secret_resolver` now wires to `security_engine.
  integration_oauth_manager.resolve_access_token` (a superset of the old `security_engine.
  get_secret` — passes a plain, non-OAuth secret through unchanged) instead of `get_secret`
  directly; a new `integration_credential_revoker` callable wires from `...
  .disconnect_credential`, mirroring the existing injection style.
- `ConnectorEngine.register_profile()`/`delete_profile()`: a new branch, parallel to the
  existing MCP branch, triggered by `profile.options.get("integration_provider") == "github"` —
  registers/unregisters the four GitHub capabilities via a direct `RegistryEngine` reference
  (never `Kernel.register_capability`, which rejects registration once booted).
- `ConnectorPipeline.execute()`: the `secret_resolver` call site passes `profile.profile_id` as
  a third argument.
- `RegistryEngine._CAPABILITY_RISK_CLASSIFICATION`: four new entries
  (`kortex.connector.integration.disconnect`, `kortex.security.integration_oauth.begin/
  complete/status`).
- `SecurityEngine`: constructs `IntegrationOAuthManager` in `initialize()`; registers the three
  new capabilities through the existing canonical-capability loop (no bespoke registration code
  path).
- `GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` — new configuration keys, documented in
  `docker/.env.example`, read via `kernel.get_config(...)` exactly like the existing
  `GOOGLE_OAUTH_CLIENT_ID` pair. A GitHub connection is offered only when both are set.

`kernel_bootstrap.py` required **zero changes** — every piece of wiring above happens inside the
engines' own `initialize()`/capability-registration paths, the same place M1's MCP wiring
already lives.

### 6. Tests

36 new backend tests, 6 new frontend tests, plus updates to 7 pre-existing test files whose
fixtures needed to reflect the additive signature/capability-set changes (`test_connector_
pipeline.py`, `test_security_engine.py`, `test_workflow_connector_integration.py`,
`test_connector_engine_integration.py`, `test_capability_metadata_completeness.py`,
`McpConnectionForm.test.tsx`, `ConnectionsTab.test.tsx`).

- `backend/tests/unit/test_integration_oauth_manager.py` (13) — begin/complete happy path,
  tampered/mismatched-profile/mismatched-principal state rejection, **atomic single-use
  consumption proven under `asyncio.gather`** (two concurrent completions of the same `state`
  resolve to exactly one success), plain non-OAuth secret pass-through, cross-profile handle
  binding refusal, idempotent disconnect.
- `backend/tests/unit/test_integration_oauth_token_lifecycle.py` (7) — non-expiring token
  compatibility, unexpired-token no-refresh-call, expired-token refresh + rotation (old refresh
  token never reused), missing/expired/provider-rejected refresh token → `REAUTHORIZATION_
  REQUIRED`, **concurrent resolution past expiry refreshes exactly once** (proven under
  `asyncio.gather`, not merely asserted from reading the code).
- `backend/tests/unit/test_github_actions.py` (13) — descriptor catalog shape, profile-scoped
  register/idempotent-re-register/unregister against a real `RegistryEngine`, two profiles never
  cross-affect each other's capabilities, handler behavior (fixed `user_get` URL injection,
  caller-supplied `url` forwarding, failure surfacing).
- `backend/tests/integration/test_github_connector_oauth_integration.py` (3) — the full chain
  through real `Kernel.invoke_capability()` dispatch (begin → complete → profile.register →
  dispatch a dynamically-registered capability against a mocked `HttpRestConnectorDriver` call →
  disconnect → capability confirmed gone, idempotent on a second call), cross-profile credential
  isolation within one tenant, cross-tenant credential isolation.
- `apps/desktop/.../GitHubConnectionForm.test.tsx` (6) — required-fields UX, begin/open-browser,
  begin-failure surfacing, deep-link-match → complete → register → `onSuccess`, deep-link
  state-mismatch is ignored, complete-failure surfacing without registering a profile.

### 7. Verification performed

1. `pytest backend/tests/unit/test_integration_oauth_manager.py backend/tests/unit/
   test_integration_oauth_token_lifecycle.py backend/tests/unit/test_github_actions.py -v` —
   33/33 passed.
2. `pytest backend/tests/integration/test_github_connector_oauth_integration.py -v` — 3/3
   passed.
3. Full backend regression (`pytest backend/tests -q`) — see §8.
4. `tsc --noEmit` (desktop) — clean. `vitest run src/features/connectors src/auth` — 169/169
   passed (163 pre-existing + 6 new), zero regressions.
5. No live-browser click-through: this flow is a Tauri-specific OAuth Authorization Code +
   custom-URI-scheme deep-link round-trip requiring a real GitHub OAuth App and the native
   desktop shell — not something the web preview tooling can exercise. Verification is via the
   automated test suites above (unit + integration + component), matching this repo's existing
   precedent for the identical constraint on the Phase A SSO flow (`OAuthLoginButtons.tsx`/
   `useOAuthDeepLink.ts` also have no live-browser test coverage, only component tests with the
   deep link mocked).

### 8. Dependency-chain review

Per CLAUDE.md's mandatory dependency-chain rule, after implementing the resolver signature
change and the new capabilities, the following were checked:

- **Callers/consumers of `secret_resolver`**: grepped every construction site across
  `backend/tests/` and `backend/src/`. Two production wiring points (`ConnectorEngine.
  initialize()`, the only place a resolver is assigned in production) and eight test files
  constructing a resolver directly. All eight test-fixture resolvers were 2-arg lambdas/`async
  def`s that would raise `TypeError` under the new 3-arg call site — every one was updated to
  accept (and, where relevant, ignore) the new `profile_id` parameter. Two call sites already
  used `AsyncMock` (accepts any arity) and needed no change.
- **Bypass paths around `options.integration_provider`**: the only two places that read it
  (`ConnectorEngine.register_profile`/`delete_profile`/`disconnect_integration`) are the only
  places capability registration lifecycle is triggered from; nothing else inspects
  `ConnectorProfile.options` to make a security decision.
- **Tenant/profile isolation**: proven, not merely reasoned about — see the two isolation tests
  in §6's integration suite, plus the unit-level binding-mismatch test.
- **Persistence effects**: two new tables (`security_integration_oauth_credentials`,
  `security_integration_oauth_state_nonces`), both auto-created via the existing `Base.metadata.
  create_all()` boot path — no Alembic migration required, matching every other Security Engine
  table added since M2's `SecretRecord`.
- **Concurrency**: the single-use state consumption and the refresh-rotation synchronization are
  each proven under `asyncio.gather`, not asserted from code reading alone (§6).
- **Security boundaries**: `resolve_access_token` never returns a token without confirming the
  `(tenant_id, profile_id)` → `OAuthIntegrationCredentialRecord` → `secret_handle` chain matches
  what was requested; `disconnect_integration` unregisters capabilities before revoking anything,
  closing the specific gap Round 2 of the corrections identified.
- **Downstream consumers of capability metadata**: `test_capability_metadata_completeness.py`'s
  independent, hand-maintained `_EXPECTED_RISK` table (a deliberate drift-detection gate,
  distinct from the production `_CAPABILITY_RISK_CLASSIFICATION` table it doesn't re-import) did
  not yet know about the four new capabilities — updated; this is exactly the kind of gap that
  test exists to catch, not a defect in it.

### 9. Pre-existing, unrelated defects encountered (not introduced by this milestone)

Final full regression: `pytest backend/tests -q` → **4023 passed, 4 failed, 2 skipped** (41m51s).
All 4 failures are pre-existing and unrelated to Integration Hub M2:

- **3 failures**, `tests/integration/test_update_integration.py`
  (`test_end_to_end_successful_update_lifecycle`, `test_recovery_delegation_on_post_mutation_
  failure`, `test_checkpoint_failure_aborts_before_any_destructive_mutation`) — identical root
  cause: a hardcoded test-fixture update manifest with `expires_at: "2026-09-12T00:00:00Z"`, now
  in the past relative to real wall-clock time. Verified on the **unmodified `main` baseline**
  via `git stash`: fails identically with zero code changes. A stale test-fixture date, unrelated
  to the Auto-Update engine's actual logic. Formally recorded as **`DEFECT-003`** in
  `docs/release/rc-testing/KNOWN_FINDINGS.md` (distinct from `DEFECT-002` below).
- **1 failure**, `tests/unit/test_workflow_durability.py::test_restart_recovery_ready_and_
  approved_workflows` — this is **`DEFECT-002`**, the exact same optimistic-lock race condition
  on durability restart recovery already classified as a PRE-EXISTING BASELINE DEFECT in M1's
  own acceptance record (`.kortex/roadmap.md`, M1 section: "Backend full suite: 1 pre-existing
  failure (`test_restart_recovery_ready_and_approved_workflows`)"). Evidently intermittent/timing
  -sensitive (did not reproduce in an earlier, smaller partial run this milestone) — already
  known, already deferred, untouched by this milestone's changes.

Both are deferred for separate investigation outside this milestone's boundary, per the same
precedent M1 already established.

### 10. Commit / push

Not yet committed or pushed — pending this report's review and explicit COMMIT AUTHORIZATION per
CLAUDE.md's milestone workflow.
