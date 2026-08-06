# KORTEX System Engines

System engines provide the platform's core capabilities.
Every engine registers its capabilities in the Kernel Registry
and communicates through the Kernel Event Bus.

## Engine Catalogue

| Engine | Package | Description |
|--------|---------|-------------|
| Boot Engine | `boot` | System startup and initialization sequencing |
| Configuration Engine | `configuration` | Settings, environment, and runtime configuration |
| Registry Engine | `registry` | Capability registration and service discovery |
| Identity Engine | `identity` | Users, workspaces, sessions, and tenant isolation |
| License Engine | `license` | Commercial license validation and feature gating |
| Module Engine | `module_engine` | Module lifecycle, loading, and dependency resolution |
| Event Engine | `event` | Async event bus, pub/sub, and event sourcing |
| AI Engine | `ai` | LLM orchestration via Ollama, prompt management |
| Knowledge Engine | `knowledge` | RAG, vector embeddings, organizational knowledge |
| Connector Engine | `connector` | External system integration management |
| Tool Engine | `tool` | AI function/tool schema generation from capabilities |
| Workflow Engine | `workflow` | Recipe execution, state machines, approval queues |
| Process Intelligence Engine | `process_intelligence` | Execution telemetry and process analytics |
| Document Intelligence Engine | `document_intelligence` | PDF parsing, OCR, document schema extraction |
| Communication Engine | `communication` | Notifications, email, messaging integration |
| Security Engine | `security` | RBAC, encryption, API keys, audit logging |
| Sentinel | `sentinel` | System health monitoring and integrity checks |
| Monitoring Engine | `monitoring` | Metrics, dashboards, and alerting |
| Update Engine | `update` | System updates and version management |
| Recovery Engine | `recovery` | Disaster recovery and system restoration |
| Backup Engine | `backup` | Automated backup scheduling and management |

## Engine Contract

Every engine must implement:

1. **Initialization** — Register capabilities with the Kernel Registry.
2. **Event Handling** — Subscribe to and publish domain events.
3. **Health Check** — Report operational status on demand.
4. **Graceful Shutdown** — Release resources cleanly.
