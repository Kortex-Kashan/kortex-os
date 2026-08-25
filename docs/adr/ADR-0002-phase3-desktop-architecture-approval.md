# ADR-0002: Approval of the Phase 3 Desktop Architecture Specification

Status: ACCEPTED
Date: 2026-08-25
Author: Claude Code (drafting), recording direct approval issued by the Chief Architect (KASHAN) in this session
Authority: Chief Architect (KASHAN)
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`) §20, §22; `docs/architecture/phase3_desktop_architecture.md` v0.1.0

---

## 1. Context & Problem Statement

A forensic, read-only audit of the repository (this session) established that Phase 3 ("Desktop Container & UI System") had zero implementation: `apps/desktop/`, `apps/server/`, and `design-system/` each contained only a `README.md`; `backend/src/kortex/api/` contained only a `README.md` and an empty `__init__.py`; no `package.json`, `Cargo.toml`, `tauri.conf.json`, `tsconfig.json`, or frontend source file existed anywhere in the repository.

Following that audit, `docs/architecture/phase3_desktop_architecture.md` (v0.1.0) was drafted as a full architecture specification for Phase 3, covering the Tauri v2 shell (M1), the React/TypeScript UI (M2), the IPC bridge (M3), and the KORTEX Design System (M4). That document was explicitly authored with Status `DRAFT — Proposed Architecture (Pending Chief Architect Approval)`, per `ARCHITECTURE_VERSION_1.0.md` §22's Change Management Policy, which requires any new or extending architecture to pass through Proposal → Discussion → Written ADR/Spec → Chief Architect Approval → Version Increment before it may be cited as binding.

This ADR is that approval step. It formally ratifies `phase3_desktop_architecture.md` v0.1.0 as the binding architecture for Phase 3, and is the authoritative written record of which of its decisions are approved outright, which remain deferred pending further input, and which candidate alternatives were formally rejected — as required by `docs/adr/README.md`'s ADR Process Lifecycle (steps 3–5) and by the precedent already established in `ADR-0001` (§6: "requires explicit Chief Architect approval... before its status may change to `ACCEPTED`").

---

## 2. Decision Drivers

- Phase 3 is the first phase where KORTEX OS's capability-driven, event-driven backend discipline (Article 6/7 of `AGENTS.md`; `platform_service_contracts.md`; `capability_registry.md`) is exposed to an external, untrusted presentation surface (a desktop webview) — the approval must not weaken any already-ratified backend invariant.
- `ARCHITECTURE_VERSION_1.0.0` §20 already names Phase 3's constituent technologies (Tauri v2, React 18+, TypeScript 5+, TailwindCSS) as approved stack elements in `.kortex/stack.md`; this ADR does not introduce new foundational technology choices, only ratifies the detailed design built on top of them.
- Avoidance of unnecessary dependencies and premature abstraction (per `AGENTS.md` / session operating instructions) must be visible in the record, not just the spec — hence the explicit "Rejected Alternatives" section below.
- Every open item the spec itself flagged as unresolved (`phase3_desktop_architecture.md` §21.3) must be dispositioned here: either resolved by this ADR, or explicitly carried forward as still-deferred, never silently dropped.

---

## 3. Considered Options

- **Option 1 (chosen)**: Approve `phase3_desktop_architecture.md` v0.1.0 as drafted, in full, with its own internally-flagged open items (§21.3) carried forward as deferred decisions rather than resolved by fiat in this ADR.
- **Option 2**: Approve with modifications — accept the architecture but overrule one or more of its technology decisions (e.g. mandate TanStack Router over React Router, or Redux over Zustand). Rejected — no technical objection to any individual decision in the spec was raised; each decision already carries a documented rationale and rejected-alternative comparison (spec §21.1), and re-litigating them here without new information would not improve the outcome.
- **Option 3**: Reject and return for rework, requiring re-scoping (e.g. demanding the backend presentation layer be fully implemented before any architecture is ratified). Rejected — the specification itself already identifies the backend presentation layer as a hard dependency and schedule risk (spec §18, §19); withholding architectural approval would not accelerate that work, it would only block Rust/React scaffolding (M1, M2, M4) that has no such dependency and can proceed in parallel.

---

## 4. Decision Outcome

**Chosen Option**: Option 1 — `docs/architecture/phase3_desktop_architecture.md` v0.1.0 is **APPROVED** in full as the binding Phase 3 architecture.

### 4.1 Approved Decisions

The following binding architectural decisions from the specification are ratified without modification:

1. **Network egress isolation**: the webview is granted no `http`/`websocket` capability; all backend communication (request/response and streaming) is mediated exclusively by the Rust shell process (spec §6.6, §11, §13).
2. **IPC contract reuse**: the IPC bridge transports the already-ratified `CapabilityRequest`/`UniversalResult`/`UniversalError` contracts (`platform_service_contracts.md`) unchanged; it does not define a parallel request/response system (spec §8.1–§8.3).
3. **Session token custody**: session tokens are held exclusively by the Rust process via OS-native secure credential storage; they never enter JavaScript-reachable storage (spec §8.4, §11.2).
4. **Backend-owned authorization**: RBAC/ABAC evaluation remains exclusively in the Security Engine at Kernel dispatch time; the frontend may reflect a permission for UX purposes only, never as enforcement (spec §8.5, §11.3).
5. **One uniform FastAPI presentation surface** (`kortex.api.main:app`) serves both the desktop shell (via Rust) and the headless `apps/server` mode — no separate Python-side "Tauri adapter" (spec §9.1).
6. **State management split**: Zustand for local/ephemeral UI state; TanStack Query for all server-derived state; the two are never mixed (spec §12).
7. **Routing**: React Router, in-memory/hash mode (spec §7.2).
8. **Component organization**: feature-based structure for `apps/desktop`; atomic/foundation structure only inside the shared `design-system` workspace package (spec §7.4).
9. **Design system foundation**: shadcn/ui vendored as source (not an installed runtime dependency); Motion.dev as the single animation library (spec §10.5).
10. **Component documentation**: an in-app, dev-build-only `/dev/components` gallery route, not Storybook (spec §10.7).
11. **Tooling**: pnpm workspaces, with `design-system` as a shared package consumed by `apps/desktop` and future KORTEX frontends; Vitest as the sole test runner (spec §7.1, §14, §15).
12. **Tauri permission model**: a single, narrowly-scoped `capabilities/default.json` granting only window/app lifecycle, a shell-exec grant scoped to the exact bundled sidecar binary path, and app-data-directory-scoped filesystem access — no broader grant (spec §6.6).
13. **Crash/update strategy**: bounded exponential-backoff sidecar restart (max 3 attempts) before surfacing a recovery screen; desktop updates ship as a single Ed25519-signed unit via Tauri's updater plugin, reusing the existing platform signature scheme rather than introducing a second one (spec §6.4–§6.5).
14. **Non-goals** (spec §20) are ratified as stated: no business-module screens, no AI chat UI, no marketplace/plugin UI, no installer/code-signing polish, no formal accessibility certification, no i18n, no telemetry dashboards, and no generic per-domain REST surface beyond the single `/capabilities/invoke` endpoint — all explicitly out of Phase 3 scope.

### 4.2 Deferred Decisions

The specification's own §21.3 flagged five unresolved items. Item 1 (ratification of the document itself) is resolved by this ADR. The remaining four are formally **carried forward as deferred** — they are not blocking for M1/M2/M4 scaffolding to begin, but must be resolved before the milestones they gate can be marked complete:

1. **Backend presentation layer scope boundary** — whether implementing `kortex.api.main:app`, `/capabilities/invoke`, `/events/stream`, and `/health` (spec §9) sits inside Phase 3's own milestone boundary (as M3, per the spec's working assumption) or must be sequenced as separate backend-track work landing first. **Blocks**: M3 completion criteria. Deferred pending explicit scope confirmation.
2. **Sidecar packaging tool choice** (PyInstaller vs. Nuitka vs. other) for freezing the Python backend into a distributable sidecar binary. **Blocks**: M1's production-mode (not dev-mode) completion criteria, and §16 Production Build Architecture. Deferred pending a packaging spike.
3. **`SESSION_EXPIRED` error taxonomy addition** — a proposed additive category alongside the existing ratified `UniversalError` categories in `platform_service_contracts.md`. Deferred pending explicit sign-off, since it touches a separately-ratified, protected specification that this ADR does not have standing to amend on its own.
4. **Documentation cross-referencing** — whether `docs/architecture/README.md`'s index and `.kortex/roadmap.md`'s Phase 3 checklist should be updated to reference `phase3_desktop_architecture.md` and this ADR. Deliberately left untouched by both the original spec-drafting task and this ADR (both were scoped not to modify other existing documents); flagged here as a follow-up action item requiring separate authorization, since `docs/architecture/*` and `.kortex/*` are protected paths.

Additionally, this ADR itself does not modify `phase3_desktop_architecture.md`'s own header (still reading `Status: DRAFT — Proposed Architecture (Pending Chief Architect Approval)`) — see §6 Compliance & Audit Verification.

### 4.3 Rejected Alternatives

The following alternatives, evaluated in the specification's Technology Evaluation Matrix (spec §21.1), are formally rejected and must not be introduced during implementation without a superseding ADR:

| Rejected | In favor of | Reason |
|---|---|---|
| Redux | Zustand | Centralized-store-plus-middleware ceremony is unjustified for Phase 3's relatively small, cohesive local UI-state surface; no requirement for time-travel debugging or a middleware pipeline exists (spec §12.1). |
| TanStack Router | React Router | Would duplicate data-loading concepts already owned by TanStack Query, adding configuration surface with no offsetting benefit for a desktop app with no URL bar or deep-linking requirement (spec §7.2). |
| Anime.js | Motion.dev | A second animation runtime is redundant; one library is sufficient and more idiomatic for a React component tree (spec §10.5). |
| Storybook | In-app `/dev/components` gallery route | Adds a second build/tooling pipeline with no matching benefit at Phase 3's component-count scale (spec §10.7). |
| Jest | Vitest | Redundant given Vite is already the bundler and Vitest is Vite-native (spec §14). |
| Bare React Context (as the sole state mechanism) | Zustand | Lacks selector-based partial subscriptions, causing broad re-renders as the app grows (spec §12.1). |
| Jotai | Zustand | Its atomic model fits fine-grained, independently-composed state better than Phase 3's relatively small, cohesive UI-state surface; would add conceptual overhead without a matching benefit (spec §12.1). |
| 21st.dev / Aceternity UI / Skiper UI as runtime dependencies | Reference-only use during design ideation, reimplemented inside the vendored `design-system` package | These are inspiration galleries, not maintained installable packages with a security review trail; importing them live would violate the vendored-ownership model already adopted for shadcn/ui (spec §10.5). |
| Haikei / Grainient / Blobmaker as runtime/API dependencies | Reference-only, design-time static asset generation | Live third-party API calls at runtime would violate the offline-first principle (spec §10.5). |

### 4.4 Phase 3 Milestone Structure

The following four milestones (spec §17) are the ratified execution structure for Phase 3. No additional milestones are introduced, and none of the four is descoped:

| Milestone | Purpose | Key Dependency | Completion Criteria (summary) |
|---|---|---|---|
| **M1 — Tauri v2 Desktop Shell** | Window lifecycle, sidecar process supervision, secure IPC transport, OS integration — container only, zero business logic. | None blocking to start; production-mode completion requires the sidecar packaging decision (§4.2 item 2). | App launches in dev/prod; supervises the Python sidecar with verified crash/restart behavior; permission file grants exactly the scope in spec §6.6, nothing more; clean shutdown with no orphaned processes. |
| **M2 — React + TypeScript UI** | Application shell, routing (React Router), state foundation (Zustand + TanStack Query), feature-based structure. | M1 (a host to render inside); Design System (M4) at least at token level before real screens are styled. | App builds and renders inside the Tauri shell in dev and production; routing/state wired and demonstrated with one real end-to-end capability call (shared with M3); lint/type-check pass. |
| **M3 — IPC Bridge** | The single capability-contract-compliant transport connecting the UI to the Kernel Capability Dispatcher. | M1 (Rust host); the backend presentation layer (spec §9) — **currently fully unimplemented, the largest schedule risk** (§4.2 item 1). | A full round trip succeeds end-to-end through a real engine; an event-stream scenario updates the UI without manual refresh; session token never observably present in webview-accessible storage; a permission-denied scenario fails correctly end-to-end. |
| **M4 — KORTEX Design System** | Shared, reusable design tokens, themes, and foundation components, consumed by `apps/desktop` and future KORTEX frontends via pnpm workspace. | Should reach at least token + a few components before M2 styles real screens; otherwise independent of M1/M3. | Tailwind + token configuration committed; all eleven foundation components (spec §10.3) implemented with co-located tests and demonstrated reuse across ≥2 screens; dark mode verified visually and via contrast checks; consumed via workspace protocol, not copy-pasted. |

---

## 5. Architectural Impacts & Consequences

### Positive Consequences

- Phase 3 now has a single, ratified architectural reference; implementation work (Rust scaffolding, React scaffolding, Design System package) can begin against a fixed target instead of the prose-only intent previously scattered across `apps/desktop/README.md`, `apps/server/README.md`, and `.kortex/stack.md`.
- The network-egress-isolation decision (§4.1 item 1) gives Phase 3 a security posture stronger than a typical Tauri+webview default (no `http`/`websocket` grant to the webview at all), closing off an entire class of supply-chain-compromise risk before any frontend dependency is even installed.
- Every "TBD" in `apps/desktop/README.md` (state management was explicitly marked TBD) is now resolved and recorded, removing a source of ambiguity that would otherwise have been decided ad hoc during implementation.
- The four still-deferred items (§4.2) are explicit and trackable rather than implicit — future sessions can locate exactly what remains open without re-deriving it from the full specification.

### Negative Consequences / Trade-offs

- Approving the architecture does not unblock M3: the backend presentation layer (`kortex.api.main:app` and its endpoints) still does not exist, and this ADR does not authorize or perform that implementation work — it only ratifies the design M3 must conform to once built.
- Two decisions (sidecar packaging tool, `SESSION_EXPIRED` taxonomy) remain genuinely open and require follow-up spikes/sign-off before their respective milestones can close; approving the architecture around them is a calculated choice to unblock the majority of Phase 3 rather than wait on every last detail.
- `phase3_desktop_architecture.md`'s own header still reads `DRAFT` pending a separate, explicitly-authorized edit (see §6) — a reader of that file in isolation, without also checking this ADR, would not see it reflected as approved.

---

## 6. Compliance & Audit Verification

- `docs/architecture/phase3_desktop_architecture.md` requires no modification for this approval to take effect — this ADR is the authoritative record of its ratification, per the same pattern established in `ADR-0001` and in `ADR-0000`'s ratification of `ARCHITECTURE_VERSION_1.0.0` (neither required editing the target document's own header to take effect).
- A future editorial pass may update that document's Status line to read `Status: ACCEPTED — Ratified by ADR-0002` for internal consistency; that edit is deferred (§4.2 item 4) because `docs/architecture/*` is a protected path under the operational governance rules and was outside this task's explicitly authorized scope (which named only "Create ADR-0002" and "Do not modify implementation files").
- `docs/adr/README.md`'s "Index of Approved ADRs" table is updated by this same change to add the `ADR-0002` row — this is ADR governance bookkeeping (the table's own stated purpose), not an implementation-file modification, and follows the exact indexing precedent already set for `ADR-0000`/`ADR-0001`.
- No `docs/architecture/*`, `.kortex/*`, `AGENTS.md`, or backend/frontend implementation file was modified by this ADR. Only `docs/adr/` (this file and the index) was touched.
- No automated test coverage requirement applies to this ADR — it approves a specification document, not executable code; the coverage/test obligations recorded in `phase3_desktop_architecture.md` §14 (Testing Strategy) apply to the eventual implementation of M1–M4, not to this approval record.
