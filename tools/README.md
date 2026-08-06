# KORTEX Developer Toolkit

Developer tools and automation scripts for the KORTEX OS project.

## Available Tools

| Tool | Description |
|------|-------------|
| `create_project.py` | Generates the complete project directory structure (idempotent). |

## Usage

```bash
# Generate project structure (auto-detects root)
python tools/create_project.py

# Preview changes without writing
python tools/create_project.py --dry-run

# Verbose output
python tools/create_project.py --verbose
```

## Adding New Tools

Place new developer tools in this directory. Each tool must:

- Be a self-contained Python script with zero external dependencies.
- Include a comprehensive module docstring.
- Support `--help` for usage documentation.
- Follow KORTEX coding standards (PEP 8, type hints, docstrings).
