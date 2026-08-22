# KORTEX OS — AI Orchestration Engine Milestone 2: Provider Registry & Provider Lifecycle Specification

Status: IMPLEMENTED — pending commit; audited against the shipped code in
`backend/src/kortex/engines/ai/registry.py` and
`backend/tests/unit/test_ai_provider_registry.py` (see Section 23 for how
each originally-open decision was resolved)
Depends On: AI Orchestration Engine Milestone 1 (commit `61847baa`)
Target File: `backend/src/kortex/engines/ai/registry.py` (implementation, not part of this document)

This document is a specification only. No production code was created or
modified to produce it. It was written after a fresh, direct inspection of
the committed M1 implementation and of the current state of Core, Connector
Engine, Registry Engine, Security Engine, and Knowledge Engine.

---

## 1. M2 Objective

Establish `ProviderRegistry`: the mechanism by which the AI Orchestration
Engine tracks which `BaseAIProvider` instances exist, how they are
identified, how they are registered/unregistered, how they are looked up
and enumerated, and what "healthy" means for one of them. M2 answers
"which providers exist and how do I access them?" — nothing about which
provider a given request *should* use (that is M3).

## 2. Relationship to M1

M1 (commit `61847baa`, `backend/src/kortex/engines/ai/`) already froze the
contracts M2 builds against. Fresh inspection of the committed files:

- `models.py`: `AIProviderMetadata` (frozen Pydantic model — `provider_id`,
  `display_name`, `vendor`, `endpoint_type: Literal["local_host","network","cloud"]`,
  `url`, `credential_requirement`, `secret_handle`, `supported_models: list[str]`),
  with a `model_validator` already enforcing that any provider declaring a
  credential requirement carries a `secret_handle`. `LLMRequest`/`LLMResponse`
  are not directly relevant to M2.
- `base_provider.py`: `BaseAIProvider(ABC)` — abstract `metadata` property,
  derived `provider_id`/`supported_models` properties, abstract
  `generate_text`, `generate_embeddings`, `health_check() -> bool`. No
  `close()`/`shutdown()`/version field/status enum exists.
- `interfaces.py`: `IBaseAIProvider` (structural twin of `BaseAIProvider`),
  `IAIOrchestrationEngine.register_provider(provider: AIProviderMetadata) -> None`
  (facade-level signature, not yet implemented — that's M7).
- `exceptions.py`: `AIOrchestrationError` (base, inherits `KortexError` per
  `kortex/core/exceptions.py`) and `AIProviderError` (provider *execution*
  failures). Its own docstring states leaf exception types are deferred to
  "Milestone 2... once the reference provider's error-simulation behavior
  determines which are actually needed" — M2 extending this file is
  anticipated by M1, not a redesign of it.
- `events.py`: four events (generation/tool/agent lifecycle). No
  provider-registration event exists.

**No actual architectural contradiction was found between M1 and the
broader KORTEX architecture during this investigation.** M1's design
choices (no version field, no status enum, `KortexError` base) remain
consistent with what follows below. This document does not propose
modifying any M1 file.

## 3. Architectural Context

Fresh inspection of `backend/src/kortex/core/{kernel.py,container.py,dispatch.py}`
and `backend/src/kortex/engines/{connector,registry}/` establishes the
governing precedent decisively:

- **`kortex.engines.registry.engine.RegistryEngine`** is the Kernel-level,
  cross-cutting registry. Its typed surface is `register_engine`/`get_engine`,
  `register_module`/`get_module`, `register_recipe`/`get_recipe`,
  `register_template`/`get_template`, `register_connector`/`get_connector`,
  `register_service`/`get_service`, plus `register_capability`/`get_capability`
  (all backed by one generic `register_resource(name, category, instance, ...)`
  / `RegistryCategory` enum — `registry/engine.py:29-38,153-244`).
- **Connector Engine does not use any of this for its own driver plugins.**
  `ConnectorEngine.initialize()` (`connector/engine.py:171-227`) registers
  exactly four **Kernel capabilities** (`kortex.connector.action.execute`,
  `kortex.connector.driver.register`, `kortex.connector.driver.list`,
  `kortex.connector.profile.get`), each with `required_permissions`. It never
  calls `kernel.register_connector(...)` for a driver. Driver plugins live
  and die entirely inside Connector Engine's own `ConnectorDriverRegistry`
  (`connector/registry.py`), a plain, Kernel-unaware class holding
  `dict[str, dict[str, BaseConnectorDriver]]` behind a `threading.RLock`.
- **`kortex.core.container.Container`** (the Kernel's DI container) is used
  to register *engine instances* and a handful of core singletons
  (`kernel`, `container`, `db`) — never an engine's internal plugin
  registry. `ConnectorDriverRegistry` has zero `Container` dependency.
- **`kortex.core.dispatch.CapabilityDispatcher`** (`core/dispatch.py`) is now
  a fully wired enforcement path: `Kernel.invoke_capability()` resolves the
  `CapabilityDescriptor`, authenticates via `SecurityEngine.authentication_manager`,
  authorizes via `SecurityEngine.authorize()`, audits both outcomes, then
  invokes the handler. This did not exist when earlier AI planning rounds
  in this project flagged "no engine has an established authorization
  call-path" — it now does, and it is Kernel/Security-owned end to end. M2
  does not touch it (no capability registration happens in M2), but M2's
  spec is written knowing this path exists for M7 to use later.

**Conclusion:** the strongest, most directly analogous, and most complete
precedent for M2 is `ConnectorDriverRegistry`, not `RegistryEngine`. Every
structural decision below mirrors it unless stated otherwise, with
reasoning given for each deviation.

Knowledge Engine was also inspected (`knowledge/sources.py`): it defines
only `ReferenceSourceProvider`, a single reference implementation with no
registry class of its own (no register/unregister/lookup/lock semantics).
It is not a competing precedent for a multi-provider registry.

## 4. Provider Registry Responsibility

`ProviderRegistry` owns exactly: accepting a `BaseAIProvider` instance under
a `provider_id`, rejecting invalid or duplicate registrations, retrieving a
previously registered instance by id, enumerating registered providers'
metadata, and removing a registration. It owns nothing about which
provider a request should use, how a provider is authenticated, or what a
provider does when invoked.

## 5. Provider Identity

**Canonical identity: `AIProviderMetadata.provider_id: str` alone.**

Connector Engine's `DriverMetadata` uses a compound `(driver_id, version)`
key with full SemVer resolution (`connector/registry.py:39-62,149-200`).
M1's `AIProviderMetadata` (already committed, frozen) has **no version
field**. Adding one now would mean modifying the M1 model, which is out of
this milestone's authority — per the instruction to stop rather than
silently change M1, this is not done. Consequence, stated plainly rather
than hidden: M2 supports exactly one registered instance per `provider_id`,
not multiple versions of the same provider coexisting. If multi-version
provider registration is ever required, it requires an explicit M1
amendment decision first (see Open Decision 3, section 23).

**Canonicalization rule (added during final pre-commit audit):**
`provider_id` must be supplied already in canonical form — non-empty and
with no leading/trailing whitespace. `register()` rejects a padded value
with `ProviderValidationError` rather than silently trimming it. The
initial implementation stripped whitespace only when computing the
internal dict key, while leaving `provider.metadata.provider_id` (and
therefore whatever `list_providers()`/`find_providers_*()` return) in its
original, unstripped form — a genuine bug allowing the registry's key and
the metadata callers observe to diverge, reproduced and fixed before
commit. `get()`/`unregister()` correspondingly perform an exact string
match with no normalization of their own.

## 6. Provider Lifecycle

```
                 register()
                     │
                     ▼
              ┌─────────────┐
   (absent) ──▶  REGISTERED  │◀── remains here regardless of health_check() result
              └─────┬───────┘
                     │
        get(id) ─────┼───── list_providers() / find_*(...)
        (returns      (returns metadata for all
        live instance) REGISTERED providers)
                     │
                unregister(id)
                     │
                     ▼
                 (absent)
```

There is no intermediate "pending," "validating," or "health-degraded"
state. A provider is either registered or it is not — matching M1's
deliberately minimal `health_check() -> bool` contract (no status enum) and
Connector Engine's own registry, which has no lifecycle states beyond
present/absent either. `BaseAIProvider` defines no `close()`/`shutdown()`
method, so `unregister()` performs no cleanup call on the removed instance
— it cannot, because M1 gives it nothing to call. If provider-side resource
cleanup is ever needed, that requires adding a method to `BaseAIProvider`
first (an M1 amendment, not M2's to decide).

## 7. Registration Contract

`register(provider: BaseAIProvider | AIProviderMetadata) -> BaseAIProvider`

- Accepts either a live `BaseAIProvider` instance, or a bare
  `AIProviderMetadata` (wrapped internally in a new `MetadataOnlyAIProvider`,
  directly mirroring `MetadataDriverWrapper` at `connector/registry.py:65-86`
  — useful for registering a provider's existence/metadata before a real
  implementation is wired in, exactly Connector's own stated use case).
- Validates: `provider.metadata` is accessible without raising (if it
  raises, wrap and re-raise as `ProviderValidationError`, mirroring
  `connector/registry.py:174-184`'s exact pattern); `provider.metadata` is
  an `AIProviderMetadata` instance; `provider_id` is non-empty. Credential
  consistency (secret handle required when a credential is declared) is
  **already enforced by M1's own `model_validator`** at object-construction
  time (`models.py`'s `_validate_credential_consistency`) — M2 does not
  re-implement this check.
- Duplicate `provider_id`: raises `ProviderAlreadyRegisteredError`
  (mirrors `ConnectorDriverError` at `connector/registry.py:191-194`). No
  silent overwrite, no version-based coexistence (see section 5).
- **Synchronous, not async.** Connector's `register_driver` is a plain
  `def`, never `async def`, even though the objects it registers have async
  methods. Registration is bookkeeping on an already-constructed object; it
  does not need to await anything. `register()` follows the same shape.
- **Does not call `health_check()`.** Per the explicit instruction not to
  assume this: Connector's `register_driver` never calls `test_connection()`.
  A newly registered provider's health is unknown until a caller explicitly
  checks it.
- Thread-safe via `threading.RLock`, matching `connector/registry.py:100-104`.

## 8. Lookup Contract

`get(provider_id: str) -> BaseAIProvider`

Returns the live, registered instance (not a copy, not just its metadata —
so a caller can immediately call `await provider.generate_text(...)` on
it, exactly as Connector's `get_driver_by_id` returns a live
`BaseConnectorDriver`). Unknown `provider_id` raises `ProviderNotFoundError`
— never returns `None` (mirrors `DriverNotFoundError` at
`connector/registry.py:298-299`, and is consistent with `RegistryEngine`'s
own `ResourceNotFoundError` convention at `registry/engine.py`).

## 9. Enumeration Contract

`list_providers() -> list[AIProviderMetadata]` — metadata only, not live
instances (mirrors `list_drivers() -> list[DriverMetadata]`,
`connector/registry.py:377-389`). Deterministic order: Python dict
insertion order (registration order), the same guarantee Connector's
implementation implicitly relies on.

Two additional read-only enumeration methods, directly justified by
existing M1 contracts and by Connector's `find_drivers_for_action`/
`find_drivers_by_capability` precedent (`connector/registry.py:391-449`):

- `find_providers_by_endpoint_type(endpoint_type: EndpointType) -> list[AIProviderMetadata]`
  — filters on `AIProviderMetadata.endpoint_type`, already an M1 field.
- `find_providers_supporting_model(model_id: str) -> list[AIProviderMetadata]`
  — filters on `AIProviderMetadata.supported_models`, already an M1 field.

**Deliberate deviation from Connector precedent:** Connector's
`get_driver_by_action`/`get_driver_by_capability` do not stop at returning
a list — they auto-select `matching[0]` and return a single live driver
(`connector/registry.py:333-375`). M2 does **not** provide an equivalent
single-result auto-pick method for endpoint type or model. Given this
milestone's explicit instruction to keep registry lookup and model routing
separate, auto-selecting "the" provider for a model — even by a simple
first-match rule — is a routing decision, not a lookup one, and belongs to
M3. This is a considered deviation, not an oversight; flagged as Open
Decision 2 (section 23) in case the Chief Architect prefers matching
Connector's convenience exactly instead.

## 10. Unregistration Contract

`unregister(provider_id: str) -> bool` — returns `True` if a provider was
removed, `False` if `provider_id` was not registered (mirrors
`unregister_driver`'s boolean-return, not-found-is-not-exceptional
convention at `connector/registry.py:202-229`, deliberately *not*
mirroring `get`'s raise-on-not-found convention — removal of something
already absent is a no-op fact, not an error, exactly as Connector treats
it). No cleanup hook is invoked (section 6).

## 11. Health Check Contract

M1 defines exactly `BaseAIProvider.health_check() -> bool`. M2 does **not**
add a status enum, a health-state model, latency telemetry, or a cached
health value — per the explicit prior decision reaffirmed by this milestone's
own instructions, and per the finding that `ConnectorDriverRegistry` itself
has **zero health-check-related method** (`test_connection` lives on
`BaseConnectorDriver` and is called directly by whoever holds a driver
instance — the registry never mediates it).

**M2's registry therefore does not expose any health-check method at all.**
A caller retrieves a provider via `get(provider_id)` and calls
`await provider.health_check()` directly, exactly as a caller of
Connector's registry calls `await driver.test_connection(profile)` directly.
This is the smallest defensible interpretation, and it keeps the registry
class entirely synchronous (section 7), avoiding a class that mixes sync
registry methods with one async delegation method for no precedented reason.

## 12. Failure Semantics

| Situation | Behavior |
|---|---|
| `provider.metadata` raises during `register()` | Wrapped and re-raised as `ProviderValidationError`, chained with `from err` |
| `provider.metadata` is not an `AIProviderMetadata` | `ProviderValidationError` |
| `provider_id` empty/whitespace-only | `ProviderValidationError` |
| `provider_id` has leading/trailing whitespace but is otherwise non-empty | `ProviderValidationError` — rejected, not silently trimmed (see Section 5) |
| Duplicate `provider_id` on `register()` | `ProviderAlreadyRegisteredError` — registration rejected, existing entry untouched |
| `get()`/lookup-by-id on unknown `provider_id` | `ProviderNotFoundError` |
| `unregister()` on unknown `provider_id` | Returns `False`, no exception |
| `provider.health_check()` returns `False` | Provider remains registered — health is orthogonal to registration (no existing precedent unregisters on unhealthy) |
| `provider.health_check()` raises | Not caught or normalized by the registry (it isn't even called by the registry — see section 11); a caller invoking it directly should expect `AIProviderError` per M1's exception design intent, but that is the caller's/provider's contract, not the registry's |
| A provider method raises a *non*-`AIProviderError` exception during `generate_text`/`generate_embeddings` | Entirely outside M2's scope — the registry never calls these methods |

## 13. Exception Semantics

Three new exceptions, added to `exceptions.py`, subclassing `AIOrchestrationError`
**directly** (not `AIProviderError`) — mirroring Connector's own separation
between registry/lookup-level errors (`ConnectorDriverError`,
`DriverNotFoundError`, both subclass `ConnectorEngineError` directly) and
execution-level errors (`ConnectorOperationError`, a sibling, not a
supertype). `AIProviderError` is reserved for provider *execution* failures
per M1's own docstring; registry bookkeeping failures are a different
concern and should not be caught by a handler that only wants to catch
execution failures.

- `ProviderAlreadyRegisteredError(AIOrchestrationError)`
- `ProviderNotFoundError(AIOrchestrationError)`
- `ProviderValidationError(AIOrchestrationError)`

This is exactly the extension M1's own `exceptions.py` docstring
anticipates ("Specific leaf exception types... are introduced in Milestone
2"), so it is not a redesign of M1.

## 14. Dependency Injection

**None.** `ProviderRegistry` has no dependency on `kortex.core.container.Container`,
matching `ConnectorDriverRegistry`'s complete absence of any `Container`
reference. It is a plain class, instantiated directly by whatever holds
it (in M7, that will be `AIOrchestrationEngine.__init__()`; for M2's own
tests, test code instantiates it directly). It takes no constructor
arguments beyond its own empty internal state, exactly like
`ConnectorDriverRegistry.__init__(self) -> None`.

## 15. Event Semantics

**No new event is required, and the registry does not publish events.**
Fresh inspection shows `ConnectorDriverRegisteredEvent` is published by
`ConnectorEngine` (the facade, `engine.py`), not by `ConnectorDriverRegistry`
itself — the registry class has no Event Engine or Kernel reference at all.
Event publication is a facade-layer concern, which for AI is M7's
responsibility, not M2's. If a future `AIProviderRegisteredEvent` is wanted,
it would be added to `events.py` and published by the M7 facade wrapping
calls into this registry — out of scope here.

## 16. Configuration Boundary

No configuration-file or environment-driven provider bootstrapping.
Registration is purely programmatic (`registry.register(provider)`),
matching Connector Engine, which has no auto-discovery/config-loading
mechanism feeding its own driver registry either (`ConnectorDriverLoader`
exists for *dynamic module loading* of a driver class from a module path,
a distinct, heavier mechanism — nothing in M2's boundary or in existing
precedent requires an equivalent for M2, and building one would be
importing router/bootstrap scope prematurely).

## 17. Security/Credential Boundary

Unchanged from M1, reaffirmed, not re-implemented: `AIProviderMetadata.secret_handle`
is the only credential-adjacent field; M2 never resolves it, never imports
`kortex.engines.security`, and never stores a raw credential. Credential
*consistency* validation (handle required when a credential type is
declared) already happens inside the frozen M1 Pydantic model, not in the
registry. The registry deals in provider identity and liveness only —
never in "is this credential valid," which remains exclusively Security
Engine's concern, invoked (later, not in M2) via `SecretStore` through a
handle, the same pattern Connector Engine already uses via its injected
`secret_resolver` (`connector/pipeline.py:42,49-51,125-161`).

## 18. Registry vs. Router Boundary

```
M2 (this milestone):
    registry.get("ollama-local")              -> BaseAIProvider instance
    registry.list_providers()                 -> [AIProviderMetadata, ...]
    registry.find_providers_supporting_model("qwen2.5:7b")
                                                -> [AIProviderMetadata, ...]  (candidates, unranked)

M3 (future, not built here):
    router.select_model(request, context)
        │
        ├─ inspects request (task type, privacy classification, offline state)
        ├─ calls registry.find_providers_supporting_model(...) / find_providers_by_endpoint_type(...)
        │      to obtain CANDIDATES
        ├─ applies ranking/policy (cost, latency, ADR-001 local-first default,
        │      entitlement tier) — none of this exists in M2
        └─ calls registry.get(chosen_provider_id) to obtain the live instance to execute against
```

The registry never ranks, scores, or picks among multiple matching
providers on the caller's behalf (section 9's deliberate deviation from
Connector's auto-pick convenience exists specifically to keep this line
sharp).

## 19. Future M3 Integration

M3's `ModelRouter` is expected to hold a reference to this `ProviderRegistry`
(passed in, not looked up via DI — consistent with section 14) and to call
only its read methods (`list_providers`, `find_providers_by_endpoint_type`,
`find_providers_supporting_model`, `get`). M3 introduces no new registry
method; if routing logic later needs a registry capability that doesn't
exist yet (e.g., filtering by a richer per-model capability tag), that is
an M3-scoped registry extension to request then, not something to
anticipate now.

## 20. Explicit Non-Goals

Model routing, automatic model selection, load balancing, failover
routing, prompt management, context management, conversation memory,
long-term memory, tool execution, agents, agent planning, workflow
orchestration, RAG, embeddings, vector databases, provider-specific
business logic, credential storage/management, authorization policy
implementation, new Security Engine behavior, new Kernel behavior, AI
billing, licensing enforcement, a provider health-state machine, a
provider `close()`/cleanup lifecycle, configuration-driven bootstrapping,
and any new Event Engine/DI Container/database abstraction.

## 21. Proposed Module/File Structure

```
backend/src/kortex/engines/ai/
    registry.py          # NEW — ProviderRegistry, MetadataOnlyAIProvider
    exceptions.py         # EXTENDED — + ProviderAlreadyRegisteredError,
                           #   ProviderNotFoundError, ProviderValidationError
                           #   (additive; existing AIOrchestrationError/
                           #   AIProviderError untouched)
```

No `router.py`, `pipeline.py`, `memory.py`, `tools.py`, `agents.py`, or
`engine.py` — nothing in this investigation shows the registry itself
requires any of them. `__init__.py` would gain the new exports (additive,
same pattern as its current form).

## 22. Test Strategy

New file: `backend/tests/unit/test_ai_provider_registry.py` (name already
anticipated by the original spec's folder structure). Fake providers only
— no real Ollama/OpenAI/Anthropic credentials, implemented as local
`BaseAIProvider` subclasses within the test file (mirroring `_StubAIProvider`
in `test_ai_models.py`).

Required tests:

1. Register a provider; `get()` returns the same instance.
2. Register via bare `AIProviderMetadata`; `get()` returns a
   `MetadataOnlyAIProvider` wrapping it.
3. Duplicate `provider_id` registration raises `ProviderAlreadyRegisteredError`;
   original registration is untouched.
4. `get()` on an unknown `provider_id` raises `ProviderNotFoundError`.
5. `unregister()` on a known id returns `True` and removes it; a
   subsequent `get()` then raises `ProviderNotFoundError`.
6. `unregister()` on an unknown id returns `False` (no exception).
7. `list_providers()` returns metadata for all registered providers, in
   registration order, and does **not** return anything for unregistered
   ones.
8. `find_providers_by_endpoint_type()` / `find_providers_supporting_model()`
   return correct filtered subsets, including an empty list when nothing
   matches.
9. A provider whose `health_check()` returns `False` remains fully
   retrievable via `get()` and `list_providers()` (health does not affect
   registration).
10. Registering an object whose `metadata` property raises is rejected
    with `ProviderValidationError`, chained from the original exception.
11. Registration/lookup/unregistration are independent across two
    separate `ProviderRegistry()` instances (isolation — no shared module-
    level state).
12. Concurrent registration of distinct `provider_id`s from multiple
    threads does not corrupt internal state (mirrors Connector's
    thread-safety intent; exercised via `threading`, not `asyncio`, since
    the registry itself is synchronous).
13. `register()` never calls `health_check()` (assert via a fake provider
    that raises if `health_check()` is invoked, to prove it is never
    called during registration).
14. Exception hierarchy: `ProviderAlreadyRegisteredError`,
    `ProviderNotFoundError`, `ProviderValidationError` are all
    `AIOrchestrationError` subclasses and explicitly **not**
    `AIProviderError` subclasses.

Coverage target: 100% of `registry.py`, matching M1's own achieved
coverage and Security/Connector Engine's established practice.

## 23. Open Architectural Decisions — Resolution

All three decisions originally raised here have been resolved by explicit
direction and are reflected in the shipped implementation:

**Decision 1 — Canonical dummy provider: RESOLVED as recommended.** M2
uses test-file-local fakes only (`test_ai_provider_registry.py`); no
shared `providers/dummy_provider.py` was built. Deferred to whichever
milestone first needs to reuse one across multiple test files.

**Decision 2 — Single-result convenience lookup: RESOLVED as recommended.**
`find_providers_by_endpoint_type`/`find_providers_supporting_model` remain
list-only, with no auto-pick-first-match method. Confirmed by the actual
M2 implementation instructions, which specified list-returning candidate
discovery only.

**Decision 3 — Provider versioning: RESOLVED as recommended.** No
`version` field was added to `AIProviderMetadata`. Not revisited.

**A fourth question was explicitly settled by direction, not by this
document's original recommendation:** whether `ProviderRegistry.register()`
should accept only a live `BaseAIProvider`, or also bare `AIProviderMetadata`
wrapped in `MetadataOnlyAIProvider`. This document's Section 7 always
specified the latter; a later implementation pass briefly argued for
requiring only a live instance, and was then explicitly overridden back to
this document's original design. The shipped code matches Sections 7 and
21 of this document as written — `MetadataOnlyAIProvider` is implemented.

---

# FINAL REPORT

### 1. M2 architectural conclusion

Build `ProviderRegistry` — a plain, synchronous, thread-safe, Kernel- and
DI-unaware class inside `kortex.engines.ai`, holding `BaseAIProvider`
instances keyed by `provider_id`, providing register/unregister/get/
enumerate operations and nothing else. No health-state machine, no
routing, no credential handling beyond what M1 already enforces.

### 2. M2 boundary

**Inside:** provider registration, unregistration, lookup by id,
enumeration (all providers, by endpoint type, by supported model),
duplicate/unknown-id handling, registration-time validation, three new
registry-level exceptions. **Outside:** everything in section 20 — most
importantly, any model-selection/ranking logic (M3), any health-state
model beyond M1's boolean (already decided), any credential resolution
(Security Engine's exclusive domain), and any Kernel capability
registration or event publication (M7).

### 3. Existing KORTEX precedents

Primary: `kortex.engines.connector.registry.ConnectorDriverRegistry`
(`connector/registry.py`) — structurally near-identical problem (manage
interchangeable, pluggable implementations behind one interface), fully
implemented, directly analogous. Secondary/confirmatory: `kortex.core.container.Container`
(singleton-caching convention), `kortex.engines.registry.engine.RegistryEngine`
(confirms, by contrast, that generic Kernel-level registration is *not*
used for this kind of plugin registry). Knowledge Engine's `sources.py`
was checked and found not to be a competing pattern (single reference
provider, no registry class).

### 4. Proposed interfaces/classes

- `ProviderRegistry` (`registry.py`, new) — `register`, `unregister`,
  `get`, `list_providers`, `find_providers_by_endpoint_type`,
  `find_providers_supporting_model`, `clear` (test utility).
- `MetadataOnlyAIProvider(BaseAIProvider)` (`registry.py`, new) — mirrors
  `MetadataDriverWrapper`.
- `ProviderAlreadyRegisteredError`, `ProviderNotFoundError`,
  `ProviderValidationError` (`exceptions.py`, extended) — all
  `AIOrchestrationError` subclasses.

No changes to `models.py`, `interfaces.py`, `base_provider.py`, or
`events.py`.

### 5. Data models

None new. M2 consumes `AIProviderMetadata` and `BaseAIProvider` exactly as
M1 committed them.

### 6. Lifecycle model

See section 6's diagram: `REGISTERED` ↔ absent, two states only, no
health-derived state transitions, no cleanup on removal (M1 gives
`BaseAIProvider` no cleanup hook to call).

### 7. Registry ↔ Provider relationship

The registry holds live `BaseAIProvider` instances (or
`MetadataOnlyAIProvider` wrappers) as singletons for the duration of their
registration. It never constructs a provider itself (no factory role) and
never calls any of a provider's execution or health methods on its
behalf — it only stores and returns them.

### 8. Registry ↔ Router relationship

M3 begins exactly where a caller needs to decide, among the candidates
`ProviderRegistry` can enumerate, which single one should handle a
specific request. The registry supplies candidate lists (section 9);
M3 supplies the decision. The registry never decides.

### 9. Security boundary

Security Engine owns credential resolution (`SecretStore`, invoked later
via `secret_handle`) and authorization decisioning
(`AuthorizationEngine`/`SecurityEngine.authorize()`, now fully wired via
`Kernel.invoke_capability()` per `core/dispatch.py` — confirmed in this
investigation, not used by M2). M2 owns nothing security-related beyond
already-inherited M1 field shapes; it introduces no new secret handling,
no new authorization logic, and no new Kernel capability.

### 10. Test plan

The 14 tests enumerated in section 22, targeting 100% coverage of
`registry.py`, using only local fake providers, no real credentials or
external services.

### 11. Open decisions

None remain open. All three (plus the `MetadataOnlyAIProvider` question)
are resolved per Section 23.

### 12. Recommendation

**IMPLEMENTED.** All decisions resolved, code and tests shipped, a
pre-commit audit found and fixed one genuine bug (provider_id
canonicalization — Section 5), and no architectural contradiction with M1
or the broader KORTEX architecture was found.
