# KORTEX OS — Platform Service Contracts Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/platform_service_contracts.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- KORTEX OS Phase 2 Architecture Design (`docs/architecture/phase2_design.md`)

---

## 1. Purpose

This document defines the formal **Platform Service Contracts** governing all inter-engine and inter-module communication across KORTEX OS.

In accordance with Article 6 and Article 7 of the KORTEX OS Engineering Constitution, KORTEX OS is a capability-driven, event-driven system. Direct engine-to-engine coupling, direct module-to-module imports, and unmediated cross-layer method invocations are strictly forbidden.

This specification defines the universal protocols, capability invocation patterns, request/response models, error handling standards, execution modes (synchronous, asynchronous, long-running, streaming), discovery rules, versioning policies, timeout/retry/idempotency rules, correlation tracking, and security contracts required for all system services.

---

## 2. Design Principles

Every service contract in KORTEX OS adheres to these foundational principles:

1. **Kernel Orchestration Authority**: The Kernel IoC container and Registry Engine act as the sole broker for capability discovery and invocation resolution.
2. **Universal Capability Naming**: All capabilities follow the canonical naming standard: $\text{kortex}.<\text{domain}>.<\text{resource}>.<\text{action}>$.
3. **Decoupled Event-Driven Communication**: Asynchronous notification and state synchronization occur via immutable system events published through the Event Engine.
4. **Clean Architecture & SOLID**: Service contracts expose pure interface protocols (`Protocol` / ABC) decoupled from concrete infrastructure implementations.
5. **Universal Model Reuse**: Service contracts strictly consume and return models inheriting from `docs/architecture/shared_domain_models.md` (`UniversalResult`, `UniversalError`, `UniversalIdentity`).
6. **Local-First Reliability**: Service contracts execute deterministically in offline environments without cloud RPC dependencies.

---

## 3. Service Contract Philosophy

KORTEX OS service contracts enforce a strict boundary between **Capability Invocation** (direct request-response capability execution through Kernel IoC) and **Event Propagation** (decoupled broadcast of state changes via Event Engine).

```
                      ┌──────────────────────────────────────────┐
                      │             Caller Engine                │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │      Kernel Capability Dispatcher        │
                      │   (Resolves capability & permissions)    │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │             Target Engine                │
                      │    (Executes capability implementation)  │
                      └─────────────────────┬────────────────────┘
                                            │
                                            ▼
                      ┌──────────────────────────────────────────┐
                      │               Event Engine               │
                      │  (Broadcasts immutable KortexEvent log)  │
                      └──────────────────────────────────────────┘
```

---

## 4. Capability Invocation Model

All service requests execute through the Kernel Capability Dispatcher using canonical capability names. Direct instantiation of target engine facades by caller modules is forbidden.

### Invocation Sequence:
1. **Resolution**: Caller requests capability execution by string name (e.g. `kortex.storage.object.put`).
2. **Authorization**: Kernel checks caller permissions against `UniversalCapabilityMetadata.required_permissions`.
3. **Dispatch**: Kernel routes the structured `CapabilityRequest` payload to the registered service implementation.
4. **Response**: Target engine processes the request and returns a standard `UniversalResult` payload.
5. **Event Emission**: Target engine emits an immutable `KortexEvent` to Event Engine detailing operation outcome.

---

## 5. Request Models (`CapabilityRequest`)

All capability invocations encapsulate input arguments inside a standardized `CapabilityRequest` wrapper inheriting from `UniversalMetadata`.

### Structural Fields Specification:

- `request_id`: Unique UUID string identifying the invocation instance.
- `capability_name`: Canonical capability string (`kortex.<domain>.<resource>.<action>`).
- `caller_identity`: `UniversalIdentity` object representing the invoking actor or engine.
- `tenant_id`: Multi-tenant organization identifier.
- `correlation_id`: Distributed trace correlation UUID string.
- `parameters`: Dictionary containing typed input arguments and payload models.
- `timeout_ms`: Integer execution timeout threshold in milliseconds (default 30000ms).
- `idempotency_key`: Optional UUID string guaranteeing idempotent execution.
- `timestamp_utc`: ISO 8601 UTC timestamp string of request generation.

---

## 6. Response Models (`UniversalResult`)

All service contracts return the standardized `UniversalResult` model defined in `shared_domain_models.md`.

### Structural Fields Specification:

- `request_id`: Matching UUID string of the invocation request.
- `correlation_id`: Matching correlation trace ID.
- `status`: Execution status enum (`SUCCESS`, `FAILURE`, `PARTIAL_SUCCESS`, `CANCELLED`).
- `payload`: Dictionary or Pydantic model containing output domain data.
- `errors`: List of `UniversalError` objects detailing fatal failures.
- `warnings`: List of `UniversalError` objects detailing non-fatal warnings.
- `execution_duration_ms`: Float total execution duration in milliseconds.
- `timestamp_utc`: ISO 8601 UTC timestamp string of completion.

---

## 7. Error Models (`UniversalError`)

Service failures must never throw unhandled exceptions across engine boundaries. All errors are caught, mapped, and returned as structured `UniversalError` objects inside `UniversalResult.errors`.

### Standardized Error Categories:

- `CAPABILITY_NOT_FOUND`: Capability is not registered in Registry Engine.
- `PERMISSION_DENIED`: Caller lacks required RBAC/ABAC permissions.
- `VALIDATION_FAILED`: Input parameters violate `UniversalValidationReport` rules.
- `TIMEOUT_EXCEEDED`: Operation failed to complete within `timeout_ms`.
- `SERVICE_UNAVAILABLE`: Target engine is uninitialized or in `FAILED` diagnostic state.
- `EXECUTION_FAILED`: Internal engine exception occurred during execution.

---

## 8. Long Running Operations (`LongRunningOperation`)

Operations exceeding standard execution thresholds (e.g. multi-page document transformation, large package extraction) return an asynchronous operation handle immediately.

### Protocol Specification:

1. **Initial Response**: Returns `UniversalResult` containing `status: PENDING` and an `operation_id` tracking key.
2. **Status Inspection**: Caller polls `kortex.system.operation.status` passing `operation_id`.
3. **Progress Updates**: Emits periodic `OperationProgressEvent` payloads via Event Engine.
4. **Completion Handoff**: Upon completion, status updates to `SUCCESS` and stores result in `IObjectStore`.

---

## 9. Async Operations

All platform service contracts enforce non-blocking execution using Python `async`/`await` primitives. Blocking main thread execution or sync blocking I/O is strictly forbidden across service interfaces.

---

## 10. Streaming Operations (`IStreamingServiceContract`)

Large binary operations (e.g. blob storage streaming, video/audio processing, telemetry ingestion) utilize chunked async generator interfaces (`AsyncGenerator[bytes, None]`).

### Streaming Rules:
- Stream chunks must specify fixed block sizes (default 64KB).
- Backpressure must be managed via async generator iteration.
- SHA256 checksums are calculated incrementally over the stream and verified upon termination.

---

## 11. Events vs Direct Calls

| Criteria | Direct Capability Invocation | Event Engine Broadcast |
| :--- | :--- | :--- |
| **Communication Type** | Synchronous / Asynchronous Request-Response | Decoupled Publish-Subscribe |
| **Execution Dependency** | Caller expects immediate execution result | Publisher requires zero knowledge of subscribers |
| **Fail-Safety** | Failure returned directly in `UniversalResult` | Subscriber failures never crash publisher (Isolated) |
| **Primary Purpose** | Operational execution, queries, commands | Audit logging, cache invalidation, telemetry |

---

## 12. Service Discovery

Service discovery is managed dynamically by the Kernel Registry Engine (`kortex.engines.registry`).

- Engines register capabilities during boot phase (`register_capability()`).
- Callers query capability metadata (`find_capability()`) without hardcoding module paths.
- Capability health is verified via `IEngineDiagnostics.status()` prior to routing.

---

## 13. Versioning Rules

All service contracts enforce Semantic Versioning 2.0.0 (`MAJOR.MINOR.PATCH`):

- **MAJOR**: Breaking changes to input parameter names/types or removed response fields. Requires new capability namespace or major version bump.
- **MINOR**: Adding optional input parameters or supplementary response fields.
- **PATCH**: Internal performance optimizations or documentation fixes.

---

## 14. Timeout Rules

1. Every capability invocation MUST specify a hard `timeout_ms` threshold.
2. Default timeouts:
   - Metadata / Read Queries: 5,000ms
   - Operational Transformations: 30,000ms
   - Long Running Tasks: Handled via `LongRunningOperation` handle with 300,000ms limit.
3. Upon timeout, Kernel cancels the target task and returns `TIMEOUT_EXCEEDED`.

---

## 15. Retry Rules

1. Retries are permitted ONLY for idempotent capabilities (`UniversalCapabilityMetadata.is_idempotent = True`).
2. Retries MUST use exponential backoff with jitter (initial delay 100ms, multiplier 2.0, max attempts 3).
3. Non-idempotent operations MUST NOT be retried automatically by infrastructure.

---

## 16. Idempotency Rules

1. Idempotent requests MUST supply an `idempotency_key` (UUID string).
2. The Kernel checks `ICacheStore` for existing `idempotency_key` results before executing.
3. Duplicate requests within TTL (default 86400s) return cached `UniversalResult` instantly.

---

## 17. Cancellation Rules

1. Long-running or streaming operations MUST support cooperative cancellation tokens (`CancellationToken`).
2. Calling `kortex.system.operation.cancel` updates token status to cancelled.
3. Engines poll token state and abort execution cleanly, freeing memory and temporary workspace paths.

---

## 18. Correlation IDs

1. Every service request MUST carry a `correlation_id` UUID string.
2. If omitted by caller, Kernel generates a new `correlation_id`.
3. The `correlation_id` is propagated across all child capability calls, log entries, and emitted events for complete end-to-end trace auditing.

---

## 19. Security Requirements

1. **Permission Validation**: Kernel verifies `caller_identity` against required RBAC/ABAC permissions before capability dispatch.
2. **Payload Sanitization**: Input parameters are validated against `UniversalValidationReport` constraints.
3. **No Secret Leakage**: Passwords, tokens, or encryption keys MUST NOT be passed in plain parameter dictionaries or written to audit logs.

---

## 20. Acceptance Criteria

- ✓ **Canonical Architecture**: Serves as the authoritative specification for all inter-engine service contracts.
- ✓ **Clean Boundaries**: Zero direct engine-to-engine code imports exist.
- ✓ **Capability Compliant**: All service invocations use `kortex.<domain>.<resource>.<action>` format.
- ✓ **Model Integration**: Strictly consumes and returns universal domain models (`UniversalResult`, `UniversalError`, `UniversalMetadata`).
- ✓ **Fault Isolated**: Retries, timeouts, idempotency, and cancellation rules fully specified.
