# KORTEX OS — Coding Rules

## Architecture Rules

1. Always use Clean Architecture (Domain → Application → Infrastructure → Presentation).
2. Always follow SOLID principles.
3. Use dependency injection where appropriate.
4. Never allow direct module-to-module coupling.
5. All inter-module communication must flow through the Kernel Event Bus.

## Python Standards

1. Target Python 3.12+.
2. Follow PEP 8 strictly.
3. Maximum line length: 120 characters.
4. Use `from __future__ import annotations` in every module.
5. Use `pathlib.Path` instead of `os.path`.
6. Use type hints for all function signatures.
7. Use `dataclasses` or `Pydantic` for data structures.

## Code Quality

1. Never duplicate code.
2. Never generate unnecessary code.
3. Never use placeholder implementations if production-quality code can be written.
4. Write small, reusable functions (≤ 25 lines preferred).
5. Keep classes focused (Single Responsibility).
6. Keep files organized (≤ 300 lines preferred).

## Documentation

1. Every public class must have a docstring.
2. Every public function must have a docstring.
3. Comment complex logic inline.
4. Document all non-obvious design decisions.

## Testing

1. Every engine must have unit tests.
2. Every module must have unit tests.
3. Use pytest fixtures for shared state.
4. Mark tests with appropriate markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.slow`.
5. Aim for ≥ 80% code coverage.

## Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Packages | snake_case | `process_intelligence` |
| Modules | snake_case | `boot_engine.py` |
| Classes | PascalCase | `BootEngine` |
| Functions | snake_case | `start_engine()` |
| Constants | UPPER_SNAKE | `MAX_RETRY_COUNT` |
| Private | _prefix | `_internal_state` |
| Pydantic Models | PascalCase + suffix | `UserCreate`, `InvoiceResponse` |

## Git Practices

1. Write clear, descriptive commit messages.
2. Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
3. Never commit secrets, keys, or credentials.
4. Run pre-commit hooks before pushing.

## Import Order (enforced by Ruff)

1. Standard library
2. Third-party packages
3. KORTEX packages (`kortex.*`)
4. Local relative imports
