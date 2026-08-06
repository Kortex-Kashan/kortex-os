# KORTEX Shared Utilities

Cross-cutting utilities and base abstractions used across the KORTEX system.

## Contents

- **Base Models** — Pydantic base classes with common fields (id, timestamps).
- **Types** — Shared type aliases, enums, and value objects.
- **Helpers** — Date/time utilities, string helpers, validation functions.
- **Protocols** — Shared interface definitions (typing.Protocol).

## Rules

- Code here must have **zero** domain-specific knowledge.
- No imports from engines, modules, or connectors.
- Everything must be independently testable.
