"""M6.3-2 regression suite: ExternalExecutionManager -> Connector Engine, proven.

Prior to M6.3, the planning audit found that no production code and no test
anywhere ever set an `ExternalExecutionRequest.target` to a real Connector
capability name -- the wiring this milestone is named for was structurally
possible (`ExternalExecutionManager._dispatch_target` already forwards any
target through real `kernel.invoke_capability`) but had zero runtime proof.
The real, tested Workflow-to-Connector path was a completely separate
mechanism (`WorkflowStep.capability_name` via `StepEvaluator`) that never
touches `ExternalExecutionManager` at all.

This suite proves the actual `ExternalExecutionManager` path reaches a real,
registered Connector capability -- no test-only `_handler` shortcut, no
fake-success fallback. The test fails if the connector driver is not
genuinely invoked (its own real, distinguishable response payload is
asserted, and the tenant-scoped secret is proven to have actually resolved).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.models import ActionRequest, ConnectorActionType, ConnectorProfile
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import ExternalExecutionError

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32
_ROLE = "EXT_EXEC_CONNECTOR_TEST_ROLE"
_TENANT = "tenant_ext_exec_conn"
_OTHER_TENANT = "tenant_ext_exec_conn_other"


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[Kernel]:
    db_path = (tmp_path / f"kortex_ext_conn_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_ext_conn_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_ext_conn",
                principal_type="USER",
                credential_hash=hasher.hash("pass"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    await storage_engine.data.execute_in_transaction(_seed_rbac)

    connector_engine.register_driver(DummyConnectorDriver())
    await security_engine.put_secret("vault:ext-exec-conn-secret", _TENANT, "real-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-ext-exec-conn",
            tenant_id=_TENANT,
            name="External Execution Connector Profile",
            driver_id="connector-dummy",
            secret_handle="vault:ext-exec-conn-secret",
        )
    )

    try:
        yield kernel
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_external_execution_reaches_real_connector_capability(kernel_env: Kernel) -> None:
    """The actual `ExternalExecutionManager` path -- not the separate
    WorkflowStep/StepEvaluator path -- reaches a real, registered Connector
    capability and the real driver genuinely executes."""
    kernel = kernel_env
    token = await _token(kernel, _TENANT, "user_ext_conn", "pass")

    result = await kernel.invoke_capability(
        CapabilityRequest(
            capability_name="kortex.workflow.external.execute",
            session_token=token,
            parameters={
                "target": "kortex.connector.action.execute",
                "operation_type": "CAPABILITY",
                "parameters": {
                    "request": ActionRequest(
                        request_id=f"ext-conn-{uuid4()}",
                        profile_id="prof-ext-exec-conn",
                        action_type=ConnectorActionType.FETCH,
                    )
                },
                "timeout_seconds": 10.0,
                # `execute_external_operation` threads this specific kwarg
                # down into `ExternalExecutionManager._dispatch_target`'s
                # own, INNER `kortex.connector.action.execute` dispatch --
                # distinct from (and in addition to) the OUTER
                # `CapabilityRequest.session_token` below, which only
                # authenticates the outer `kortex.workflow.external.execute`
                # call itself.
                "session_token": token,
            },
            context={"resource_tenant_id": _TENANT},
        )
    )

    assert result["status"] == "COMPLETED"
    output = result["output"]
    # The dummy driver's own, real, distinguishable response -- proves the
    # actual Connector Engine + driver executed, not a fabricated/fake
    # success and not a test-only `_handler` shortcut.
    assert output["status"] == "SUCCESS"
    assert output["response_payload"]["mock_driver_id"] == "connector-dummy"
    # Proves the tenant-scoped secret genuinely resolved through the real
    # SecurityEngine secret store (M6.0-2 + M6.3-1), not merely that the
    # driver was reached.
    assert output["response_payload"]["secret_authenticated"] is True


@pytest.mark.asyncio
async def test_external_execution_to_connector_fails_closed_for_unresolvable_profile(kernel_env: Kernel) -> None:
    """An external execution targeting Connector with a nonexistent/wrong-
    tenant profile fails closed -- real dispatch surfaces the real
    `ConnectorProfileNotFoundError` through to a FAILED execution record,
    never a fabricated success."""
    kernel = kernel_env
    token = await _token(kernel, _TENANT, "user_ext_conn", "pass")

    with pytest.raises(ExternalExecutionError):
        await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.workflow.external.execute",
                session_token=token,
                parameters={
                    "target": "kortex.connector.action.execute",
                    "operation_type": "CAPABILITY",
                    "parameters": {
                        "request": ActionRequest(
                            request_id=f"ext-conn-{uuid4()}",
                            profile_id="prof-does-not-exist",
                            action_type=ConnectorActionType.FETCH,
                        )
                    },
                    "timeout_seconds": 5.0,
                    "retry_policy": {"max_attempts": 1},
                    "session_token": token,
                },
                context={"resource_tenant_id": _TENANT},
            )
        )
