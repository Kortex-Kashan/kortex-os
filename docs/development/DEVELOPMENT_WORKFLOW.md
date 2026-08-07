# KORTEX OS — Development Workflow Specification

Status: Approved Workflow Guide  
Authority: Chief Architect (KASHAN)  
Reference Architecture: Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)  

---

## 1. Development Sequence

All engineering tasks follow the sequence established in Section 406 of `AGENTS.md`:

```
Architecture Verification  ──>  Implementation  ──>  Testing  ──>  Code Review  ──>  Git Commit  ──>  Merge
```

1. **Architecture Verification**: Verify task compliance against `docs/architecture/ARCHITECTURE_VERSION_1.0.md`.
2. **Implementation**: Write clean, type-annotated code adhering to Clean Architecture and SOLID principles.
3. **Testing**: Write unit and integration tests achieving $\ge 90\%$ line coverage.
4. **Code Review**: Run automated quality gates, linting, and peer code review.
5. **Git Commit**: Commit small, atomic changes with descriptive commit messages.
6. **Merge**: Merge feature branch into target branch via pull request.

---

## 2. Environment Setup

- Python 3.11+ virtual environment (`.venv`).
- Dependencies installed via poetry / pip-tools (`requirements.txt`).
- Pre-commit hooks installed (`ruff`, `mypy`, `black`).

---

## 3. Local Execution & Verification

- Run unit test suite: `pytest backend/tests/unit/`
- Run integration test suite: `pytest backend/tests/integration/`
- Run type checking: `mypy backend/src/`
- Run linter & formatter: `ruff check backend/src/`
