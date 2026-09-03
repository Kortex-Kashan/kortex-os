"""Assembles and boots the `Kernel` instance backing the M3 FastAPI app.

There is no existing non-test bootstrap for a fully-wired `Kernel` anywhere
in `backend/src` (confirmed during the M3 audit — every real construction
site is test code that builds a bare `Kernel()` then registers exactly the
engines that test needs). This module is the first one; it mirrors the
existing test convention (`backend/tests/unit/test_capability_dispatch.py`)
rather than inventing a new pattern.

Master/signing key provisioning is a known, explicitly out-of-scope-for-M3
gap: `phase3_desktop_architecture.md` never specifies a production key
management story, so this generates ephemeral keys at process startup when
none are configured via environment variables. That means every restart of
the sidecar invalidates all previously-issued session tokens and encrypted
secrets — acceptable for M3's demonstration scope, not for a shipped
product. Flagged in the M3 final report's Known Limitations, not hidden.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, cast

from kortex.core.kernel import Kernel
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE, AISystemIdentity
from kortex.engines.ai.ollama_provider import OllamaProvider
from kortex.engines.ai.tools import ToolDefinition, ToolRegistry
from kortex.engines.configuration.engine import SystemSettings
from kortex.engines.connector.drivers import DummyConnectorDriver, HttpRestConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.document_intelligence.engine import DocumentIntelligenceEngine
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import SecretNotFoundError
from kortex.engines.security.models import PrincipalType
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.modules.finance.module import FinanceModule

logger = logging.getLogger("kortex.api.kernel_bootstrap")

_MASTER_KEY_ENV = "KORTEX_MASTER_KEY"
_SIGNING_KEY_ENV = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
_STORAGE_DIR_ENV = "KORTEX_STORAGE_DIR"
_DEFAULT_STORAGE_DIR = "kortex_api_storage"
# S105 false positive: a SecretStore *handle* (lookup key), not a credential.
_AI_SYSTEM_CREDENTIAL_SECRET_HANDLE = "kortex/ai-system-credential"  # noqa: S105


def _build_ai_system_identity(security_engine: SecurityEngine) -> AISystemIdentity:
    """Construct the AI system principal's session-token holder (M6.2-1).

    Only holds a reference to `security_engine` in closures defined here —
    `AISystemIdentity` itself (part of the AI package) never sees that
    reference; it only ever receives the opaque token objects these two
    callables return. Both callables are lazy: neither is invoked until the
    AI actually attempts to act within a given tenant, which is always well
    after `await kernel.boot()` below has completed and Security Engine is
    RUNNING — safe even though this function itself runs before that boot
    call, since it only *constructs* the closures here, it does not call
    them.
    """

    async def _provision(tenant_id: str) -> None:
        try:
            credential = await security_engine.get_secret(_AI_SYSTEM_CREDENTIAL_SECRET_HANDLE, tenant_id)
        except SecretNotFoundError:
            credential = secrets.token_urlsafe(32)
            await security_engine.put_secret(_AI_SYSTEM_CREDENTIAL_SECRET_HANDLE, tenant_id, credential)

        await security_engine.authentication_manager.provision_principal(
            tenant_id=tenant_id,
            principal_id=AI_SYSTEM_PRINCIPAL_ID,
            principal_type=PrincipalType.AGENT,
            credential=credential,
            roles=[AI_SYSTEM_ROLE],
            # ABAC's classification check (`abac.py`) compares this against
            # each capability's own `security_classification` — most
            # existing capabilities default to "INTERNAL" (see
            # `Kernel.register_capability`'s own default), so an unset
            # clearance (which ranks as the lowest, "PUBLIC") would deny the
            # AI system principal from calling almost anything. "INTERNAL"
            # is the least-elevated clearance that matches the platform's
            # own prevailing default classification, not a broad grant.
            attributes={"clearance_level": "INTERNAL"},
        )

    async def _authenticate(tenant_id: str) -> Any:
        credential = await security_engine.get_secret(_AI_SYSTEM_CREDENTIAL_SECRET_HANDLE, tenant_id)
        principal = await security_engine.authenticate(
            {
                "principal_type": PrincipalType.AGENT.value,
                "tenant_id": tenant_id,
                "principal_id": AI_SYSTEM_PRINCIPAL_ID,
                "credential": credential,
            }
        )
        return await security_engine.authentication_manager.issue_token(principal)

    return AISystemIdentity(provisioner=_provision, authenticator=_authenticate)


def _resolve_key(env_var: str, length: int) -> bytes:
    raw = os.environ.get(env_var)
    if raw:
        key = raw.encode("utf-8") if not raw.startswith("0x") else bytes.fromhex(raw[2:])
        if len(key) != length:
            raise ValueError(f"{env_var} must decode to exactly {length} bytes, got {len(key)}.")
        return key
    logger.warning(
        "%s not set — generating an ephemeral key for this process only. "
        "Every restart invalidates existing sessions/secrets. Not production-ready.",
        env_var,
    )
    return os.urandom(length)


async def build_and_boot_kernel() -> Kernel:
    """Construct a `Kernel` with Storage + Security registered, then boot it.

    Mirrors `test_capability_dispatch.py`'s `_build_kernel`/`_boot_kernel`
    helpers exactly — same engines, same registration order, same
    `await kernel.boot()` call — this is not a new bootstrap pattern, only
    its first non-test caller.
    """
    kernel = Kernel()

    storage_dir = os.environ.get(_STORAGE_DIR_ENV, _DEFAULT_STORAGE_DIR)
    storage_engine = StorageEngine(base_directory=storage_dir)
    kernel.register_engine(storage_engine)

    security_engine = SecurityEngine(
        master_key=_resolve_key(_MASTER_KEY_ENV, 32),
        signing_private_key=_resolve_key(_SIGNING_KEY_ENV, 32),
    )
    kernel.register_engine(security_engine)

    # M5: Connector/Driver Registry. No constructor arguments — it resolves
    # its Storage Engine data/cache stores from the Kernel IoC container
    # during `initialize()` (see `ConnectorEngine.initialize`), the same
    # deferred-wiring pattern Security/Storage already establish here.
    kernel.register_engine(ConnectorEngine())

    # M6: Workflow Engine. Same deferred-wiring pattern — its Storage Engine
    # dependency is resolved from the Kernel IoC container during
    # `initialize()` (see `WorkflowEngine.initialize`), not passed here.
    kernel.register_engine(WorkflowEngine())

    # M7: Marketplace Engine — read-only catalog visibility slice. No
    # constructor arguments and no engine dependencies (in-memory only).
    kernel.register_engine(MarketplaceEngine())

    # Slice 4.6: AI Orchestration Engine. Wired exactly as this engine's own
    # certified integration test assembles it
    # (tests/integration/test_ai_production_runtime.py): a
    # `KernelBridgeAdapter` over this exact Kernel instance, and a
    # `RelationalDataStore` over `kernel.db` — safe to construct before
    # `kernel.boot()` connects it, since `RelationalDataStore.__init__` only
    # holds a reference and performs no I/O until a session is actually
    # requested. `environment="production"` is deliberate, not a default:
    # `KernelProductionBootstrap.validate_production_wiring` refuses to
    # assemble a production engine that would fall back to a non-durable
    # in-memory conversation store or a tool-execution port that bypasses
    # Kernel/Security authorization — the same production-wiring guarantee
    # already relied on for every other engine registered above.
    #
    # M6.1-2: a real `OllamaProvider` is now registered, sourced from
    # `SystemSettings.ollama_url`/`ollama_default_model` (already declared,
    # env-prefixed `KORTEX_`, previously read by nothing anywhere in
    # `backend/src`). `SystemSettings` is instantiated directly rather than
    # through a `ConfigurationEngine` instance, since no `ConfigurationEngine`
    # is registered on this production boot path today and adding one is a
    # broader, unrelated change to the boot sequence this milestone doesn't
    # require — `SystemSettings` is a plain `pydantic_settings.BaseSettings`
    # that reads the same env vars either way. Registered unconditionally,
    # matching every other engine above: if Ollama isn't actually running,
    # `health_check()`/the circuit breaker reveal that at call time, not at
    # boot time — `kortex.ai.provider.list`/`kortex.ai.model.list` now report
    # this one real provider instead of fabricating or omitting it.
    ai_config = AIEngineRuntimeConfig(environment="production")
    system_settings = SystemSettings()
    ollama_provider = OllamaProvider(
        base_url=system_settings.ollama_url,
        model_name=system_settings.ollama_default_model,
        timeout_seconds=max(1.0, ai_config.default_generation_timeout_seconds - 5.0),
    )
    # M6.2-1: the AI system principal's session-token holder. Constructed
    # here (not inside the AI package) because provisioning/authentication
    # requires direct `SecurityEngine` access, which is a hard, AST-enforced
    # forbidden import for `kortex.engines.ai.*` — see `identity.py`'s
    # module docstring. Safe to construct before `kernel.boot()`: it only
    # captures `security_engine` in a closure, it does not call it yet.
    ai_identity = _build_ai_system_identity(security_engine)
    ai_bootstrap = KernelProductionBootstrap(ai_config)
    ai_engine = ai_bootstrap.create_ai_engine(
        # `KernelBridgeAdapter.__init__` structurally requires
        # `invoke_capability(request: object)`, which `Kernel.invoke_capability`
        # narrows to `request: CapabilityRequest` — mypy correctly flags this
        # as unsound in general, but `KernelBridgeAdapter` only ever calls it
        # with a `CapabilityRequest` it just built itself
        # (`bridge.py::invoke_capability`), so this is safe in practice.
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=RelationalDataStore(kernel.db),
        custom_providers=[ollama_provider],
        registered_engines=list(kernel.get_all_engines().keys()),
        ai_identity=ai_identity,
    )
    kernel.register_engine(ai_engine)

    # Slice 4.7: Document Engine. No constructor arguments — it self-resolves
    # its Storage Engine data/file/object/cache stores from the Kernel IoC
    # container during `initialize()` (see `DocumentEngine.initialize`), the
    # same deferred-wiring pattern Connector/Workflow already establish here.
    kernel.register_engine(DocumentEngine())

    # Slice 4.7: Knowledge Engine. No constructor arguments — it resolves
    # Storage Engine's IDataStore/IObjectStore via `kernel.get_engine("storage")`
    # during `initialize()` (see `KnowledgeEngine.initialize`), requiring
    # Storage to already be registered, which it is, above.
    kernel.register_engine(KnowledgeEngine())

    # Phase 6 / Finance-pilot planning pass: Finance Business Module (first
    # pilot module under the new BaseModule foundation). No constructor
    # arguments — it resolves Storage Engine's IDataStore via
    # `kernel.get_engine("storage")` during `initialize()`, the same
    # deferred-wiring pattern every engine above already establishes.
    # `FinanceModule` is not a `BaseEngine` subclass (`BaseModule` is a
    # deliberate sibling abstraction — see `core/base_module.py`); it
    # registers here via the same `kernel.register_engine()` call every
    # engine uses because `Kernel.register_engine`/`BootEngine.boot_system`
    # are proven, by direct inspection, to be pure duck-typed dispatch over
    # `.dependencies`/`.initialize`/`.start`/`.stop`/`.state` with no
    # `isinstance(..., BaseEngine)` check anywhere — no Kernel modification
    # was needed or made to support this.
    # `BaseModule` is deliberately not a `BaseEngine`; registration relies on the
    # Kernel's duck-typed boot dispatch. See `kortex.core.base_module`.
    kernel.register_engine(FinanceModule())  # type: ignore[arg-type]

    # Phase 4: Document Intelligence Engine — local PDF parsing and local OCR.
    kernel.register_engine(DocumentIntelligenceEngine())

    await kernel.boot()

    # M7.3-W1: register the production connector drivers now that the engine
    # is READY (ConnectorEngine.register_driver requires READY/RUNNING state,
    # so this cannot happen before kernel.boot()).
    connector_engine = cast("ConnectorEngine", kernel.get_engine("connector"))
    register_production_connector_drivers(connector_engine)

    # M7.3-W4: register the reference connector AI tools.
    register_connector_ai_tools(ai_engine.tool_registry)

    # M7.4-W3: register the Document Engine AI tools.
    register_document_ai_tools(ai_engine.tool_registry)

    # M7.5-W3: register the Knowledge Engine AI tool.
    register_knowledge_ai_tools(ai_engine.tool_registry)

    return kernel


def register_production_connector_drivers(connector_engine: ConnectorEngine) -> None:
    """Register the production connector drivers on an already-booted engine.

    Both `DummyConnectorDriver` and `HttpRestConnectorDriver` already exist
    and are independently unit/integration tested (see
    docs/architecture/m7.3_connector_integration_planning_report.md, Pass 1
    §3.A) -- this only wires them into the production boot path, which no
    prior milestone did. Idempotent: safe to call more than once against the
    same engine instance (e.g. a caller that boots, shuts down, and re-boots
    without recreating the engine) without raising `ConnectorDriverError` for
    an already-registered driver id.
    """
    already_registered = {d.driver_id for d in connector_engine.list_drivers()}
    for driver in (DummyConnectorDriver(), HttpRestConnectorDriver()):
        if driver.metadata.driver_id not in already_registered:
            connector_engine.register_driver(driver)


def _register_tool_if_absent(tool_registry: ToolRegistry, tool: ToolDefinition) -> None:
    """Idempotently register one AI tool (M7.5 hygiene extraction).

    `ToolRegistry.register_tool` raises `ToolValidationError` on a duplicate
    name, so every `register_<engine>_ai_tools` function already needed this
    exact `if not tool_registry.has_tool(...): tool_registry.register_tool(...)`
    guard -- by M7.5 this pattern had been copy-pasted five times across
    `register_connector_ai_tools`/`register_document_ai_tools` with no shared
    helper. Extracted here, at the point the third engine's tools are added,
    per the M7.5 planning report's own finding (§1/§6/§17 Q3) that this was
    real, evidence-justified debt worth closing opportunistically rather than
    a speculative refactor. Deliberately minimal: only the guard is shared,
    not tool construction itself, so each engine's registration function
    keeps full, independent control over its own tools' shape and rationale.
    """
    if not tool_registry.has_tool(tool.name):
        tool_registry.register_tool(tool)


def register_connector_ai_tools(tool_registry: ToolRegistry) -> None:
    """Register the M7.3 reference connector AI tools into an AI Engine's `ToolRegistry`.

    Two tools prove both the direct-dispatch (read) and approval-gated
    (mutation) paths through the existing, unmodified `AIToolInvoker` ->
    `KernelToolExecutionPort` -> `CapabilityDispatcher` -> `ConnectorEngine`
    chain -- no new plumbing, only the tool declarations the AI Engine's
    `ToolRegistry` was always able to hold but nothing had ever populated.

    `action_type` is pinned via a JSON Schema `const` per tool so the LLM
    cannot use the read-only tool to smuggle a mutating action_type past
    `ToolDefinition.is_mutation`'s approval-gating decision
    (`DurableAIApprovalPolicy` keys off which TOOL was called, not the
    payload). `profile_id` is left caller-supplied because real tenant
    isolation is already enforced one layer down by
    `ConnectorEngine.execute_action`'s principal-authoritative tenant
    binding and `ConnectorProfileManager.get_profile`'s tenant-scoped,
    enumeration-resistant masking (M6.3-1), which a schema-level constant
    would only duplicate, not strengthen, and cannot do per-tenant anyway
    since `ToolRegistry` is a single process-wide catalog, not per-tenant.

    The schema nests `profile_id`/`action_type`/`payload` under a top-level
    `request` object because `ConnectorEngine.execute_action(self, request,
    principal=None)` takes exactly one parameter named `request` -- the
    Kernel dispatcher splats a capability's `parameters` dict as
    `handler(**parameters)`, so the arguments dict a tool call produces must
    literally contain a `request` key to land on that parameter, identical
    to the shape `ExternalExecutionManager` already uses to reach this same
    capability (`parameters={"request": ActionRequest(...)}}`,
    `test_external_execution_vertical_slice.py`). This is an existing
    contract this milestone conforms to, not a new convention invented here.

    Idempotent: `ToolRegistry.register_tool` raises `ToolValidationError` on
    a duplicate name, so this is a no-op on a registry that already holds
    these tools.
    """
    _register_tool_if_absent(
        tool_registry,
        ToolDefinition(
            name="connector_read_status",
            description=(
                "Fetch read-only status information from a connected external "
                "service via its connector profile. Safe, no side effects, "
                "never requires approval."
            ),
            canonical_capability="kortex.connector.action.execute",
            parameters_schema={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "object",
                        "properties": {
                            "request_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A unique identifier you generate for this request.",
                            },
                            "profile_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "The connector profile to read from.",
                            },
                            "action_type": {"type": "string", "const": "FETCH"},
                            "payload": {"type": "object"},
                        },
                        "required": ["request_id", "profile_id", "action_type"],
                    },
                },
                "required": ["request"],
            },
            is_mutation=False,
            timeout_seconds=30.0,
        ),
    )
    _register_tool_if_absent(
        tool_registry,
        ToolDefinition(
            name="connector_send_action",
            description=(
                "Send a mutating action to an external service via its connector "
                "profile. This changes state on the external service and always "
                "requires human approval before it executes."
            ),
            canonical_capability="kortex.connector.action.execute",
            parameters_schema={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "object",
                        "properties": {
                            "request_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A unique identifier you generate for this request.",
                            },
                            "profile_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "The connector profile to send the action to.",
                            },
                            "action_type": {"type": "string", "const": "SEND"},
                            "payload": {"type": "object"},
                        },
                        "required": ["request_id", "profile_id", "action_type"],
                    },
                },
                "required": ["request"],
            },
            is_mutation=True,
            timeout_seconds=30.0,
        ),
    )


def register_document_ai_tools(tool_registry: ToolRegistry) -> None:
    """Register the M7.4 Document Engine AI tools into an AI Engine's `ToolRegistry`.

    Two tools, directly mirroring `register_connector_ai_tools`'s M7.3 shape:
    a read tool (`document_list_templates`, `kortex.document.template.list`,
    zero parameters -- the underlying handler takes none) and a mutation
    tool (`document_generate`, `kortex.document.operation.execute`,
    `is_mutation=True` -- gated by the same, unmodified
    `DurableAIApprovalPolicy`/Workflow Approval Queue chain M7.3 already
    proved is fully engine-agnostic).

    `document_generate`'s schema mirrors `execute_profile(self, profile_id:
    str, request: OperationRequest, principal=None)`'s real two-parameter
    signature exactly -- a *different* shape from either connector tool's
    single-`request`-parameter pattern, deliberately not assumed uniform
    (see the M7.4 planning report §7 item 5, which explicitly flags this as
    the mistake M7.3 made and had to correct mid-implementation). `payload`
    (the actual document-generation input data, e.g. invoice line items) is
    intentionally left open-shaped (`{"type": "object"}`) inside
    `binding_context.data`, since its real shape depends on whichever
    template the targeted profile requires and cannot be statically known
    here -- identical reasoning to the connector tools' own open `payload`
    field.

    Content-security note (M7.4-W5, planning report §15 T4): `execute_profile`
    returns an `OperationResult` whose `output_bytes` field genuinely can
    carry the full generated document content (it is the same bytes
    `DocumentStorageBinder` also persists to object storage under
    `OperationResult.storage_key` -- the two are redundant, not
    alternatives). `KernelToolExecutionPort`/`AIToolInvoker` (existing,
    generic, unmodified) pass a capability's raw return value through
    unchanged for every tool, by design -- there is no per-tool
    output-shaping hook to selectively drop `output_bytes` without either
    changing `execute_profile`'s own return contract for every caller (not
    just AI) or adding a platform-wide per-tool transformation layer, both
    out of this milestone's narrow scope. The actual, existing control that
    bounds this is the same one that already bounds every other
    capability's potentially-large output: `ToolResult.to_context_entry`'s
    generic `MAX_TOOL_OUTPUT_CHARS`/`max_tool_result_bytes` hard truncation
    -- not a new mitigation invented for this tool, the same backstop the
    connector tools already rely on. This is a real, accepted limitation
    (a large document's content is truncated into conversation history
    rather than replaced with a clean reference), not a solved problem --
    see the M7.4 implementation report's Known Limitations for the
    follow-up (a `document.result.get`-by-reference pattern, or trimming
    `OperationResult` fields specifically on the AI path) this milestone
    deliberately does not build.

    Idempotent: `ToolRegistry.register_tool` raises `ToolValidationError` on
    a duplicate name, so this is a no-op on a registry that already holds
    these tools.
    """
    _register_tool_if_absent(
        tool_registry,
        ToolDefinition(
            name="document_list_templates",
            description=(
                "List the document templates available for generating a document. "
                "Read-only, no side effects, never requires approval."
            ),
            canonical_capability="kortex.document.template.list",
            parameters_schema={"type": "object", "properties": {}},
            is_mutation=False,
            timeout_seconds=30.0,
        ),
    )
    _register_tool_if_absent(
        tool_registry,
        ToolDefinition(
            name="document_generate",
            description=(
                "Generate a document by executing a Document Operation Profile. "
                "This creates real output and always requires human approval "
                "before it executes."
            ),
            canonical_capability="kortex.document.operation.execute",
            parameters_schema={
                "type": "object",
                "properties": {
                    "profile_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The operation profile to execute.",
                    },
                    "request": {
                        "type": "object",
                        "properties": {
                            "request_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A unique identifier you generate for this request.",
                            },
                            "profile_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Must match the top-level profile_id.",
                            },
                            "binding_context": {
                                "type": "object",
                                "properties": {
                                    "context_id": {"type": "string", "minLength": 1},
                                    "data": {"type": "object"},
                                },
                                "required": ["context_id"],
                            },
                        },
                        "required": ["request_id", "profile_id"],
                    },
                },
                "required": ["profile_id", "request"],
            },
            is_mutation=True,
            timeout_seconds=60.0,
        ),
    )


def register_knowledge_ai_tools(tool_registry: ToolRegistry) -> None:
    """Register the M7.5 Knowledge Engine AI tool into an AI Engine's `ToolRegistry`.

    One tool, deliberately narrower than the M7.3/M7.4 read+mutation pairs:
    `knowledge_search` (`kortex.knowledge.query.search`, `is_mutation=False`,
    no approval). The M7.5 planning report (§9/§17 Q1) found no product
    evidence -- no acceptance scenario, desktop feature, or prior planning
    artifact -- for an AI-triggered mutation (indexing a source, loading a
    pack) yet, and the master implementation prompt's own scope guardrails
    ("Do NOT automatically add mutation tools... unless the repository
    evidence and explicit milestone scope require them") agree. A
    mutation-class Knowledge tool remains an explicit open question for a
    future milestone, not silently added or silently foreclosed here.

    `knowledge_search`'s schema mirrors `search(self, query: KnowledgeQuery,
    principal=None)`'s real single-parameter signature -- the arguments dict
    a tool call produces must contain a `query` key to land on that
    parameter, the same `handler(**parameters)` splatting rule the connector
    and document tools' schemas already conform to.

    Tenant isolation (M7.5-W1, security-critical -- see the planning report
    §10): the schema deliberately does NOT expose `tenant_id` as a tool
    parameter at all, so the LLM has no way to even attempt supplying one.
    `KnowledgeQuery.tenant_id` gained a `"default"` fallback value (M7.5-W3,
    `models.py`, mirroring `document.models.BindingContext.tenant_id`'s
    identical precedent) specifically so construction from a dict that omits
    it succeeds; `KnowledgeEngine.search`'s own M7.5-W1 fix then
    unconditionally overrides whatever value is present with the
    Kernel-verified `principal.tenant_id` before the query ever reaches
    `KnowledgeSearchEngine`. The fallback value is never actually read by
    search logic on the real dispatch path -- it exists only so the model
    stays constructible without a tenant_id, not as a trust boundary of its
    own.

    Also intentionally excluded from the schema: `filters` (spec-reserved,
    unimplemented, no evidence any implementation reads it -- exposing it to
    the LLM would suggest a capability that does not exist),
    `entity_types`/`trust_states`/`as_of` (all have safe model defaults;
    `trust_states` in particular defaults to excluding unverified
    `SOURCE_EVIDENCE`/`AI_CANDIDATE` content, a default this tool preserves
    rather than giving the LLM a lever to loosen with no evidenced need).
    `max_results` is included as the one optional field, since bounding
    result size is directly useful for the content-security concern M7.5-W5
    identified (a knowledge search can return substantially more text than a
    connector response) -- on top of, not instead of, the existing generic
    `ToolResult.to_context_entry` truncation backstop every tool already
    relies on (M7.3/M7.4's identical, unmodified mitigation; no new
    Knowledge-specific truncation or scrubbing was added, per the master
    prompt's own instruction not to invent one without an actual gap).

    Idempotent via the shared `_register_tool_if_absent` helper.
    """
    _register_tool_if_absent(
        tool_registry,
        ToolDefinition(
            name="knowledge_search",
            description=(
                "Search the tenant's indexed knowledge base (documents, sources, "
                "and graph entities already ingested) to ground an answer in the "
                "tenant's own knowledge. Read-only, no side effects, never "
                "requires approval."
            ),
            canonical_capability="kortex.knowledge.query.search",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "object",
                        "properties": {
                            "query_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "A unique identifier you generate for this request.",
                            },
                            "query_text": {
                                "type": "string",
                                "minLength": 1,
                                "description": "The natural-language or keyword search text.",
                            },
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Optional cap on the number of results returned.",
                            },
                        },
                        "required": ["query_id", "query_text"],
                    },
                },
                "required": ["query"],
            },
            is_mutation=False,
            timeout_seconds=30.0,
        ),
    )
