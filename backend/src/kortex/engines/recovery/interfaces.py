"""KORTEX Recovery Engine interface protocols.

Phase 7 — Production Hardening — Recovery Engine.
Defines typing Protocols for the Recovery Engine facade, validator,
staging restorer, and durable journal manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from kortex.engines.recovery.models import (
    CreateRecoveryRequest,
    CreateRecoveryResponse,
    DeleteRecoveryRequest,
    DeleteRecoveryResponse,
    GetRecoveryRequest,
    GetRecoveryResponse,
    ListRecoveriesRequest,
    ListRecoveriesResponse,
    RecoveryJournalEntry,
    RecoveryJournalPhase,
    VerifyRecoveryRequest,
    VerifyRecoveryResponse,
)


@runtime_checkable
class IRecoveryJournal(Protocol):
    """Protocol for write-ahead durable journal manager."""

    @property
    def journal_path(self) -> Path: ...

    def load_journal(self) -> RecoveryJournalEntry | None: ...

    def record_phase(
        self,
        phase: RecoveryJournalPhase,
        operation: str | None = None,
        error_message: str | None = None,
        operator_notes: str | None = None,
    ) -> None: ...

    def update_journal(self, entry: RecoveryJournalEntry) -> None: ...

    def delete_journal(self) -> bool: ...


@runtime_checkable
class IRecoveryValidator(Protocol):
    """Protocol for multi-tier pre-restore and post-restore validation."""

    async def verify_backup(
        self,
        request: VerifyRecoveryRequest,
    ) -> VerifyRecoveryResponse: ...


@runtime_checkable
class IRecoveryEngine(Protocol):
    """Public protocol for the KORTEX Recovery Engine."""

    async def create_recovery(
        self,
        request: CreateRecoveryRequest,
    ) -> CreateRecoveryResponse: ...

    async def list_recoveries(
        self,
        request: ListRecoveriesRequest,
    ) -> ListRecoveriesResponse: ...

    async def get_recovery(
        self,
        request: GetRecoveryRequest,
    ) -> GetRecoveryResponse: ...

    async def verify_recovery(
        self,
        request: VerifyRecoveryRequest,
    ) -> VerifyRecoveryResponse: ...

    async def delete_recovery(
        self,
        request: DeleteRecoveryRequest,
    ) -> DeleteRecoveryResponse: ...
