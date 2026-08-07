# KORTEX OS — Versioning Policy Specification

Status: Approved Versioning Policy  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Versioning Standard

KORTEX OS and all extensible platform assets (Recipes, Templates, Adapters, Modules, Connectors, Packages) strictly enforce **Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`)**.

---

## 2. Version Increment Rules

### 2.1 MAJOR Version Bump (`1.0.0` $\rightarrow$ `2.0.0`)
Triggered when breaking architectural or API changes are introduced:
- Altered capability input/output schemas.
- Removed or renamed capabilities.
- Structural database migration requiring non-backward-compatible transforms.
- Requires explicit ADR approval by Chief Architect.

### 2.2 MINOR Version Bump (`1.0.0` $\rightarrow$ `1.1.0`)
Triggered when backward-compatible features are added:
- New capabilities added (`kortex.<domain>.<resource>.<action>`).
- New optional input parameters added to existing capability schemas.
- New engines, adapters, or modules introduced.

### 2.3 PATCH Version Bump (`1.0.0` $\rightarrow$ `1.0.1`)
Triggered for backward-compatible bug fixes and optimizations:
- Internal performance optimizations.
- Documentation updates or docstring fixes.
- Bug fixes not altering capability interface schemas.

---

## 3. Dependency Version Specifications

Dependencies declared in `manifest.yaml` MUST specify SemVer ranges (e.g. `kortex.kernel: ">=1.0.0,<2.0.0"`). Wildcard dependencies (`*`) are strictly prohibited in production packages.
