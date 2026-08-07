# KORTEX OS — Pull Request Guide

Status: Approved PR Guide  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Pull Request Principles

Every Pull Request (PR) submitted to KORTEX OS MUST verify compliance with Architecture Version 1.0.0, satisfy all quality gates, and include complete test coverage before merging.

---

## 2. Pull Request Checklist

Before submitting a PR, the author MUST verify:

- [ ] **Architecture Compliant**: Code preserves Clean Architecture, SOLID, and DI. Zero breaking architectural changes introduced.
- [ ] **Tests Added & Passing**: 100% unit and integration tests passing (`pytest`).
- [ ] **Coverage Threshold Met**: Line coverage $\ge 90\%$ verified across modified source files.
- [ ] **Type Checked**: `mypy` type checking passes cleanly with zero errors.
- [ ] **Linted & Formatted**: `ruff` linter and formatter pass cleanly.
- [ ] **No Hardcoded Configs/Secrets**: Configuration uses `ConfigurationEngine`; secrets use Security Engine handles.
- [ ] **Documentation Updated**: Docstrings and README files updated.
