"""The harness that runs *inside* the governed execution boundary.

This file is copied verbatim into each ephemeral workspace and executed by the
provisioned interpreter. It therefore has three hard constraints:

* It must import nothing from `kortex` -- inside the boundary there is no
  KORTEX package, no database, no SecretStore, and no Kernel. The only channel
  out is the governed capability bridge, over a socket, authenticated by a
  single-use execution token.
* It must depend only on the standard library present in the provisioned
  runtime image.
* It must never let the action's own output corrupt the result channel.

The stdio contract (frozen architecture):

    stdin  -> structured JSON input
    stdout -> structured JSON result
    stderr -> diagnostics

`stdout` is reserved exclusively for the single JSON result document. The
action's own `print()` is redirected to `stderr` for the duration of the call,
so an action that prints cannot forge, prefix, or corrupt the result KORTEX
parses. This is what lets the Gateway treat a stdout parse failure as a real
protocol failure rather than as "the action printed something".
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import socket
import sys
import time
import traceback
from types import ModuleType
from typing import Any

ENV_BRIDGE_ADDRESS = "KORTEX_BRIDGE_ADDRESS"
ENV_EXECUTION_TOKEN = "KORTEX_EXECUTION_TOKEN"  # noqa: S105 - a variable NAME, not a secret

# Bounds a single bridge response so a compromised or malfunctioning bridge
# cannot drive the sandboxed process into unbounded memory growth.
_MAX_BRIDGE_RESPONSE_BYTES = 8 * 1024 * 1024

# Bounded retry for the Windows named-pipe connect race (see `_connect`).
# Worst case ~0.55s before giving up, well inside any execution timeout.
_CONNECT_ATTEMPTS = 10
_CONNECT_BACKOFF_SECONDS = 0.01


class CapabilityError(RuntimeError):
    """Raised inside the sandbox when a governed capability call is refused.

    Carries only the reason string the bridge chose to return. The bridge
    never returns a token, a principal, or an internal identifier, so this
    exception cannot become an exfiltration channel by way of an action
    catching it and returning its text as output.
    """


class KortexBridge:
    """The only way sandboxed Trusted Python can reach KORTEX.

    Every call crosses a local socket to the KORTEX-side bridge, which derives
    the authoritative tenant/workflow/execution identity from the presented
    execution token. Nothing this class sends can influence that derivation:
    the capability name and its parameters are the entire payload, and the
    token is read from the environment, never from the action.
    """

    def __init__(self, address: str, token: str) -> None:
        self._address = address
        self._token = token
        self.call_count = 0

    def _connect(self) -> Any:
        """Open the bridge endpoint, retrying briefly while it is unavailable.

        The KORTEX-side accept loop serves one connection per pipe instance and
        then creates the next one. Between those two steps there is a short
        window in which no instance exists, and a connection attempt landing in
        it fails with `FileNotFoundError`. Retrying closes that race; without
        it, a Trusted Action making several capability calls in quick
        succession fails intermittently, for a reason that has nothing to do
        with its own logic.

        The retry budget is deliberately small: a genuinely absent endpoint --
        an untrusted execution, or one whose bridge has already been torn down
        -- must still fail quickly rather than stalling until the execution's
        timeout kills it.
        """
        if sys.platform != "win32":
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(self._address)
            return connection

        last_error: OSError | None = None
        for attempt in range(_CONNECT_ATTEMPTS):
            try:
                # A Windows named pipe is opened as a file, not a socket. The
                # KORTEX side ACLs it for this execution's AppContainer SID only.
                return open(self._address, "r+b", buffering=0)
            except OSError as exc:
                last_error = exc
                time.sleep(_CONNECT_BACKOFF_SECONDS * (attempt + 1))
        raise CapabilityError(f"The KORTEX capability bridge is unavailable: {last_error}")

    def call_capability(self, capability_name: str, parameters: dict[str, Any] | None = None) -> Any:
        """Invoke one explicitly permitted KORTEX capability.

        The action supplies a capability name and parameters. It cannot supply
        a tenant, a principal, a workflow, an execution, or a session token --
        those are not parameters of this method, so there is no value an action
        could pass that would change whose authority the call runs under.
        """
        request = json.dumps(
            {"token": self._token, "capability": capability_name, "parameters": parameters or {}},
            separators=(",", ":"),
        ).encode("utf-8")

        handle = self._connect()
        try:
            if sys.platform == "win32":
                handle.write(request + b"\n")
                handle.flush()
                raw = handle.read(_MAX_BRIDGE_RESPONSE_BYTES)
            else:
                handle.sendall(request + b"\n")
                chunks: list[bytes] = []
                received = 0
                while received < _MAX_BRIDGE_RESPONSE_BYTES:
                    chunk = handle.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                raw = b"".join(chunks)
        finally:
            with contextlib.suppress(Exception):
                handle.close()

        self.call_count += 1
        try:
            response = json.loads(raw.decode("utf-8").strip() or "{}")
        except ValueError as exc:
            raise CapabilityError(f"Malformed response from the KORTEX capability bridge: {exc}") from None

        if not response.get("ok", False):
            raise CapabilityError(str(response.get("error", "Capability call refused.")))
        return response.get("result")


def _load_action_module(path: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location("kortex_action", path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Unable to load the action module at {path}.")
    module = importlib.util.module_from_spec(specification)
    sys.modules["kortex_action"] = module
    specification.loader.exec_module(module)
    return module


def _emit(document: dict[str, Any]) -> None:
    """Write the single result document to the real stdout.

    `sys.__stdout__` rather than `sys.stdout`: the action's own `print` is
    redirected to stderr for the duration of the call, so the result must be
    written to the *original* stream. It is typed `Optional` in typeshed
    (it is None under pythonw.exe), and the boundary always provides a real
    stdout pipe, so falling back to `sys.stdout` keeps this total.
    """
    stream = sys.__stdout__ if sys.__stdout__ is not None else sys.stdout
    stream.write(json.dumps(document, default=str, separators=(",", ":")))
    stream.flush()


def main() -> int:
    raw_request = sys.stdin.read()
    try:
        request = json.loads(raw_request or "{}")
    except ValueError as exc:
        _emit({"status": "error", "error": f"Malformed execution request: {exc}", "error_type": "ProtocolError"})
        return 2

    action_path = str(request.get("action_path", ""))
    entrypoint_name = str(request.get("entrypoint", "main"))
    payload = request.get("input", {})

    address = os.environ.get(ENV_BRIDGE_ADDRESS)
    token = os.environ.get(ENV_EXECUTION_TOKEN)
    bridge = KortexBridge(address, token) if address and token else None

    # The token must not remain readable in the action's own environment: an
    # action that dumps `os.environ` into its result, or into a log, would
    # otherwise exfiltrate a live credential. It is captured above and removed
    # here, before any action code runs.
    os.environ.pop(ENV_EXECUTION_TOKEN, None)

    try:
        module = _load_action_module(action_path)
    except BaseException as exc:
        _emit(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(limit=20),
            }
        )
        return 1

    entrypoint = getattr(module, entrypoint_name, None)
    if entrypoint is None or not callable(entrypoint):
        _emit(
            {
                "status": "error",
                "error": f"Action defines no callable entrypoint named {entrypoint_name!r}.",
                "error_type": "EntrypointError",
            }
        )
        return 1

    # Anything the action prints is diagnostics, not result. Redirecting for
    # the duration of the call (rather than asking actions not to print) means
    # the result channel is structurally protected, not protected by convention.
    original_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = entrypoint(payload, bridge) if _accepts_bridge(entrypoint) else entrypoint(payload)
        document: dict[str, Any] = {"status": "ok", "output": result}
        if bridge is not None:
            document["capability_calls"] = bridge.call_count
        _emit(document)
        return 0
    except BaseException as exc:
        document = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(limit=20),
        }
        if bridge is not None:
            document["capability_calls"] = bridge.call_count
        _emit(document)
        return 1
    finally:
        sys.stdout = original_stdout


def _accepts_bridge(entrypoint: Any) -> bool:
    """Whether the action's entrypoint wants the capability bridge.

    Inspected rather than mandated so an action that needs no KORTEX access
    can be written as a plain `def main(payload)`. Inspection failing (a
    builtin, a C callable, an exotic wrapper) falls back to *not* passing the
    bridge, which is the fail-closed direction: an action never receives a
    capability channel it did not declare a parameter for.
    """
    import inspect

    try:
        return len(inspect.signature(entrypoint).parameters) >= 2
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    sys.exit(main())
