"""KORTEX Security Engine Audit Enforcement Manager (Milestone M6).

Implements `IAuditManager` to record immutable `UniversalAuditEntry` records
to `IDataStore` (persisted in `security_audit_records`) and publish security events
to `EventEngine` on `kortex.event.security.*` topics.

All operations strictly enforce tenant isolation and fail-closed error handling.
Storage failures are normalized to `AuditError`. Event publishing errors are caught
and logged without causing audit rollback or application failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.events import SecurityAuditEvent, SecurityBaseEvent
from kortex.engines.security.exceptions import AuditError, SecurityEngineError
from kortex.engines.security.interfaces import IAuditManager, ICryptoProvider
from kortex.engines.security.models import AuditRecord, PrincipalType, UniversalAuditEntry
from kortex.engines.storage.interfaces import IDataStore

if TYPE_CHECKING:
    from kortex.engines.event.engine import EventEngine

logger = logging.getLogger("kortex.engines.security.audit")

_MAX_QUERY_LIMIT = 1000


class AuditManager(IAuditManager):
    """Audit Enforcement Manager for immutable audit trail recording and querying."""

    def __init__(
        self,
        data_store: IDataStore,
        event_engine: EventEngine | None = None,
        crypto_provider: ICryptoProvider | None = None,
    ) -> None:
        """Initialize AuditManager.

        Args:
            data_store: Required `IDataStore` persistence provider.
            event_engine: Optional `EventEngine` for publishing audit events.
            crypto_provider: Optional `ICryptoProvider` for SHA256 hashing.
        """
        self._data_store = data_store
        self._event_engine = event_engine
        self._crypto_provider = crypto_provider

    # -- State Hashing Helpers ------------------------------------------------

    def compute_state_hash(self, data: bytes | str | dict[str, Any] | None) -> str | None:
        """Compute a deterministic SHA-256 hex digest for state tracking.

        Returns None if data is None.
        """
        if data is None:
            return None

        if isinstance(data, bytes):
            payload_bytes = data
        elif isinstance(data, str):
            payload_bytes = data.encode("utf-8")
        elif isinstance(data, dict):
            payload_bytes = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
        else:
            payload_bytes = str(data).encode("utf-8")

        if self._crypto_provider is not None:
            return self._crypto_provider.hash_sha256(payload_bytes)
        return hashlib.sha256(payload_bytes).hexdigest()

    # -- Transaction Runner ---------------------------------------------------

    async def _run_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        """Run `action` via `IDataStore.execute_in_transaction`, normalizing any
        unexpected exception into `AuditError`.
        """
        try:
            return await self._data_store.execute_in_transaction(action)
        except SecurityEngineError:
            raise
        except Exception as exc:
            raise AuditError("Audit storage operation failed.") from exc

    # -- IAuditManager Implementation -----------------------------------------

    async def record_audit_entry(self, entry: UniversalAuditEntry) -> UniversalAuditEntry:
        """Persist an immutable audit entry to `IDataStore` and dispatch event to `EventEngine`.

        Raises:
            AuditError: If validation or underlying database storage fails.
        """
        if not entry.tenant_id or not entry.tenant_id.strip():
            raise AuditError("Audit entry must specify a non-empty tenant_id.")
        if not entry.action or not entry.action.strip():
            raise AuditError("Audit entry must specify a non-empty action.")
        if not entry.actor_id or not entry.actor_id.strip():
            raise AuditError("Audit entry must specify a non-empty actor_id.")

        async def _action(session: AsyncSession) -> None:
            record = AuditRecord(
                id=str(uuid.uuid4()),
                audit_id=entry.audit_id,
                timestamp_utc=entry.timestamp_utc,
                action=entry.action,
                actor_id=entry.actor_id,
                actor_type=entry.actor_type,
                tenant_id=entry.tenant_id,
                resource_id=entry.resource_id,
                previous_state_hash=entry.previous_state_hash,
                new_state_hash=entry.new_state_hash,
                client_ip=entry.client_ip,
                context=entry.context,
            )
            session.add(record)

        await self._run_in_transaction(_action)

        # Dispatch event to EventEngine if connected (non-blocking / error isolated)
        if self._event_engine is not None:
            try:
                event_payload = SecurityAuditEvent(
                    tenant_id=entry.tenant_id,
                    audit_id=entry.audit_id,
                    action=entry.action,
                    actor_id=entry.actor_id,
                    actor_type=entry.actor_type,
                    resource_id=entry.resource_id,
                    previous_state_hash=entry.previous_state_hash,
                    new_state_hash=entry.new_state_hash,
                    client_ip=entry.client_ip,
                    context=entry.context,
                )
                topic = "kortex.event.security.audit"
                await self._event_engine.publish(
                    topic=topic,
                    payload=event_payload.model_dump(mode="json"),
                    sender="kortex.engines.security.audit",
                )
            except Exception as exc:
                logger.warning("Failed to publish security audit event: %s", exc)

        return entry

    async def publish_security_event(self, event: SecurityBaseEvent) -> None:
        """Publish a typed security event (e.g. `SecurityAuthSuccessEvent`) to
        `EventEngine`, if connected.

        Best-effort, same error-isolation policy as the generic audit-event
        publish inside `record_audit_entry`: a subscriber or transport
        failure is logged and swallowed, never raised — publishing a
        typed signal is a supplementary notification, not the durable
        audit record itself (that is `record_audit_entry`'s job).
        """
        if self._event_engine is None:
            return
        try:
            await self._event_engine.publish(
                topic=event.event_type,
                payload=event.model_dump(mode="json"),
                sender="kortex.engines.security.audit",
            )
        except Exception as exc:
            logger.warning("Failed to publish security event '%s': %s", event.event_type, exc)

    async def record_event(
        self,
        action: str,
        actor_id: str,
        actor_type: str | PrincipalType,
        tenant_id: str,
        resource_id: str | None = None,
        previous_state_hash: str | None = None,
        new_state_hash: str | None = None,
        client_ip: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> UniversalAuditEntry:
        """Convenience method to construct and record a `UniversalAuditEntry`."""
        actor_type_str = actor_type.value if isinstance(actor_type, PrincipalType) else str(actor_type)
        entry = UniversalAuditEntry(
            action=action,
            actor_id=actor_id,
            actor_type=actor_type_str,
            tenant_id=tenant_id,
            resource_id=resource_id,
            previous_state_hash=previous_state_hash,
            new_state_hash=new_state_hash,
            client_ip=client_ip,
            context=context or {},
        )
        return await self.record_audit_entry(entry)

    async def get_audit_entries(
        self,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
        action: str | None = None,
        actor_id: str | None = None,
    ) -> list[UniversalAuditEntry]:
        """Retrieve audit entries scoped strictly to `tenant_id`.

        Args:
            tenant_id: Multi-tenant boundary identifier.
            limit: Maximum entries to return (capped at 1000).
            offset: Pagination offset.
            action: Optional filter by action/capability name.
            actor_id: Optional filter by actor identifier.

        Returns:
            List of matching `UniversalAuditEntry` models ordered by timestamp descending.
        """
        if not tenant_id or not tenant_id.strip():
            raise AuditError("tenant_id must be provided to query audit entries.")

        clamped_limit = max(1, min(limit, _MAX_QUERY_LIMIT))
        clamped_offset = max(0, offset)

        async def _action(session: AsyncSession) -> list[AuditRecord]:
            stmt = select(AuditRecord).where(AuditRecord.tenant_id == tenant_id)
            if action:
                stmt = stmt.where(AuditRecord.action == action)
            if actor_id:
                stmt = stmt.where(AuditRecord.actor_id == actor_id)

            stmt = stmt.order_by(desc(AuditRecord.timestamp_utc)).offset(clamped_offset).limit(clamped_limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

        records: list[AuditRecord] = await self._run_in_transaction(_action)

        return [
            UniversalAuditEntry(
                audit_id=r.audit_id,
                timestamp_utc=r.timestamp_utc,
                action=r.action,
                actor_id=r.actor_id,
                actor_type=r.actor_type,
                tenant_id=r.tenant_id,
                resource_id=r.resource_id,
                previous_state_hash=r.previous_state_hash,
                new_state_hash=r.new_state_hash,
                client_ip=r.client_ip,
                context=r.context or {},
            )
            for r in records
        ]

    async def get_audit_entry(self, audit_id: str, tenant_id: str) -> UniversalAuditEntry | None:
        """Retrieve a specific audit entry by `audit_id` and `tenant_id`.

        Returns None if not found or if `tenant_id` does not match (preventing cross-tenant leakage).
        """
        if not audit_id or not audit_id.strip() or not tenant_id or not tenant_id.strip():
            raise AuditError("audit_id and tenant_id must be non-empty strings.")

        async def _action(session: AsyncSession) -> AuditRecord | None:
            stmt = select(AuditRecord).where(
                AuditRecord.audit_id == audit_id,
                AuditRecord.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            return result.scalars().first()

        record: AuditRecord | None = await self._run_in_transaction(_action)
        if record is None:
            return None

        return UniversalAuditEntry(
            audit_id=record.audit_id,
            timestamp_utc=record.timestamp_utc,
            action=record.action,
            actor_id=record.actor_id,
            actor_type=record.actor_type,
            tenant_id=record.tenant_id,
            resource_id=record.resource_id,
            previous_state_hash=record.previous_state_hash,
            new_state_hash=record.new_state_hash,
            client_ip=record.client_ip,
            context=record.context or {},
        )
