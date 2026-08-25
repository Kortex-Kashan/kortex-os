# KORTEX OS — Architecture Decision Records (ADR Log)

All major architectural decisions for KORTEX OS are logged in this document in chronological order.

> **Note (ADR-0004, 2026-08-26): this file is retained as a historical record.**
> As of ADR-0004, new Architecture Decision Records are created in
> `docs/adr/` (which has its own lifecycle, template, and index —
> see `docs/adr/README.md`), not appended here. The entry below predates
> that process and is kept exactly as originally recorded — it is not
> deleted, renumbered, or migrated. See `docs/adr/ADR-0004-canonical-decision-log-location.md`
> for the full rationale.

---

## ADR #001: Local-First Architecture Foundation

- **Status**: Approved
- **Date**: 2026-08-06
- **Context**: KORTEX OS is defined as a Local-First AI Business Operating System. Business operations, organizational data, business recipes, and core AI processing must run securely on local user infrastructure without mandatory cloud connectivity or third-party SaaS dependencies.

### Decision
KORTEX OS adopts a **Local-First Architecture** as a fundamental platform principle:
1. **Data Sovereignty & Security**: All business data, database persistence (PostgreSQL / SQLite), vector embeddings, and organizational knowledge remain under local ownership and on-premise control.
2. **Offline Resilience**: The system operates fully without internet connectivity. Business recipes, workflows, module operations, and UI interactions never block on external network calls.
3. **Local AI Native**: Primary LLM execution relies on local inference engines (Ollama). Cloud AI providers are strictly optional secondary adapters.
4. **Cloud Enhanced**: Cloud capabilities (remote backup sync, multi-office federation, optional model offloading) serve exclusively as optional enhancements, never as hard operational dependencies.

### Consequences
- **Positive**: Zero latency dependence on SaaS APIs, complete data privacy compliance, uninterrupted business operation during network outages, predictable operational costs.
- **Negative / Trade-offs**: Higher hardware requirements on host machine (RAM/GPU for local LLMs), need for local sync/replication mechanisms, client-side database management complexity.
