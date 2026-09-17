"""Governed capability IPC for Trusted Python.

This is the only channel from inside the execution boundary back into KORTEX,
and it is deliberately narrow. A sandboxed action can say two things: *which*
capability it wants and *what parameters* it wants to pass. It cannot say who
it is.

Identity derivation is the whole security property. The bridge looks up the
presented execution token, and the grant it finds -- not anything in the
request -- supplies the tenant, principal, workflow, execution, and the
already-verified session token. The nested call then goes through
`Kernel.invoke_capability`, so `CapabilityDispatcher` re-authenticates that
session token and builds the authoritative `CapabilityExecutionContext`
itself, exactly as it does for any other caller. There is no second execution
authority here: this module resolves a token to a grant and then *calls the
existing dispatcher*.

Token handling rules enforced below:

* The plaintext token never enters the store -- only its SHA-256 digest is
  kept, so a memory disclosure of the store does not yield usable tokens.
* Lookup is constant-time against the digest.
* A grant is bound to exactly one execution and is revoked the moment that
  execution terminates, which is what makes a replayed token fail.
* `token_id` (a non-secret correlation handle) is the only token-related value
  that is ever logged or audited. The token itself appears in no log line, no
  audit context, no exception message, and no result payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import hashlib
import json
import logging
import os
import platform
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kortex.engines.python_exec.exceptions import (
    CapabilityNotPermittedError,
    ExecutionTokenError,
    IsolationUnavailableError,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.security.models import SecurityPrincipal, TokenPayload

logger = logging.getLogger("kortex.engine.python_exec.ipc")

_IS_WINDOWS = platform.system() == "Windows"

# A single bridge request is small by construction (a capability name and a
# JSON parameter object). The cap exists so a malfunctioning or hostile client
# inside the boundary cannot drive the KORTEX-side reader into unbounded
# allocation.
MAX_REQUEST_BYTES = 1024 * 1024


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionGrant:
    """The authority a single execution holds while it is running.

    Every field here is established by KORTEX *before* the execution starts.
    None of them is derived from anything the execution sends.
    """

    token_id: str
    tenant_id: str
    action_id: str
    version: int
    allowed_capabilities: frozenset[str]
    expires_at: float
    principal: SecurityPrincipal | None = None
    session_token: TokenPayload | None = None
    workflow_id: str | None = None
    execution_id: str | None = None
    correlation_id: str | None = None

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.expires_at


@dataclass
class _Issued:
    grant: ExecutionGrant
    token: str


class ExecutionTokenStore:
    """Issues, validates and revokes ephemeral execution tokens.

    Thread-safe: the bridge server reads it from its own accept thread while
    the Gateway issues and revokes from the event loop.
    """

    def __init__(self) -> None:
        self._grants: dict[str, ExecutionGrant] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        tenant_id: str,
        action_id: str,
        version: int,
        allowed_capabilities: frozenset[str],
        ttl_seconds: float,
        principal: SecurityPrincipal | None,
        session_token: TokenPayload | None,
        workflow_id: str | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
    ) -> _Issued:
        """Mint one token for one execution.

        `secrets.token_urlsafe(32)` is 256 bits of CSPRNG output -- not
        guessable, and not derived from any execution-visible value such as a
        request id or a timestamp, so observing many tokens reveals nothing
        about the next one.
        """
        token = secrets.token_urlsafe(32)
        grant = ExecutionGrant(
            token_id=uuid.uuid4().hex,
            tenant_id=tenant_id,
            action_id=action_id,
            version=version,
            allowed_capabilities=allowed_capabilities,
            expires_at=time.monotonic() + ttl_seconds,
            principal=principal,
            session_token=session_token,
            workflow_id=workflow_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
        )
        with self._lock:
            self._grants[_digest(token)] = grant
        return _Issued(grant=grant, token=token)

    def redeem(self, token: str) -> ExecutionGrant:
        """Resolve a presented token to its grant, or refuse.

        The comparison is over SHA-256 digests via `secrets.compare_digest`,
        and every refusal raises the *same* exception type with a message that
        does not distinguish "no such token" from "expired token" -- a caller
        inside the boundary learns only that it was refused.
        """
        presented = _digest(token)
        with self._lock:
            for stored, grant in self._grants.items():
                if secrets.compare_digest(stored, presented):
                    if grant.is_expired():
                        del self._grants[stored]
                        raise ExecutionTokenError("The execution token is not valid.")
                    return grant
        raise ExecutionTokenError("The execution token is not valid.")

    def revoke(self, token: str) -> None:
        """Invalidate a token. Idempotent.

        Called unconditionally when an execution terminates -- success,
        failure, or timeout -- so a token can never outlive the execution it
        was minted for.
        """
        presented = _digest(token)
        with self._lock:
            self._grants.pop(presented, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._grants)


class CapabilityBridge:
    """Resolves one bridge request into one governed capability dispatch."""

    def __init__(self, kernel: Kernel, store: ExecutionTokenStore) -> None:
        self._kernel = kernel
        self._store = store

    async def handle(self, raw_request: bytes) -> dict[str, Any]:
        """Validate, authorize, and dispatch. Never raises to the transport.

        Returns a response document in every case, because the transport's job
        is to answer the sandbox, not to decide policy. Refusal reasons are
        deliberately coarse: an action learns that it was refused, not which
        internal check refused it.
        """
        try:
            document = json.loads(raw_request.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"ok": False, "error": "Malformed bridge request."}
        if not isinstance(document, dict):
            return {"ok": False, "error": "Malformed bridge request."}

        token = document.get("token")
        capability_name = document.get("capability")
        parameters = document.get("parameters", {})
        if not isinstance(token, str) or not isinstance(capability_name, str) or not isinstance(parameters, dict):
            return {"ok": False, "error": "Malformed bridge request."}

        try:
            grant = self._store.redeem(token)
        except ExecutionTokenError:
            logger.warning("Rejected a Trusted-Python bridge request presenting an invalid execution token.")
            return {"ok": False, "error": "The execution token is not valid."}

        if capability_name not in grant.allowed_capabilities:
            logger.warning(
                "Trusted-Python execution %s requested capability %r outside its declared allowlist.",
                grant.token_id,
                capability_name,
            )
            return {
                "ok": False,
                "error": f"Capability {capability_name!r} is not permitted for this Python Action.",
            }

        try:
            result = await self._dispatch(grant, capability_name, parameters)
        except Exception as exc:
            logger.warning(
                "Trusted-Python capability call %r failed for execution %s: %s",
                capability_name,
                grant.token_id,
                type(exc).__name__,
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "result": result}

    async def _dispatch(self, grant: ExecutionGrant, capability_name: str, parameters: dict[str, Any]) -> Any:
        """Hand off to the single authoritative execution boundary.

        `session_token` is the grant's own -- the same already-verified token
        the execution's identity was established from, mirroring
        `WorkflowEngine.execute_external_operation`'s precedent. The dispatcher
        re-verifies it and derives the real `CapabilityExecutionContext`, so
        nothing the sandbox sent can influence whose authority this runs under.
        """
        from kortex.core.dispatch import CapabilityRequest

        if grant.session_token is None:
            raise CapabilityNotPermittedError(
                "This execution holds no session authority and cannot invoke KORTEX capabilities."
            )

        request = CapabilityRequest(
            capability_name=capability_name,
            session_token=grant.session_token,
            parameters=dict(parameters),
            correlation_id=grant.correlation_id or uuid.uuid4().hex,
            context={"resource_tenant_id": grant.tenant_id},
        )
        return await self._kernel.invoke_capability(request)


class CapabilityBridgeServer:
    """Transport for the capability bridge.

    Windows uses a named pipe whose DACL names only SYSTEM, the KORTEX account,
    and the execution's AppContainer SID. Linux uses a Unix domain socket
    inside the (0700, workspace-ACL'd) execution workspace. In both cases the
    endpoint is reachable by exactly the one sandbox identity it was created
    for, and by nothing else on the machine.

    The server runs its accept loop on a dedicated thread and marshals each
    request onto the KORTEX event loop with `run_coroutine_threadsafe`, because
    the dispatcher is async and the pipe/socket APIs used here are blocking.
    """

    def __init__(
        self,
        bridge: CapabilityBridge,
        loop: asyncio.AbstractEventLoop,
        *,
        container_sid: str | None,
        workspace: Path,
    ) -> None:
        self._bridge = bridge
        self._loop = loop
        self._container_sid = container_sid
        self._workspace = workspace
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unix_socket: socket.socket | None = None
        self.address: str = ""
        self.request_count = 0
        self._counter_lock = threading.Lock()

    def start(self) -> str:
        """Create the endpoint and begin accepting. Returns its address."""
        if _IS_WINDOWS:
            self.address = rf"\\.\pipe\kortex-pyexec-{uuid.uuid4().hex}"
        else:
            self.address = str(self._workspace / f"bridge-{uuid.uuid4().hex}.sock")
            # `AF_UNIX` is absent from the `socket` module on Windows, so it is
            # resolved dynamically: this branch is unreachable there, but a
            # type-check running on a Windows host still analyses it.
            af_unix = getattr(socket, "AF_UNIX")  # noqa: B009 - see comment above
            self._unix_socket = socket.socket(af_unix, socket.SOCK_STREAM)
            self._unix_socket.bind(self.address)
            os.chmod(self.address, 0o600)
            self._unix_socket.listen(8)
            self._unix_socket.settimeout(0.5)

        self._thread = threading.Thread(
            target=self._serve_windows if _IS_WINDOWS else self._serve_unix,
            name="kortex-python-bridge",
            daemon=True,
        )
        self._thread.start()
        return self.address

    def stop(self) -> None:
        """Stop accepting and release the endpoint. Never raises.

        Setting the stop flag is not sufficient on Windows: the accept loop is
        blocked inside `ConnectNamedPipe`, which is synchronous and never
        returns until a client connects. Without the self-connect below, every
        Trusted-Python execution would leak a thread and a pipe instance that
        survive until the whole KORTEX process exits.
        """
        self._stop.set()

        if _IS_WINDOWS and self.address:
            # One throwaway connection, purely to wake the blocked accept.
            # The loop re-checks `_stop` immediately after connecting and
            # returns without reading, so this never reaches the dispatcher.
            with contextlib.suppress(Exception), open(self.address, "r+b", buffering=0) as waker:
                waker.close()

        if self._unix_socket is not None:
            with contextlib.suppress(Exception):
                self._unix_socket.close()
            with contextlib.suppress(OSError):
                os.unlink(self.address)

        if self._thread is not None:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("The capability bridge accept thread did not stop within its grace period.")

    def _handle_bytes(self, raw: bytes) -> bytes:
        with self._counter_lock:
            self.request_count += 1
        future = asyncio.run_coroutine_threadsafe(self._bridge.handle(raw), self._loop)
        try:
            response = future.result(timeout=120)
        except Exception as exc:
            logger.warning("Capability bridge request failed: %s", type(exc).__name__)
            response = {"ok": False, "error": "The capability call could not be completed."}
        return json.dumps(response, default=str, separators=(",", ":")).encode("utf-8") + b"\n"

    # -- Unix transport ------------------------------------------------------

    def _serve_unix(self) -> None:
        assert self._unix_socket is not None
        while not self._stop.is_set():
            try:
                connection, _ = self._unix_socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with contextlib.suppress(Exception), connection:
                connection.settimeout(120)
                chunks: list[bytes] = []
                received = 0
                while received < MAX_REQUEST_BYTES:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    if chunks[-1].endswith(b"\n"):
                        break
                connection.sendall(self._handle_bytes(b"".join(chunks)))

    # -- Windows transport ---------------------------------------------------

    def _pipe_security_attributes(self) -> Any:
        """Build the pipe's DACL: SYSTEM, KORTEX, and this container SID only."""
        import ctypes.wintypes as wt

        from kortex.engines.python_exec import winapi
        from kortex.engines.python_exec.workspace import current_user_sid

        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{current_user_sid()})"
        if self._container_sid:
            sddl += f"(A;;GRGW;;;{self._container_sid})"

        descriptor = ctypes.c_void_p()
        size = wt.DWORD()
        if not winapi.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, winapi.SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)
        ):
            raise IsolationUnavailableError("Unable to build the capability bridge pipe ACL.")
        attributes = winapi.SECURITY_ATTRIBUTES()
        attributes.nLength = ctypes.sizeof(attributes)
        attributes.lpSecurityDescriptor = descriptor
        attributes.bInheritHandle = False
        return attributes

    def _serve_windows(self) -> None:
        from kortex.engines.python_exec import winapi

        attributes = self._pipe_security_attributes()
        try:
            self._accept_windows(attributes)
        finally:
            # `ConvertStringSecurityDescriptorToSecurityDescriptorW` allocates
            # the descriptor with `LocalAlloc`. It has to stay valid for every
            # pipe instance this loop creates, so it is released only once the
            # loop is finished -- otherwise each Trusted-Python execution would
            # leak one descriptor for the lifetime of the KORTEX process.
            with contextlib.suppress(Exception):
                winapi.kernel32.LocalFree(attributes.lpSecurityDescriptor)

    def _accept_windows(self, attributes: Any) -> None:
        from kortex.engines.python_exec import winapi

        while not self._stop.is_set():
            handle = winapi.kernel32.CreateNamedPipeW(
                self.address,
                winapi.PIPE_ACCESS_DUPLEX,
                winapi.PIPE_TYPE_BYTE
                | winapi.PIPE_READMODE_BYTE
                | winapi.PIPE_WAIT
                | winapi.PIPE_REJECT_REMOTE_CLIENTS,
                8,
                65536,
                65536,
                5000,
                ctypes.byref(attributes),
            )
            if not handle or handle == winapi.INVALID_HANDLE_VALUE:
                logger.error("Unable to create the capability bridge pipe: %s", winapi.last_error())
                return
            try:
                connected = winapi.kernel32.ConnectNamedPipe(handle, None)
                if not connected and winapi.last_error() != winapi.ERROR_PIPE_CONNECTED:
                    continue
                if self._stop.is_set():
                    return
                raw = self._read_pipe(handle)
                if raw:
                    self._write_pipe(handle, self._handle_bytes(raw))
            finally:
                with contextlib.suppress(Exception):
                    winapi.kernel32.FlushFileBuffers(handle)
                    winapi.kernel32.DisconnectNamedPipe(handle)
                winapi.close_handle(handle)

    def _read_pipe(self, handle: Any) -> bytes:
        import ctypes.wintypes as wt

        from kortex.engines.python_exec import winapi

        chunks: list[bytes] = []
        buffer = ctypes.create_string_buffer(65536)
        read = wt.DWORD()
        total = 0
        while total < MAX_REQUEST_BYTES:
            if not winapi.kernel32.ReadFile(handle, buffer, 65536, ctypes.byref(read), None):
                break
            if read.value == 0:
                break
            chunks.append(buffer.raw[: read.value])
            total += read.value
            if chunks[-1].endswith(b"\n"):
                break
        return b"".join(chunks)

    def _write_pipe(self, handle: Any, payload: bytes) -> None:
        import ctypes.wintypes as wt

        from kortex.engines.python_exec import winapi

        written = wt.DWORD()
        winapi.kernel32.WriteFile(handle, payload, len(payload), ctypes.byref(written), None)


__all__ = [
    "MAX_REQUEST_BYTES",
    "CapabilityBridge",
    "CapabilityBridgeServer",
    "ExecutionGrant",
    "ExecutionTokenStore",
]
