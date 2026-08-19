# ADR-0001: Knowledge Engine Redesigned Scope, and Pack Loader / Facade Closure

Status: PROPOSED
Date: 2026-08-20
Author: Claude Code (primary implementation agent), on behalf of the Chief Architect's session-by-session M1–M8 authorizations
Authority: Chief Architect (KASHAN)
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`) §17

---

## 1. Context & Problem Statement

The Knowledge Engine (`kortex.engines.knowledge`) was implemented across eight
milestones (M1–M8: Foundation, Graph, Lineage, Annotations, Source Provider,
Trust Promotion, Persistence, Search) plus a ninth, unnumbered closure pass
(this ADR's own subject: Knowledge Pack Loader and Kernel-integrated
facade). Two governance gaps existed at the time this ADR was drafted:

1. **The M1–M8 organizational-memory scope (versioned record lineage,
   trust-state promotion, non-destructive annotations) materially exceeds**
   `docs/architecture/knowledge_engine_implementation_spec.md` v3.0.0's own
   §1/§5, which describes only a directed graph, pack loader, search
   coordinator, and source provider — not versioned lineage, trust
   promotion, or annotations. This redesign was authorized session-by-session
   by the Chief Architect directly and is documented extensively in the
   affected modules' own docstrings (`models.py`, `interfaces.py`,
   `lineage.py`, `annotations.py`), but was never captured in a written ADR,
   as `ARCHITECTURE_VERSION_1.0.md` §22 requires for any deviation from a
   frozen specification.
2. **The Knowledge Pack Loader and Kernel-integrated facade**, explicitly
   named in `ARCHITECTURE_VERSION_1.0.md` §17 as one of exactly three
   approved Knowledge Engine pillars ("directed graph, search coordinator,
   knowledge pack loader") and mandated in full detail by the dedicated,
   "Approved for Implementation" spec (§3 folder structure, §13 capability
   names, §14 event integration), had not been built as of M8. A post-M8
   reconciliation audit (this session) confirmed this was the single
   remaining gap between ratified scope and shipped code, and confirmed no
   ADR had ever been filed to formally either implement or descope it.

This ADR retroactively documents (1) and formally proposes ratifying (2),
which has now been implemented in the same session as this ADR's drafting.

---

## 2. Decision Drivers

- Every deviation from a frozen `docs/architecture/*` specification must be
  traceable to a written decision record, per §22 — not left as an implicit
  pattern scattered across module docstrings.
- The Knowledge Engine must reach parity with every other implemented KORTEX
  System Engine (Storage, Workflow, Recipe, Document, Connector, Security):
  a `BaseEngine` subclass, full lifecycle (`initialize`/`start`/`stop`/
  `health_check`), Kernel capability registration, and `IEngineDiagnostics`.
- No new persistence mechanism, no new event mechanism, no new capability
  naming convention — all closure work reuses `StorageEngine`, the existing
  `EventEngine`/`Kernel.publish_event`, and the capability names already
  fixed in the approved spec §13.
- Preserve every frozen M1–M8 contract and trust-state invariant unchanged;
  additive only.

---

## 3. Considered Options

- **Option 1 (chosen)**: Ratify the M1–M8 redesign retroactively, and
  implement the pack loader (`packs.py`) + Kernel-integrated facade
  (`engine.py`) exactly as the approved spec already specifies, closing the
  gap without inventing new scope.
- **Option 2**: Formally descope the pack loader and facade, amending
  `ARCHITECTURE_VERSION_1.0.md` §17 to remove them from Knowledge Engine's
  approved scope. Rejected — no evidence anywhere suggests the pack loader
  or Kernel integration are no longer wanted; the default posture for
  already-ratified, never-explicitly-removed scope is to build it, not
  quietly drop it.
- **Option 3**: Leave both gaps undocumented and unimplemented pending a
  separate, later Chief-Architect-initiated decision. Rejected — this is
  the status quo that produced two independent, inconclusive audits earlier
  in this same engineering effort; closing the gap now, evidenced and
  reviewable, is strictly better than deferring again.

---

## 4. Decision Outcome

**Chosen Option**: Option 1 — ratify the M1–M8 redesign retroactively;
implement the Knowledge Pack Loader and Kernel-integrated facade exactly as
already specified.

### Decision Rationale

Both the M1–M8 redesign and the pack-loader/facade closure work are
additive extensions of already-approved architecture (the redesign was
directly authorized by the Chief Architect at each milestone; the pack
loader/facade were already named and detailed in a spec marked "Approved
for Implementation"). Neither required inventing new architectural
authority — only a written record confirming what was already decided.

---

## 5. Architectural Impacts & Consequences

### Positive Consequences

- Knowledge Engine now satisfies all three `ARCHITECTURE_VERSION_1.0.md`
  §17 pillars (directed graph, search coordinator, knowledge pack loader)
  and matches every implemented sibling engine's Kernel-integration pattern.
- The M9/M10/M11 informal milestone-numbering ambiguity (found during the
  post-M8 audit: internally self-contradictory, with M10 entirely
  undefined) is retired — this ADR is now the authoritative record instead
  of scattered code comments.
- `KnowledgeQuery.filters`, `graph_relationships`, and `ICacheStore`
  traversal caching are each explicitly resolved (reserved/disclosed-gap/
  deferred respectively — see the closure work's own module docstrings)
  rather than left ambiguous.
- Persistence failures in `lineage.py`/`annotations.py`/`packs.py` are now
  uniformly normalized to `KnowledgePersistenceError`, matching this
  module's own established error-hierarchy convention.

### Negative Consequences / Trade-offs

- `KnowledgePack.digital_signature` is stored but not cryptographically
  verified — no specification anywhere defines a signing algorithm or
  trust-root model for Knowledge Packs specifically. Flagged explicitly in
  `packs.py`'s own docstring as a disclosed scope boundary, not a silent
  gap; a future ADR would be needed before adding real signature
  verification.
- `indexing.py` (named in the spec's aspirational folder listing) was not
  built — `search.py` (M8) already performs full-text/graph search directly
  without a separate index layer, by deliberate, already-ratified M8
  design; building a redundant indexer now would duplicate existing,
  frozen functionality.

---

## 6. Compliance & Audit Verification

- Full Knowledge Engine test suite: 264/264 passing (231 pre-existing +
  16 pack-loader + 15 facade + 2 integration), independently re-run.
- Full backend regression suite (`backend/tests/`): 1350/1350 passing, no
  regressions introduced anywhere else in the codebase.
- `docs/architecture/knowledge_engine_implementation_spec.md` requires no
  modification — this ADR ratifies compliance with it, rather than amending
  it.
- `.kortex/roadmap.md`'s Knowledge Engine entry corrected to reflect actual
  shipped scope (previously stale: unchecked, described only "RAG, vector
  store, chunking," matching nothing actually built).
- This ADR requires explicit Chief Architect approval (status `PROPOSED`)
  before its status may change to `ACCEPTED` and before its entry in
  `docs/adr/README.md`'s index table is anything more than a pending
  record — per the ADR Process Lifecycle's own step 4.
