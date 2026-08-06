# KORTEX Test Suite

Automated tests for the KORTEX OS backend.

## Structure

```
tests/
├── conftest.py       # Shared fixtures and pytest configuration
├── unit/             # Fast, isolated unit tests
├── integration/      # Tests requiring database or services
└── e2e/              # Full end-to-end workflow tests
```

## Running Tests

```bash
# Run all tests
pytest

# Run only unit tests
pytest tests/unit/

# Run with coverage
pytest --cov=kortex --cov-report=html

# Run specific markers
pytest -m "unit"
pytest -m "not slow"
```

## Writing Tests

- Place unit tests in `tests/unit/<engine_or_module>/`.
- Use `conftest.py` fixtures for database sessions, test clients, etc.
- Mark slow tests with `@pytest.mark.slow`.
- Mark integration tests with `@pytest.mark.integration`.
