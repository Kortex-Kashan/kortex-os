# KORTEX Desktop

The Tauri v2 desktop application — the primary user interface for KORTEX OS.

## Technology Stack

- **Shell**: Tauri v2 (Rust-based lightweight desktop container)
- **Frontend**: React 18+ with TypeScript
- **Styling**: TailwindCSS
- **State Management**: TBD
- **IPC**: Tauri command bridge → FastAPI backend

## Architecture

The desktop app communicates with the Python backend via:
1. **Tauri IPC Commands** — For synchronous request/response patterns.
2. **WebSocket Channels** — For real-time streaming (AI responses, live updates).

The Python backend runs as a sidecar process managed by the Tauri shell.
