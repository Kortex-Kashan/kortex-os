# KORTEX OS — Integration Test Standard Specification

Status: Approved Integration Test Standard  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Integration Test Rules

1. **Multi-Engine Flows**: Integration tests verify end-to-end interactions between Kernel, Storage Engine, Event Engine, and target business modules.
2. **Real Persistence**: Tests utilize real SQLite in-memory database sessions (`IDataStore`) and temporary sandboxed workspace directories (`IFileStore`).
3. **Location**: All integration test files reside inside `backend/tests/integration/`.
4. **Cleanup**: Tests MUST clean up temporary databases, cache keys, and file paths post-execution.
