# KORTEX OS

**Local-First AI Business Operating System**

KORTEX OS is an AI-native Business Operating System designed to bring business operations, organizational knowledge, workflows, integrations, automation, documents, and intelligent agents together in one governed platform.

> **Build once. Connect everything. Automate intelligently. Keep humans in control.**

---

## What is KORTEX OS?

KORTEX OS is being built as more than an ERP, chatbot, or standalone automation tool. It is a unified operating layer for businesses that combines:

- **AI-native business operations** — AI capabilities are treated as first-class system capabilities.
- **Business workflows and recipes** — repeatable processes can become structured, reusable automations with human approval where required.
- **Organizational knowledge** — business knowledge can be organized and made available to the systems and agents that need it.
- **Managed integrations** — external applications and services can be connected through governed connectors and capability-driven execution.
- **Local-first operation** — designed to work locally with optional cloud and self-hosted infrastructure.
- **Human-in-the-loop governance** — AI can recommend and execute authorized work while approval boundaries remain explicit.

The long-term KORTEX vision extends this foundation toward a unified automation and integration fabric, including visual workflow construction, API/OAuth integrations, MCP, native connectors, browser and desktop automation, Python automation, testing and sandboxing, deployment, AI agents, voice communications, and a marketplace ecosystem.

**Important:** items in the roadmap are future capabilities unless their implementation status is explicitly marked as complete in the repository.

---

## Architecture

```text
KORTEX OS
│
├── Kernel & Governance
│   ├── Capability Registry
│   ├── Authorization & Tenant Isolation
│   ├── Events & System Coordination
│   └── Audit / Approval Boundaries
│
├── System Engines
│   ├── Storage
│   ├── Workflow
│   ├── Recipe
│   ├── Document
│   ├── Connector
│   └── Knowledge
│
├── AI Layer
│   ├── AI Studio / Agents
│   ├── Model Providers
│   ├── Local Models
│   └── Tool / Capability Integration
│
├── Automation & Integration Fabric
│   ├── Visual Workflow Canvas
│   ├── Manual Builder
│   ├── API / OAuth
│   ├── MCP / MCP Gateway
│   ├── Native Connectors
│   ├── Browser Automation
│   ├── Desktop Automation
│   └── Python Automation
│
└── Experience Layer
    ├── Desktop Application
    ├── Dashboard
    ├── Document & Knowledge UX
    ├── AI / Agent UX
    └── Future Motion & Design System
```

The architectural principle is that system components communicate through governed platform boundaries rather than creating uncontrolled direct coupling between business modules.

---

## Current Foundation

The repository contains the foundational KORTEX architecture and business engines, including the Kernel, capability model, storage, workflow, recipe, connector, document, AI, and related infrastructure.

Current development follows a milestone-driven approach with automated tests, static analysis, architecture verification, and CI validation. The repository should be treated as the source of truth for implementation status rather than this overview alone.

### Core Technology Stack

| Layer | Technology |
| :--- | :--- |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0, Pydantic v2 |
| Database | SQLite (default) / PostgreSQL |
| Desktop | Tauri v2, React, TypeScript, TailwindCSS |
| AI | Local and cloud model providers; Ollama currently used for local inference |
| Containers | Docker, Docker Compose |
| Testing | Python backend tests, frontend tests, type checking, linting, CI validation |

---

## Product Roadmap

KORTEX is being developed in controlled layers rather than attempting to build every capability simultaneously.

### Automation & Integration

- Visual workflow canvas
- AI-assisted workflow generation
- Manual workflow builder
- API integrations and OAuth authorization
- MCP support and MCP Gateway
- Native and managed connectors
- Integration and connector catalog
- Browser automation
- Desktop automation
- Python automation
- Scheduling, triggers, testing, sandboxing, and deployment

### AI & Agents

- Multi-provider AI architecture
- Local/self-hosted model support
- AI Studio and business-aware agents
- Capability-driven AI tools
- Human approval and governance
- Future voice-agent runtime
- Future phone/SIP/WebRTC communications
- Human escalation for decisions requiring operator input

### Business Intelligence & Knowledge

- Document workflows and UX
- Organizational knowledge UX
- Business dashboards
- Cross-system operational intelligence
- Unified search and business context

### Ecosystem

- Integration marketplace
- Reusable automation packages
- Developer tooling and SDKs
- Self-hosted and cloud deployment options

### Product Experience

- Unified KORTEX design system
- Consistent component library
- Motion and interaction system
- Accessible, responsive desktop UX
- Smooth AI, workflow, approval, and execution feedback

---

## Core Principles

- **Local First** — Cloud enhanced and designed for offline-capable operation where practical.
- **AI Native** — AI capabilities are part of the operating model, not an afterthought.
- **Capability Driven** — Actions are represented through governed capabilities and system boundaries.
- **Recipe / Workflow Driven** — Repetitive business work becomes structured, reusable processes.
- **Human Approval** — AI can assist and recommend; sensitive decisions remain governed by explicit authorization and approval boundaries.
- **Everything Modular** — Components should have clear responsibilities and controlled interfaces.
- **Event Driven** — Decoupled communication is preferred where event-driven architecture is appropriate.
- **Security by Design** — Tenant isolation, authorization, credential protection, auditability, and least privilege are architectural requirements.
- **Local + Cloud Choice** — Users should be able to choose appropriate local, self-hosted, or cloud infrastructure for different workloads.

---

## Project Structure

```text
kortex-os/
├── .kortex/          # System metadata and architecture documentation
├── apps/
│   ├── desktop/      # Tauri + React desktop application
│   └── server/       # Headless enterprise server
├── backend/
│   ├── src/kortex/   # Python backend package
│   │   ├── core/     # Kernel, base engine, container, event bus
│   │   └── engines/  # KORTEX system engines
│   ├── tests/        # Unit and integration test suite
│   └── alembic/      # Database migrations
├── docker/           # Container configurations
├── docs/             # Architecture and development documentation
├── scripts/          # Build and setup automation
└── tools/            # Developer tooling
```

---

## Getting Started

```bash
git clone https://github.com/Kortex-Kashan/kortex-os.git
cd kortex-os

# Backend
cd backend
..\\.venv\\Scripts\\Activate.ps1
pip install -e ".[dev]"

# Run the backend test suite
pytest
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development setup and contribution workflow.

---

## Development Philosophy

KORTEX is developed as a long-lived platform rather than a collection of disconnected features. New capabilities are expected to preserve the system's architectural boundaries, security model, tenant isolation, capability registry, governance rules, and testability.

Major features are introduced through explicit discovery, implementation, verification, and acceptance milestones. Architectural changes should be validated against the existing system before implementation rather than added as isolated UI features.

---

## License

KORTEX OS is currently released under the **MIT License**. See [`LICENSE`](LICENSE) for the exact terms.

The licensing strategy is subject to a future dedicated review covering KORTEX-owned code, third-party dependencies, derivative works, and the project's long-term open-source and commercial objectives.

---

## Project Status

KORTEX OS is an active development project. The foundation is implemented and the platform is being expanded toward a complete AI-powered business operating system.

For implementation-level status, architecture decisions, milestone reports, and engineering constraints, see the documentation and project history in this repository.

---

**KORTEX OS — one operating layer for AI-powered business operations.**
