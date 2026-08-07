# KORTEX OS — Architecture Audit Standard Specification

Status: Approved Audit Standard  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Automated Architecture Audits

KORTEX OS employs automated static code analysis test suites (`backend/tests/architecture/`) to verify architectural compliance before merging code into `main`.

---

## 2. Automated Verification Checks

1. **Import Rule Guard**: Scans module source code to ensure business modules NEVER import code from sibling business modules.
2. **Storage Access Guard**: Scans business modules to ensure zero direct database driver (`sqlite3`, `asyncpg`) or file I/O (`open()`) imports exist.
3. **Capability Format Guard**: Verifies that all registered capabilities conform to `kortex.<domain>.<resource>.<action>`.
4. **Tenant Isolation Guard**: Verifies that all entity model definitions and queries contain mandatory `tenant_id` fields.
5. **No `print()` Guard**: Scans infrastructure and module code to ensure zero `print()` statements exist.
