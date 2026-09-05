"""KORTEX Update Engine interfaces and typing protocols.

Phase 7 — Production Hardening — Update Engine.
"""

from __future__ import annotations

from typing import Any, Protocol

from kortex.engines.update.constants import UpdateJournalPhase
from kortex.engines.update.models import (
    UpdateApplyRequest,
    UpdateApplyResponse,
    UpdateCancelRequest,
    UpdateCancelResponse,
    UpdateCheckRequest,
    UpdateCheckResponse,
    UpdateGetRequest,
    UpdateGetResponse,
    UpdateJournalRecord,
    UpdateManifest,
    UpdateStageRequest,
    UpdateStageResponse,
)


class IUpdateEngine(Protocol):
    """Authoritative public contract for KORTEX Update Engine."""

    async def check(self, request: UpdateCheckRequest) -> UpdateCheckResponse:
        """Check for available update manifests and verify cryptographic signatures."""
        ...

    async def stage(self, request: UpdateStageRequest) -> UpdateStageResponse:
        """Download, verify, and unpack update archive into isolated staging workspace."""
        ...

    async def apply(self, request: UpdateApplyRequest) -> UpdateApplyResponse:
        """Execute checkpoint, quiescence, forward migration, file swap, and verification."""
        ...

    async def cancel(self, request: UpdateCancelRequest) -> UpdateCancelResponse:
        """Abort unapplied staged update, purge workspace, and return to IDLE."""
        ...

    async def get(self, request: UpdateGetRequest) -> UpdateGetResponse:
        """Retrieve active update state, journal details, and history."""
        ...


class IUpdateJournal(Protocol):
    """Durable write-ahead journal manager interface."""

    def record_phase(
        self,
        phase: UpdateJournalPhase,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomically record durable phase transition to disk."""
        ...

    def get_active_record(self) -> UpdateJournalRecord | None:
        """Return currently active uncommitted journal record, if any."""
        ...


class IUpdateVerifier(Protocol):
    """Post-update verification interface."""

    async def verify_post_update(
        self,
        manifest: UpdateManifest,
        expected_schema_revision: str | None = None,
    ) -> dict[str, Any]:
        """Execute deterministic verification gates on restored/swapped state."""
        ...
