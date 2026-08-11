"""KORTEX Connector Engine — External system integration management."""

from __future__ import annotations

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import (
    ConnectorConnectionError,
    ConnectorDriverError,
    ConnectorEngineError,
    ConnectorOperationError,
    ConnectorProfileNotFoundError,
    ConnectorSecurityError,
    ConnectorValidationError,
    DriverExecutionError,
    DriverLoadError,
    DriverNotFoundError,
    RateLimitExceededError,
)
from kortex.engines.connector.interfaces import (
    IBaseConnectorDriver,
    IConnectorDriverLoader,
    IConnectorDriverRegistry,
    IConnectorEngine,
    IConnectorPipeline,
    IConnectorProfileManager,
    IEngineDiagnostics,
    IRateLimiter,
)
from kortex.engines.connector.loader import ConnectorDriverLoader
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorPipelineDefinition,
    ConnectorProfile,
    ConnectorStatus,
    DriverMetadata,
    PipelineStage,
)
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.connector.rate_limiter import (
    TokenBucketRateLimiter,
    calculate_backoff_delay,
    execute_with_retry,
)
from kortex.engines.connector.registry import ConnectorDriverRegistry

__all__ = [
    "ActionRequest",
    "ActionResult",
    "BaseConnectorDriver",
    "ConnectorActionType",
    "ConnectorCapability",
    "ConnectorConnectionError",
    "ConnectorDriverError",
    "ConnectorDriverLoader",
    "ConnectorDriverRegistry",
    "ConnectorEngineError",
    "ConnectorOperationError",
    "ConnectorPipelineDefinition",
    "ConnectorProfile",
    "ConnectorProfileManager",
    "ConnectorProfileNotFoundError",
    "ConnectorSecurityError",
    "ConnectorStatus",
    "ConnectorValidationError",
    "DriverExecutionError",
    "DriverLoadError",
    "DriverMetadata",
    "DriverNotFoundError",
    "IBaseConnectorDriver",
    "IConnectorDriverLoader",
    "IConnectorDriverRegistry",
    "IConnectorEngine",
    "IConnectorPipeline",
    "IConnectorProfileManager",
    "IEngineDiagnostics",
    "IRateLimiter",
    "PipelineStage",
    "RateLimitExceededError",
    "TokenBucketRateLimiter",
    "calculate_backoff_delay",
    "execute_with_retry",
]
