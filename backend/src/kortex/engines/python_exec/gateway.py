"""The KORTEX Python Execution Gateway.

KORTEX owns this Gateway. The platform boundaries (AppContainer + restricted
token + Job Object on Windows, nsjail on Linux) are isolation *dependencies* it
invokes -- none of them is a control plane, and none of them decides who may
execute what. That decision was already made upstream by `SecurityEngine` and
`CapabilityDispatcher` before `kortex.python.execute` was ever called.

One execution, in order:

    resolve pinned Action version   (tenant-scoped, integrity-checked)
      -> resolve policy             (trust level, limits, network, capabilities)
      -> ephemeral ACL'd workspace
      -> mint execution token + start bridge   (Trusted only)
      -> governed platform boundary
      -> parse the single JSON result document
      -> revoke token, stop bridge, reclaim workspace
      -> persist sanitized execution lineage

Teardown is unconditional. The token is revoked, the bridge stopped and the
workspace reclaimed in a `finally`, so a crash, a timeout, or an isolation
failure all leave the same clean state as a success. That is what makes
"invalid after execution termination" true for the token rather than merely
intended.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.python_exec.actions import PythonActionManager
from kortex.engines.python_exec.exceptions import (
    IsolationUnavailableError,
    PythonExecutionError,
)
from kortex.engines.python_exec.ipc import CapabilityBridge, CapabilityBridgeServer, ExecutionTokenStore
from kortex.engines.python_exec.models import (
    BoundaryResult,
    PythonBoundaryKind,
    PythonExecutionRecordModel,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
    PythonTrustLevel,
)
from kortex.engines.python_exec.policy import ExecutionPolicy, build_environment, resolve_policy
from kortex.engines.python_exec.workspace import (
    ExecutionWorkspace,
    RuntimeImage,
    SandboxIdentity,
    create_sandbox_identity,
    default_execution_root,
    provision_runtime,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.security.models import SecurityPrincipal, TokenPayload
    from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engine.python_exec.gateway")

_IS_WINDOWS = platform.system() == "Windows"

# How much stderr is retained for diagnostics. Enough to carry a Python
# traceback, small enough that a chatty action cannot bloat every audit record.
STDERR_EXCERPT_LIMIT = 8192

# The execution token's lifetime is bounded independently of the execution's
# own timeout, so a boundary that fails to terminate cannot leave a usable
# token behind indefinitely.
_TOKEN_TTL_MARGIN_SECONDS = 30.0

RUNNER_FILENAME = "kortex_runner.py"
ACTION_FILENAME = "kortex_action.py"


class PythonExecutionGateway:
    """Executes pinned Python Action versions inside a governed boundary."""

    def __init__(
        self,
        *,
        action_manager: PythonActionManager,
        kernel: Kernel | None = None,
        execution_root: Path | None = None,
        data_store: IDataStore | None = None,
        identity_name: str = "KortexPythonExec",
    ) -> None:
        self._actions = action_manager
        self._kernel = kernel
        self._execution_root = execution_root or default_execution_root()
        self._data_store = data_store
        self._identity_name = identity_name
        self._identity: SandboxIdentity | None = None
        self._runtime: RuntimeImage | None = None
        self._token_store = ExecutionTokenStore()

    @property
    def token_store(self) -> ExecutionTokenStore:
        return self._token_store

    def bind_kernel(self, kernel: Kernel) -> None:
        self._kernel = kernel

    def bind_data_store(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    def ensure_provisioned(self) -> RuntimeImage:
        """Establish the sandbox identity and runtime image, or fail closed.

        Provisioning is lazy rather than done at engine start: a KORTEX
        deployment that never executes Python should not pay a ~40MB runtime
        copy, and an environment where isolation cannot be established should
        fail when execution is actually attempted, with a clear error, rather
        than preventing the whole Kernel from booting.
        """
        if self._runtime is not None and self._identity is not None:
            return self._runtime
        self._identity = create_sandbox_identity(self._identity_name)
        self._runtime = provision_runtime(root_directory=self._execution_root, identity=self._identity)
        return self._runtime

    async def execute(
        self,
        request: PythonExecutionRequest,
        *,
        principal: SecurityPrincipal | None = None,
        session_token: TokenPayload | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> PythonExecutionResult:
        """Run one pinned Action version under the governed boundary.

        Every governed refusal -- an unresolvable action, a cross-tenant miss, a
        failed integrity check, an unsupported trust level, an unavailable
        isolation primitive -- becomes a structured REJECTED result rather than a
        propagating exception. A workflow step must be able to observe that its
        Python did not run without the Gateway destroying WorkflowEngine's
        authoritative state model on the way out. The dispatcher still audits the
        invocation either way.
        """
        started = time.monotonic()
        version: Any = None
        policy: ExecutionPolicy | None = None
        workspace: ExecutionWorkspace | None = None
        bridge_server: CapabilityBridgeServer | None = None
        issued_token: str | None = None

        try:
            # Resolution and policy live *inside* the try: an action that cannot
            # be resolved, or a trust level this platform refuses, must produce
            # a REJECTED result, not an exception escaping the capability.
            version = await self._actions.get_version(
                tenant_id=request.tenant_id, action_id=request.action_id, version=request.version
            )
            # Raises `UnsupportedTrustLevelError` for Windows Untrusted Python
            # before any workspace exists or any process is created.
            policy = resolve_policy(version)
            runtime = self.ensure_provisioned()
            assert self._identity is not None

            workspace = ExecutionWorkspace(self._execution_root / "workspaces", self._identity)
            workspace.write_text(ACTION_FILENAME, version.source_code)
            shutil.copy2(Path(__file__).with_name("sandbox_runner.py"), workspace.path / RUNNER_FILENAME)

            bridge_address: str | None = None
            if policy.capability_bridge_enabled:
                bridge_address, issued_token, bridge_server = self._start_bridge(
                    policy=policy,
                    request=request,
                    version_number=version.version,
                    principal=principal,
                    session_token=session_token,
                    workspace=workspace,
                    loop=loop,
                )

            environment = build_environment(
                parent_environment=self._parent_environment(),
                workspace_path=str(workspace.path),
                runtime_path=str(runtime.root),
                input_file=str(workspace.path / "input.json"),
                bridge_address=bridge_address,
                execution_token=issued_token,
            )

            stdin_payload = json.dumps(
                {
                    "action_path": str(workspace.path / ACTION_FILENAME),
                    "entrypoint": version.entrypoint,
                    "input": request.input_payload,
                },
                default=str,
            ).encode("utf-8")

            arguments = [str(runtime.interpreter), "-I", "-B", str(workspace.path / RUNNER_FILENAME)]
            # Off the event loop, deliberately. `_run_boundary` blocks for the
            # entire lifetime of the sandboxed process -- potentially the full
            # timeout budget. Running it inline would freeze the whole KORTEX
            # backend for that duration, and it would *deadlock Trusted Python
            # outright*: the capability bridge marshals each request back onto
            # this same loop with `run_coroutine_threadsafe`, so a blocked loop
            # can never answer the sandbox, which then waits until its own
            # timeout kills it. Every Trusted-Python capability call depends on
            # this being a thread hand-off.
            boundary_result = await asyncio.to_thread(
                self._run_boundary,
                policy=policy,
                runtime=runtime,
                workspace=workspace,
                arguments=arguments,
                environment=environment,
                stdin_payload=stdin_payload,
            )

            result = self._interpret(boundary_result, version=version, policy=policy, bridge=bridge_server)
        except PythonExecutionError as exc:
            # `version`/`policy` are `None` when the refusal happened during
            # resolution itself (unknown action, cross-tenant miss, failed
            # integrity check, refused trust level). A rejected execution then
            # reports no boundary, because none was ever entered -- which is
            # exactly the distinction a reader needs from this result.
            result = PythonExecutionResult(
                status=PythonExecutionStatus.REJECTED,
                boundary=None if policy is None or isinstance(exc, IsolationUnavailableError) else policy.boundary,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - started) * 1000,
                action_id=request.action_id,
                version=request.version,
                trust_level=version.trust_level if version is not None else None,
            )
        finally:
            # Unconditional: the token dies with the execution, on every path.
            if issued_token is not None:
                self._token_store.revoke(issued_token)
            if bridge_server is not None:
                bridge_server.stop()
            if workspace is not None:
                workspace.dispose()

        await self._persist(request, result)
        return result

    def _parent_environment(self) -> dict[str, str]:
        import os

        return dict(os.environ)

    def _start_bridge(
        self,
        *,
        policy: ExecutionPolicy,
        request: PythonExecutionRequest,
        version_number: int,
        principal: SecurityPrincipal | None,
        session_token: TokenPayload | None,
        workspace: ExecutionWorkspace,
        loop: asyncio.AbstractEventLoop | None,
    ) -> tuple[str, str, CapabilityBridgeServer]:
        """Mint the execution token and open the governed IPC endpoint."""
        if self._kernel is None:
            raise IsolationUnavailableError(
                "Trusted Python requires a Kernel reference for governed capability access."
            )
        assert self._identity is not None

        issued = self._token_store.issue(
            tenant_id=request.tenant_id,
            action_id=request.action_id,
            version=version_number,
            allowed_capabilities=frozenset(policy.allowed_capabilities),
            ttl_seconds=policy.limits.timeout_seconds + _TOKEN_TTL_MARGIN_SECONDS,
            principal=principal,
            session_token=session_token,
            workflow_id=request.workflow_id,
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
        )
        server = CapabilityBridgeServer(
            CapabilityBridge(self._kernel, self._token_store),
            loop or asyncio.get_running_loop(),
            container_sid=self._identity.container_sid,
            workspace=workspace.path,
        )
        address = server.start()
        logger.info(
            "Opened the governed capability bridge for Python execution %s (action=%s v%d, %d permitted capabilities).",
            issued.grant.token_id,
            request.action_id,
            version_number,
            len(policy.allowed_capabilities),
        )
        return address, issued.token, server

    def _run_boundary(
        self,
        *,
        policy: ExecutionPolicy,
        runtime: RuntimeImage,
        workspace: ExecutionWorkspace,
        arguments: list[str],
        environment: dict[str, str],
        stdin_payload: bytes,
    ) -> BoundaryResult:
        """Dispatch to the platform boundary. Imported lazily by design.

        `windows_boundary` loads `WinDLL` at module scope and cannot be
        imported off-Windows; `linux_boundary` resolves the nsjail binary in
        its constructor. Importing either eagerly would make the Gateway
        unimportable on the other platform.
        """
        assert self._identity is not None

        if policy.boundary is PythonBoundaryKind.WINDOWS_RESTRICTED_TOKEN_JOB:
            from kortex.engines.python_exec.windows_boundary import WindowsExecutionBoundary

            return WindowsExecutionBoundary(self._identity, runtime).run(
                arguments=arguments,
                working_directory=workspace.path,
                environment=environment,
                stdin_payload=stdin_payload,
                limits=policy.limits,
            )

        from kortex.engines.python_exec.linux_boundary import LinuxExecutionBoundary

        return LinuxExecutionBoundary(runtime).run(
            arguments=arguments,
            working_directory=workspace.path,
            environment=environment,
            stdin_payload=stdin_payload,
            limits=policy.limits,
            trust_level=policy.trust_level,
            network_policy=policy.network_policy,
        )

    def _interpret(
        self,
        boundary_result: BoundaryResult,
        *,
        version: Any,
        policy: ExecutionPolicy,
        bridge: CapabilityBridgeServer | None,
    ) -> PythonExecutionResult:
        """Turn raw boundary output into a structured, workflow-safe result.

        A timeout is reported as TIMEOUT regardless of what the process may
        have written before it was killed: partial output from a terminated
        execution must never be presented to a workflow as a completed result.
        """
        stderr_text = boundary_result.stderr.decode("utf-8", "replace")[:STDERR_EXCERPT_LIMIT]
        common: dict[str, Any] = {
            "boundary": policy.boundary,
            "exit_code": boundary_result.exit_code,
            "duration_ms": boundary_result.duration_ms,
            "stderr_excerpt": stderr_text,
            "capability_calls": bridge.request_count if bridge is not None else 0,
            "action_id": version.action_id,
            "version": version.version,
            "trust_level": version.trust_level,
        }

        if boundary_result.timed_out:
            return PythonExecutionResult(
                status=PythonExecutionStatus.TIMEOUT,
                timed_out=True,
                error=(
                    f"Execution exceeded its {policy.limits.timeout_seconds}s budget and its "
                    f"process tree was terminated."
                ),
                **common,
            )

        try:
            document = json.loads(boundary_result.stdout.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # stdout is reserved exclusively for the result document, so a
            # parse failure is a genuine protocol failure -- not an action that
            # happened to print, which the runner routes to stderr.
            return PythonExecutionResult(
                status=PythonExecutionStatus.FAILED,
                error="The execution produced no parseable structured result on stdout.",
                **common,
            )

        if document.get("status") == "ok":
            common["capability_calls"] = document.get("capability_calls", common["capability_calls"])
            return PythonExecutionResult(
                status=PythonExecutionStatus.SUCCEEDED, output=document.get("output"), **common
            )

        common["capability_calls"] = document.get("capability_calls", common["capability_calls"])
        return PythonExecutionResult(
            status=PythonExecutionStatus.FAILED,
            error=str(document.get("error", "The Python Action failed.")),
            **common,
        )

    async def _persist(self, request: PythonExecutionRequest, result: PythonExecutionResult) -> None:
        """Record sanitized execution lineage. Best-effort by design.

        A storage outage must not convert a completed, correctly-contained
        execution into a failure -- the same policy `CapabilityDispatcher`
        already applies to its own audit recording.
        """
        if self._data_store is None:
            return

        import uuid as _uuid

        async def _write(session: AsyncSession) -> None:
            session.add(
                PythonExecutionRecordModel(
                    id=_uuid.uuid4().hex,
                    tenant_id=request.tenant_id,
                    action_id=request.action_id,
                    version=request.version,
                    trust_level=(result.trust_level or PythonTrustLevel.UNTRUSTED).value,
                    boundary=result.boundary.value if result.boundary else None,
                    status=result.status.value,
                    exit_code=result.exit_code,
                    duration_ms=int(result.duration_ms),
                    capability_calls=result.capability_calls,
                    workflow_id=request.workflow_id,
                    execution_id=request.execution_id,
                    correlation_id=request.correlation_id,
                    principal_id=request.principal_id,
                    error_message=result.error,
                )
            )

        try:
            await self._data_store.execute_in_transaction(_write)
        except Exception as exc:
            logger.warning("Unable to persist Python execution lineage: %s", exc)


__all__ = ["ACTION_FILENAME", "RUNNER_FILENAME", "STDERR_EXCERPT_LIMIT", "PythonExecutionGateway"]
