# KORTEX Core

The microkernel runtime — the foundation of KORTEX OS.

## Responsibilities

- **Kernel**: Central orchestrator that boots engines, manages lifecycle, and routes events.
- **Configuration**: System-wide configuration management via Pydantic Settings.
- **Dependency Container**: Inversion-of-control container for engine and service registration.
- **Base Types**: Core domain types, enums, and value objects.
- **Exceptions**: Hierarchy of KORTEX-specific exceptions.

## Architecture

The Kernel follows a microkernel pattern:

```
Kernel
  ├── Boot Sequence → Discovers and initializes engines
  ├── Registry → Maps capabilities to engine implementations
  ├── Event Bus → Async pub/sub for cross-engine communication
  └── Lifecycle → Startup, health checks, graceful shutdown
```

All modules and engines communicate exclusively through the Kernel.
Direct module-to-module imports are prohibited.
