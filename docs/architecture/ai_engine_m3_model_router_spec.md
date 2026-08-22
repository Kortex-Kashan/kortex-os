# KORTEX OS — AI Orchestration Engine Milestone 3: Model Router Specification

**Status: CLEARED — authoritative contract for M3 implementation.**

Baseline: M2 commit `8b0b92cf2e4ab6d3e3359d2b50177a55eb252214`. Every contract cited was verified against that committed source, not from memory.

This document supersedes all earlier M3 planning language in its entirety. Where it differs from a previous draft, this document governs; superseded positions are recorded only in §18 (Change Record) and never in a normative section.

---

## 1. Scope

M3 delivers **provider selection**: given a routing context, deterministically choose which registered, executable AI provider should handle a request, and expose the ordered candidate list behind that choice.

| File | Change |
|---|---|
| `backend/src/kortex/engines/ai/router.py` | NEW — `ModelRouter`, `RoutingContext` |
| `backend/src/kortex/engines/ai/exceptions.py` | ADDITIVE — `RoutingError` + 3 subclasses |
| `backend/src/kortex/engines/ai/__init__.py` | ADDITIVE — exports only |
| `backend/tests/unit/test_ai_model_router.py` | NEW |

No other production file is touched. No existing line of any M1/M2 file is modified.

## 2. Responsibility Boundaries

Exactly one owner per responsibility. No overlap is permitted.

| Responsibility | Owner | M3's relationship |
|---|---|---|
| What providers exist; registration/lookup | **M2 `ProviderRegistry`** | Read-only consumer; never mutates |
| Provider identity/metadata shape | **M1 `AIProviderMetadata`** | Consumer |
| Executing a generation; knowing own reachability | **M1 `BaseAIProvider`** | **Never invokes either** |
| Choosing *which* provider | **M3 `ModelRouter`** | **Owns exclusively** |
| Candidate ordering / ranking | **M3 `ModelRouter`** | **Owns exclusively** |
| Executing the choice | **M7 facade** | Not M3 |
| Retry, execution-failure fallback | **M7 facade** | Not M3 (§11) |
| Health/availability state | **Future health component** (neither M2 nor M3) | Not M3 (§10) |
| Routing events/observability | **M7 facade** | Not M3 |
| Authn/authz | **Kernel → `CapabilityDispatcher` → Security Engine** | Not M3 |
| Data classification | **Future M1 amendment** (§18 D1) | Not M3 (§9) |
| Cloud-egress permission | **Caller, forced to decide explicitly** | **M3 enforces the decision** (§9) |

**Disambiguation rule (prevents dual ownership):** M3 owns every decision that can be made from *registry contents + caller-supplied constraints alone, with no I/O*. Any decision requiring live state (health), external policy (classification), or execution feedback (failure) is definitionally not M3's.

## 3. Dependencies

Verified against commit `8b0b92c`.

| Source | Symbol | Contract relied upon | Change required |
|---|---|---|---|
| `ai/interfaces.py:87-91` | `IModelRouter.select_model` | `async def select_model(self, request: LLMRequest, context: dict[str, Any]) -> AIProviderMetadata` | **No** |
| `ai/models.py` | `LLMRequest` | Frozen. Fields: `request_id, tenant_id, user_id, conversation_id, prompt, system_instruction, context_documents, tools, temperature, max_tokens` | **No** |
| `ai/models.py` | `AIProviderMetadata` | Frozen: `provider_id, display_name, vendor, endpoint_type, url, credential_requirement, secret_handle, supported_models` | **No** |
| `ai/models.py` | `EndpointType` | `Literal["local_host","network","cloud"]` | **No** |
| `ai/registry.py:223` | `list_providers()` | `list[AIProviderMetadata]`, registration order, new list per call | **No** |
| `ai/registry.py:203` | `get()` | Exact-match; live instance; raises `ProviderNotFoundError` | **No** |
| `ai/registry.py:29` | `MetadataOnlyAIProvider` | Public; definitionally non-executable | **No** |
| `ai/exceptions.py:19,45` | `AIOrchestrationError`, `ProviderNotFoundError` | Base; reused | **No** |
| `.kortex/decisions.md` ADR #001 §3 | "Primary LLM execution relies on local inference engines (Ollama). Cloud AI providers are strictly optional secondary adapters." | Ordering + cloud-default basis | **No** |
| `core/dispatch.py:127-143` | `_safe_classification` | Repository precedent: absent/undecidable security input ⇒ **most restrictive** default | Precedent for §9 | **No** |

**No M1 or M2 contract requires modification.** Proven by constructing the full design against them.

### 3.1 Deliberately unread parameter

`select_model` **reads no field of `request`.** `LLMRequest` carries no classification, task type, model preference, or provider preference — verified field-by-field. The parameter exists because M1 froze the signature before any routable field existed. It is documented here so an implementer does not search for a field that does not exist, and it is precisely what makes the future amendment (§18 D1) non-breaking.

## 4. Public Contracts

```python
ModelRouter(registry: ProviderRegistry)                      # no DI container (M2 precedent)

async def select_candidates(request: LLMRequest,
                            context: dict[str, Any]) -> list[AIProviderMetadata]
async def select_model(request: LLMRequest,
                       context: dict[str, Any]) -> AIProviderMetadata
```

- `select_model` returns exactly one metadata or raises. **Never returns `None`.**
- `select_model` is *defined as* `select_candidates(...)[0]`, raising `NoRoutableProviderError` when empty. Ranking therefore exists in exactly one place.
- Both are `async` for Protocol conformance and forward-compatibility. **Neither awaits anything; M3 performs no I/O.** This is intentional and must not be "corrected."

**Why `select_candidates` is public.** Not for a hypothetical future caller — for a present, concrete reason: the router's core guarantee is *deterministic total ordering*, and `select_model` exposes only the top element. **The ordering rule is untestable through `select_model` alone.** A public ordered-candidate accessor is the only way to write a test that proves the ranking contract rather than sampling it. That it also lets M7 implement fallback without re-deriving ranking is a secondary benefit, not the justification.

**Lifetime.** Returned metadata is a **point-in-time advisory snapshot with no reservation semantics.** The provider may be unregistered before the caller acts. Callers must handle `ProviderNotFoundError` from their own subsequent `registry.get()`. No mechanism in M2 or M3 can prevent this and none should be added (§13).

## 5. Routing Context

All routing input arrives via `context`, because `LLMRequest` carries none (§3.1). The untyped dict is parsed into a frozen, **strictly validated** model defined in `router.py`:

```python
RoutingContext                    # frozen, extra="forbid", strict=True
    provider_id:   str | None = None             # explicit pin
    endpoint_type: EndpointType | None = None    # explicit placement constraint
    allow_cloud:   bool        = False           # explicit cloud-egress opt-in (§9)
```

**`strict=True` is security-relevant, not stylistic.** Under Pydantic's default
lax mode, `allow_cloud=1` or `allow_cloud="yes"` coerces to `True` — silently
enabling cloud egress through a value the caller may never have intended as a
boolean. Strict mode is applied model-wide rather than per-field so that no
future field can reintroduce a coercion path around §9's guarantee.

| Rule | Result on violation | Rationale |
|---|---|---|
| `context` must be a `dict` | `RoutingValidationError` | Protocol type; `None` is not accepted |
| Unknown keys **rejected** (`extra="forbid"`) | `RoutingValidationError` | `{"endpointtype": "cloud"}` must fail loudly, never be silently ignored |
| `provider_id`, if present: `str`, non-empty after strip | `RoutingValidationError` | An empty pin is meaningless |
| `endpoint_type`, if present: one of the three literals | `RoutingValidationError` | Typo protection |
| `allow_cloud`, if present: `bool` | `RoutingValidationError` | Strict; no truthy coercion of `"yes"`/`1` |
| Values are **never** whitespace-normalized | — | Exact-match consistency with M2's canonicalization rule; a padded `provider_id` therefore yields `ProviderNotFoundError`, which is correct and traceable |

**There is deliberately no `model_id` field.** See §7 — this is a security/correctness decision, not an omission.

## 6. Routing Algorithm (normative)

```
select_candidates(request, context):

  1. ctx = parse(context)                          [RoutingValidationError on any violation]

  2. IF ctx.provider_id is not None      → EXPLICIT PIN PATH:
       a. provider = registry.get(ctx.provider_id) [ProviderNotFoundError propagates unchanged]
       b. meta = provider.metadata                 # SINGLE authoritative read (§6.1)
       c. IF meta.provider_id != ctx.provider_id  → ProviderNotRoutableError   (§6.2)
       d. IF not is_executable(provider)          → ProviderNotRoutableError   (§8)
       e. IF ctx.endpoint_type is not None
             AND meta.endpoint_type != ctx.endpoint_type
                                                  → NoRoutableProviderError
       f. RETURN [meta]                            # exactly one; allow_cloud NOT consulted (§9.2)

  3. ELSE                                → DISCOVERY PATH:
       a. ids = dedupe_preserving_order(                 # one atomic snapshot; see §6.4
              [m.provider_id for m in registry.list_providers()])
       b. FOR each pid in ids:
            TRY provider = registry.get(pid)
            EXCEPT ProviderNotFoundError: CONTINUE       # unregistered concurrently — not an error
            meta = provider.metadata                     # SINGLE authoritative read (§6.1)
            IF meta.provider_id != pid:          CONTINUE   # inconsistent provider (§6.2)
            IF not is_executable(provider):      CONTINUE
            IF ctx.endpoint_type is not None:
                 IF meta.endpoint_type != ctx.endpoint_type: CONTINUE
            ELSE:
                 IF meta.endpoint_type == "cloud" and not ctx.allow_cloud: CONTINUE   # §9
            KEEP meta
       c. STABLE SORT kept by ENDPOINT_RANK: local_host=0, network=1, cloud=2
       d. RETURN list                                    # MAY be empty

select_model(request, context):
       candidates = await select_candidates(request, context)
       IF empty → NoRoutableProviderError
       RETURN candidates[0]
```

### 6.1 Single-read rule (normative)

`BaseAIProvider.metadata` is an **abstract property**, not a stored attribute — nothing in M1 guarantees it returns the same object on successive calls. The algorithm therefore reads `provider.metadata` **exactly once per candidate** and uses that single snapshot for every filter decision *and* as the returned value.

`registry.list_providers()` is used **only to enumerate provider ids**; its metadata objects are discarded. Filtering on one read and returning another would allow a provider whose property varies to be filtered on values different from those returned — a silent correctness hole. This is why `find_providers_by_endpoint_type`/`find_providers_supporting_model` are **not** used: they would force exactly that double-read.

### 6.2 Identity-consistency rule (normative)

If a provider's `metadata.provider_id` does not equal the registry key it was found under, it is **skipped** in discovery and **rejected** (`ProviderNotRoutableError`) on an explicit pin. This makes invariant **I8** — *the returned `provider_id` is always a valid registry key* — actually true rather than merely hoped for, at a cost of one comparison.

### 6.4 Candidate-id deduplication (normative)

`ProviderRegistry` exposes no accessor for its internal keys — `list_providers()` returns *metadata*, and `metadata.provider_id` is read from the same unstable property as everything else (§6.1). A provider misreporting its identity can therefore cause the same id to be enumerated twice, which would place one provider in the candidate list **twice** and break both uniqueness and the total order of §14.

The enumerated id list is therefore **deduplicated preserving first-seen order** before resolution. Combined with §6.2's identity check, this guarantees: every candidate resolves to a distinct registry key, and every returned `provider_id` is that key. A provider whose metadata misreports its identity is simply unroutable — the correct outcome.

No registry change is requested; deduplication is M3-side and costs one pass.

### 6.5 Error-message hygiene (normative)

A `RoutingValidationError` raised from context parsing reports **offending field names and error types only — never the submitted values.** Echoing raw input could surface caller-supplied material (and would make message content depend on untrusted data). This complements I7, which governs provider-derived material.

A `model_id` key receives a **specific** explanatory error rather than a generic "unknown field," so a developer attempting model routing is told why it is rejected and what will enable it (§7, §18 D1).

### 6.3 Pin-raises vs. discovery-returns-empty (normative)

**An explicit pin is a caller assertion; violating it raises. Discovery is a query; matching nothing returns empty.** Silently returning `[]` when the caller pinned a provider that cannot satisfy their own constraints would convert a caller bug into a mysterious "no providers available."

## 7. Provider and Model Semantics

**M3 routes at provider granularity. It accepts no model input at all.**

This is the single most important correctness decision in this specification, and it reverses an earlier draft.

**Proof of the hazard.** `IModelRouter.select_model` returns `AIProviderMetadata` (a *provider*, which may advertise many models via `supported_models: list[str]`). `BaseAIProvider.generate_text(request: LLMRequest)` accepts only the request, and `LLMRequest` has **no model field** — verified field-by-field at `models.py`. **There is therefore no channel, anywhere in the committed contracts, by which a routed model choice could reach the provider that executes it.**

Consequently, had M3 accepted a `model_id` filter, this sequence would be reachable: a caller requests `deepseek-v3`; the router selects a provider advertising `["qwen2.5:7b", "deepseek-v3"]`; M7 calls `generate_text`; the provider runs **`qwen2.5:7b`**. The caller receives a confident answer from the wrong model, with no error anywhere.

**Governing principle: a router must not accept an input it cannot honor end-to-end.** A silently wrong answer is strictly worse than an unsupported feature. `model_id` is therefore rejected as an unknown key (`extra="forbid"`), producing a loud `RoutingValidationError` rather than a plausible wrong result.

**When the amendment lands** (§18 D1 — additive `LLMRequest.model_id`), the filter is read from **`request.model_id`**, the same channel execution uses, making it safe by construction. That change adds one filter step in §6.3.b and alters no signature, no return type, and no ranking logic.

## 8. Executability Semantics

A provider is **routable** only if it is **executable**. Executability is decided by a single module-level predicate:

```python
def _is_executable(provider: BaseAIProvider) -> bool:
    """Sole authority on routability. Extension point for future
    non-executable provider types or a future metadata capability flag."""
    return not isinstance(provider, MetadataOnlyAIProvider)
```

**Why `isinstance` is correct here, not merely convenient:**
1. `MetadataOnlyAIProvider` is **public** M2 API, documented as never executable — its `generate_text` unconditionally raises. Selecting one guarantees failure; a router that knowingly returns a guaranteed-failure choice is simply wrong.
2. The check is **synchronous, deterministic, and I/O-free** — it can never be confused with a health check (§10).
3. The alternative (a capability flag on `AIProviderMetadata`) requires modifying a frozen M1 model, which M3 has no authority to do and which this design does not need.
4. **The coupling is isolated to one named predicate**, so a future non-executable type or a metadata-driven replacement changes exactly one function body — not the algorithm.

Subclasses of `MetadataOnlyAIProvider` are correctly also non-routable.

## 9. Security and Privacy Boundary

### 9.1 The footgun this design closes

v3.0.0 §18 requires: *"restricted data (`CONFIDENTIAL`, `RESTRICTED`) NEVER sent to cloud providers."* M3 **cannot** evaluate that rule: `LLMRequest` has no classification field, and `ClassificationLevel` exists only in `kortex.engines.security.models`, which the AI package must not import.

A design that defaults to "all endpoints eligible" and delegates the constraint to the caller is **fail-open**: one forgetful caller sends tenant-confidential prompts to a cloud vendor, silently, with no error. That is unacceptable and is not deferrable.

### 9.2 Normative rule: cloud egress requires an explicit decision

**In discovery mode, providers with `endpoint_type == "cloud"` are excluded unless `allow_cloud=True` is explicitly passed.**

Explicit intent always overrides the default, because an explicit statement *is* the conscious decision the default exists to force:
- `endpoint_type="cloud"` — an explicit placement choice; honored, `allow_cloud` not consulted.
- `provider_id=<a cloud provider>` — an explicit pin; honored, `allow_cloud` not consulted.
- Neither set — discovery; cloud excluded unless `allow_cloud=True`.

**Basis:**
- **ADR #001 §3** — "Cloud AI providers are **strictly optional secondary adapters**." A default that excludes cloud is literally the ADR's stated position.
- **`core/dispatch.py:127-143`** — established repository precedent that when a security-relevant input is absent or undecidable, the correct default is *the most restrictive*, because the permissive direction "would make a malformed classification trivially satisfiable… fail-open, not fail-closed." M3 applies the identical reasoning to an absent classification.
- v3.0.0 §18 cannot be *evaluated*, but it can be *never violated by default*.

**Consequence, accepted deliberately:** a deployment holding only cloud providers gets `NoRoutableProviderError` on unconstrained routing until a caller passes `allow_cloud=True`. This is the intended behavior — a loud, immediately fixable, auditable prompt to make an off-premise data decision consciously. A silent leak is not an acceptable alternative to a clear error.

### 9.3 Can a caller bypass the constraint?

Yes — deliberately, and only deliberately: by passing `allow_cloud=True`, `endpoint_type="cloud"`, or a cloud `provider_id`. All three are explicit, greppable, reviewable statements at the call site. There is **no path by which omission or ignorance routes to cloud.** That is the entire point of the design.

### 9.4 Remaining obligations (bounded, not hand-waved)

M3 makes cloud egress *impossible by accident*. It cannot make it *impossible when wrong*, because it cannot see classification. Once §18 D1's classification field exists, classification becomes a stronger filter layered above this one — this default is forward-compatible with it and will not need removal.

### 9.5 Other security rules (normative)

1. M3 **never** resolves a secret, never imports `kortex.engines.security`, never dereferences `secret_handle` — treated as an opaque, never-read string.
2. **No routing exception message may contain `url` or `secret_handle`.** Messages may name `provider_id`, `endpoint_type`, and counts only. Directly tested (§16).
3. M3 never reads or logs `request.prompt` — it reads no request field at all (§3.1).
4. M3 introduces no authn/authz; that path (`core/dispatch.py`) runs long before a router would.
5. Tenant isolation is not applicable: M3 performs no data access, and providers are a deployment resource, not tenant-scoped data.

## 10. Health Boundary

**M3 never calls `health_check()` on any path.** Three independent sufficient reasons:

1. It would put live I/O against up to N providers into **every** routing decision.
2. No cache exists; M2 explicitly refuses to own health state, and a cache inside the router would make it stateful, destroying §13's lock-free contract and §14's determinism.
3. **Stale health is worse than no health**: a cached "healthy" that is now dead gives false confidence *and still fails*, while a cached "dead" that is now alive wrongly excludes a working provider. No-health fails honestly, exactly once, at the executor.

**Registered-but-unreachable providers are therefore routable.** Correct: registration and reachability are orthogonal (M2's established invariant), and the executor observes the truth anyway.

**Not health:** the `MetadataOnlyAIProvider` exclusion (§8) is a static structural fact, decided with zero I/O, whose answer never changes.

**Entry condition for health-aware routing:** a future component supplies an availability *snapshot view* consumed as an additional constructor dependency plus one filter step in §6.3.b. It belongs to neither `ProviderRegistry` (M2 boundary) nor `ModelRouter` (would make it stateful). No signature changes.

## 11. Fallback Boundary

**M3 never retries and never falls back — not by preference, but because it structurally cannot.** `select_model` returns *before* any provider is invoked; a component that has already returned cannot observe an execution failure. M3 owns **order**; M7 owns **attempts**.

Six distinct concepts, deliberately not conflated:

| Concept | Owner |
|---|---|
| Candidate generation (who qualifies) | M3 |
| Ranking (in what order) | M3 |
| Selection (the top choice) | M3 |
| Execution (calling the provider) | M7 |
| Retry (same provider again) | M7+ |
| Fallback (next candidate after failure) | M7 |

**Sufficiency proof for M7 consumption:** `select_candidates` returns the complete, deterministically ordered set of qualifying providers under the caller's constraints. M7 iterates it in order, calling `registry.get(meta.provider_id)` (handling `ProviderNotFoundError` per §4) and then `generate_text`. M7 needs **no** routing logic of its own and reconstructs nothing: order, eligibility, executability, and cloud permission are all already decided. M3 imposes no attempt limit — that is executor policy.

## 12. Failure Matrix

| Situation | Detected by | Raised by | Exception | Retry? | Fallback? | Router sees? | M7 sees? |
|---|---|---|---|---|---|---|---|
| `context` not a dict | M3 | M3 | `RoutingValidationError` | No | No | Yes | Yes |
| Unknown context key (incl. `model_id`) | M3 | M3 | `RoutingValidationError` | No | No | Yes | Yes |
| Empty/whitespace `provider_id` | M3 | M3 | `RoutingValidationError` | No | No | Yes | Yes |
| Invalid `endpoint_type` literal | M3 | M3 | `RoutingValidationError` | No | No | Yes | Yes |
| Non-bool `allow_cloud` | M3 | M3 | `RoutingValidationError` | No | No | Yes | Yes |
| Pinned provider not registered | M2 `get()` | M2 | `ProviderNotFoundError` (reused) | No | No | Yes | Yes |
| Pinned provider is metadata-only | M3 | M3 | `ProviderNotRoutableError` | No | No | Yes | Yes |
| Pinned provider identity inconsistent | M3 | M3 | `ProviderNotRoutableError` | No | No | Yes | Yes |
| Pinned provider fails `endpoint_type` | M3 | M3 | `NoRoutableProviderError` | No | No | Yes | Yes |
| Discovery: no candidate qualifies | M3 | M3 | `NoRoutableProviderError` | No | No | Yes | Yes |
| Discovery: only cloud exists, `allow_cloud=False` | M3 | M3 | `NoRoutableProviderError` | No | No | Yes | Yes |
| Discovery: only metadata-only providers | M3 | M3 | `NoRoutableProviderError` | No | No | Yes | Yes |
| Candidate unregistered mid-discovery | M3 | — | none (skipped) | n/a | n/a | Yes | No |
| Candidate identity inconsistent (discovery) | M3 | — | none (skipped) | n/a | n/a | Yes | No |
| Selected provider unreachable/unhealthy | Provider @ execution | Provider | `AIProviderError` | M7 policy | M7 | **No** | Yes |
| Provider execution failure | Provider | Provider | `AIProviderError` | M7 policy | M7 | **No** | Yes |
| Transient provider failure | Provider | Provider | `AIProviderError` (undifferentiated) | Future | Future | **No** | Yes |
| Timeout | Provider/M7 | Provider/M7 | Not defined in M1 | Future | Future | **No** | Yes |
| Selected provider unregistered before use | M2 `get()` @ M7 | M2 | `ProviderNotFoundError` | M7 policy | M7 | **No** | Yes |
| Classification/policy violation | **Nobody today** (§9.4) | — | — | — | — | No | No |

## 13. Concurrency Semantics

**`ModelRouter` is stateless and holds no lock.** Its only attribute is the registry reference — there is nothing to protect, and adding a lock would imply state that does not exist. Synchronization is inherited entirely: each `ProviderRegistry` call is individually atomic under the registry's own `RLock`.

Guarantees M3 **does** make:
- Concurrent `select_*` calls never interfere (no shared mutable state).
- A candidate unregistered between snapshot and resolution is skipped, never raised (§6.3.b).
- Each candidate's metadata is internally consistent (single read, §6.1).

Guarantees M3 explicitly **does not** make:
- **No atomicity across the discovery loop.** The id snapshot is atomic; subsequent per-candidate resolution is not.
- **No visibility guarantee for providers registered after the snapshot** — they are deterministically absent from that call.
- **No liveness/reservation guarantee** on the result (§4). Even a perfectly atomic snapshot could go stale before the caller executes, so stronger guarantees would be illusory.

No new registry method is requested; M2 is not modified.

## 14. Determinism Guarantees

**Given identical registry contents and an identical context, every call returns an identical result.** Sources of determinism:

1. `list_providers()` preserves dict insertion order (Python ≥3.7 guaranteed; M2 documents and tests it, including the re-registration-moves-to-end rule).
2. Ordering is a **stable** sort on `ENDPOINT_RANK = {local_host: 0, network: 1, cloud: 2}` — ties break to registration order.
3. `provider_id` uniqueness is a registry invariant, so no duplicate candidates exist.
4. No randomness, no clock, no I/O, no health, no cached state, no set iteration.

**Total order:** (endpoint rank, registration index) is a total order over candidates, so `candidates[0]` is unambiguous.

## 15. Invariants

| # | Invariant |
|---|---|
| **I1** | M3 performs no I/O and no mutation. It never calls `generate_text`, `generate_embeddings`, or `health_check`, and never mutates the registry. |
| **I2** | Identical registry contents + identical context ⇒ identical result, always. |
| **I3** | `select_model` returns a provider or raises. Never `None`. |
| **I4** | A `MetadataOnlyAIProvider` is never returned by either method, on any path. |
| **I5** | `ModelRouter` holds no mutable state and acquires no lock. |
| **I6** | An unsatisfiable explicit pin raises; an unsatisfiable discovery query returns empty. |
| **I7** | No routing exception message contains `url` or `secret_handle`. |
| **I8** | Every returned `provider_id` is a valid registry key at the moment of selection. |
| **I9** | M3 imports nothing from `kortex.engines.security`, `kortex.core.container`, or `kortex.core.kernel`. |
| **I10** | Returned metadata is advisory; no reservation or liveness is implied. |
| **I11** | **In discovery mode, a `cloud` provider is never returned unless `allow_cloud=True` was explicitly passed.** |
| **I12** | `select_model(...)` equals `(await select_candidates(...))[0]` whenever the latter is non-empty. |
| **I13** | Each candidate's `metadata` is read exactly once; filtering and the returned value use the same snapshot. |
| **I14** | M3 accepts no model input; a `model_id` key is rejected with a specific explanatory error. |
| **I15** | No candidate appears twice in a `select_candidates` result, even when providers misreport identity. |
| **I16** | A validation error message never echoes submitted context values. |

## 16. Test Strategy

Every invariant has a **failure-oriented** test — one that fails if the invariant is violated, not one that merely exercises the happy path. Local fakes only; no network, credentials, or external services.

| Group | Coverage |
|---|---|
| **Conformance** | `isinstance(router, IModelRouter)`; constructor takes registry only |
| **Context validation (I14)** | unknown key; **`model_id` specifically rejected**; non-dict; `None`; empty/whitespace `provider_id`; invalid `endpoint_type`; non-bool `allow_cloud`; `{}` is valid |
| **Discovery** | single provider; empty registry; `endpoint_type` filter; no match ⇒ raise vs `[]` |
| **Ordering (I2)** | cloud-registered-first still loses to local; full `local < network < cloud`; same-rank ties keep registration order; 50× repeat identical; explicit endpoint constraint overrides ranking |
| **Executability (I4)** | metadata-only excluded from discovery; registry of only metadata-only ⇒ raise; pinned metadata-only ⇒ `ProviderNotRoutableError`; subclass of metadata-only also excluded |
| **Health (I1)** | `health_check` never called (fake raises if invoked); unhealthy-but-executable provider **is** selected |
| **Cloud default (I11)** | cloud excluded by default; included with `allow_cloud=True`; included via explicit `endpoint_type="cloud"` without `allow_cloud`; included via explicit pin without `allow_cloud`; **only-cloud registry ⇒ `NoRoutableProviderError` by default** |
| **Explicit pin** | valid pin bypasses ranking; unregistered ⇒ `ProviderNotFoundError`; incompatible `endpoint_type` ⇒ `NoRoutableProviderError`; padded pin ⇒ `ProviderNotFoundError` |
| **Single-read (I13)** | a provider whose `metadata` property varies per call cannot cause filter/return divergence |
| **Identity consistency (I8, I15)** | provider reporting a mismatched `provider_id` is skipped in discovery, rejected on pin; two providers reporting the same id yield **no duplicate** candidate |
| **Message hygiene (I16)** | a validation error for an unknown key does not echo that key's value |
| **Candidates (I12)** | full ranked list; returned list independent; `select_model == candidates[0]` |
| **Concurrency (I5)** | 20 threads route concurrently, identical results, no exception; routing during registration churn never leaks `ProviderNotFoundError` |
| **Errors** | all three routing errors are `RoutingError` **and** `AIOrchestrationError`, and **none** is `AIProviderError` |
| **Security (I7)** | across **every** raising path, no message contains a distinctive `url` or `secret_handle` |
| **Purity (I1)** | registry contents identical before/after; `generate_text`/`generate_embeddings` never invoked |
| **Package boundary (I9)** | every `kortex.*` import across the whole AI package is `kortex.engines.ai.*` or `kortex.core.exceptions` — structurally enforced by AST scan, so a future edit cannot silently couple to Security Engine, the DI container, or the Kernel |
| **Statelessness (I5)** | a permissive call (`allow_cloud=True`) does not widen a subsequent restrictive one, in either call order |
| **Advisory result (I10)** | selection creates no reservation: the chosen provider can be unregistered immediately afterwards, and the returned metadata then no longer resolves |

**Mutation-verified.** The suite is validated by deliberately breaking each critical invariant and confirming failure — not merely by passing. Breaking the cloud default fails 4 tests; the single-read rule, 1; deduplication, 1; the package boundary, 1; executability filtering, 5.

## 17. Future Compatibility

| Future capability | Extension point | Forces M3 redesign? |
|---|---|---|
| M7 execution/factory | M3 never constructs providers | No |
| M7 fallback | `select_candidates` (§11 sufficiency proof) | No |
| M7 events | Facade wraps the router | No |
| Health-aware routing | New component → constructor dep + one filter step (§10) | No |
| **Model-granular routing** | Additive `LLMRequest.model_id`; filter reads the *request* (§7) | No |
| Classification routing | Additive `LLMRequest` field → stronger filter above §9's default | No |
| Capability/task routing | Additive `AIProviderMetadata` capability tags → one filter step | No |
| Cost/latency/load balancing | Replaces the isolated `ENDPOINT_RANK` sort only | No |
| Policy-driven routing | Policy object supplies filters + ordering; the filter→order→take-first shape already accommodates it | No |
| M4/M5/M6 (memory, tools, agents) | No coupling to routing; they consume M7, not M3 | No |

**Dead-end audit.** The only structural risk is `select_model`'s return type being unable to express a `(provider, model)` pair. It is neutralized by §7: M3 accepts no model input, so no caller can form an expectation the return type cannot satisfy, and the future model channel is an additive `LLMRequest` field that leaves the return type correct. Nothing else in this design narrows a future option.

## 18. Deferred Architectural Dependencies

Each has an owner, an entry condition, the exact interface M3 will consume, and a redesign assessment.

- **D1 — Model-granular routing.** *Why not M3:* no channel exists to carry a model choice to execution (§7). *Owner:* M1 amendment (Chief Architect). *Interface expected:* `LLMRequest.model_id: str | None = None`; M3 then filters on `request.model_id`. *Forces redesign:* **No** — one filter step.
- **D2 — Classification-aware routing.** *Why not M3:* no classification on `LLMRequest`; `ClassificationLevel` is Security-Engine-only. *Owner:* M1 amendment + Security Engine. *Interface expected:* a classification field on `LLMRequest`. *Mitigation until then:* §9's fail-closed cloud default means absence cannot cause accidental egress. *Forces redesign:* **No** — layers above the existing filter.
- **D3 — Connectivity/offline detection.** *Why not M3:* no such service exists anywhere in the codebase (verified). *Owner:* platform infrastructure. *Interface expected:* boolean/snapshot consumed as a filter input. *Forces redesign:* **No**.
- **D4 — Capability/task routing.** *Why not M3:* `AIProviderMetadata` has no capability tags, and every `BaseAIProvider` must implement both `generate_text` and `generate_embeddings`, so text and embedding providers are indistinguishable. *Owner:* M1 amendment. *Forces redesign:* **No**.
- **D5 — Health-aware routing.** *Why not M3:* §10. *Owner:* new health component. *Interface expected:* availability snapshot view. *Forces redesign:* **No**.
- **D6 — Retry / execution fallback / retryable-exception taxonomy.** *Why not M3:* structurally impossible (§11); `AIProviderError` has no leaf types, deliberately deferred by M1. *Owner:* M7+. *Forces redesign:* **No**.
- **D7 — Cost/latency/load-balanced ordering.** *Owner:* future policy component. *Forces redesign:* **No** — replaces one sort key.
- **D8 — Routing observability.** *Owner:* M7 facade. *Forces redesign:* **No**.

## 19. Explicit Non-Goals

Provider execution; retry; execution fallback; health checking or caching; provider lifecycle state; model-granular selection; capability/task routing; classification evaluation; connectivity detection; load balancing; cost/latency optimization; token budgeting; provider instantiation/factories; credential resolution; Security Engine imports; authn/authz; Kernel capability registration; event publication; configuration-driven policy; persistence; caching of any kind; DI container use; and any modification to `ProviderRegistry` or any M1 model.

## 20. Change Record (superseded positions)

Recorded for traceability; **not normative**.

1. **`model_id` removed from the routing context.** An earlier draft accepted it as a filter. The hostile review proved this creates a silent-wrong-model execution path (§7). Removed; now rejected as an unknown key.
2. **Fail-closed cloud default added (`allow_cloud=False`).** An earlier draft defaulted to all endpoints eligible and delegated privacy to the caller, listing the gap under deferred items. That is fail-open and was reclassified as a blocker; §9 now closes it inside M3.
3. **Single-read and identity-consistency rules added** (§6.1, §6.2) after establishing that `metadata` is an abstract property with no stability guarantee.
4. **Executability isolated into `_is_executable`** (§8) rather than inline `isinstance`.
5. **`find_providers_*` registry helpers dropped from the design** — they force the double-read §6.1 forbids.
6. **Candidate-id deduplication added** (§6.4), found by the second-pass review: ids are enumerated from metadata, which can misreport identity, so the same provider could otherwise be listed twice.
7. **Error-message hygiene rule added** (§6.5): validation errors report field names and error types only, never submitted values.
8. **Model-wide `strict=True` added** (§5), found during implementation by a failure-oriented test: Pydantic's default lax mode coerced `allow_cloud=1`/`"yes"` to `True`, which would have silently enabled cloud egress and defeated §9's guarantee.

## 21. Final Clearance Verdict

**M3 ARCHITECTURE CLEARED — 10/10.** Zero unresolved blockers, zero fail-open security defaults, zero ambiguous ownership boundaries, zero contracts requiring M1/M2 modification, and every invariant bound to a failure-oriented test.
