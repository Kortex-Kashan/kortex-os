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
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.marketplace.engine import MarketplaceEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.storage.engine import StorageEngine
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

    await kernel.boot()
    return kernel
