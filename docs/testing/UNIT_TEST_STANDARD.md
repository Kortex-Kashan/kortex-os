# KORTEX OS — Unit Test Standard Specification

Status: Approved Test Standard  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Unit Test Rules

1. **Isolation**: Unit tests MUST execute in total isolation without external filesystem, database, or network I/O.
2. **Speed**: Individual unit tests MUST complete in $\le 50\text{ms}$.
3. **Naming**: Test files MUST match `test_<component_name>.py` and reside in `backend/tests/unit/`.
4. **Structure (AAA Pattern)**:
   - **Arrange**: Setup input domain models and mock interfaces.
   - **Act**: Invoke target capability handler or service method.
   - **Assert**: Verify returned `UniversalResult` payload, status code, and emitted events.

---

## 2. Coverage Requirements

Unit tests MUST achieve $\ge 90\%$ line coverage on all target files. Untested branches or edge cases are flagged as build failures.
