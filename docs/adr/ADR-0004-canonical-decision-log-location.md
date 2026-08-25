# ADR-0004: Canonical Location for Architecture Decision Records

Status: ACCEPTED
Date: 2026-08-26
Author: Claude Code (Phase 3 stabilization engineer), recording direct approval issued by the Chief Architect (KASHAN) in this session
Authority: Chief Architect (KASHAN)
Reference Architecture: AGENTS.md ("every architectural decision belongs inside `.kortex/decisions.md`"); `docs/adr/README.md` (ADR Process Lifecycle, Index of Approved ADRs)

---

## 1. Context & Problem Statement

A read-only architectural audit of the repository (this session, preceding this ADR) found two independent, uncoordinated Architecture Decision Record sequences:

- **`.kortex/decisions.md`** — named by AGENTS.md as the canonical log ("Every architectural decision belongs inside `.kortex/decisions.md`"). Contains exactly one entry: "ADR #001: Local-First Architecture Foundation" (dated 2026-08-06).
- **`docs/adr/`** — a separate directory with its own README, ADR Process Lifecycle (Proposal → Architectural Review → Written ADR → Chief Architect Approval → Version Increment), status vocabulary (`PROPOSED`/`ACCEPTED`/`REJECTED`/`DEPRECATED`/`SUPERSEDED`), and template (`ADR_TEMPLATE.md`). Contains `ADR-0000` (Ratification of Architecture Version 1.0.0, 2026-08-08), `ADR-0001` (Knowledge Engine Redesigned Scope, 2026-08-20, `PROPOSED`), and `ADR-0002` (Approval of the Phase 3 Desktop Architecture Specification, 2026-08-25, `ACCEPTED`).

Both sequences independently use the number "001" for two entirely unrelated decisions (Local-First Architecture Foundation vs. Knowledge Engine scope). Neither log references the other. AGENTS.md's text still points exclusively at `.kortex/decisions.md`, but every substantive architectural decision since `ADR-0000` has, in practice, been recorded in `docs/adr/` — which has already accumulated a real process (lifecycle, status states, a template, a self-maintained index) that `.kortex/decisions.md` never developed past its first entry. Left unresolved, this ambiguity would only compound: Phase 4 (AI Engine) will generate more architecturally significant decisions, and a decision-maker or auditor searching only `.kortex/decisions.md` — the location AGENTS.md's text actually names — would miss ADR-0000 through ADR-0003 entirely.

---

## 2. Decision Drivers

- A canonical decision log is only useful if there is exactly one of it; two independently-numbered logs defeat the purpose AGENTS.md's rule exists to serve.
- `docs/adr/` already has the more mature, self-documented process (lifecycle stages, status vocabulary, a template, a maintained index table) and a real track record (four decisions, three of them substantive) — building a second, parallel process on top of `.kortex/decisions.md` from here forward would duplicate that machinery for no benefit.
- AGENTS.md is a protected file (per CLAUDE.md's operational rules) and amending its text is outside this task's explicit authorization; this ADR resolves the *practical* conflict without editing AGENTS.md, and flags the remaining textual inconsistency as a follow-up requiring its own authorization — mirroring the precedent ADR-0002 §6 already set for deferring a protected-file edit it wasn't authorized to make.
- History must not be deleted (explicit instruction for this task): `.kortex/decisions.md`'s existing entry records a real, already-approved decision and remains valid; it is preserved verbatim, not rewritten or renumbered to avoid the "001" collision.

---

## 3. Considered Options

- **Option 1 (chosen)**: `docs/adr/` becomes the single canonical location for all *future* Architecture Decision Records, continuing its existing numbering sequence and process. `.kortex/decisions.md` is preserved unmodified below a short header note marking it as the historical/legacy log that predates the `docs/adr/` process, pointing readers to `docs/adr/README.md` going forward. `docs/adr/README.md`'s index is extended with a note cross-referencing the legacy entry, without assigning it a colliding new number.
- **Option 2**: Migrate `.kortex/decisions.md`'s entry into `docs/adr/` as a renumbered `ADR-000X`, then delete or empty `.kortex/decisions.md`. Rejected — deleting or emptying a file that records a real, already-approved decision is exactly the "delete history" outcome this task was explicitly instructed to avoid; renumbering it also risks breaking any existing reference to "ADR #001: Local-First Architecture Foundation" by that name.
- **Option 3**: Make `.kortex/decisions.md` canonical instead, and migrate `docs/adr/`'s four entries into it. Rejected — `docs/adr/` has the substantially more developed process (lifecycle, status states, template, index) and the larger, more recent body of real decisions; migrating backward into the thinner log would discard that machinery for no gain, and would still require deciding what happens to the colliding "001" number in the other direction.
- **Option 4**: Leave both in place indefinitely, uncoordinated. Rejected — this is the status quo that created the problem being resolved; explicitly rejecting it here forecloses it from being reintroduced by omission.

---

## 4. Decision Outcome

**Chosen Option**: Option 1 — `docs/adr/` is the canonical Architecture Decision Record log for KORTEX OS, effective immediately, for all decisions from this ADR forward. `.kortex/decisions.md` is retained, unedited in its substantive content, as the historical record of the one decision made before this process existed.

### 4.1 Approved Decisions

1. Every new Architecture Decision Record, from this ADR onward, is created in `docs/adr/` following its existing lifecycle, template, status vocabulary, and numbering sequence (continuing from `ADR-0004`).
2. `.kortex/decisions.md`'s existing "ADR #001: Local-First Architecture Foundation" entry is preserved verbatim — not deleted, not renumbered, not rewritten — with a short header note added above it (not replacing any existing text) stating that it predates the `docs/adr/` process and that new decisions are recorded there.
3. `docs/adr/README.md`'s "Index of Approved ADRs" table gains a note cross-referencing `.kortex/decisions.md`'s legacy entry, so a reader of the canonical index is not unaware of it, without giving it a number that collides with `docs/adr/`'s own sequence.
4. **Deferred, not resolved by this ADR**: AGENTS.md's own text ("every architectural decision belongs inside `.kortex/decisions.md`") still names the old location and is now inconsistent with this decision. AGENTS.md is a protected file; amending it requires its own explicit authorization, which this task did not grant (it authorized resolving the *conflict*, not editing AGENTS.md itself). This inconsistency is flagged here as an explicit follow-up action item, exactly as ADR-0002 §4.2 item 4 / §6 flagged `docs/architecture/README.md` and `.kortex/roadmap.md` cross-referencing as deferred follow-ups rather than silently dropping them.

### 4.2 Rejected Alternatives

| Rejected | In favor of | Reason |
|---|---|---|
| Deleting/emptying `.kortex/decisions.md` after migrating its entry elsewhere | Preserving it in place, annotated | Explicit instruction not to delete history; deletion also risks breaking existing references to "ADR #001" by name. |
| Making `.kortex/decisions.md` canonical and migrating `docs/adr/`'s four entries into it | Making `docs/adr/` canonical | `docs/adr/` already has the more developed process and the larger, more recent decision record; migrating backward discards that for no gain. |
| Leaving both logs in place, uncoordinated, with no decision | Explicitly designating one canonical location | The uncoordinated status quo is the problem being resolved, not a viable option. |

---

## 5. Architectural Impacts & Consequences

### Positive Consequences

- A future reader (human or AI assistant) now has exactly one place to look for the authoritative decision history, with a clear pointer from the legacy location.
- The colliding "001" number is left alone in both places (not renumbered), so no existing reference to either "ADR #001: Local-First Architecture Foundation" or "ADR-0001: Knowledge Engine Redesigned Scope" is invalidated by this change.
- Establishes the discipline needed before Phase 4 (AI Engine) starts generating its own architecturally significant decisions.

### Negative Consequences / Trade-offs

- AGENTS.md's text remains technically inaccurate about the canonical location until a separately-authorized edit corrects it — a known, explicitly-flagged gap rather than a silent one.
- Two "001"-numbered decisions continue to coexist under different naming conventions (`ADR #001` vs `ADR-0001`) in two different files; this is the accepted cost of not deleting or renumbering history.

---

## 6. Compliance & Audit Verification

- `.kortex/decisions.md`: a header note is added above the existing "ADR #001" entry; no existing text below it is modified or removed.
- `docs/adr/README.md`: the "Index of Approved ADRs" table gains an `ADR-0004` row and a cross-reference note for the legacy `.kortex/decisions.md` entry.
- No file under `docs/architecture/*` was modified by this ADR.
- AGENTS.md was not modified by this ADR (see §4.1 item 4 — deferred, requires separate authorization).
