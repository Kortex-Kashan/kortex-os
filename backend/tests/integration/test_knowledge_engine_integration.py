"""
Integration tests for KORTEX Knowledge Engine: Kernel boot, dependency
resolution, capability registration, and capability invocation end-to-end.

Mirrors `test_storage_integration.py`'s own established pattern exactly
(register engine(s), `kernel.boot()`, resolve via DI container, look up
capabilities, invoke a handler via the M8 test-only raw-handler accessor,
verify aggregated health check, then `kernel.shutdown()`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel
from kortex.engines.knowledge.engine import KnowledgeEngine
from kortex.engines.knowledge.models import KnowledgeTrustState
from kortex.engines.storage.engine import StorageEngine


@pytest.mark.asyncio
async def test_knowledge_engine_kernel_boot_integration(tmp_path: Path) -> None:
    """End-to-end: register Storage + Knowledge Engine, boot Kernel
    (topologically ordering Knowledge after Storage per its own
    `dependencies`), resolve capabilities, invoke one through the raw
    handler path, verify aggregated health, then shut down cleanly."""
    kernel = Kernel()
    # See test_knowledge_engine.py's `_build_ready_engine` for why this
    # override is necessary: `Kernel()` defaults to a real, shared
    # `./kortex_local.db` file with no test isolation of its own.
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path}/kernel.db")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "integration_storage"))
    knowledge_engine = KnowledgeEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(knowledge_engine)

    await kernel.boot()

    assert kernel.state.value == "RUNNING"
    assert knowledge_engine.state == EngineState.RUNNING

    resolved_engine = kernel.container.resolve("engine.knowledge")
    assert resolved_engine is knowledge_engine

    for cap_name in [
        "kortex.knowledge.query.search",
        "kortex.knowledge.graph.traverse",
        "kortex.knowledge.pack.load",
        "kortex.knowledge.source.index",
    ]:
        cap = kernel.get_capability(cap_name)
        assert cap.provider == "knowledge"

    # Invoke a capability handler directly via the M8 test-only accessor,
    # bypassing authentication/authorization (Security Engine integration is
    # not this engine's own scope) -- proves registration and handler wiring
    # actually work end-to-end, not merely that registration calls succeeded.
    created = await kernel._registry_engine.get_raw_handler_for_testing("kortex.knowledge.source.index")(
        "kortex.knowledge.source.reference", "tenant-a"
    )
    assert len(created) == 2
    assert all(r.trust_state == KnowledgeTrustState.SOURCE_EVIDENCE for r in created)

    health_report = await kernel.health_check()
    assert health_report["kernel_state"] == "RUNNING"
    assert "knowledge" in health_report["system_health"]["engines"]
    assert health_report["system_health"]["engines"]["knowledge"]["healthy"] is True

    await kernel.shutdown()
    assert kernel.state.value == "STOPPED"
    assert knowledge_engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_knowledge_engine_capability_invocation_denied_without_dispatch_boundary(tmp_path: Path) -> None:
    """Adversarial: `Kernel.invoke_capability()` (the real, authenticated
    dispatch path -- as opposed to the raw-handler test accessor above)
    must reject an unauthenticated call to a Knowledge Engine capability,
    exactly like every sibling engine's own registered capabilities, since
    `requires_authentication` defaults to `True` and no `session_token` is
    supplied here."""
    from kortex.core.dispatch import CapabilityRequest
    from kortex.engines.security.engine import SecurityEngine
    from kortex.engines.security.exceptions import AuthenticationError

    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{tmp_path}/kernel2.db")

    storage_engine = StorageEngine(base_directory=str(tmp_path / "integration_storage2"))
    # Deterministic test master/signing keys -- matches
    # `test_security_engine.py`'s own `_TEST_MASTER_KEY`/`_TEST_AUTH_SIGNING_KEY`
    # constants exactly, via SecurityEngine's constructor-injection path
    # ("deterministic test fixtures... tests never depend on
    # KORTEX_MASTER_KEY/KORTEX_AUTH_SIGNING_PRIVATE_KEY environment
    # variables").
    security_engine = SecurityEngine(master_key=b"\x11" * 32, signing_private_key=b"\x55" * 32)
    knowledge_engine = KnowledgeEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(knowledge_engine)

    await kernel.boot()

    request = CapabilityRequest(
        capability_name="kortex.knowledge.query.search",
        parameters={},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)

    await kernel.shutdown()
