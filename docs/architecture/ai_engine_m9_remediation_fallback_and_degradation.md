# KORTEX OS — AI Orchestration Engine: M9 Remediation — Provider Fallback Routing & Storage-Write Degradation

**Status: IMPLEMENTED.**

Baseline: M13 commit `ddb8392`. This is not a new milestone — it closes two requirements that `docs/architecture/ai_engine_m9_production_runtime_spec.md` ratified for Milestone 9 but that were never actually wired into the live request path, discovered during the post-M13 final implementation verification.

## 1. What was missing

M9's own "Systematic Failure Recovery Matrix" (§2, Attack 6) specifies, as ratified rows:

| Failure Point | Recovery Strategy (as ratified) |
|---|---|
| Primary LLM Unreachable / Crash | Trip circuit breaker; **route to secondary local/cloud candidate**; fail-safe response if exhausted. |
| Storage Engine Offline / DB Lock | Retry 3x; if unrecoverable, **return generation with degraded flag** and emit system alert. |

Neither was implemented. `ProviderFallbackChain` (`resilience.py`) existed and was unit-tested in isolation since M9.2, but was never referenced by `engine.py` or `bootstrap.py` — `RouterLLMExecutionPort.generate_step` and `generate_response`'s inline execution both selected exactly one provider via `ModelRouter.select_model` and failed the whole request if it raised. Separately, `LLMResponse` had no degraded/diagnostic field, and a conversation-history write failure after a successful generation was caught by the same generic exception handler as a generation failure, emitting `ai.generation.failed` and re-raising — discarding the successfully generated response rather than returning it.

(The M10 certification report's checklist line "[x] Global timeout enforced in `generate_response()` with fallback chain timeout test" does not correspond to any actual test or code path — noted here so the discrepancy isn't rediscovered as a surprise.)

## 2. Fix 1 — Provider fallback routing

### Root cause

`ModelRouter.select_model` (unchanged, still correct) returns only the single best-ranked candidate. The missing piece was a caller that, on failure, asks for the *rest* of the ranked list and tries them in order — `ModelRouter.select_candidates` already returns exactly that list and required no change.

### Change

A new shared helper, `_generate_with_fallback` (`engine.py`), used by both call sites that execute an LLM request:

```python
async def _generate_with_fallback(
    router: ModelRouter, registry: ProviderRegistry, request: LLMRequest,
    context: dict[str, Any], telemetry: object | None = None,
) -> LLMResponse:
    candidates = await router.select_candidates(request, context)
    if not candidates:
        raise NoRoutableProviderError("No routable AI provider matched the routing constraints.")
    providers = [registry.get(metadata.provider_id) for metadata in candidates]
    chain = ProviderFallbackChain(providers=providers, retry_policy=_FALLBACK_ATTEMPT_POLICY, telemetry=telemetry)
    return await chain.generate_text(request)
```

- `RouterLLMExecutionPort.generate_step` (agent reasoning steps) and `AIOrchestrationEngine.generate_response`'s inline `_execute_generation` (direct generation) both now call this instead of hand-rolling single-provider selection — one definition, identical behavior in both places.
- Reuses `ProviderFallbackChain` and its existing `emit_provider_fallback`/`AIProviderFallbackEvent` telemetry hook exactly as built in M9.2 — no new resilience mechanism was invented.
- `retry_policy=RetryPolicy(max_attempts=1)` is deliberate: fallback *breadth* (try the next candidate) is this helper's concern; per-provider retry/backoff/circuit-breaker *depth* stays owned by whatever `ResilientAIProvider` the registry already holds for a given provider (production providers registered via `bootstrap.py`'s `custom_providers` path are already wrapped and are used as-is, never double-wrapped). Using `max_attempts=1` here avoids adding retry latency or re-classifying failures for providers that aren't pre-wrapped, and was the deciding factor in preserving all 567 pre-existing tests unchanged.
- `RouterLLMExecutionPort.__init__` gained an optional `telemetry` parameter, threaded through from both construction sites (`engine.py`'s internal default wiring, `bootstrap.py`).

### Behavior change (intentional)

If every eligible provider fails, the raised exception is now `ProviderFallbackExhaustedError` (already defined in `exceptions.py` since M9.2) instead of the last-tried provider's own exception type. No existing test asserted the old single-provider exception type through `generate_response`/`orchestrate_agent` (checked directly), so nothing needed updating for this change.

### Tests added (`test_ai_engine.py`, §3.5)

- `test_generate_response_falls_back_to_secondary_provider_on_primary_failure`
- `test_generate_response_raises_when_every_fallback_candidate_fails`
- `test_router_llm_execution_port_falls_back_for_agent_steps` (proves the agent-step path independently of the direct-generation path)

Mutation-verified: reverting `RouterLLMExecutionPort.generate_step` to single-provider selection fails exactly the third test above and none of the others, confirming each call site's fix is independently covered.

## 3. Fix 2 — Graceful degradation on storage-write failure

### Change

- `LLMResponse` (`models.py`) gains `degraded: bool = False` — additive, does not alter any existing field or method signature.
- New event `AIStorageWriteFailedEvent` (`events.py`, topic `ai.storage.write_failed`) — carries `request_id`/`tenant_id`/`conversation_id`/`error_category`/`user_id` only, never response or history content.
- New `AITelemetryEmitter.emit_storage_write_failed(...)` (`telemetry.py`), mirroring the existing `emit_generation_failed` pattern but deliberately *not* recording a failed generation in diagnostics, since the generation itself succeeded.
- `generate_response`'s `_execute_generation` now wraps only the `append_history` call in its own `try/except ConversationStoreError`: on failure it logs `logger.critical`, emits the new event, and returns `response.model_copy(update={"degraded": True})` instead of letting the exception reach the outer generic handler (which previously re-raised and discarded the response). A successful write is unaffected — `degraded` stays `False`.

### Tests added (`test_ai_engine.py`, §3.6)

- `test_generate_response_degrades_gracefully_when_history_write_fails` — forces `append_history` to raise via a dedicated `_WriteFailingConversationStore` fake; asserts the response is returned (not raised), `degraded=True`, and the generation is still counted as successful in diagnostics (the caller did get an answer).
- `test_generate_response_emits_storage_write_failed_event_on_degradation` — asserts `ai.storage.write_failed` is published, `ai.generation.failed` is not, and `ai.generation.completed` still is.
- `test_generate_response_happy_path_is_never_degraded` — sanity check on the unaffected success path.

Mutation-verified: reverting the `try/except` back to a bare `await self._memory_manager.append_history(...)` call fails exactly the first two tests above and leaves the third passing.

## 4. Verification

```
pytest tests/unit/test_ai_*.py -q                 570 passed (564 + 6 new)
pytest -q (full backend)                          1921 passed, 0 failed
pytest tests/integration/test_ai_production_runtime.py -q   1 passed
pytest -k forbidden_dependency (AST quarantine)    1 passed
mypy src/kortex/engines/ai/                        Success: 0 issues, 22 files
ruff check <all files touched by this remediation> All checks passed
coverage (AI package)                              91% (was 90%; resilience.py
                                                    coverage rose since
                                                    ProviderFallbackChain is
                                                    now exercised through the
                                                    production call path)
```

No test was weakened, deleted, or had its assertions loosened to pass. The 25 ruff findings in unrelated, untouched AI test files (confirmed pre-existing at the `7c145b0`/`ddb8392` baseline in the prior verification pass) are unaffected and unrelated to this remediation.

## 5. What did NOT change

- `ModelRouter` — untouched. `select_candidates` already existed and already returned exactly the ranked list this fix needed.
- `ProviderFallbackChain`, `ResilientAIProvider`, `CircuitBreaker`, `RetryPolicy` — untouched. All reused exactly as built in M9.2.
- No M1–M8 contract was modified. `LLMResponse.degraded` is additive with a safe default; every other field is unchanged.
- Knowledge Engine RAG wiring remains a separately-tracked, explicitly deferred limitation (see `ai_engine_m13_agent_lifecycle_api_spec.md` §8) — out of scope for this remediation, which addresses only the two M9 recovery-matrix rows above.

## 6. Ambiguity resolved

Before this fix, "provider resilience" meant three different things depending on which document was read: the M9 narrative (fallback wired into the facade), the M9 "Owned by M9" scope summary (only the single-provider `ResilientAIProvider` wrapper), and the actual code (no fallback at all, single-provider only). That inconsistency is now resolved in the code's favor of the stronger, doubly-corroborated requirement (the Attack-6 recovery matrix plus the required-files table's explicit "fallback routing" phrase for `engine.py`): fallback is implemented, tested, and this document is the single place that states so unambiguously. The M10 certification report's inaccurate checklist line (§1 above) is flagged rather than corrected in place, since that document is a point-in-time historical certification record, not a living spec.
