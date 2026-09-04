"""Integration tests for KORTEX Backup Engine with Kernel runtime.

Tests:
- Kernel registration and boot sequence integration
- BootEngine health rollup integration via Kernel.health_check()
- Canonical capability invocation through Kernel.invoke_capability():
  1. kortex.backup.create
  2. kortex.backup.list
  3. kortex.backup.get
  4. kortex.backup.verify
  5. kortex.backup.diagnostics.get
  6. kortex.backup.delete
- Security enforcement:
  * Unauthenticated invocation rejected (AuthenticationError)
  * Unauthorized invocation rejected (AuthorizationDeniedError)
- Clean runtime shutdown and task draining
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.backup.constants import (
    CAPABILITY_BACKUP_CREATE,
    CAPABILITY_BACKUP_DELETE,
    CAPABILITY_BACKUP_DIAGNOSTICS_GET,
    CAPABILITY_BACKUP_GET,
    CAPABILITY_BACKUP_LIST,
    CAPABILITY_BACKUP_VERIFY,
    PERMISSION_BACKUP_MANAGE,
    PERMISSION_BACKUP_READ,
)
from kortex.engines.backup.crypto import BackupCryptoManager
from kortex.engines.backup.engine import BackupEngine
from kortex.engines.backup.models import BackupConfig
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import (
    PrincipalType,
    RolePermissionRecord,
    TokenPayload,
)
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32


@pytest.mark.asyncio
async def test_backup_kernel_boot_and_dispatch_integration(tmp_path: Path) -> None:
    """Verify Backup boots in real Kernel, rolls up into health_check, and dispatches capabilities."""
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")

    storage_dir = tmp_path / "backup_storage"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)

    backup_dir = tmp_path / "backups"
    crypto_mgr = BackupCryptoManager(key=_TEST_MASTER_KEY, key_id="integration-key")
    backup_engine = BackupEngine(
        config=BackupConfig(backup_directory=str(backup_dir)),
        crypto_manager=crypto_mgr,
    )

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(backup_engine)

    # Boot the Kernel
    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    # 1. Verify engine registration
    resolved = kernel.get_engine("backup")
    assert resolved is backup_engine
    assert backup_engine.state.value == "RUNNING"

    # 2. Kernel health check rollup check
    sys_health = await kernel.health_check()
    assert "system_health" in sys_health
    reports = sys_health["system_health"]["engines"]
    assert "backup" in reports
    assert reports["backup"]["healthy"] is True

    # 3. Seed some data into storage
    await storage_engine.file.write_file("documents/test.txt", b"Integration test storage data")

    # 4. Provision authorized operator with system:backup:manage and system:backup:read
    tenant_id = "tenant-backup-test"
    principal_id = f"backup-operator-{uuid.uuid4().hex[:6]}"
    session_token = await _provision_backup_principal(
        kernel=kernel,
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        permissions=[PERMISSION_BACKUP_MANAGE, PERMISSION_BACKUP_READ],
    )

    # 5. Invoke kortex.backup.create
    create_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_CREATE,
        session_token=session_token,
        parameters={"scope": "FULL_INSTANCE", "metadata": {"origin": "integration_test"}},
        context={"resource_tenant_id": tenant_id},
    )
    create_res = await kernel.invoke_capability(create_req)
    assert isinstance(create_res, dict)
    assert create_res["state"] == "VALID"
    backup_id = create_res["backup_id"]
    assert create_res["is_encrypted"] is True

    # 6. Invoke kortex.backup.list
    list_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_LIST,
        session_token=session_token,
        parameters={"limit": 10},
        context={"resource_tenant_id": tenant_id},
    )
    list_res = await kernel.invoke_capability(list_req)
    assert isinstance(list_res, dict)
    assert list_res["total_count"] == 1
    assert list_res["backups"][0]["backup_id"] == backup_id

    # 7. Invoke kortex.backup.get
    get_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_GET,
        session_token=session_token,
        parameters={"backup_id": backup_id},
        context={"resource_tenant_id": tenant_id},
    )
    get_res = await kernel.invoke_capability(get_req)
    assert isinstance(get_res, dict)
    assert get_res["backup"]["backup_id"] == backup_id

    # 8. Invoke kortex.backup.verify
    verify_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_VERIFY,
        session_token=session_token,
        parameters={"backup_id": backup_id},
        context={"resource_tenant_id": tenant_id},
    )
    verify_res = await kernel.invoke_capability(verify_req)
    assert isinstance(verify_res, dict)
    assert verify_res["is_valid"] is True
    assert verify_res["checksum_verified"] is True
    assert verify_res["encryption_verified"] is True

    # 9. Invoke kortex.backup.diagnostics.get
    diag_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_DIAGNOSTICS_GET,
        session_token=session_token,
        parameters={},
        context={"resource_tenant_id": tenant_id},
    )
    diag_res = await kernel.invoke_capability(diag_req)
    assert isinstance(diag_res, dict)
    assert diag_res["state"] in ("READY", "RUNNING")

    # 10. Security verification: Unauthenticated caller rejected
    unauth_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_CREATE,
        session_token=None,
        parameters={"scope": "FULL_INSTANCE"},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(unauth_req)

    # 11. Security verification: Unauthorized caller rejected (lacks system:backup:manage)
    unauthorized_token = await _provision_backup_principal(
        kernel=kernel,
        security_engine=security_engine,
        tenant_id=tenant_id,
        principal_id=f"unauth-user-{uuid.uuid4().hex[:6]}",
        permissions=[PERMISSION_BACKUP_READ],  # Has read, but lacks manage!
    )
    unauthorized_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_CREATE,
        session_token=unauthorized_token,
        parameters={"scope": "FULL_INSTANCE"},
        context={"resource_tenant_id": tenant_id},
    )
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(unauthorized_req)

    # 12. Invoke kortex.backup.delete
    del_req = CapabilityRequest(
        capability_name=CAPABILITY_BACKUP_DELETE,
        session_token=session_token,
        parameters={"backup_id": backup_id},
        context={"resource_tenant_id": tenant_id},
    )
    del_res = await kernel.invoke_capability(del_req)
    assert isinstance(del_res, dict)
    assert del_res["deleted"] is True

    # 13. Clean shutdown
    await kernel.shutdown()
    assert kernel.state == KernelState.STOPPED
    assert backup_engine.state.value == "STOPPED"


async def _provision_backup_principal(
    kernel: Kernel,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
    permissions: list[str],
) -> TokenPayload:
    """Helper to provision a test principal with specific backup permissions."""
    role_name = f"role-{uuid.uuid4().hex[:6]}"
    password = "SafePassword123!"

    # Provision principal
    await security_engine.authentication_manager.provision_principal(
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_type=PrincipalType.USER,
        credential=password,
        roles=[role_name],
        attributes={"clearance_level": "INTERNAL"},
    )

    # Grant permissions to role in DB
    async def _action(session: Any) -> None:
        for perm in permissions:
            perm_rec = RolePermissionRecord(
                id=str(uuid.uuid4()),
                role=role_name,
                permission=perm,
            )
            session.add(perm_rec)

    storage = kernel.get_engine("storage")
    await storage.data.execute_in_transaction(_action)

    # Authenticate and obtain session token
    principal = await security_engine.authenticate(
        {
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": password,
            "principal_type": PrincipalType.USER.value,
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)
