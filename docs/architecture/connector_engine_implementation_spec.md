# KORTEX OS — Connector Engine Implementation Specification

Status: Approved for Implementation
Version: 3.0.0
Authority: KORTEX OS Engineering Constitution & Phase 2 Architecture Design
Target Release: KORTEX OS Phase 2: Business Foundation
Target File: `docs/architecture/connector_engine_implementation_spec.md`

Depends On:
- Phase 1 Foundation (Kernel Engine, Event Engine, Registry Engine, Configuration Engine)
- Storage Engine (`kortex.engines.storage`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)

---

## 1. Scope

The Connector Engine (`kortex.engines.connector`) is an enterprise-grade, adapter-driven integration host responsible for managing external communication channels, connector profiles, connector pipelines, driver adapters, rate limiting, retry backoffs, permissions, and event publication across KORTEX OS.

As defined in Article 11 of the KORTEX OS Engineering Constitution and Phase 2 Architecture Design (`docs/architecture/phase2_design.md`), the Connector Engine functions exclusively as an infrastructure integration driver host. It contains zero business logic and zero hardcoded protocol implementations.

Phase 2 implementation scope:
1. **Connector Driver Registry (`ConnectorDriverRegistry`)**: Registry for registering, discovering, and resolving connector driver plugins based on connector profiles and advertised capabilities.
2. **Abstract Base Connector Driver (`BaseConnectorDriver`)**: Abstract base class and protocol defining the formal plugin interface for connector drivers.
3. **Dynamic Driver Loader (`ConnectorDriverLoader`)**: Dynamic module inspector for discovering and instantiating connector drivers inside sandboxed environments.
4. **Dummy Connector Driver (`DummyConnectorDriver`)**: Reference driver plugin implementing `BaseConnectorDriver` for pipeline verification and mock action dispatches without external network dependencies.
5. **Connector Profiles (`ConnectorProfile`)**: Declarative configuration specifications defining channel credentials references, rate limits, retry policies, and security parameters.
6. **Connector Pipelines (`ConnectorPipeline`)**: Execution pipeline coordinating multi-stage connector actions (e.g. Authentication $\rightarrow$ Rate Limiting $\rightarrow$ Action Dispatch $\rightarrow$ Verification $\rightarrow$ Audit Event).
7. **Rate Limiter & Backoff Manager (`TokenBucketRateLimiter`)**: Token-bucket rate limiting and exponential backoff retry handler for outbound actions.
8. **Connector Engine Core Facade (`ConnectorEngine`)**: Facade inheriting `BaseEngine`, implementing capability handlers and diagnostic telemetry.
9. **Common Diagnostics Interface (`IEngineDiagnostics`)**: Implementation of standard diagnostics (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
10. **Storage Engine Integration**: Exclusive use of `StorageEngine` (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) for config caching, credential reference lookups, and audit trails.

---

## 2. Out of Scope

1. **Production Connector Drivers**: Native HTTP REST, Webhook, WhatsApp, Outlook, Gmail, FTP, MQTT, or Database drivers belong to subsequent feature phases. Phase 2 implements `DummyConnectorDriver` only.
2. **Business Domain Logic**: No business decision rules, invoice formatting, or domain logic belong in the engine.
3. **Direct Storage & File I/O**: Direct filesystem access or database connections are forbidden; all persistence flows through Storage Engine.
4. **Plaintext Credential Storage**: Secrets and API keys are stored exclusively in Security Engine secret storage; Connector Engine references secret handles only.
5. **Workflow & State Machine Scheduling**: Multi-step workflow orchestration belongs exclusively to Workflow Engine.

---

## 3. Folder Structure

All source code strictly resides inside `backend/src/kortex/engines/connector/`:

```
backend/src/kortex/engines/connector/
├── __init__.py                # Package exports (ConnectorEngine, models, interfaces)
├── engine.py                  # ConnectorEngine core facade inheriting BaseEngine
├── interfaces.py              # Abstract interfaces (IConnectorEngine, IBaseConnectorDriver, etc.)
├── models.py                  # Pydantic v2 domain models, enums, and profile schemas
├── exceptions.py              # Connector engine exception hierarchy
├── registry.py                # ConnectorDriverRegistry for managing driver plugins
├── loader.py                  # ConnectorDriverLoader for dynamic driver discovery
├── base_driver.py             # BaseConnectorDriver abstract base class
├── profiles.py                # ConnectorProfile manager & configuration schemas
├── pipeline.py                # ConnectorPipeline stage coordinator
├── rate_limiter.py            # TokenBucketRateLimiter for outbound rate limiting
├── diagnostics.py             # Common Diagnostics Interface (IEngineDiagnostics)
├── events.py                  # Immutable event payload definitions
└── drivers/
    ├── __init__.py            # Driver package marker
    └── dummy_driver.py        # DummyConnectorDriver reference plugin implementation

backend/tests/unit/
├── test_connector_models.py          # Unit tests for models and profile validation
├── test_connector_registry.py        # Unit tests for driver registration and lookup
├── test_connector_loader.py          # Unit tests for dynamic driver loading
├── test_dummy_driver.py              # Unit tests for DummyConnectorDriver execution
├── test_connector_pipeline.py        # Unit tests for pipeline stage execution
├── test_rate_limiter.py             # Unit tests for token-bucket rate limiting
├── test_connector_diagnostics.py     # Unit tests for IEngineDiagnostics methods
└── test_connector_engine.py          # Unit tests for core ConnectorEngine facade

backend/tests/integration/
└── test_connector_engine_integration.py # Integration tests with Kernel, Storage & Event Engine
```

---

## 4. Interfaces

- `IConnectorEngine`: Primary facade interface (`execute_action`, `register_driver`, `list_drivers`, `get_profile`).
- `IBaseConnectorDriver`: Abstract base class for drivers (`driver_id`, `supported_actions`, `execute_action`, `test_connection`).
- `IConnectorDriverRegistry`: Driver registration and lookup protocol.
- `IRateLimiter`: Token-bucket rate limiter protocol (`acquire_token`, `release_token`).

---

## 5. Models

- `ConnectorActionType`: Enum (`SEND`, `RECEIVE`, `FETCH`, `PUSH`, `VERIFY`).
- `ConnectorProfile`: Model (`profile_id`, `name`, `driver_id`, `secret_handle`, `rate_limit_per_sec`, `max_retries`, `options`).
- `ActionRequest`: Model (`request_id`, `profile_id`, `action_type`, `payload`, `correlation_id`).
- `ActionResult`: Model (`request_id`, `status`, `response_payload`, `execution_time_ms`, `error_details`).

---

## 6. Connector Registry (`ConnectorDriverRegistry`)

Thread-safe registry for registering, unregistering, and resolving connector driver plugins by driver ID and supported action capabilities.

---

## 7. Connector Adapter Architecture (`BaseConnectorDriver`)

Drivers implement `BaseConnectorDriver` ABC and operate inside sandboxed execution contexts. Drivers advertise capabilities, handle connection testing, and process action dispatches cleanly.

---

## 8. Connector Pipelines (`ConnectorPipeline`)

Coordinates action execution through pipeline stages:
1. **Authentication**: Resolves secret handle via Security Engine.
2. **Rate Limiting**: Acquires token via `TokenBucketRateLimiter`.
3. **Dispatch**: Dispatches payload to resolved `BaseConnectorDriver`.
4. **Audit & Event**: Records execution metrics and emits `ConnectorActionCompletedEvent`.

---

## 9. Connector Profiles (`ConnectorProfile`)

Declarative channel configuration profiles decoupling driver implementation from environment settings (URLs, secret handles, rate limits).

---

## 10. Permissions

Capabilities (`kortex.connector.action.execute`) require explicit RBAC permissions validated by Kernel middleware prior to dispatch.

---

## 11. Storage Requirements

Exclusive use of `StorageEngine`:
- `IDataStore`: Profile definitions and action execution history.
- `ICacheStore`: Rate limiter token buckets and active driver caches.
- Zero direct file or database operations.

---

## 12. Event Integration

Emits immutable events to Event Engine:
- `ConnectorActionStartedEvent` (`connector.action.started`)
- `ConnectorActionCompletedEvent` (`connector.action.completed`)
- `ConnectorActionFailedEvent` (`connector.action.failed`)
- `ConnectorDriverRegisteredEvent` (`connector.driver.registered`)

---

## 13. Capability Registration

Canonical capabilities:
- `kortex.connector.action.execute`
- `kortex.connector.driver.register`
- `kortex.connector.driver.list`
- `kortex.connector.profile.get`

---

## 14. Testing Requirements

- Unit tests across all engine components in `backend/tests/unit/`.
- Integration tests in `backend/tests/integration/`.
- Quality gates: 100% test pass rate, $\ge$90% code coverage.

---

## 15. Performance Requirements

- Non-blocking async execution (`async`/`await`).
- Orchestration overhead $\le$ 50ms per action dispatch.
- In-memory token-bucket rate limiting via `ICacheStore`.

---

## 16. Security Requirements

- Secrets referenced strictly by handle (never logged or exposed in plain text).
- Sandboxed driver execution isolation.
- Audit event generation for every outbound action.

---

## 17. Acceptance Criteria

- ✓ **Architecture Compliant**: Inherits `BaseEngine`, implements `IEngineDiagnostics`.
- ✓ **Driver Architecture Only**: Plug-in driver architecture using `BaseConnectorDriver`.
- ✓ **Zero Business Logic**: Infrastructure contains zero domain rules.
- ✓ **Storage Engine Only**: All persistence flows through `StorageEngine`.
- ✓ **Capability Registered**: Canonical capabilities registered in Kernel Registry.
- ✓ **Tests $\ge$ 90%**: Coverage threshold met across all core files.
