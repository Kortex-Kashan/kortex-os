# KORTEX Server

Headless enterprise server runner for KORTEX OS.

## Purpose

Runs the KORTEX backend without the Tauri desktop shell, enabling:

- Server-based deployment for multi-user enterprise environments.
- Headless operation for CI/CD pipelines and automated workflows.
- Docker container deployment.

## Usage

```bash
# Start the server
uvicorn kortex.api.main:app --host 0.0.0.0 --port 8000
```
