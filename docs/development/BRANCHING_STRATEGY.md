# KORTEX OS — Git Branching Strategy

Status: Approved Strategy  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Branching Model

KORTEX OS uses a structured **Git Flow** variant optimized for high architectural discipline and local-first release stability:

```
main (Production Releases / Architecture v1.0.0 Tagged)
  └── develop (Phase 3 Active Development)
        ├── feature/storage-engine-phase3
        ├── feature/workflow-engine-phase3
        ├── feature/recipe-engine-phase3
        └── bugfix/issue-102-capability-resolution
```

---

## 2. Branch Naming Conventions

- **Main Branch (`main`)**: Always reflects production-ready, release-tagged code matching frozen Architecture Version 1.0.0.
- **Development Branch (`develop`)**: Integration branch for current development milestone (Phase 3).
- **Feature Branches (`feature/<component>-<short-description>`)**: Isolated branches for implementing specific engine modules or capabilities.
- **Bugfix Branches (`bugfix/<issue-id>-<short-description>`)**: Isolated branches for bug fixes.
- **Release Branches (`release/vX.Y.Z`)**: Target branch for release candidate verification prior to merging to `main`.
