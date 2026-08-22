# KORTEX OS — AI Orchestration Engine Milestone 5: Context Composition & Knowledge Retrieval Specification

**Status: CLEARED (final audit) — authoritative record of the implemented M5 contract.**

Baseline: M4 commit `fe31fba` (Conversation Memory & Persistence). Every contract cited was verified against committed and implemented source. Unlike the M2/M3/M4 specs, this document was written *after* implementation and final audit rather than before — a process gap identified and closed during the M5 final clean-path audit (§9). It records what was actually built and verified, not a forward plan.

---

## 1. Scope

**M5 = Context Composition & Knowledge Retrieval.** Carved out of M4's first planning pass, which bundled `PromptPipeline`, `IKnowledgeQueryPort`, and `RetrievedDocument` into memory (see `ai_engine_m4_context_memory_spec.md` §1). The second M4 architecture review proved zero runtime coupling (DTO-only), no consumer inside M4, a different risk discipline (content security vs. data correctness), and an effectively-permanent schema-mistake cost — all reasons to split rather than share a review pass.

**M5 delivers** the layer that turns stored conversation history plus retrieved knowledge into a safely assembled `LLMRequest`, and the port through which Knowledge Engine is reached without the AI package ever importing it.

| # | Milestone | Status |
|---|---|---|
| M1 | Foundation & Contracts | committed `61847ba` |
| M2 | Provider Registry & Lifecycle | committed `8b0b92c` |
| M3 | Model Router | committed `07f9ff0` |
| M4 | Conversation Memory & Persistence | committed `fe31fba` |
| **M5** | **Context Composition & Knowledge Retrieval** | **this document** |
| M6 | Tool Invocation | pending |
| M7 | Agent Orchestration | pending |
| M8 | Engine Facade, Kernel, Security, Storage & Diagnostics | pending |
| M9 | Full Integration & Closure | pending |

## 2. Purpose

Compose a request-ready `LLMRequest` from conversation history and (optionally) retrieved knowledge, without ever letting untrusted content forge a trust boundary, and without the AI package acquiring a dependency on Knowledge Engine, Security Engine, or the Kernel.

## 3. Deliverables

| File | Change |
|---|---|
| `backend/src/kortex/engines/ai/retrieval.py` | NEW — `RetrievedDocument`, `IKnowledgeQueryPort`, `InMemoryKnowledgeQueryPort`, classification constants and normalizers |
| `backend/src/kortex/engines/ai/pipeline.py` | NEW — marker registry, `PromptPipeline`, `ContextComposer` |
| `backend/src/kortex/engines/ai/memory.py` | ADDITIVE — `_sanitize_for_rendering` → public `sanitize_context_content`; `_require_identifier` → public `require_identifier` (both promoted because M5 reuses them rather than duplicating a security primitive) |
| `backend/src/kortex/engines/ai/exceptions.py` | ADDITIVE — `ContextCompositionError`, `KnowledgeRetrievalError` |
| `backend/src/kortex/engines/ai/__init__.py` | ADDITIVE — exports only |
| `backend/tests/unit/test_ai_retrieval.py` | NEW — 27 tests |
| `backend/tests/unit/test_ai_pipeline.py` | NEW — 54 tests |

**Untouched:** `models.py`, `interfaces.py`, `base_provider.py`, `events.py`, `registry.py`, `router.py`, `persistence.py`. The existing AST dependency-boundary test (`test_ai_package_imports_no_forbidden_dependency`) required no change — the new modules fall under the existing `base_allowed` set and import nothing else from the `kortex` tree.

## 4. Non-Goals

The real Knowledge Engine adapter (M8 — see §8.3); Kernel construction, composition root, or any application wiring (M8); a `messages`-array amendment to `LLMRequest` (M1, deferred, §9 D1); token counting, token-budget truncation, or any context-window sizing (no tokenizer exists in the platform); semantic/embedding-based retrieval or relevance ranking (Knowledge Engine performs neither, §8.3); result caching; citation rendering (`source_id` is retained but never surfaced, §6.1); retry of any kind; tool invocation (`[[tool]]` is reserved, not implemented, §7.2); agent orchestration; provider execution or routing (`ContextComposer` never contacts a provider and reads no routing field).

## 5. Ownership — Frozen

| Responsibility | Owner | Everyone else |
|---|---|---|
| Turn → safe string rendering (markers, sanitization) | **M4** `AIMemoryManager.get_context` | M5 consumes the output verbatim, never re-renders |
| Identifier validation before any retrieval boundary | **M4** `require_identifier`, reused by M5 | No caller may skip it |
| Knowledge retrieval contract & bounded/filtered policy | **M5** `IKnowledgeQueryPort`, `ContextComposer` | M4 has no knowledge dependency at all |
| Prompt assembly (trust layers, final `LLMRequest`) | **M5** `PromptPipeline` | M4 never builds an `LLMRequest`; M3 never reads assembled content |
| Real knowledge adapter, session-token binding | **M8** (deferred, §8.3) | M5 defines the port only |
| Provider execution, routing | M3 / M8 | M5 never contacts a provider, never routes |
| Classification authority (real semantics) | nobody yet — Security Engine, deferred | M5's allowlist is defense-in-depth only, not authoritative |

**Disambiguation rule:** M5 owns everything derivable from *conversation history plus a retrieved-document list*, with no principal, Kernel reference, or live knowledge query of its own. Anything needing a session token, a real `KnowledgeQuery`, or Knowledge Engine's own models is outside M5.

## 6. Contracts (as implemented)

### 6.1 Retrieval (`retrieval.py`)

```python
PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED = "PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"
KNOWN_CLASSIFICATIONS = frozenset({PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED})
DEFAULT_ALLOWED_CLASSIFICATIONS = frozenset({PUBLIC, INTERNAL})   # fail-closed

def normalize_classification(value: str | None) -> str | None: ...
def normalize_allowed_classifications(values: frozenset[str]) -> frozenset[str]: ...

class RetrievedDocument(BaseModel):          # frozen
    content: str                              # adapter-rendered; never a dict
    classification: str | None = None         # normalized then matched; never interpreted
    source_id: str | None = None              # provenance only — never rendered into a prompt

class IKnowledgeQueryPort(Protocol):
    async def search(self, tenant_id: str, query_text: str,
                     max_results: int) -> list[RetrievedDocument]: ...

class InMemoryKnowledgeQueryPort(IKnowledgeQueryPort):
    """Dev/test reference implementation: case-insensitive substring match over a canned corpus."""
```

`source_id` exists solely for future observability/audit. It is never inserted into an assembled `LLMRequest`; M5 exposes no citation feature. A dedicated test (`test_source_id_never_appears_in_assembled_context`) asserts a distinctive `source_id` value never appears in any assembled `context_documents` entry.

### 6.2 Assembly (`pipeline.py`)

```python
USER_MARKER = "[[user]]"            # M4
ASSISTANT_MARKER = "[[assistant]]"  # M4
KNOWLEDGE_MARKER = "[[knowledge]]"  # M5
TOOL_MARKER = "[[tool]]"            # reserved — M6, not implemented
MARKER_SENTINEL = "[["
RESERVED_CONTEXT_MARKERS: dict[str, str]   # the single marker registry

class PromptPipeline:                         # pure, synchronous, no I/O
    def assemble(self, request: LLMRequest, history_entries: list[str],
                 documents: list[RetrievedDocument]) -> LLMRequest: ...

class ContextComposer:                        # async orchestration
    def __init__(self, memory: AIMemoryManager, pipeline: PromptPipeline,
                 knowledge: IKnowledgeQueryPort | None = None,
                 max_documents: int = 5,                       # clamped [1, 50]
                 allowed_classifications: frozenset[str] = DEFAULT_ALLOWED_CLASSIFICATIONS): ...
    async def compose(self, request: LLMRequest, *,
                      knowledge_query: str | None = None) -> LLMRequest: ...
```

`assemble` returns a **new** request via `model_copy(update=...)`; `LLMRequest` is frozen and is never mutated. There is deliberately **no `[[system]]` marker anywhere in the package** — the trusted layer is carried only by `LLMRequest.system_instruction`, a separate field, so no in-band token exists for untrusted content to forge one.

## 7. Normative Rules

### 7.1 Assembly order and trust handling

```
system_instruction   ← passed through UNTOUCHED (trusted; separate field)
context_documents = [ caller-supplied entries ]   ← position preserved, SANITIZED, unmarked
                  + [ [[knowledge]] entries ]      ← sanitized, marked by M5
                  + [ history entries ]            ← inserted VERBATIM from memory.get_context()
prompt               ← passed through UNTOUCHED
```

1. **Every untrusted entry M5 places into `context_documents` is sanitized, with no exemptions — including the caller's own `context_documents`.** A caller entry containing `[[assistant]]` would otherwise forge a role boundary; a guarantee with an exemption is not a guarantee. Caller entries keep their position and receive no marker: M5 sanitizes the caller's data but does not relabel it.
2. **History entries are never re-sanitized.** `AIMemoryManager.get_context()` returns entries that are already sanitized and already marked. Re-sanitizing `[[user]]\ntext` would rewrite the marker itself into `[ [user]]` and destroy it — verified empirically, not assumed. History entries are inserted verbatim.
3. **Ordering — caller → knowledge → history — is tunable policy, not a correctness invariant.** History sits adjacent to the current prompt for conversational recency; knowledge is background framing; caller entries lead because they are the application's own explicit framing.
4. Sanitization is a single implementation (`memory.sanitize_context_content`), imported by `pipeline.py` rather than redefined — a security primitive with two implementations is a security primitive with two behaviours.

### 7.2 Context marker registry

| Marker | Owner | Status |
|---|---|---|
| `[[user]]` | M4 | in use |
| `[[assistant]]` | M4 | in use |
| `[[knowledge]]` | M5 | introduced here |
| `[[tool]]` | M6 | **reserved — not implemented** |

Every registered marker begins with `MARKER_SENTINEL` (`[[`) — the exact prefix sanitization neutralizes — so a future marker using a different delimiter would silently fall outside the anti-forgery guarantee; a test asserts this for every entry, including the reserved one.

### 7.3 Retrieval policy

1. **Retrieval is opt-in.** Without `knowledge_query`, `IKnowledgeQueryPort` is never called.
2. **Blank/whitespace-only query is treated as no query** and skips the port (Knowledge Engine's `KnowledgeQuery.query_text` has `min_length=1`; a blank string is never forwarded).
3. **Retrieval requested with no port configured raises `ContextCompositionError`** — the anti-dead-port rule. This mirrors and deliberately avoids Connector Engine's `secret_resolver` failure mode, where an unwired injection port silently no-ops rather than failing.
4. **Port failure raises `KnowledgeRetrievalError`** (approved fail-loud posture — an ungrounded answer indistinguishable from a grounded one is worse than a visible failure).
5. **A port returning more documents than `max_documents` raises `KnowledgeRetrievalError`.** Truncating silently was considered and rejected during planning as itself a silent-truncation violation; an over-returning adapter is a contract violation worth surfacing, not absorbing.
6. **`tenant_id` is always taken from `request.tenant_id`**, never from any other source — no path can query another tenant's corpus.
7. **Records only, never nodes** — Knowledge Engine's own `search_graph` does not trust-filter nodes; only the adapter (M8) can honour this, but the port contract states it here so it cannot be forgotten.
8. **Identifiers are validated before any retrieval boundary is reached.** `ContextComposer.compose` calls `require_identifier` on `request.tenant_id` and `request.conversation_id` as its first two statements — strictly before `_retrieve` (which reaches the knowledge port) and before `AIMemoryManager.get_context` (which reaches the memory store). See §9 for why this ordering is a normative rule rather than an implementation detail.
9. **Classification filtering is fail-closed** (§7.4) and duplicate documents (by exact `content`) are removed, keeping the first occurrence — normalization, not truncation, since it removes redundancy only and cannot lose information.

### 7.4 Classification normalization

Comparison is string-based; Security Engine's `ClassificationLevel` is never imported — this filter is defense-in-depth, not the authoritative classification model.

1. Reject `None` outright — absent classification is unknown, never public.
2. **Reject any value containing a non-ASCII character before case-folding.** `("publ" + chr(0x131) + "c").upper() == "PUBLIC"` evaluates `True` while `.isascii()` on the same string evaluates `False` — a real, empirically-verified bypass that an ASCII guard closes.
3. Normalize with `value.strip().upper()`.
4. Reject if not in `KNOWN_CLASSIFICATIONS`, and reject if not in the identically-normalized `allowed_classifications` — unknown is never assumed safe.
5. The allowlist is normalized once, at `ContextComposer` construction, so `{"public"}` behaves identically to `{"PUBLIC"}` rather than silently matching nothing.

### 7.5 Context length boundary

M5 does not calculate tokens (no tokenizer exists in the platform, and `AIProviderMetadata` carries no context-window field), does not silently truncate content (`max_documents` is an explicit retrieval bound, not a length-fitting operation), and does not retry. Oversized-context and provider context-window failures belong to M8 / provider execution; M5 emits no size guarantee and must not imply one.

## 8. Architecture Verification

### 8.1 M1 (contracts)

No contract change. Assembly uses `LLMRequest.model_copy(update=...)` on a frozen model; `context_documents: list[str]` is sufficient. Providers that parse markers get structure; providers that ignore them see harmless prefixed text.

### 8.2 M3 (routing)

`ModelRouter.select_model` reads no field of `LLMRequest` — verified against committed M3 source. No retrieved document or history entry can therefore influence provider selection or cloud egress; injected content cannot steer where tenant data is sent. The residual interaction is by design: a `CONFIDENTIAL` document can still reach a cloud provider only if a caller both opts into that classification *and* sets `allow_cloud=True` — two independently fail-closed gates, each requiring deliberate action, which is defense in depth rather than a single point of failure.

### 8.3 Knowledge Engine boundary — why the real adapter is deferred to M8

The dispatcher splats parameters into the handler (`handler(**request.parameters)`), and `kortex.knowledge.query.search`'s handler signature is `async def search(self, query: KnowledgeQuery)`. Calling it therefore requires constructing a real `KnowledgeQuery`, which requires importing `kortex.engines.knowledge` — forbidden in the AI package without exception. The capability is also registered `requires_authentication=True`, so the call needs a session token that `LLMRequest` does not carry. Both concerns are pushed to the adapter, which the AI package cannot host; **M8 supplies it, together with the session token.**

No composition root exists anywhere in `backend/src` today (`Kernel()` is never instantiated in production code, and Connector Engine's own `secret_resolver` injection port has been unwired since it shipped, wired only by test doubles) — the unwired-port risk is platform-wide, not M5-specific, which is additional evidence that deferring the real adapter to M8 fits the platform's current stage rather than cutting a corner.

### 8.4 M6 / M7 / M8 compatibility

- **M6 Tool Invocation:** `[[tool]]` is reserved in the marker registry; assembly gains one branch. No redesign required.
- **M7 Agent Orchestration:** `ContextComposer` is stateless apart from its injected collaborators, so it is safe to reuse per agent step. No redesign required.
- **M8 Facade:** supplies the real `IKnowledgeQueryPort` adapter and session token. Two items are handed to M8 explicitly rather than left implicit:
  - **F11 (Kernel-annotation gap):** `kortex.core.kernel` is on the AI package's forbidden-import list and the AST boundary scan is unconditional (it fails even a `TYPE_CHECKING`-guarded import), yet `BaseEngine.initialize(self, kernel: Kernel)` requires the facade to annotate a `Kernel` parameter. M8 will need a second designated boundary module (parallel to `persistence.py`) to hold that annotation. This is an M8 problem; M5 does not need a Kernel reference anywhere and introduces none.
  - **Request-scoped authority requirement:** `IKnowledgeQueryPort` carries no credential by design. M8 must bind the real adapter to the *requesting principal's* authority, never a long-lived service principal — otherwise knowledge retrieval becomes an intra-tenant privilege-escalation path. `ContextComposer` is cheap and stateless, so per-request construction is fine.

### 8.5 Dependency boundary scan

`retrieval.py` and `pipeline.py` import only `kortex.engines.ai.*`, `kortex.core.exceptions`, pydantic, and stdlib — no `sqlalchemy`, no `kortex.core.db`, no Kernel, no Security Engine, no Knowledge Engine. `test_ai_package_imports_no_forbidden_dependency` (AST-based, unconditional, scans every `kortex.engines.ai` module) passes unchanged against the full M5 file set.

## 9. Final Audit Findings (recorded here because this is where the contract lives)

The M5 final clean-path audit found and fixed one genuine defect against this contract, in addition to a mechanical lint fix and an unrelated line-ending artifact (both reported separately, neither an architecture finding):

**Validation-ordering gap in `ContextComposer.compose`.** The initial implementation called `self._retrieve(...)` — which reaches the knowledge port — before `self._memory.get_context(...)`, and identifier validation lived only inside the (private, at the time) `AIMemoryManager._require_identifier`, which `get_context` calls internally. A blank or whitespace-only `tenant_id` therefore crossed the knowledge-port boundary completely unvalidated before the eventual `MemoryValidationError` was raised. Reproduced with an instrumented port that printed on every call, confirming the print fired before the exception. **Fixed** by promoting the guard to public `require_identifier` and calling it — on both `tenant_id` and `conversation_id` — as the literal first two statements of `compose`, strictly before either `_retrieve` or `get_context` runs. Re-verified with the same reproduction (no port call observed) and with mutation testing (removing the two calls fails exactly the three dedicated regression tests; restored file verified byte-identical). This is why §7.3 rule 8 above is stated as a normative ordering rule rather than left as an implementation detail: the ordering itself is the security property.

## 10. Testing Requirements

Every invariant is mutation-capable: verified by deliberately breaking it and confirming failure, then restoring.

| # | Invariant | Failure scenario caught | Test area |
|---|---|---|---|
| P1 | `system_instruction`/`prompt` pass through byte-identical | either field rewritten | pass-through/purity |
| P2 | Input request is never mutated; a new object is returned | in-place mutation introduced | purity |
| P3 | Assembly is pure — no I/O | an I/O call added to `assemble` | purity |
| P4 | Order is caller → knowledge → history | ordering swapped | ordering |
| P5 | History markers survive assembly intact | history re-sanitized | ordering (guards §7.1 rule 2) |
| P6 | All registered markers distinct and start with `[[` | a marker changed delimiter | marker registry |
| P7 | No `[[system]]` marker anywhere in the package | one introduced | marker registry (constant-value scan, not text scan — see below) |
| P8 | A knowledge document containing `[[assistant]]` yields no parseable marker | sanitization removed from the knowledge path | injection |
| P9 | A caller-supplied entry containing `[[assistant]]` yields no parseable marker | caller entries exempted from sanitization | injection (closes the §7.1 rule 1 hole) |
| P10 | Sanitization is imported from `memory`, not redefined | a second implementation introduced | injection hygiene (function-identity assertion) |
| P11 | `CONFIDENTIAL`/`RESTRICTED`/`None`/unknown dropped by default; explicit opt-in admits | classification filter removed | classification |
| P12 | Non-ASCII look-alike rejected, not normalized into an allowed value | ASCII guard removed | classification |
| P13 | No query / blank query → port never called | opt-in check removed | retrieval policy |
| P14 | No port + retrieval requested → `ContextCompositionError` | anti-dead-port check removed | retrieval policy |
| P15 | Port raises → `KnowledgeRetrievalError`; over-returning port → `KnowledgeRetrievalError` | error swallowed, or over-return silently truncated | retrieval policy |
| P16 | Duplicate documents removed by exact content | dedup removed | retrieval policy |
| P17 | A distinctive `source_id` never appears in any assembled entry | provenance rendered | provenance |
| P18 | Blank/whitespace `tenant_id`/`conversation_id` rejected **before** the knowledge port is called | validation reordered or removed | identifier validation (§9 fix — 3 dedicated tests) |
| P19 | No exception message contains document, prompt, or history content | content interpolated into a message | hygiene |
| P20 | Dependency boundary holds for the full M5 file set | a forbidden import added | boundary (AST scan, unchanged from M3/M4) |

`test_no_system_marker_exists_anywhere_in_the_package` was initially written as a raw-source-text scan and false-flagged its own explanatory docstring prose; it was rewritten as `test_no_system_marker_constant_is_defined_anywhere`, which inspects runtime constant *values* via `pkgutil`/`importlib` rather than source text, with `test_composed_output_never_contains_a_system_marker` added as a complementary end-to-end check.

## 11. Explicit Non-Overlap Statement

M5 never builds a `KnowledgeQuery`, never contacts Knowledge Engine, Security Engine, or the Kernel, never persists anything, never contacts a provider, and never makes a routing or authority decision. `PromptPipeline` performs no I/O. `ContextComposer` is the only caller of `IKnowledgeQueryPort.search` and of `AIMemoryManager.get_context` for composition purposes.

## 12. Final Architecture Verdict

**READY** — as implemented and audited. Zero M1–M4 contract changes; two additive visibility promotions in `memory.py` (`sanitize_context_content`, `require_identifier`); no boundary-test change required; one validation-ordering defect found and fixed during final audit (§9), mutation-verified.

## 13. Change Record

1. Split out of M4's first planning pass (§1); roadmap moved 8 → 9 milestones.
2. Three self-inflicted defects found and corrected during architecture review, before implementation: unsanitized caller entries (closed by §7.1 rule 1), silent defensive truncation of over-returning adapters (closed by §7.3 rule 5), and history re-sanitization (closed by §7.1 rule 2).
3. One defect found and corrected during final implementation audit, after tests passed: the identifier-validation-ordering gap (§9), which existing tests did not catch because none exercised a blank identifier on the retrieval path specifically — three tests were added to close that gap.
4. This document itself was written during the final audit rather than before implementation, closing a process gap against the M2/M3/M4 precedent (each received a pre-implementation spec). Recorded here rather than silently backfilled, since the audit that produced it was expressly asked to surface every inconsistency it found.
