# KORTEX OS — Architecture Decision Records (ADR)

Status: Active Governance Repository  
Authority: Chief Architect (KASHAN) & KORTEX OS Engineering Constitution (`AGENTS.md`)  
Reference Architecture: KORTEX OS Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## Purpose

This repository folder contains the official **Architecture Decision Records (ADR)** for KORTEX OS.

As mandated by Article 30 of the KORTEX OS AI Engineering Constitution and Section 22 of `ARCHITECTURE_VERSION_1.0.md`, **Architecture Version 1.0.0 is officially FROZEN and RATIFIED**. No software engineer, AI assistant, automation agent, or developer may modify, redesign, simplify, or alter any architectural decision defined in Architecture Version 1.0.0 without a formally approved ADR.

An ADR documents a significant technical or architectural decision, including the context, decision rationale, trade-offs, and consequences.

---

## ADR Process Lifecycle

```
1. Proposal  ──>  2. Architectural Review  ──>  3. Written ADR  ──>  4. Chief Architect Approval  ──>  5. Version Increment
```

1. **Proposal**: A developer or AI assistant identifies a mandatory architectural change or extension and submits a proposal.
2. **Architectural Review**: The team reviews the proposal against Clean Architecture, SOLID, Local-First, and Phase 2 design principles.
3. **Written ADR**: Author drafts a numbered ADR markdown file using `ADR_TEMPLATE.md`.
4. **Approval**: Explicit review and approval by Chief Architect (KASHAN).
5. **Version Increment**: Upon approval, the status changes to `ACCEPTED` and the specification is versioned.

---

## Index of Approved ADRs

| ADR Number | Title | Date | Status | Target Component |
| :--- | :--- | :--- | :--- | :--- |
| `ADR-0000` | Ratification of KORTEX OS Architecture Version 1.0.0 | 2026-08-08 | `ACCEPTED` | System-Wide Architecture |
| `ADR-0001` | Knowledge Engine Redesigned Scope, and Pack Loader / Facade Closure | 2026-08-20 | `PROPOSED` | Knowledge Engine (`kortex.engines.knowledge`) |
| `ADR-0002` | Approval of the Phase 3 Desktop Architecture Specification | 2026-08-25 | `ACCEPTED` | Desktop Shell, React UI, IPC Bridge, Design System |
| `ADR-0003` | Phase 3 Stabilization — Panel-State Persistence Consolidation, Implementation-Tracking Slices, and State Ownership Clarification | 2026-08-26 | `ACCEPTED` | Desktop Shell (`apps/desktop/src/session`, `panels`, `workspace`) |
| `ADR-0004` | Canonical Location for Architecture Decision Records | 2026-08-26 | `ACCEPTED` | Governance / ADR Process |

**Legacy record**: `.kortex/decisions.md` contains one entry ("ADR #001: Local-First Architecture Foundation," 2026-08-06) predating this directory's ADR process. It is preserved there as historical record per ADR-0004 and is not renumbered into this sequence — see ADR-0004 for the full rationale. It does not appear in the table above because it is not part of this directory's numbering sequence.

---

## ADR Status Definitions

- **`PROPOSED`**: Under active architectural discussion; not approved for implementation.
- **`ACCEPTED`**: Approved by Chief Architect; official architectural standard.
- **`REJECTED`**: Reviewed and declined; must not be implemented.
- **`DEPRECATED`**: Formerly accepted but superseded by a later ADR.
- **`SUPERSEDED`**: Replaced by a newer approved ADR (reference provided).
