"""Phase B / B-3 regression coverage: `kortex.security.secret.get` through the
real Kernel Capability Enforcement Boundary (`kortex.core.dispatch`).

The vulnerability this suite pins down: the capability used to register
`SecretStore.get_secret` as its handler *directly*, so its `tenant_id`
argument came straight from `request.parameters` and was fully authoritative.
Any principal holding `security:read` — a deliberately broad permission that
also gates `access.authorize` and `signature.verify`, so a great many
legitimate principals hold it — could decrypt ANY tenant's secret simply by
naming that tenant. Once Phase B stores real provider API keys in the same
`SecretStore`, that is a cross-tenant credential-theft path.

`SecretStore`'s AES-GCM tenant binding was never the missing control: it
authenticates that a ciphertext belongs to the `tenant_id` it is handed, and
it was being handed the attacker's chosen value. The fix is upstream of it —
the handler now derives the tenant from the dispatcher-verified
`CapabilityExecutionContext`.

Every test drives the real dispatch path (real Storage + Security Engines,
real authentication, real RBAC), never the raw handler, because the
dispatcher is the component that injects the identity being tested.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import (
    AuthenticationError,
    AuthorizationDeniedError,
    SecretNotFoundError,
)
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord, TokenPayload
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_CAPABILITY = "kortex.security.secret.get"
_CREDENTIAL = "secret-get-test-credential"
_VICTIM_SECRET = "sk-victim-tenant-provider-api-key"  # nosec - test fixture, not a real key


def _tenant(tmp_path: Path, suffix: str) -> str:
    return f"tenant-secret-get-{tmp_path.name}-{suffix}-{uuid.uuid4().hex[:8]}"


async def _boot_kernel(tmp_path: Path) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "secret_get_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()
    return kernel, storage_engine, security_engine


async def _seed_principal(
    data_store: IDataStore, tenant_id: str, principal_id: str, roles: list[str] | None = None
) -> None:
    credential_hash = PasswordHasher().hash(_CREDENTIAL)

    async def _action(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=roles or [],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await data_store.execute_in_transaction(_action)


async def _grant_role_permission(data_store: IDataStore, role: str, permission: str) -> None:
    async def _action(session: AsyncSession) -> None:
        existing = await session.scalar(
            select(RolePermissionRecord).where(
                RolePermissionRecord.role == role,
                RolePermissionRecord.permission == permission,
            )
        )
        if existing is None:
            session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))

    await data_store.execute_in_transaction(_action)


async def _issue_token(security_engine: SecurityEngine, tenant_id: str, principal_id: str) -> TokenPayload:
    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": _CREDENTIAL,
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


async def _reader_token(
    storage_engine: StorageEngine,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
) -> TokenPayload:
    """A principal holding `security:read` — exactly the privilege the attacker
    in these tests legitimately has for their OWN tenant."""
    role = f"role-{uuid.uuid4().hex[:8]}"
    await _seed_principal(storage_engine.data, tenant_id, principal_id, roles=[role])
    await _grant_role_permission(storage_engine.data, role, "security:read")
    return await _issue_token(security_engine, tenant_id, principal_id)


# ---------------------------------------------------------------------------
# The vulnerability itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_cannot_read_another_tenants_secret_by_naming_that_tenant(tmp_path: Path) -> None:
    """THE B-3 REGRESSION. Attacker in tenant B holds `security:read` for their
    own tenant and asks for a handle that exists only in tenant A, naming
    tenant A. Before the fix this returned tenant A's plaintext."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    victim = _tenant(tmp_path, "victim")
    attacker = _tenant(tmp_path, "attacker")

    await security_engine.secret_store.put_secret("kortex/ai/openai", victim, _VICTIM_SECRET)
    attacker_token = await _reader_token(storage_engine, security_engine, attacker, "mallory")

    with pytest.raises(SecretNotFoundError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name=_CAPABILITY,
                session_token=attacker_token,
                parameters={"secret_handle": "kortex/ai/openai", "tenant_id": victim},
                context={"resource_tenant_id": attacker},
            )
        )


@pytest.mark.asyncio
async def test_the_victims_plaintext_never_appears_in_the_failure(tmp_path: Path) -> None:
    """The refusal must not leak what it refused: neither the value nor the
    victim tenant may surface in the raised error."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    victim = _tenant(tmp_path, "victim")
    attacker = _tenant(tmp_path, "attacker")

    await security_engine.secret_store.put_secret("kortex/ai/openai", victim, _VICTIM_SECRET)
    attacker_token = await _reader_token(storage_engine, security_engine, attacker, "mallory")

    with pytest.raises(SecretNotFoundError) as excinfo:
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name=_CAPABILITY,
                session_token=attacker_token,
                parameters={"secret_handle": "kortex/ai/openai", "tenant_id": victim},
                context={"resource_tenant_id": attacker},
            )
        )

    assert _VICTIM_SECRET not in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_same_named_handle_resolves_to_the_callers_own_value(tmp_path: Path) -> None:
    """Subtler and more dangerous than a miss: both tenants hold the SAME
    handle. A caller naming the other tenant must receive its own value, never
    the other tenant's — otherwise the shared handle names Phase B will
    actually use (`kortex/ai/openai`) become a silent cross-tenant read."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    victim = _tenant(tmp_path, "victim")
    attacker = _tenant(tmp_path, "attacker")

    await security_engine.secret_store.put_secret("kortex/ai/openai", victim, _VICTIM_SECRET)
    await security_engine.secret_store.put_secret("kortex/ai/openai", attacker, "sk-attackers-own-key")
    attacker_token = await _reader_token(storage_engine, security_engine, attacker, "mallory")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=_CAPABILITY,
            session_token=attacker_token,
            parameters={"secret_handle": "kortex/ai/openai", "tenant_id": victim},
            context={"resource_tenant_id": attacker},
        )
    )

    assert result == "sk-attackers-own-key"
    assert result != _VICTIM_SECRET


# ---------------------------------------------------------------------------
# The legitimate path must be untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_reads_its_own_secret(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path, "owner")

    await security_engine.secret_store.put_secret("kortex/ai/openai", tenant_id, _VICTIM_SECRET)
    token = await _reader_token(storage_engine, security_engine, tenant_id, "alice")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=_CAPABILITY,
            session_token=token,
            parameters={"secret_handle": "kortex/ai/openai", "tenant_id": tenant_id},
            context={"resource_tenant_id": tenant_id},
        )
    )

    assert result == _VICTIM_SECRET


@pytest.mark.asyncio
async def test_omitting_tenant_id_entirely_still_resolves_from_the_verified_identity(tmp_path: Path) -> None:
    """The verified identity is sufficient on its own — a caller need not (and
    after this fix, has no reason to) state a tenant at all."""
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path, "owner")

    await security_engine.secret_store.put_secret("kortex/ai/openai", tenant_id, _VICTIM_SECRET)
    token = await _reader_token(storage_engine, security_engine, tenant_id, "alice")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=_CAPABILITY,
            session_token=token,
            parameters={"secret_handle": "kortex/ai/openai"},
            context={"resource_tenant_id": tenant_id},
        )
    )

    assert result == _VICTIM_SECRET


@pytest.mark.asyncio
async def test_in_process_engine_method_is_unchanged(tmp_path: Path) -> None:
    """`SecurityEngine.get_secret`/`SecretStore.get_secret` are the trusted
    in-process path (the credential resolver Phase B builds calls one of
    them). The capability-boundary fix must not have altered their contract."""
    _kernel, _storage, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path, "inproc")

    await security_engine.secret_store.put_secret("kortex/ai/openai", tenant_id, _VICTIM_SECRET)

    assert await security_engine.get_secret("kortex/ai/openai", tenant_id) == _VICTIM_SECRET


# ---------------------------------------------------------------------------
# Enforcement boundary regressions (unchanged behaviour, pinned)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_token_is_denied_authentication(tmp_path: Path) -> None:
    kernel, _storage, _security = await _boot_kernel(tmp_path)

    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name=_CAPABILITY,
                session_token=None,
                parameters={"secret_handle": "kortex/ai/openai"},
            )
        )


@pytest.mark.asyncio
async def test_without_security_read_permission_is_denied(tmp_path: Path) -> None:
    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path, "noperm")
    await _seed_principal(storage_engine.data, tenant_id, "bob")
    token = await _issue_token(security_engine, tenant_id, "bob")

    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name=_CAPABILITY,
                session_token=token,
                parameters={"secret_handle": "kortex/ai/openai"},
                context={"resource_tenant_id": tenant_id},
            )
        )


@pytest.mark.asyncio
async def test_caller_may_not_supply_the_reserved_execution_context_parameter(tmp_path: Path) -> None:
    """The dispatcher rejects a caller-supplied `execution_context` outright
    rather than overwriting it, so the fix cannot be defeated by forging the
    channel it reads from."""
    from kortex.core.dispatch import ReservedParameterError

    kernel, storage_engine, security_engine = await _boot_kernel(tmp_path)
    tenant_id = _tenant(tmp_path, "reserved")
    token = await _reader_token(storage_engine, security_engine, tenant_id, "alice")

    with pytest.raises(ReservedParameterError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name=_CAPABILITY,
                session_token=token,
                parameters={"secret_handle": "h", "execution_context": {"principal": {"tenant_id": "elsewhere"}}},
                context={"resource_tenant_id": tenant_id},
            )
        )
