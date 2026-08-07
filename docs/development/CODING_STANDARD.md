# KORTEX OS — Python Coding Standard Specification

Status: Approved Coding Standard  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Coding Rules Summary

1. **Python 3.11+ Type Annotations**: 100% of function signatures MUST include explicit type hints (`from __future__ import annotations`).
2. **Pydantic v2 Models**: All domain models, requests, results, and schemas MUST use Pydantic v2 (`BaseModel`).
3. **Async First**: Service interfaces, engine facades, and storage operations MUST use non-blocking `async`/`await` primitives.
4. **No Raw `print()` Statements**: Raw `print()` statements are forbidden. Use structured logger (`structlog`).
5. **No Direct Storage Access**: Direct SQL drivers (`sqlite3`, `asyncpg`) or raw file operations (`open()`) inside business modules are forbidden. Use `StorageEngine`.
6. **No Global State**: Global mutable variables are prohibited. Singletons MUST be registered in Kernel IoC.

---

## 2. Code Formatting & Style

- **Formatter**: PEP 8 compliance enforced via `ruff` / `black` (line length 100 characters).
- **Docstrings**: Google-style docstrings required for every public class, method, and module as mandated by Article 23 of Constitution.
- **Naming Conventions**:
  - Modules & packages: `snake_case`
  - Classes & Protocols: `PascalCase`
  - Functions & variables: `snake_case`
  - Constants: `UPPER_SNAKE_CASE`
  - Capabilities: `kortex.<domain>.<resource>.<action>`
