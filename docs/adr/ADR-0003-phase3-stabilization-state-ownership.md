# ADR-0003: Phase 3 Stabilization — Panel-State Persistence Consolidation, Implementation-Tracking Slices, and State Ownership Clarification

Status: ACCEPTED
Date: 2026-08-26
Author: Claude Code (Phase 3 stabilization engineer), recording direct approval issued by the Chief Architect (KASHAN) in this session
Authority: Chief Architect (KASHAN)
Reference Architecture: `docs/architecture/phase3_desktop_architecture.md` v0.1.0; ADR-0002 (its ratification)

---

## 1. Context & Problem Statement

A read-only architectural audit of the Phase 3 desktop implementation (this session, preceding this ADR) identified three related documentation/consistency gaps, none of which required a new architectural decision on their own — each is a clarification or a bug-level correction of an already-ratified decision (ADR-0002):

1. **Duplicate panel-layout persistence.** `SessionProvider`'s `SessionSync()` mirrored `PanelProvider`'s open-panel-ids/sizes into the session document (`kortex.session.v1`, via a `panelState` field on `KortexSession`) on every panel-state change, while `PanelProvider` independently persisted the exact same data to its own `kortex.panels.v1` key (`panels/panelPersistence.ts`). Two independently-written copies of the same data, with no ordering guarantee between them, is a drift risk with no corresponding benefit — nothing in the codebase ever read `session.panelState` back out again; `PanelProvider` always restores from its own key.
2. **Untracked implementation-slice labels.** Code comments and task briefs across `apps/desktop/src` refer to "M2.1" (Desktop Shell), "M2.2" (Workspace Runtime), "M2.3" (Application Navigation), "M2.4" (Panel System), and "M2.5" (Session Management) — none of which exist in ADR-0002's ratified milestone structure (which names only M1–M4, spec §17/ADR-0002 §4.4). Left unaddressed, a future reader could reasonably (and incorrectly) conclude these are new, separately-ratified milestones requiring their own approval, or that ADR-0002's milestone structure has silently drifted.
3. **Incomplete state-ownership decision record.** ADR-0002 §4.1 item 6 ratifies spec §12.1's two-bucket state split (Zustand for local/ephemeral UI state; TanStack Query for server-derived state) with a hard rule that the two are never mixed. In practice, Phase 3's `WorkspaceProvider`/`PanelProvider`/`SessionProvider` implement a third pattern — React Context wrapping a plain, instantiable manager/registry class (`WorkspaceRegistry`, `PanelRegistry`, `SessionManager`) — that fits neither bucket. This is a reasonable, already-working pattern, but its absence from the ratified decision record left a real gap: nothing on record said where domain-runtime, provider-scoped, registry-shaped state was supposed to live, or why Zustand/TanStack Query aren't the right fit for it.

This ADR resolves item 1 as an implementation fix (already applied in this stabilization pass — see `apps/desktop/src/session/sessionTypes.ts`, `sessionStorage.ts`, `SessionManager.ts`, `SessionProvider.tsx` and their tests) and formally records items 2 and 3 as clarifying addenda to ADR-0002. None of the three changes anything ADR-0002 already ratified; each closes a gap ADR-0002 left open or corrects a defect discovered after the fact.

---

## 2. Decision Drivers

- AGENTS.md's Critical Dependency-Chain Rule: a remediation (removing the panel-state mirror) must not be treated as complete without checking related invariants, persistence effects, and downstream consumers — this ADR is that check, made durable.
- AGENTS.md's Roadmap Discipline: informal implementation-tracking labels (M2.1–M2.x) must not be mistaken for, or allowed to silently become, unratified milestone changes.
- ADR-0002 §4.2's own practice of dispositioning every open item explicitly rather than leaving it implicit — this ADR follows that same discipline for the three items above.
- No new technology, dependency, or architectural layer is introduced by any of the three items — this is strictly a stabilization and documentation-accuracy pass, consistent with AGENTS.md's Golden Rule ("implementation is preferred over redesign").

---

## 3. Considered Options

**Item 1 — panel-state duplication:**
- **Option 1 (chosen)**: Remove the `panelState` mirror from `KortexSession`/`SessionManager`/`SessionSync`; `PanelProvider`'s own `kortex.panels.v1` key remains the sole source of truth for panel layout. Bump `CURRENT_SESSION_VERSION` (1 → 2) so a stale v1 session carrying the now-removed field is discarded on next restore rather than silently carried forward forever.
- **Option 2**: Keep the mirror, but make `PanelProvider` read its initial state from the session document instead of `panelPersistence.ts`, collapsing to one write path. Rejected — this would make `PanelProvider` depend on `SessionProvider`'s context to function at all, which regresses `PanelProvider`'s current independence (it already works correctly with no `SessionProvider` in the tree, per its own tests) for no benefit over Option 1.
- **Option 3**: Keep both, but add a consistency test proving they can't drift. Rejected — this treats a symptom (unverified drift) rather than the cause (two writers for one fact); Option 1 is simpler and removes the class of bug entirely rather than guarding against it.

**Item 2 — implementation-slice labels:**
- **Option 1 (chosen)**: Record M2.1–M2.x as informal, internal implementation-tracking slices of ratified milestone M2, requiring no ADR of their own to introduce, rename, or retire, and carrying no independent completion criteria beyond M2's own (ADR-0002 §4.4).
- **Option 2**: Retroactively ratify M2.1–M2.5 as five new, formally numbered sub-milestones. Rejected — this would be an unnecessary process expansion for what is, and should remain, day-to-day implementation planning granularity; AGENTS.md's Roadmap Discipline asks for the *milestone* structure to stay sequential and deliberate, not for every internal work-breakdown label to be individually ratified.

**Item 3 — state ownership:**
- **Option 1 (chosen)**: Document the third bucket (React Context + plain manager/registry class) as "domain runtime state" alongside the two already-ratified buckets, as a clarifying addendum to ADR-0002 §4.1 item 6 / spec §12.1 — not a new decision, since the pattern already exists in ratified, shipped code and no alternative was actually adopted instead of it.
- **Option 2**: Force `WorkspaceProvider`/`PanelProvider`/`SessionProvider` into Zustand to match the letter of the two-bucket table. Rejected — spec §12.1's own rationale for Zustand explicitly favors it for a "small, cohesive UI-state surface"; `WorkspaceRegistry`/`PanelRegistry`'s register/unregister/duplicate-id-throw semantics are not the same shape as `useUiStore`'s existing single-field theme store, and forcing them into Zustand purely for taxonomic tidiness would be redesign for its own sake, which AGENTS.md's Golden Rule forbids absent an explicit instruction.

---

## 4. Decision Outcome

**Chosen Option**: Option 1 in all three items above.

### 4.1 Approved Decisions

1. **Panel-state single source of truth**: `PanelProvider`'s own `kortex.panels.v1` persistence (`panels/panelPersistence.ts`) is the sole authoritative store for panel open/size state. `KortexSession` (`session/sessionTypes.ts`) no longer has a `panelState` field; `SessionManager`/`SessionSync` no longer read, write, or mirror it. `CURRENT_SESSION_VERSION` is 2; a persisted v1 session (which would carry a now-orphaned `panelState` field) is treated as an incompatible version and discarded on restore, per the version-mismatch behavior `SessionManager.restoreSession()` already implemented for exactly this purpose.
2. **M2.1–M2.x are internal implementation-tracking slices, not milestones.** They may continue to appear in code comments, commit messages, and task briefs as a work-breakdown aid, but: (a) they carry no independent ratification requirement, completion criteria, or ADR obligation; (b) they must never be represented as amending, replacing, or standing in place of ADR-0002's ratified M1–M4 structure; (c) "M2 is complete" means ADR-0002 §4.4's own stated M2 completion criteria are met in full — including "App builds and renders inside the Tauri shell in dev and production" — regardless of how many M2.x slices have individually landed. As of this ADR, M2 is **not** complete by that standard, because M1 (Tauri shell) does not yet exist; the M2.1–M2.5 slices delivered so far are real, tested progress toward M2, not a substitute for it.
3. **State ownership — three buckets, not two**, clarifying ADR-0002 §4.1 item 6 / spec §12.1–§12.2:
   - **Zustand** — ephemeral, local, presentation-only UI state with no restore/registry semantics of its own (today: `theme`, in `stores/uiStore.ts`).
   - **React Context wrapping a plain, instantiable manager/registry class** ("domain runtime state") — application-lifetime state that needs imperative registration semantics (register/unregister/duplicate-id detection: `WorkspaceRegistry`, `PanelRegistry`) and/or its own localStorage-backed restore cycle (`SessionManager`), scoped to a provider rather than a module-level singleton (AGENTS.md's Dependency Injection rule: no bare module-level singletons). Today: `WorkspaceProvider`, `PanelProvider`, `SessionProvider`.
   - **TanStack Query** — server-derived state, i.e. anything that originates from a real Kernel capability call once the IPC bridge (M3) exists. Not yet exercised in practice (`ipc/client.ts` is a deliberate not-yet-wired stub; see ADR-0002 §4.2 item 1) — this bucket is wired at the `QueryClientProvider` level (`app/App.tsx`) but has zero real usage today.
   - **The Hard Rule stands unchanged**: server-derived data must never be copied into either of the other two buckets, once M3 exists to produce any.

### 4.2 Rejected Alternatives

| Rejected | In favor of | Reason |
|---|---|---|
| Making `PanelProvider` read its initial state from the session document | `PanelProvider` keeps its own independent `kortex.panels.v1` persistence | Would make panel restore depend on `SessionProvider` existing in the tree, regressing `PanelProvider`'s current, tested independence for no benefit. |
| A consistency test proving the two panel-state copies can't drift | Removing the second copy outright | Treats the symptom, not the cause; a single writer needs no drift test. |
| Formally ratifying M2.1–M2.5 as five new sub-milestones | Treating them as informal, unratified implementation-tracking labels | Unnecessary process expansion for day-to-day work-breakdown granularity; ADR-0002's milestone structure is explicitly the ratified unit of tracking, not its internal slices. |
| Forcing `WorkspaceProvider`/`PanelProvider`/`SessionProvider` into Zustand for taxonomic consistency with a strict two-bucket reading of spec §12.1 | Documenting the existing Context+manager pattern as a third, named bucket | Spec §12.1's own stated rationale for Zustand (small, cohesive UI-state surface) does not fit registry-shaped state with imperative register/unregister semantics; forcing the fit would be redesign without a corresponding benefit. |

---

## 5. Architectural Impacts & Consequences

### Positive Consequences

- Eliminates a real, previously-undetected class of state-drift bug (two independent writers of the same panel-layout fact) before it could compound as more UI state gets added to the session document.
- Closes the gap between what ADR-0002 formally ratified for state management (two buckets) and what Phase 3 actually and correctly implements (three), removing a source of confusion for future implementation or review work.
- Gives "M2 is complete" an unambiguous, ADR-0002-traceable definition, preventing M2.1–M2.5's real progress from being mistaken for M2's actual ratified completion criteria — which still requires M1.
- No code outside `apps/desktop/src/session/*` and its tests needed to change; `PanelProvider`/`panelPersistence.ts` were already correct and required zero modification.

### Negative Consequences / Trade-offs

- A user with an existing v1 session in `localStorage` loses their persisted `activeApplication`/theme/`sidebarCollapsed` preferences exactly once, on first launch after this change (the version bump discards it rather than migrating it) — an accepted, one-time, non-data-loss-of-record cost, since none of this state is business data.
- This ADR does not, and cannot, resolve M2's actual blocking dependency on M1 (Tauri shell, still fully unimplemented) or M3 (backend presentation layer, same) — it only makes that dependency's status unambiguous in the decision record.

---

## 6. Compliance & Audit Verification

- Implementation: `apps/desktop/src/session/sessionTypes.ts`, `sessionStorage.ts`, `SessionManager.ts`, `SessionProvider.tsx`, and `routes/index.tsx` (doc comments only) were edited in this same stabilization pass; `apps/desktop/src/session/sessionStorage.test.ts`, `SessionManager.test.ts`, and `SessionProvider.test.tsx` were updated to match (obsolete panel-mirror assertions removed; a regression guard added asserting panel-state changes do not appear in the session document).
- No file under `docs/architecture/*` was modified by this ADR (it clarifies ADR-0002, it does not amend the underlying spec's text).
- `docs/adr/README.md`'s "Index of Approved ADRs" table is updated by this same change to add the `ADR-0003` row, per the indexing precedent set by ADR-0000/ADR-0001/ADR-0002.
- Full typecheck/test/build verification for this change is reported in the stabilization pass's diff report, not repeated here.
