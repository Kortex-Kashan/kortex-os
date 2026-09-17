"""Exception hierarchy for the KORTEX Python Execution Gateway.

Every exception here is raised *inside* the governed execution boundary and
surfaces to the caller through the ordinary `CapabilityDispatcher` failure
path -- none of them bypass audit, and none of them are caught and downgraded
into a successful-looking result.
"""

from __future__ import annotations


class PythonExecutionError(Exception):
    """Base exception for every Python Execution Gateway failure."""

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class PythonActionNotFoundError(PythonExecutionError):
    """Raised when an action_id/version is not resolvable for the caller's tenant.

    Deliberately indistinguishable from "exists but belongs to another tenant"
    (enumeration resistance, matching the Connector/MCP M6.3-1 precedent).
    """


class PythonActionValidationError(PythonExecutionError):
    """Raised when an action definition or execution request fails validation."""


class PythonActionImmutabilityError(PythonExecutionError):
    """Raised on any attempt to mutate an already-published action version."""


class IsolationUnavailableError(PythonExecutionError):
    """Raised when the required isolation boundary cannot be established.

    The Gateway fails closed on this: no Python is ever executed after this
    is raised. It is the single exception type that proves "fail closed if
    required isolation cannot be established" is a code path, not a comment.
    """


class UnsupportedTrustLevelError(PythonExecutionError):
    """Raised when a trust level is not supported on the current platform.

    Windows Untrusted Python is rejected here, *before* any workspace is
    created or any process is spawned (MVP non-goal: Windows Untrusted
    Python is not supported).
    """


class PythonExecutionTimeoutError(PythonExecutionError):
    """Raised when the execution exceeded its deterministic timeout budget.

    Raised only after the complete process tree has been terminated.
    """


class PythonCapabilityBridgeError(PythonExecutionError):
    """Base exception for governed Trusted-Python capability IPC failures."""


class ExecutionTokenError(PythonCapabilityBridgeError):
    """Raised when an execution token is absent, malformed, replayed, expired,
    or bound to a different tenant/workflow/execution than the one presenting it."""


class CapabilityNotPermittedError(PythonCapabilityBridgeError):
    """Raised when Trusted Python requests a capability outside its action's
    explicitly declared allowlist. Raised before any dispatch occurs."""


__all__ = [
    "CapabilityNotPermittedError",
    "ExecutionTokenError",
    "IsolationUnavailableError",
    "PythonActionImmutabilityError",
    "PythonActionNotFoundError",
    "PythonActionValidationError",
    "PythonCapabilityBridgeError",
    "PythonExecutionError",
    "PythonExecutionTimeoutError",
    "UnsupportedTrustLevelError",
]
