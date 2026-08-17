"""Unit tests for the KORTEX Security Engine core facade (Milestone M1 + M2 + M3).

Verifies engine identity, dependency declaration, `BaseEngine` lifecycle,
Kernel/Registry capability registration, fail-closed capability placeholder
behavior under a spread of inputs, Kernel dependency-ordered boot, fail-closed
missing-dependency boot behavior, real `SecretStore`/`AuthenticationManager`
delegation through the `kortex.security.secret.get`/`kortex.security.auth.authenticate`
capabilities, fail-closed boot when the master key or the authentication
signing key is missing/malformed, and diagnostic truthfulness.

`kortex.security.access.authorize` and `kortex.security.signature.verify`
remain M1 structural placeholders — they never authorize or verify anything.
`kortex.security.secret.get` (M2) and `kortex.security.auth.authenticate` (M3)
are real: encrypted/fail-closed, delegating to `SecretStore`/`AuthenticationManager`.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import EngineStateError, KernelBootError
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthenticationError,
    MasterKeyError,
    SecretNotFoundError,
    SecurityEngineError,
    SigningKeyError,
)
from kortex.engines.security.models import PrincipalRecord
from kortex.engines.storage.engine import StorageEngine

_CANONICAL_CAPABILITY_NAMES = [
    "kortex.security.auth.authenticate",
    "kortex.security.access.authorize",
    "kortex.security.secret.get",
    "kortex.security.signature.verify",
]
_STILL_PLACEHOLDER_CAPABILITY_NAMES = [
    "kortex.security.access.authorize",
    "kortex.security.signature.verify",
]
# Deterministic 32-byte test fixtures — never real production keys.
_TEST_MASTER_KEY = b"\x11" * 32
_TEST_AUTH_SIGNING_KEY = b"\x55" * 32


async def _boot_kernel_with_security(
    tmp_path: Path,
    master_key: bytes | None = _TEST_MASTER_KEY,
    signing_private_key: bytes | None = _TEST_AUTH_SIGNING_KEY,
) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    """Register a real StorageEngine + SecurityEngine and boot the Kernel.

    `master_key`/`signing_private_key` are injected directly into
    `SecurityEngine`'s constructor (the same "constructor injection for
    deterministic test fixtures" path Decision 1 established for the master
    key) so tests never depend on `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY`
    environment variables.
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_test_storage"))
    security_engine = SecurityEngine(master_key=master_key, signing_private_key=signing_private_key)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(storage_engine: StorageEngine, tenant_id: str, principal_id: str, password: str) -> None:
    """Insert a `PrincipalRecord` directly via `IDataStore` — mirrors how
    `test_secret_get_capability_put_then_get_round_trip` below seeds a secret
    directly through `SecretStore` rather than through a provisioning API
    (none exists for either M2 or M3)."""
    credential_hash = PasswordHasher().hash(password)

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=[],
                attributes={},
            )
        )
        await session.flush()

    await storage_engine.data.execute_in_transaction(_action)


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
async def test_security_engine_initialize_reaches_ready(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)
    assert security_engine.state == EngineState.RUNNING  # kernel.boot() also starts every engine


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
    engine = SecurityEngine(master_key=_TEST_MASTER_KEY)

    with pytest.raises(Exception):  # noqa: B017 -- ResourceAlreadyExistsError from Registry, not re-declared here
        await engine.initialize(kernel)

    assert engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_security_engine_health_check_returns_health_dict(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)

    report = await security_engine.health_check()

    assert report["engine"] == "security"
    assert report["healthy"] is True


@pytest.mark.asyncio
async def test_security_engine_stop_reaches_stopped(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)

    await security_engine.stop()

    assert security_engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_security_engine_stop_before_start_is_a_no_op() -> None:
    engine = SecurityEngine()
    await engine.stop()  # UNINITIALIZED -> stop() must not raise
    assert engine.state == EngineState.UNINITIALIZED


# -- D. Capability registration ---------------------------------------------------


@pytest.mark.asyncio
async def test_all_four_canonical_capabilities_registered_with_kernel(tmp_path: Path) -> None:
    kernel, _storage, _security = await _boot_kernel_with_security(tmp_path)

    for capability_name in _CANONICAL_CAPABILITY_NAMES:
        descriptor = kernel.get_capability(capability_name)
        assert descriptor.provider == "security"
        assert descriptor.handler is not None


def test_capabilities_empty_before_initialize() -> None:
    engine = SecurityEngine()
    assert engine.capabilities() == []


@pytest.mark.asyncio
async def test_capabilities_reflects_all_four_registrations_after_initialize(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)

    assert set(security_engine.capabilities()) == set(_CANONICAL_CAPABILITY_NAMES)
    assert len(security_engine.capabilities()) == 4


# -- E. Capability failure behavior: placeholders fail closed for every input shape --


@pytest.mark.asyncio
@pytest.mark.parametrize("capability_name", _STILL_PLACEHOLDER_CAPABILITY_NAMES)
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
async def test_placeholder_capability_handler_fails_closed_for_every_input_shape(
    tmp_path: Path, capability_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    """`auth.authenticate`, `access.authorize`, `signature.verify` remain M1
    placeholders in M2 — every input must fail closed with NOT_IMPLEMENTED."""
    kernel, _storage, _security = await _boot_kernel_with_security(tmp_path)
    descriptor = kernel.get_capability(capability_name)

    with pytest.raises(SecurityEngineError) as exc_info:
        await descriptor.handler(*args, **kwargs)

    assert exc_info.value.code == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_capability_handler_never_returns_a_value(tmp_path: Path) -> None:
    """Structural guarantee: the placeholder handler always raises — there is
    no code path that could return a truthy/ALLOW-like value."""
    kernel, _storage, _security = await _boot_kernel_with_security(tmp_path)
    descriptor = kernel.get_capability("kortex.security.access.authorize")

    with pytest.raises(SecurityEngineError):
        await descriptor.handler(principal="anyone", requirement="anything")


@pytest.mark.asyncio
async def test_security_engine_metrics_counts_not_implemented_invocations(tmp_path: Path) -> None:
    """Uses a still-placeholder capability (`access.authorize`) — `secret.get`
    is real as of M2 and no longer increments this counter."""
    kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)
    descriptor = kernel.get_capability("kortex.security.access.authorize")

    assert security_engine.metrics()["not_implemented_invocations"] == 0
    with pytest.raises(SecurityEngineError):
        await descriptor.handler("anyone", "anything")
    assert security_engine.metrics()["not_implemented_invocations"] == 1


# -- D2. secret.get is REAL as of M2: delegates to SecretStore, fails closed -------


@pytest.mark.asyncio
async def test_secret_get_capability_put_then_get_round_trip(tmp_path: Path) -> None:
    kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)
    assert security_engine._secret_store is not None  # constructed during initialize()

    await security_engine._secret_store.put_secret("secret:kortex/test", "tenant-a", "top-secret-value")
    descriptor = kernel.get_capability("kortex.security.secret.get")

    plaintext = await descriptor.handler("secret:kortex/test", "tenant-a")

    assert plaintext == "top-secret-value"


@pytest.mark.asyncio
async def test_secret_get_capability_fails_closed_for_missing_secret(tmp_path: Path) -> None:
    kernel, _storage, _security = await _boot_kernel_with_security(tmp_path)
    descriptor = kernel.get_capability("kortex.security.secret.get")

    with pytest.raises(SecretNotFoundError):
        await descriptor.handler("secret:kortex/does-not-exist", "tenant-a")


# -- D3. auth.authenticate is REAL as of M3: delegates to AuthenticationManager -----


@pytest.mark.asyncio
async def test_auth_authenticate_capability_round_trip(tmp_path: Path) -> None:
    tenant_id = f"tenant-{tmp_path.name}"
    kernel, storage_engine, security_engine = await _boot_kernel_with_security(tmp_path)
    assert security_engine._authentication_manager is not None  # constructed during initialize()

    await _seed_principal(storage_engine, tenant_id, "principal-1", "correct-secret")
    descriptor = kernel.get_capability("kortex.security.auth.authenticate")

    principal = await descriptor.handler(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "principal-1", "password": "correct-secret"}
    )

    assert principal.principal_id == "principal-1"
    assert principal.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_auth_authenticate_capability_fails_closed_for_wrong_credential(tmp_path: Path) -> None:
    tenant_id = f"tenant-{tmp_path.name}"
    kernel, storage_engine, _security = await _boot_kernel_with_security(tmp_path)
    await _seed_principal(storage_engine, tenant_id, "principal-1", "correct-secret")
    descriptor = kernel.get_capability("kortex.security.auth.authenticate")

    with pytest.raises(AuthenticationError):
        await descriptor.handler(
            {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": "principal-1", "password": "wrong"}
        )


# -- F. Dependency ordering (real Kernel boot) -------------------------------------


@pytest.mark.asyncio
async def test_security_engine_boots_after_storage_and_registry(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel_with_security(tmp_path)

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


# -- G. Missing dependencies / missing master key: fail-closed boot ---------------


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_without_storage_dependency() -> None:
    """SecurityEngine declares dependencies=["storage", "registry"]. Registry is
    auto-registered by the Kernel, but Storage is not — registering Security
    without Storage must trip the existing dependency-resolution guard and
    fail the boot, never silently proceed."""
    kernel = Kernel()
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY)
    kernel.register_engine(security_engine)  # no StorageEngine registered

    with pytest.raises(KernelBootError):
        await kernel.boot()

    assert kernel.state.value == "FAILED"
    assert security_engine.state != EngineState.RUNNING


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_when_master_key_missing(tmp_path: Path) -> None:
    """No `master_key` constructor override and no `KORTEX_MASTER_KEY`
    configured — `SecretStore` construction must raise `MasterKeyError`,
    `SecurityEngine.initialize()` must transition to FAILED and re-raise, and
    the real `Kernel.boot()` -> `BootEngine.boot_system()` path must surface
    this as `KernelBootError` with the original `MasterKeyError` preserved as
    `__cause__`. There is no fallback path that starts Security Engine
    without a master key.

    Exercises the real `Kernel.boot()` path end-to-end (not
    `SecurityEngine.initialize()` in isolation) — this is the regression test
    for the now-fixed `boot/engine.py` logging format-string defect (`%e` on
    an exception object, which used to raise a secondary `TypeError` that
    masked `KernelBootError` here).
    """
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_test_storage_no_key"))
    security_engine = SecurityEngine(master_key=None)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    with pytest.raises(KernelBootError) as exc_info:
        await kernel.boot()

    assert isinstance(exc_info.value.__cause__, MasterKeyError)
    assert kernel.state.value == "FAILED"
    assert security_engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_when_master_key_malformed(tmp_path: Path) -> None:
    """Same as above, for a `KORTEX_MASTER_KEY` value that is configured but
    does not decode to a valid 32-byte key — exercised through the real
    `Kernel.boot()` path."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_test_storage_bad_key"))
    security_engine = SecurityEngine(master_key=None)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.set_config("KORTEX_MASTER_KEY", "not-a-valid-key-encoding")

    with pytest.raises(KernelBootError) as exc_info:
        await kernel.boot()

    assert isinstance(exc_info.value.__cause__, MasterKeyError)
    assert kernel.state.value == "FAILED"
    assert security_engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_when_auth_signing_key_missing(tmp_path: Path) -> None:
    """No `signing_private_key` constructor override and no
    `KORTEX_AUTH_SIGNING_PRIVATE_KEY` configured — `AuthenticationManager`
    construction must raise `SigningKeyError`, `SecurityEngine.initialize()`
    must transition to FAILED and re-raise, and the real `Kernel.boot()` path
    must surface this as `KernelBootError` with the original `SigningKeyError`
    preserved as `__cause__`. `master_key` is supplied so this test isolates
    the signing-key failure path from the already-covered master-key path."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_test_storage_no_signing_key"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=None)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)

    with pytest.raises(KernelBootError) as exc_info:
        await kernel.boot()

    assert isinstance(exc_info.value.__cause__, SigningKeyError)
    assert kernel.state.value == "FAILED"
    assert security_engine.state == EngineState.FAILED


@pytest.mark.asyncio
async def test_security_engine_boot_fails_closed_when_auth_signing_key_malformed(tmp_path: Path) -> None:
    """Same as above, for a `KORTEX_AUTH_SIGNING_PRIVATE_KEY` value that is
    configured but does not decode to a valid 32-byte key."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "security_test_storage_bad_signing_key"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=None)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.set_config("KORTEX_AUTH_SIGNING_PRIVATE_KEY", "not-a-valid-key-encoding")

    with pytest.raises(KernelBootError) as exc_info:
        await kernel.boot()

    assert isinstance(exc_info.value.__cause__, SigningKeyError)
    assert kernel.state.value == "FAILED"
    assert security_engine.state == EngineState.FAILED


# -- H. Diagnostics truthfulness ----------------------------------------------------


@pytest.mark.asyncio
async def test_security_engine_health_reflects_real_secret_store_status(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)

    health = security_engine.health()

    assert health["authentication_implemented"] is True  # real as of M3
    assert health["authorization_implemented"] is False
    assert health["secret_store_implemented"] is True  # real as of M2
    assert health["audit_implemented"] is False


@pytest.mark.asyncio
async def test_security_engine_diagnostics_lists_not_yet_implemented_subsystems(tmp_path: Path) -> None:
    _kernel, _storage, security_engine = await _boot_kernel_with_security(tmp_path)

    detail = security_engine.diagnostics()

    for expected in ("authorization", "audit_enforcement", "kernel_capability_dispatch"):
        assert expected in detail["not_yet_implemented"]
    assert "secret_storage" not in detail["not_yet_implemented"]
    assert "authentication" not in detail["not_yet_implemented"]


def test_security_engine_version_is_stable_string() -> None:
    engine = SecurityEngine()
    assert isinstance(engine.version(), str)
    assert engine.version() == engine.version()


@pytest.mark.asyncio
async def test_security_engine_status_reflects_real_engine_state(tmp_path: Path) -> None:
    engine = SecurityEngine()
    assert engine.status() == EngineState.UNINITIALIZED.value

    _kernel, _storage_engine, security_engine = await _boot_kernel_with_security(tmp_path)
    assert security_engine.status() == EngineState.RUNNING.value
