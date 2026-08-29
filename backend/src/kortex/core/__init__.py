"""KORTEX Core — Microkernel runtime and system foundations."""

from kortex.core.dispatch import CapabilityDispatcher, CapabilityRequest
from kortex.core.exceptions import (
    ConcurrentExecutionError,
    DispatchError,
    IdempotencyError,
    KortexError,
)
from kortex.core.idempotency import (
    ClaimResult,
    IdempotencyRecordModel,
    IdempotencyState,
    IdempotencyStore,
    sanitize_for_persistence,
)
from kortex.core.outbox import (
    EventOutboxModel,
    EventOutboxStatus,
    OutboxStore,
)

__all__ = [
    "CapabilityDispatcher",
    "CapabilityRequest",
    "ClaimResult",
    "ConcurrentExecutionError",
    "DispatchError",
    "EventOutboxModel",
    "EventOutboxStatus",
    "IdempotencyError",
    "IdempotencyRecordModel",
    "IdempotencyState",
    "IdempotencyStore",
    "KortexError",
    "OutboxStore",
    "sanitize_for_persistence",
]
