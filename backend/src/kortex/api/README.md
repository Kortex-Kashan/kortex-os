# KORTEX API Layer

The presentation layer for the KORTEX backend.

## Components

- **REST Routers** — FastAPI route handlers for HTTP endpoints.
- **WebSocket Handlers** — Real-time communication channels.
- **Tauri IPC Adapters** — Bridge between Tauri desktop shell and the Python backend.

## Design Rules

- Routers are thin — they validate input, delegate to engines, and format output.
- All business logic lives in engines and modules, never in routers.
- Every endpoint uses Pydantic models for request/response validation.
