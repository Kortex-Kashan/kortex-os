# KORTEX Browser — Authentication Architecture

**Status**: Living document — established Browser-B0 (current-state audit + future direction). "Web Account" auth method is future work (B7), not implemented in B0/B1.

## 1. The non-negotiable principle

Provider, authentication method, and model are three separate concepts and must never be collapsed into one. Example: Provider = Gemini; Authentication = API Key today, Web Account in the future; Model = dynamically discovered. Existing API-key provider configuration must remain fully intact and unaffected by anything in this document.

## 2. Current state (as of Browser-B0 audit)

### 2.1 Provider Registry

`ProviderRegistry` (`backend/src/kortex/engines/ai/registry.py:67`) is a thread-safe, purely in-memory, process-global catalogue keyed by `provider_id` — explicitly documented as never becoming a tenant credential store. `BaseAIProvider` (`ai/base_provider.py:30`) is the contract every provider implements (OpenAI, Anthropic, Gemini, OpenRouter, Ollama), each constructing a fixed `AIProviderMetadata`.

### 2.2 Credential storage

`AIProviderConfig` (`ai/models.py:97`) is the tenant-scoped config row: `(tenant_id, provider_id)` identity, a `secret_handle` (never plaintext), `enabled`, `default_model`. The actual secret lives in `SecretStore` (`engines/security/secrets.py:78`) — AES-256-GCM encrypted, AAD-bound to `(tenant_id, secret_handle)` (`_build_aad`, `secrets.py:159-179`), so two tenants' identically-named secret handles are mutually undecryptable. `TenantCredentialResolver` (`credentials.py:120`) resolves plaintext **per request, with no caching**, and the plaintext field is `repr=False` and dropped after use. The frontend never receives the handle — only a `hasCredential: boolean` (`ai-studio/types.ts`).

### 2.3 Auth method is currently a fixed, provider-level constant — not a per-tenant runtime choice

`CredentialRequirement` (`ai/models.py:35`) is a `Literal["none","api_key","bearer_token","oauth","custom"]` field **on `AIProviderMetadata`**, hard-coded once at each provider's construction (`credential_requirement="api_key"` at `anthropic_provider.py:249`, `gemini_provider.py:230`, `openai_provider.py:175`, `openrouter_provider.py:90`; `"none"` at `ollama_provider.py:91`). Notably, `"oauth"` is already a valid enum value that **no current provider uses** — the type already anticipates a non-API-key path, but there is no separate `AuthMethod` model, no per-tenant "which method did I authenticate with" field, and `AIProviderConfig` only ever carries one `secret_handle` slot today. This is the concrete gap "Web Account" auth (B7) must close without breaking anything above it.

### 2.4 Existing OAuth deep-link precedent — partially reusable

`oauthCapability.ts` (`apps/desktop/src/auth/oauthCapability.ts`) is Phase A **account sign-in** (Google/Microsoft) — unrelated to AI provider auth — and the same generalized mechanism is reused a third time for GitHub **connector** OAuth (`githubConnectorOAuth.ts`). Flow: `beginOAuthLogin()`/`beginOAuthLink()` call backend capabilities (`kortex.security.oauth.login_begin`/`.link_begin`) returning `{authorization_url, state}` (`security/engine.py:1555-1558`); the frontend calls `openUrl()` from `@tauri-apps/plugin-shell`, which opens the **OS's default external browser** — not an embedded/controlled window. The callback returns via the `kortex-auth://` custom URI scheme, a registered Tauri deep link (`tauri.conf.json:25-26`), caught by `useOAuthDeepLink.ts:20-57`, which never trusts URLs outside the `kortex-auth:` protocol. The backend verifies a signed state token (`AuthenticationManager.verify_oauth_state`) before exchanging the code.

**What transfers to B7, and what doesn't:**

- ✅ Reusable as-is: the deep-link callback-catching mechanism and the signed-state verification pattern.
- ❌ Not reusable as-is: the window-opening half. Today's flow deliberately opens the OS default browser via `plugin-shell`'s `open()` — exactly the "external, uncontrolled window" B7 must *not* use. A controlled authentication window hosted inside KORTEX Browser is genuinely new work, not a reuse of this precedent.

## 3. Future direction for B7 (design intent only — not built in B0/B1)

1. Add a distinct `auth_method` field to `AIProviderConfig` (`ai/models.py:97`), e.g. `Literal["api_key","web_account"]`, **defaulting to `"api_key"`** — every existing row remains valid with zero migration risk. Do not repurpose `credential_requirement`, which is declarative provider-level metadata about what a provider *supports*, not a per-tenant runtime selection. See `browser_decision_log.md` D6.
2. Persist a Web Account's session/state blob through the same `SecretStore`/`provider_secret_handle()` primitive used for API keys today — same AEAD envelope, same tenant-bound AAD, just a different logical handle namespace (e.g. `kortex/ai/providers/{id}/web_session`), resolved through the same no-cache `TenantCredentialResolver.resolve()` contract. This is "KORTEX remembers the connected provider account/session state," never "KORTEX extracts and replays cookies" — the distinction the task brief requires is enforced by *what* gets stored (an opaque, KORTEX-issued reference to session state, never a raw browser cookie jar replayed as an unofficial API), not by the storage mechanism itself.
3. Host the controlled authentication window as a new KORTEX Browser capability (a dedicated, narrowly-scoped browsing surface — not the general-purpose browser tab a user browses the open web in), reusing only the callback/state-verification half of the existing OAuth precedent (§2.4).
4. `BaseAIProvider`'s existing `test_connection()`/`discover_models()` hooks (`base_provider.py:73,95`) are already credential-shape-agnostic (`credential: str | None`) — a Web Account-authenticated provider can reuse this exact interface if the resolved "credential" is redefined as an opaque session reference. No change to `ProviderRegistry` or `BaseAIProvider` itself is anticipated.
5. Leave `ModelRouter`/model discovery untouched — it already treats "does this provider have a resolvable credential" as a black box via `TenantCredentialResolver`, independent of which auth method produced it.

## 4. Hard constraints (from the task brief, restated as architecture constraints)

KORTEX must not: collect provider passwords in its own forms; extract private session tokens; replay private cookies as an unofficial API; bypass CAPTCHA; bypass Turnstile; bypass authentication controls; or create an unofficial provider API. Provider-specific web adapters (B7+) are future work requiring separate review before implementation — not authorized by this document.

## 5. Not yet answered

The exact `web_session` blob schema, session expiry/refresh semantics, and per-provider adapter review process are all deferred to B7 design.
