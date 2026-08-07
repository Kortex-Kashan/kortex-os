# KORTEX OS — Developer Contribution Guidelines

Status: Approved Contribution Guide  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Welcome to KORTEX OS

Thank you for contributing to KORTEX OS — the AI-powered, Local-First Business Operating System.

All contributors (human engineers and AI assistants) MUST adhere strictly to the **KORTEX OS AI Engineering Constitution** (`AGENTS.md`) and **Architecture Version 1.0.0** (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`).

---

## 2. Core Contribution Principles

1. **Architecture is Frozen**: Architecture Version 1.0.0 is frozen. Do not redesign, simplify, or modify approved architectural specifications without an approved ADR.
2. **Implementation First**: Build existing approved architecture before proposing improvements.
3. **Local-First & Offline-First**: Ensure all feature logic operates 100% locally without cloud dependencies.
4. **Clean Architecture & SOLID**: Keep domain business rules separated from infrastructure drivers.
5. **Quality & Test Rigor**: Maintain 100% test pass rate and $\ge 90\%$ code coverage.

---

## 3. Getting Started

1. Read `AGENTS.md` and `docs/architecture/ARCHITECTURE_VERSION_1.0.md`.
2. Follow `docs/development/DEVELOPMENT_WORKFLOW.md` for setting up your environment.
3. Verify your PR against `docs/development/PULL_REQUEST_GUIDE.md`.
