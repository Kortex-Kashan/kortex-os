# KORTEX OS

**Local-First AI Business Operating System**

KORTEX OS is a Business Operating System where AI, Business Recipes,
Organizational Knowledge, Connectors and Modules work together as one
unified platform.

---

## What is KORTEX OS?

KORTEX OS is **not** an ERP, a chatbot, or an automation platform.

It is a complete Business Operating System designed to:

- Run **locally first** with optional cloud enhancement.
- Make **AI a native citizen** that understands your entire business.
- Turn repetitive tasks into reusable **Business Recipes** with human approval gates.
- Organize and leverage **Organizational Knowledge** across all operations.
- Connect to external systems through managed **Connectors**.

## Architecture & System Engines Implementation Status

```
Kernel → System Engines → Modules → Recipes → Templates → Connectors → AI
```

Everything communicates through the Kernel. No direct module coupling.

### Phase 2 Business Foundation Engines Status

| Engine | Namespace | Responsibilities | Status | Coverage |
| :--- | :--- | :--- | :--- | :--- |
| **Storage Engine** | `kortex.engines.storage` | Multi-store abstraction (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) | **Completed** | 100% |
| **Workflow Engine** | `kortex.engines.workflow` | Sole runtime state machine and recipe execution engine | **Completed** | 100% |
| **Recipe Engine** | `kortex.engines.recipe` | Declarative parser, validator, pure deterministic compiler, packager, installer, catalog registry | **Completed** | 97% |
| **Document Engine** | `kortex.engines.document` | Renderer registry & document lifecycle manager | *Pending* | - |
| **Connector Engine** | `kortex.engines.connector` | Driver registry & integration driver host | *Pending* | - |

## Technology Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, Pydantic v2 |
| Database | SQLite (Default) / PostgreSQL |
| Desktop | Tauri v2, React, TypeScript, TailwindCSS |
| AI | Ollama (Local) |
| Containers | Docker, Docker Compose |

## Getting Started

```bash
# Clone
git clone https://github.com/Kortex-AI/kortex-os.git
cd kortex-os

# Activate virtualenv & install backend
cd backend
..\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Run full test suite
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development setup guide.

## Project Structure

```
kortex-os/
├── .kortex/          # System metadata and architecture docs
├── apps/
│   ├── desktop/      # Tauri + React desktop application
│   └── server/       # Headless enterprise server
├── backend/
│   ├── src/kortex/   # Python backend package
│   │   ├── core/     # Kernel, base engine, container, event bus
│   │   └── engines/  # System engines (storage, workflow, recipe, ...)
│   ├── tests/        # Unit & integration test suite
│   └── alembic/      # Database migrations
├── docker/           # Container configurations
├── docs/             # Comprehensive documentation
├── scripts/          # Build and setup automation
└── tools/            # Developer toolkit
```

## Core Principles

- **Local First** — Cloud enhanced, offline capable.
- **AI Native** — Every module exposes AI capabilities.
- **Recipe Driven** — Repetitive tasks become reusable declarative recipes.
- **Human Approval** — AI suggests, humans decide.
- **Everything Modular** — Independently deployable components.
- **Everything Event Driven** — Decoupled communication through the Kernel.

## License

MIT License. See [LICENSE](LICENSE) for details.
