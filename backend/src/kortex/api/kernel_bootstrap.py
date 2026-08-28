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

from kortex.core.kernel import Kernel
from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap
from kortex.engines.ai.bridge import KernelBridgeAdapter
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.document.engine import DocumentEngine
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine

logger = logging.getLogger("kortex.api.kernel_bootstrap")

_MASTER_KEY_ENV = "KORTEX_MASTER_KEY"
_SIGNING_KEY_ENV = "KORTEX_AUTH_SIGNING_PRIVATE_KEY"
_STORAGE_DIR_ENV = "KORTEX_STORAGE_DIR"
_DEFAULT_STORAGE_DIR = "kortex_api_storage"


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
    # already relied on for every other engine registered above. No AI
    # provider is registered here: none exists yet anywhere in `backend/src`
    # outside test fixtures, so `kortex.ai.provider.list`/
    # `kortex.ai.model.list` honestly report empty rather than fabricating one.
    ai_bootstrap = KernelProductionBootstrap(AIEngineRuntimeConfig(environment="production"))
    ai_engine = ai_bootstrap.create_ai_engine(
        # `KernelBridgeAdapter.__init__` structurally requires
        # `invoke_capability(request: object)`, which `Kernel.invoke_capability`
        # narrows to `request: CapabilityRequest` — mypy correctly flags this
        # as unsound in general, but `KernelBridgeAdapter` only ever calls it
        # with a `CapabilityRequest` it just built itself
        # (`bridge.py::invoke_capability`), so this is safe in practice.
        kernel_bridge=KernelBridgeAdapter(kernel),  # type: ignore[arg-type]
        data_store=RelationalDataStore(kernel.db),
        registered_engines=list(kernel.get_all_engines().keys()),
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
