# KORTEX OS — Testing Strategy Architecture Specification

Status: Approved Testing Strategy  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Testing Philosophy

In accordance with Article 24 of the KORTEX OS Engineering Constitution, **every feature requires tests**. Submitting untested code is strictly forbidden.

KORTEX OS testing follows the Testing Pyramid model:

```
                      / \
                     /   \     Architecture Audit Tests
                    /-----\    (Enforce constitution & SOLID rules)
                   /       \
                  /---------\   Integration Tests
                 /           \  (Multi-engine flows & StorageEngine persistence)
                /-------------\
               /               \ Unit Tests
              /-----------------\ (Aggregates, Policies, Validators, Capabilities)
```

---

## 2. Test Execution Hierarchy

1. **Unit Tests (`backend/tests/unit/`)**: Fast, isolated tests targeting domain aggregates, business services, capability handlers, and engine logic with mocked infrastructure.
2. **Integration Tests (`backend/tests/integration/`)**: Verification of multi-engine workflows, real `IDataStore` / `IFileStore` / `IObjectStore` persistence, and Event Bus routing.
3. **Architecture Audit Tests (`backend/tests/architecture/`)**: Automated test suites inspecting code structure, import rules, capability naming, and dependency directions.

---

## 3. Mandatory Quality Thresholds

- **Unit Test Pass Rate**: 100%
- **Integration Test Pass Rate**: 100%
- **Line Coverage Minimum**: $\ge 90\%$ across all core engines and business modules.
- **Architectural Violation Rate**: 0 allowed.
