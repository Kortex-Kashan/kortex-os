# KORTEX Backend

The Python backend powering KORTEX OS.

## Technology Stack

- **Framework**: FastAPI
- **ORM**: SQLAlchemy 2.0+ (async)
- **Validation**: Pydantic v2
- **Database**: PostgreSQL (via asyncpg)
- **Migrations**: Alembic
- **Python**: 3.12+

## Directory Structure

```
backend/
├── src/
│   └── kortex/              # Main Python package
│       ├── core/            # Microkernel runtime
│       ├── engines/         # 21 System Engines
│       ├── modules/         # Business Modules
│       ├── recipes/         # Recipe definitions
│       ├── connectors/      # External integrations
│       ├── api/             # FastAPI routers & IPC
│       └── shared/          # Cross-cutting utilities
├── tests/                   # Test suite
├── alembic/                 # Database migrations
└── pyproject.toml           # Package configuration
```

## Development

```bash
# Install dependencies (with dev extras)
pip install -e ".[dev]"

# Run tests
pytest

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/
```
