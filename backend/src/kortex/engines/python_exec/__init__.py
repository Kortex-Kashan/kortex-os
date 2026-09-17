"""KORTEX Python Execution Engine.

Versioned, immutable, tenant-owned Python Actions executed inside a governed
platform boundary, reached exclusively through the existing
`Kernel.invoke_capability` -> `CapabilityDispatcher` -> `SecurityEngine` path.

Only the engine and its domain models are re-exported here. The platform
boundary modules are deliberately *not* imported at package scope:
`windows_boundary` loads `WinDLL` at module scope and is unimportable off
Windows, and `linux_boundary` resolves the nsjail binary in its constructor.
The Gateway imports whichever one applies lazily, at execution time.
"""

from __future__ import annotations

from kortex.engines.python_exec.actions import PythonActionManager
from kortex.engines.python_exec.engine import PythonExecutionEngine
from kortex.engines.python_exec.exceptions import (
    CapabilityNotPermittedError,
    ExecutionTokenError,
    IsolationUnavailableError,
    PythonActionImmutabilityError,
    PythonActionNotFoundError,
    PythonActionValidationError,
    PythonExecutionError,
    UnsupportedTrustLevelError,
)
from kortex.engines.python_exec.gateway import PythonExecutionGateway
from kortex.engines.python_exec.models import (
    PythonAction,
    PythonActionVersion,
    PythonBoundaryKind,
    PythonExecutionLimits,
    PythonExecutionRequest,
    PythonExecutionResult,
    PythonExecutionStatus,
    PythonNetworkPolicy,
    PythonTrustLevel,
)

__all__ = [
    "CapabilityNotPermittedError",
    "ExecutionTokenError",
    "IsolationUnavailableError",
    "PythonAction",
    "PythonActionImmutabilityError",
    "PythonActionManager",
    "PythonActionNotFoundError",
    "PythonActionValidationError",
    "PythonActionVersion",
    "PythonBoundaryKind",
    "PythonExecutionEngine",
    "PythonExecutionError",
    "PythonExecutionGateway",
    "PythonExecutionLimits",
    "PythonExecutionRequest",
    "PythonExecutionResult",
    "PythonExecutionStatus",
    "PythonNetworkPolicy",
    "PythonTrustLevel",
    "UnsupportedTrustLevelError",
]
