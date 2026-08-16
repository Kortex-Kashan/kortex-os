"""Unit tests for the KORTEX Security Engine core facade (Milestone M1).

Verifies engine identity, dependency declaration, `BaseEngine` lifecycle,
Kernel/Registry capability registration, fail-closed capability placeholder
behavior under a spread of inputs, Kernel dependency-ordered boot, fail-closed
missing-dependency boot behavior, and diagnostic truthfulness.

These four canonical capabilities are structural placeholders only in M1 —
they never authenticate, authorize, decrypt secrets, or grant access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import EngineStateError, KernelBootError
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import SecurityEngineError
from kortex.engines.storage.engine import StorageEngine

_CANONICAL_CAPABILITY_NAMES = [
    "kortex.security.auth.authenticate",
    "kortex.security.access.authorize",
    "kortex.security.secret.get",
    "kortex.security.signature.verify",
]


# -- A. Identity ---------------------------------------------------------------


def test_security_engine_name_is_security() -> None:
    engine = SecurityEngine()
    assert engine.name == "security"


# -- B. Dependencies -------------------------------------------------------------


def test_security_engine_dependencies_are_storage_and_registry() -> None:
    engine = SecurityEngine()
    assert engine.dependencies == ["storage", "registry"]


def test_security_engine_initial_state_is_uninitialized() -> None:
    engine = SecurityEngine()
    assert engine.state == EngineState.UNINITIALIZED


# -- C. Lifecycle ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_engine_initialize_reaches_ready() -> None:
    kernel = Kernel()
    engine = SecurityEngine()

    await engine.initialize(kernel)

    assert engine.state == EngineState.READY


@pytest.mark.asyncio
async def test_security_engine_start_reaches_running() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)

    await engine.start()

    assert engine.state == EngineState.RUNNING


@pytest.mark.asyncio
async def test_security_engine_start_before_initialize_raises_engine_state_error() -> None:
    engine = SecurityEngine()
    with pytest.raises(EngineStateError):
        await engine.start()


@pytest.mark.asyncio
async def test_security_engine_initialize_failure_transitions_to_failed_state() -> None:
    """If capability registration fails (e.g. a name collision on the Kernel
    Registry), `initialize` must transition to FAILED and re-raise — never
    silently continue to READY."""
    kernel = Kernel()
    kernel.register_capability(
        name="kortex.security.auth.authenticate",
        description="pre-existing collision",
        provider="not-security",
    )
    engine = SecurityEngine()

    with pytest.raises(Exception):  # noqa: B017 -- ResourceAlreadyExistsError from Registry, not re-declared here
        await engine.initialize(kernel)

    assert engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_security_engine_health_check_returns_health_dict() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    await engine.start()

    report = await engine.health_check()

    assert report["engine"] == "security"
    assert report["healthy"] is True


@pytest.mark.asyncio
async def test_security_engine_stop_reaches_stopped() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    await engine.start()

    await engine.stop()

    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_security_engine_stop_before_start_is_a_no_op() -> None:
    engine = SecurityEngine()
    await engine.stop()  # UNINITIALIZED -> stop() must not raise
    assert engine.state == EngineState.UNINITIALIZED


# -- D. Capability registration ---------------------------------------------------


@pytest.mark.asyncio
async def test_all_four_canonical_capabilities_registered_with_kernel() -> None:
    kernel = Kernel()
    engine = SecurityEngine()

    await engine.initialize(kernel)

    for capability_name in _CANONICAL_CAPABILITY_NAMES:
        descriptor = kernel.get_capability(capability_name)
        assert descriptor.provider == "security"
        assert descriptor.handler is not None


def test_capabilities_empty_before_initialize() -> None:
    engine = SecurityEngine()
    assert engine.capabilities() == []


@pytest.mark.asyncio
async def test_capabilities_reflects_all_four_registrations_after_initialize() -> None:
    kernel = Kernel()
    engine = SecurityEngine()

    await engine.initialize(kernel)

    assert set(engine.capabilities()) == set(_CANONICAL_CAPABILITY_NAMES)
    assert len(engine.capabilities()) == 4


# -- E. Capability failure behavior: fails closed for every input shape ------------


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_name", _CANONICAL_CAPABILITY_NAMES)
@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((), {}),
        ((None,), {}),
        (("",), {}),
        (({},), {}),
        (({"unexpected": "value"},), {}),
        (("arbitrary-string-input",), {}),
        ((1, 2, 3), {"a": "b"}),
    ],
    ids=["no-args", "none", "empty-string", "empty-dict", "malformed-dict", "arbitrary-string", "unexpected-mixed"],
)
async def test_capability_handler_fails_closed_for_every_input_shape(
    capability_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    descriptor = kernel.get_capability(capability_name)

    with pytest.raises(SecurityEngineError) as exc_info:
        await descriptor.handler(*args, **kwargs)

    assert exc_info.value.code == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_capability_handler_never_returns_a_value() -> None:
    """Structural guarantee: the placeholder handler always raises — there is
    no code path that could return a truthy/ALLOW-like value."""
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    descriptor = kernel.get_capability("kortex.security.access.authorize")

    with pytest.raises(SecurityEngineError):
        await descriptor.handler(principal="anyone", requirement="anything")


@pytest.mark.asyncio
async def test_security_engine_metrics_counts_not_implemented_invocations() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    descriptor = kernel.get_capability("kortex.security.secret.get")

    assert engine.metrics()["not_implemented_invocations"] == 0
    with pytest.raises(SecurityEngineError):
        await descriptor.handler("secret:kortex/x", "tenant-a")
    assert engine.metrics()["not_implemented_invocations"] == 1


# -- F. Dependency ordering (real Kernel boot) -------------------------------------


@pytest.mark.asyncio
async def test_security_engine_boots_after_storage_and_registry(tmp_path: Path) -> None:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_integ_storage"))
    security_engine = SecurityEngine()

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    await kernel.boot()

    assert kernel.state.value == "RUNNING"
    assert storage_engine.state == EngineState.RUNNING
    assert security_engine.state == EngineState.RUNNING

    boot_engine = kernel.get_engine("boot")
    boot_report = await boot_engine.health_check()
    order = boot_report["boot_order"]
    assert order.index("storage") < order.index("security")
    assert order.index("registry") < order.index("security")

    # Capabilities remain reachable through the running Kernel post-boot.
    for capability_name in _CANONICAL_CAPABILITY_NAMES:
        assert kernel.get_capability(capability_name).provider == "security"

    await kernel.shutdown()
    assert kernel.state.value == "STOPPED"
    assert security_engine.state == EngineState.STOPPED


# -- G. Missing dependencies: fail-closed boot -------------------------------------


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_without_storage_dependency() -> None:
    """SecurityEngine declares dependencies=["storage", "registry"]. Registry is
    auto-registered by the Kernel, but Storage is not — registering Security
    without Storage must trip the existing dependency-resolution guard and
    fail the boot, never silently proceed."""
    kernel = Kernel()
    security_engine = SecurityEngine()
    kernel.register_engine(security_engine)  # no StorageEngine registered

    with pytest.raises(KernelBootError):
        await kernel.boot()

    assert kernel.state.value == "FAILED"
    assert security_engine.state != EngineState.RUNNING


# -- H. Diagnostics truthfulness ----------------------------------------------------


@pytest.mark.asyncio
async def test_security_engine_health_never_claims_unimplemented_subsystems() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)
    await engine.start()

    health = engine.health()

    assert health["authentication_implemented"] is False
    assert health["authorization_implemented"] is False
    assert health["secret_store_implemented"] is False
    assert health["audit_implemented"] is False


@pytest.mark.asyncio
async def test_security_engine_diagnostics_lists_not_yet_implemented_subsystems() -> None:
    kernel = Kernel()
    engine = SecurityEngine()
    await engine.initialize(kernel)

    detail = engine.diagnostics()

    for expected in (
        "authentication",
        "authorization",
        "secret_storage",
        "audit_enforcement",
        "kernel_capability_dispatch",
    ):
        assert expected in detail["not_yet_implemented"]


def test_security_engine_version_is_stable_string() -> None:
    engine = SecurityEngine()
    assert isinstance(engine.version(), str)
    assert engine.version() == engine.version()


@pytest.mark.asyncio
async def test_security_engine_status_reflects_real_engine_state() -> None:
    engine = SecurityEngine()
    assert engine.status() == EngineState.UNINITIALIZED.value

    kernel = Kernel()
    await engine.initialize(kernel)
    assert engine.status() == EngineState.READY.value

    await engine.start()
    assert engine.status() == EngineState.RUNNING.value
