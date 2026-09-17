"""The KORTEX Python Execution Engine.

Registers the Python Action asset capabilities and the single governed
execution capability, `kortex.python.execute`. It adds no execution authority
of its own: every capability here is reached through the existing
`Kernel.invoke_capability` -> `CapabilityDispatcher` -> `SecurityEngine` path,
and the Trusted-Python capability bridge re-enters that same dispatcher for
nested calls.

Tenant identity comes exclusively from the dispatcher-built
`CapabilityExecutionContext`. `tenant_id` is not a parameter of any capability
below, so there is no value a caller can send that would place an action in, or
read an action from, another tenant.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.container import Container
from kortex.engines.python_exec.actions import PythonActionManager
from kortex.engines.python_exec.gateway import PythonExecutionGateway
from kortex.engines.python_exec.models import (
    PythonExecutionLimits,
    PythonExecutionRequest,
    PythonNetworkPolicy,
    PythonTrustLevel,
)

if TYPE_CHECKING:
    from kortex.core.dispatch import CapabilityExecutionContext
    from kortex.core.kernel import Kernel
    from kortex.engines.storage.interfaces import IDataStore

EXECUTE_CAPABILITY = "kortex.python.execute"
ACTION_PUBLISH_CAPABILITY = "kortex.python.action.publish"
ACTION_GET_CAPABILITY = "kortex.python.action.get"
ACTION_LIST_CAPABILITY = "kortex.python.action.list"
ACTION_VERSION_LIST_CAPABILITY = "kortex.python.action.version.list"

# Only a principal holding this permission may publish a TRUSTED action.
# Trust is an administrative grant, never a property an author can assert about
# their own code by setting a field on it.
TRUSTED_AUTHOR_PERMISSION = "python:trust"


class PythonExecutionEngine(BaseEngine):
    """System Engine owning versioned Python Actions and their governed execution."""

    def __init__(
        self,
        *,
        data_store: IDataStore | None = None,
        execution_root: Path | None = None,
        identity_name: str = "KortexPythonExec",
    ) -> None:
        super().__init__()
        self._data_store = data_store
        self._action_manager = PythonActionManager(data_store)
        self._gateway = PythonExecutionGateway(
            action_manager=self._action_manager,
            execution_root=execution_root,
            data_store=data_store,
            identity_name=identity_name,
        )
        self._kernel: Kernel | None = None

    @property
    def name(self) -> str:
        return "python_exec"

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security"]

    @property
    def action_manager(self) -> PythonActionManager:
        return self._action_manager

    @property
    def gateway(self) -> PythonExecutionGateway:
        return self._gateway

    async def initialize(self, kernel: Kernel) -> None:
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Python Execution Engine...")

        try:
            self._kernel = kernel
            self._gateway.bind_kernel(kernel)
            self._resolve_data_store(kernel)

            kernel.register_capability(
                name=EXECUTE_CAPABILITY,
                description=(
                    "Execute a pinned, immutable Python Action version inside the governed "
                    "KORTEX Python execution boundary."
                ),
                provider=self.name,
                handler=self.execute_action,
                required_permissions=["python:execute"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=ACTION_PUBLISH_CAPABILITY,
                description="Publish a new immutable version of a tenant-owned Python Action.",
                provider=self.name,
                handler=self.publish_action_version,
                required_permissions=["python:write"],
                requires_execution_context=True,
                security_classification="CONFIDENTIAL",
                is_read_only=False,
                is_idempotent=False,
            )
            kernel.register_capability(
                name=ACTION_GET_CAPABILITY,
                description="Retrieve a tenant-owned Python Action and its latest version number.",
                provider=self.name,
                handler=self.get_action,
                required_permissions=["python:read"],
                requires_execution_context=True,
                is_read_only=True,
                is_idempotent=True,
            )
            kernel.register_capability(
                name=ACTION_LIST_CAPABILITY,
                description="List the calling tenant's Python Actions.",
                provider=self.name,
                handler=self.list_actions,
                required_permissions=["python:read"],
                requires_execution_context=True,
                is_read_only=True,
                is_idempotent=True,
            )
            kernel.register_capability(
                name=ACTION_VERSION_LIST_CAPABILITY,
                description="List published version metadata for one tenant-owned Python Action.",
                provider=self.name,
                handler=self.list_action_versions,
                required_permissions=["python:read"],
                requires_execution_context=True,
                is_read_only=True,
                is_idempotent=True,
            )

            self._set_state(EngineState.READY)
            self.logger.info("KORTEX Python Execution Engine initialized.")
        except Exception:
            self._set_state(EngineState.FAILED)
            raise

    def _resolve_data_store(self, kernel: Kernel) -> None:
        """Wire Storage Engine's `IDataStore` from the Kernel IoC container.

        Deferred to `initialize` rather than taken as a constructor argument,
        matching `ConnectorEngine`/`WorkflowEngine`'s established pattern, so
        the composition root does not have to order engine construction around
        storage availability.
        """
        if self._data_store is not None:
            return
        if not isinstance(getattr(kernel, "container", None), Container):
            return
        if not kernel.container.has("engine.storage"):
            return
        with contextlib.suppress(Exception):
            storage_engine = kernel.container.resolve("engine.storage")
            if storage_engine is not None and hasattr(storage_engine, "data"):
                self._data_store = storage_engine.data
                self._action_manager.bind_data_store(storage_engine.data)
                self._gateway.bind_data_store(storage_engine.data)

    async def start(self) -> None:
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)

    async def health_check(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "state": self.state.value,
            "data_store_bound": self._data_store is not None,
            "active_execution_tokens": self._gateway.token_store.active_count(),
        }

    # -- Capability handlers -------------------------------------------------

    async def execute_action(
        self,
        action_id: str,
        version: int,
        input_payload: dict[str, Any] | None = None,
        workflow_id: str | None = None,
        execution_id: str | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.python.execute`.

        `version` is required and has no default: a workflow must pin the exact
        Action version it was authored against. Defaulting to "latest" would
        mean publishing a new version silently changes what every existing
        workflow executes.
        """
        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        principal = execution_context.principal if execution_context is not None else None
        session_token = execution_context.session_token if execution_context is not None else None

        request = PythonExecutionRequest(
            tenant_id=tenant_id,
            action_id=action_id,
            version=version,
            input_payload=input_payload or {},
            workflow_id=workflow_id,
            execution_id=execution_id,
            correlation_id=execution_context.correlation_id if execution_context is not None else None,
            principal_id=principal.principal_id if principal is not None else None,
        )
        result = await self._gateway.execute(
            request,
            principal=principal,
            session_token=session_token,
            loop=asyncio.get_running_loop(),
        )
        return result.model_dump(mode="json")

    async def publish_action_version(
        self,
        action_id: str,
        name: str,
        source_code: str,
        description: str = "",
        entrypoint: str = "main",
        trust_level: str = PythonTrustLevel.UNTRUSTED.value,
        allowed_capabilities: list[str] | None = None,
        network_policy: str = PythonNetworkPolicy.DENY.value,
        network_allow_list: list[str] | None = None,
        limits: dict[str, Any] | None = None,
        requirements_lock: list[str] | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """`kortex.python.action.publish`.

        Publishing a TRUSTED action additionally requires the
        `python:trust` permission. Without this check, `python:write` alone
        would let any author mark their own code trusted and thereby grant
        itself the capability bridge -- trust has to be conferred, not claimed.
        """
        from kortex.engines.security.exceptions import AuthorizationDeniedError

        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        principal = execution_context.principal if execution_context is not None else None
        resolved_trust = PythonTrustLevel(trust_level)

        if resolved_trust is PythonTrustLevel.TRUSTED and not await self._holds_trust_permission(principal):
            raise AuthorizationDeniedError("Publishing a TRUSTED Python Action requires the 'python:trust' permission.")

        version = await self._action_manager.publish_version(
            tenant_id=tenant_id,
            action_id=action_id,
            name=name,
            source_code=source_code,
            description=description,
            entrypoint=entrypoint,
            trust_level=resolved_trust,
            allowed_capabilities=tuple(allowed_capabilities or ()),
            network_policy=PythonNetworkPolicy(network_policy),
            network_allow_list=tuple(network_allow_list or ()),
            limits=PythonExecutionLimits.model_validate(limits) if limits else None,
            requirements_lock=tuple(requirements_lock or ()),
            created_by=principal.principal_id if principal is not None else None,
        )
        return {
            "action_id": version.action_id,
            "version": version.version,
            "source_sha256": version.source_sha256,
            "trust_level": version.trust_level.value,
            "entrypoint": version.entrypoint,
            "allowed_capabilities": list(version.allowed_capabilities),
        }

    async def _holds_trust_permission(self, principal: Any) -> bool:
        """Whether `principal` may confer TRUSTED status on a Python Action.

        Decided by `SecurityEngine.authorize()` -- the same audited
        authorization authority `CapabilityDispatcher` itself uses -- rather
        than by reading RBAC state directly here. This engine must not become a
        second authorization authority, and routing through `authorize()` means
        the grant/deny decision is recorded in the existing audit trail exactly
        like every other one.

        Any failure to reach a decision returns False, denying the TRUSTED
        publish. That is the fail-closed direction: an unavailable authorization
        service must never be the reason code becomes trusted.
        """
        if principal is None or self._kernel is None:
            return False
        try:
            from typing import cast

            from kortex.engines.security.engine import SecurityEngine
            from kortex.engines.security.models import ClassificationLevel, PermissionRequirement

            security_engine = cast(SecurityEngine, self._kernel.get_engine("security"))
            decision = await security_engine.authorize(
                principal,
                PermissionRequirement(
                    capability_name=ACTION_PUBLISH_CAPABILITY,
                    required_permissions=[TRUSTED_AUTHOR_PERMISSION],
                    security_classification=ClassificationLevel.CONFIDENTIAL,
                ),
                # ABAC denies by default when `resource_tenant_id` is absent, so
                # this check would refuse every principal -- including one that
                # genuinely holds `python:trust` -- without it. The value is the
                # principal's *own* tenant, taken from the already-verified
                # principal, never from a caller-supplied parameter.
                {"resource_tenant_id": principal.tenant_id},
            )
            return bool(decision.is_allowed)
        except Exception as exc:
            self.logger.warning("Unable to evaluate the Python Action trust permission: %s", exc)
            return False

    async def get_action(
        self, action_id: str, execution_context: CapabilityExecutionContext | None = None, **_: Any
    ) -> dict[str, Any]:
        """`kortex.python.action.get`."""
        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        action = await self._action_manager.get_action(tenant_id=tenant_id, action_id=action_id)
        return action.model_dump(mode="json")

    async def list_actions(
        self, limit: int = 200, execution_context: CapabilityExecutionContext | None = None, **_: Any
    ) -> list[dict[str, Any]]:
        """`kortex.python.action.list`."""
        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        actions = await self._action_manager.list_actions(tenant_id=tenant_id, limit=limit)
        return [action.model_dump(mode="json") for action in actions]

    async def list_action_versions(
        self,
        action_id: str,
        limit: int = 100,
        execution_context: CapabilityExecutionContext | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        """`kortex.python.action.version.list`."""
        tenant_id = execution_context.tenant_id if execution_context is not None else "default"
        return await self._action_manager.list_versions(tenant_id=tenant_id, action_id=action_id, limit=limit)


__all__ = [
    "ACTION_GET_CAPABILITY",
    "ACTION_LIST_CAPABILITY",
    "ACTION_PUBLISH_CAPABILITY",
    "ACTION_VERSION_LIST_CAPABILITY",
    "EXECUTE_CAPABILITY",
    "TRUSTED_AUTHOR_PERMISSION",
    "PythonExecutionEngine",
]
