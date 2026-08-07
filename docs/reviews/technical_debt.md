# KORTEX OS — Technical Debt & Architectural Governance Report

Status: Approved Governance Report  
Date: August 8, 2026  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Purpose

This document tracks technical debt prevention, architectural code smells, and refactoring guidelines for KORTEX OS.

By establishing strict architectural rules prior to codebase implementation, KORTEX OS prevents technical debt accumulation, anti-patterns, and framework coupling.

---

## 2. Technical Debt Prevention Rules

To maintain long-term maintainability, software engineers MUST adhere to six zero-debt principles:

1. **Zero Direct Storage Access**: Direct SQL calls, direct file I/O (`open()`), or raw SQLite/PostgreSQL drivers inside business modules are classified as Critical Technical Debt. All persistence MUST flow through `StorageEngine`.
2. **Zero Cross-Module Imports**: Importing python files from sibling business modules directly is prohibited. Modules MUST communicate through Kernel capability dispatches or Event Bus topics.
3. **Zero Hardcoded Secrets**: Storing plaintext passwords, tokens, or encryption keys in config files or code is classified as a Security Violation. All secrets MUST use Security Engine handles (`secret:kortex/...`).
4. **Zero Untested Code**: Submitting code without matching unit/integration tests achieving $\ge 90\%$ line coverage is forbidden.
5. **Zero Architecture Drift**: Modifying engine interfaces or folder structures without an approved ADR is prohibited.
6. **Zero Code in Declarative Assets**: Embedding Python, JS, or shell scripts in recipes, templates, or profiles is strictly blocked.

---

## 3. Debt Tracking & Review Process

- Technical debt items discovered during code reviews are logged in `technical_debt.md`.
- Debt items are assigned a severity rating (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`).
- `CRITICAL` technical debt items MUST be resolved prior to merging pull requests.
