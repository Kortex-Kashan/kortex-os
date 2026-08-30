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
from typing import Any

from kortex.core.kernel import Kernel
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.ai.identity import AI_SYSTEM_PRINCIPAL_ID, AI_SYSTEM_ROLE, AISystemIdentity
from kortex.engines.ai.ollama_provider import OllamaProvider
from kortex.engines.configuration.engine import SystemSettings
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import SecretNotFoundError
from kortex.engines.security.models import PrincipalType
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine

logger = logging.getLogger("kortex.api.kernel_bootstrap")

_MASTER_KEY_ENV = "KORTEX_MASTER_KEY"
_SIGNING_KEY_ENV = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
_STORAGE_DIR_ENV = "KORTEX_STORAGE_DIR"
_DEFAULT_STORAGE_DIR = "kortex_api_storage"
_AI_SYSTEM_CREDENTIAL_SECRET_HANDLE = "kortex/ai-system-credential"


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

    await kernel.boot()
    return kernel
