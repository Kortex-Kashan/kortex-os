# KORTEX Browser — Vision

**Status**: Living document — established in Phase B0 (Architecture Audit + Documentation Foundation), maintained through every subsequent Browser phase (B1–B10). See `browser_decision_log.md` D2 for why phases are always written "Browser-B0" etc. in full outside this document set.

## What this is

KORTEX Browser is a built-in, embedded web browsing surface inside the KORTEX OS desktop application — a first-class KORTEX capability and runtime, not an AI provider, not a testing tool, and not the pre-existing "browser automation" concept already declared out of scope for desktop UI automation in Phase 6.

It activates a long-deferred roadmap line item: `.kortex/roadmap.md:383` records that "the Built-in Browser... remain[s] exactly as previously deferred elsewhere in this repository's documentation and [is] not promoted into release blockers" by the most recent RC reconciliation pass. This project (Browser-B0 onward) is that deferred item's formal architecture and implementation track — intentionally scoped as post-RC, non-blocking work, not an emergency addition to the current release candidate. See `browser_decision_log.md` D3.

## What it enables

1. **Human browsing** — a user opens and drives a real Chromium-class browser surface inside KORTEX, with tabs, navigation, and normal browsing UX (B2), backed by persistent, isolated per-tenant profiles (B3).
2. **AI-assisted browsing** — the KORTEX AI Engine can observe and act on a browser surface through a small set of governed, auditable actions (`browser.navigate`, `.read`, `.click`, `.type`, `.extract`, `.download`, `.screenshot`), gated by the same approval/governance machinery that already governs every other mutating AI action in KORTEX (B5, B6).
3. **Provider web sign-in (future, B7 only)** — a controlled authentication window lets a user sign into an AI provider's own website (Google, Microsoft, etc.) so KORTEX can remember "the connected provider account/session state," strictly as a second authentication method alongside today's API-key auth — never a password collector, never a cookie-replay mechanism, never a replacement for API keys.

## What it explicitly is not

- **Not an AI provider.** OpenAI, Anthropic, Gemini, and OpenRouter remain AI providers (`backend/src/kortex/engines/ai/`); Browser is a runtime/capability the AI Engine can use, symmetrical to how the Desktop Agent's `kortex.desktop.*` capabilities are used today — never a competing concept in the provider registry.
- **Not "browser automation" in the Phase 6 sense.** `docs/architecture/phase5_locked_architecture_spec.md:32` lists "Browser automation (Playwright, Puppeteer)" as an explicit Phase 6 non-goal for *desktop UI automation* — a decision about what replaced it (FlaUI/UIA3 via the .NET Desktop Agent), unaffected by this project. Playwright/Stagehand-style tools may still appear later purely as *test/automation adapters against KORTEX Browser itself* (see `browser_architecture.md` §5) — a different question from what this project builds. See `browser_decision_log.md` D4.
- **Not a credential harvester.** KORTEX must never collect provider passwords in its own forms, extract private session tokens, replay private cookies as an unofficial API, or bypass CAPTCHA/Turnstile/authentication controls. See `browser_auth_architecture.md`.
- **Not the AI agent's security boundary.** Browser Policy — not the AI Browser Agent — is the security boundary. See `browser_security_model.md`.

## Guiding principles (carried through every phase)

1. The rest of KORTEX depends on `IKortexBrowserRuntime`, never on WebView2 (or any future concrete runtime) directly.
2. An arbitrary website loaded in the browser never automatically gains access to native KORTEX capabilities.
3. Provider, authentication method, and model remain three separate concepts — never collapsed into one.
4. Documentation is part of implementation: every phase updates this vision doc and its companions before being considered complete.

## Roadmap

See `browser_roadmap_b0_b10.md` for the full Browser-B0–B10 phase breakdown.
