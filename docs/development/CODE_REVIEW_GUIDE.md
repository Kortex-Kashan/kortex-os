# KORTEX OS — Code Review Guide

Status: Approved Review Guide  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Code Review Philosophy

Code reviews ensure that all code committed to KORTEX OS preserves the long-term architectural integrity, security, local-first performance, and maintainability of the platform.

---

## 2. Review Checklist for Reviewers

Reviewers MUST evaluate PRs against five critical checkpoints:

1. **Architectural Compliance**: Does the code violate Clean Architecture or SOLID? Are business modules attempting direct database/filesystem I/O or cross-module imports?
2. **Capability & Event Conventions**: Do capabilities follow `kortex.<domain>.<resource>.<action>`? Are event topics formatted correctly?
3. **Test Quality & Coverage**: Are unit and integration tests comprehensive? Does coverage satisfy $\ge 90\%$?
4. **Security & Data Isolation**: Are query parameters filtered by `tenant_id`? Are secrets referenced via handles rather than plaintext?
5. **Code Style & Type Safety**: Are type annotations 100% complete? Are docstrings written for all public classes and methods?
