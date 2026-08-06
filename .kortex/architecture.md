# KORTEX OS — Architecture Reference

## System Architecture

KORTEX follows a microkernel-inspired architecture where the Kernel acts as
the central hub, and all capabilities are provided by System Engines.

```
┌─────────────────────────────────────────────────┐
│                    Kernel                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ Boot     │ │ Registry │ │ Event Bus        │ │
│  │ Sequence │ │ Engine   │ │ (Pub/Sub)        │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
└────────────┬────────────────────────────────────┘
             │
    ┌────────▼────────┐
    │  System Engines  │  (21 Engines)
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │    Modules       │  (Business Domains)
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │    Recipes       │  (Automated Workflows)
    └────────┬────────┘
             │
    ┌────────▼────────┐
    │   Connectors     │  (External Integrations)
    └─────────────────┘
```

## Clean Architecture Layers

| Layer | Responsibility | Dependencies |
|-------|---------------|--------------|
| **Domain** | Core models, business rules, exceptions | None (pure Python) |
| **Application** | Engine interfaces, event definitions, use cases | Domain only |
| **Infrastructure** | Database, Ollama, file I/O, Tauri IPC | Application + Domain |
| **Presentation** | FastAPI routers, WebSocket handlers, React UI | All layers |

Dependency flow is strictly inward: Presentation → Infrastructure → Application → Domain.

## System Engines (21)

### Foundation Layer
- **Boot Engine** — Startup sequencing and engine initialization
- **Configuration Engine** — Runtime configuration and environment management
- **Registry Engine** — Capability registration and service discovery

### Identity & Security Layer
- **Identity Engine** — Users, workspaces, sessions, tenant isolation
- **License Engine** — Commercial license validation and feature gating
- **Security Engine** — RBAC, encryption, API keys, audit logging

### Communication Layer
- **Event Engine** — Async event bus, pub/sub, event sourcing
- **Communication Engine** — Notifications, email, messaging

### Intelligence Layer
- **AI Engine** — LLM orchestration via Ollama, prompt management
- **Knowledge Engine** — RAG, vector embeddings, organizational knowledge
- **Tool Engine** — AI function/tool schema from capabilities
- **Process Intelligence Engine** — Execution telemetry and analytics
- **Document Intelligence Engine** — Document parsing and extraction

### Business Layer
- **Module Engine** — Module lifecycle and dependency resolution
- **Workflow Engine** — Recipe execution, state machines, approval queues
- **Connector Engine** — External system integration management

### Operations Layer
- **Sentinel** — System health, deadlock detection, integrity
- **Monitoring Engine** — Metrics, dashboards, alerting
- **Update Engine** — System updates and version management
- **Recovery Engine** — Disaster recovery and restoration
- **Backup Engine** — Automated backup scheduling

## Module Contract

Every business module exposes exactly these facets:

| Facet | Description |
|-------|-------------|
| Data | Domain models, schemas, repositories |
| UI | React components and views |
| AI | Capabilities exposed to the LLM |
| Recipes | Declarative business workflows |
| Templates | Document and report templates |
| Knowledge | Domain knowledge for RAG |
| Reports | Analytics and exports |
| Permissions | RBAC roles and access policies |
| Tests | Automated test suite |
| Documentation | Module-specific docs |

## AI Philosophy

- Every module exposes AI capabilities.
- The AI understands the entire business.
- The AI never calls APIs directly.
- It discovers capabilities through the Capability Registry.
- Humans approve; AI assists.

## Communication Rules

1. Modules NEVER import from other modules.
2. All inter-module communication flows through the Kernel Event Bus.
3. Engines register capabilities in the Registry.
4. Synchronous lookups use the Capability Registry.
5. Asynchronous side-effects use the Event Bus pub/sub.
