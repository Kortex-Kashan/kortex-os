# KORTEX OS — Development Environment Guide

Comprehensive guide for setting up and managing the KORTEX OS local development environment.

---

## 1. Virtual Environment Setup (`.venv`)

KORTEX OS uses an isolated Python virtual environment (`.venv`) located in the project root.

### Creating the Virtual Environment

```bash
# From the project root directory
py -m venv .venv
```

### Activating the Virtual Environment

- **Windows (PowerShell)**:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Windows (CMD)**:
  ```cmd
  .\.venv\Scripts\activate.bat
  ```
- **macOS / Linux**:
  ```bash
  source .venv/bin/activate
  ```

---

## 2. Dependency Management

Dependencies are defined in `backend/pyproject.toml` and mirrored in `backend/requirements.txt` and `backend/requirements-dev.txt`.

### Installing Dependencies

```bash
# Upgrade pip and build tools inside .venv
.venv/Scripts/python -m pip install --upgrade pip setuptools wheel

# Install backend package in editable mode with development & AI extras
.venv/Scripts/python -m pip install -e "backend/[dev,ai]"

# Alternatively install via requirements file
.venv/Scripts/python -m pip install -r requirements.txt
```

---

## 3. VS Code Integration

VS Code is configured via `.vscode/settings.json` to automatically detect and activate `.venv`:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.terminal.activateEnvironment": true,
  "python.analysis.extraPaths": [
    "${workspaceFolder}/backend/src"
  ],
  "python.testing.pytestEnabled": true,
  "python.testing.pytestArgs": [
    "backend/tests",
    "-o",
    "pythonpath=backend/src"
  ]
}
```

---

## 4. Running Automated Tests

Run tests directly using the virtual environment's `pytest` executable:

```bash
# Run unit tests with coverage using the virtual environment
.venv/Scripts/python -m pytest -o pythonpath=backend/src backend/tests/ --cov=kortex --cov-report=term-missing
```
