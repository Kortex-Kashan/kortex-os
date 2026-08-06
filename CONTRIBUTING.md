# Contributing to KORTEX OS

Thank you for your interest in contributing to KORTEX OS.

## Development Philosophy

- **Quality over speed.** Every contribution must meet production standards.
- **Maintainability over shortcuts.** Think long-term.
- **Architecture is sacred.** Never redesign without explicit approval.

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker & Docker Compose
- Git

### Setup

```bash
# Clone the repository
git clone https://github.com/Kortex-AI/kortex-os.git
cd kortex-os

# Generate project structure (if not already done)
python tools/create_project.py

# Install Python dependencies
cd backend
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Start local services
docker compose -f docker/docker-compose.yml up -d
```

## Development Workflow

1. Create a feature branch from `main`.
2. Make changes following the [Coding Rules](.kortex/coding_rules.md).
3. Write tests for all new code.
4. Run the full test suite: `pytest`
5. Run linting: `ruff check src/ tests/`
6. Run formatting: `ruff format src/ tests/`
7. Run type checking: `mypy src/`
8. Commit with conventional commits.
9. Open a pull request.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add payroll recipe template
fix: resolve event bus deadlock on shutdown
docs: update engine architecture diagram
refactor: extract base engine interface
test: add unit tests for identity engine
chore: update ruff to v0.10.0
```

## Code Review Standards

- All code must pass CI checks (lint, type check, tests).
- Every public function/class must have docstrings.
- No direct module-to-module imports.
- No placeholder implementations.

## Architecture Changes

If your contribution requires architectural changes:

1. Open a discussion issue first.
2. Propose the change with clear rationale.
3. Wait for approval before implementing.

Never modify the core architecture without explicit team approval.
