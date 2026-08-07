# KORTEX OS — Quality Gates Specification

Status: Approved Quality Standard  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Automated Quality Gates

Every code contribution to KORTEX OS MUST pass six automated Quality Gates before merging:

| Quality Gate | Threshold Criterion | Failure Action |
| :--- | :--- | :--- |
| **1. Architecture Audit** | 0 Constitutuion or SOLID violations | Block Merge |
| **2. Unit Test Pass Rate** | 100% test pass rate (`pytest`) | Block Merge |
| **3. Integration Test Pass Rate**| 100% test pass rate (`pytest`) | Block Merge |
| **4. Code Coverage Minimum** | $\ge 90\%$ line coverage across modified files | Block Merge |
| **5. Static Type Checking** | 0 `mypy` type errors | Block Merge |
| **6. Security Scan** | 0 high/critical static security alerts | Block Merge |
