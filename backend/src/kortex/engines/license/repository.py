"""
KORTEX License Engine Repository Implementation (Milestone M5.7).

Provides atomic relational persistence and concurrency control over IDataStore.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.license.exceptions import (
    ConcurrentActivationError,
    LicenseConflictError,
    StorageOperationError,
    TerminalLicenseError,
)
from kortex.engines.license.interfaces import ILicenseRepository
from kortex.engines.license.models import LicenseTokenClaims
from kortex.engines.license.tables import LicenseRecord
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.license.repository")


class TenantScopedLicenseRepository(ILicenseRepository):
    """Repository managing durable license lifecycle via IDataStore."""

    def __init__(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    async def get_active_license(self, tenant_id: str) -> LicenseRecord | None:
        """Retrieve the currently active or in-grace license for a tenant, if any."""

        async def _query(session: AsyncSession) -> LicenseRecord | None:
            stmt = select(LicenseRecord).where(LicenseRecord.active_tenant_id == tenant_id)
            result = await session.execute(stmt)
            return result.scalars().first()

        try:
            return await self._data_store.execute_in_transaction(_query)
        except Exception as exc:
            logger.error("Failed to query active license for tenant '%s': %s", tenant_id, exc)
            raise StorageOperationError(f"Database query failed for tenant {tenant_id}: {exc}") from exc

    async def get_all_active_licenses(self) -> list[LicenseRecord]:
        """Retrieve all active or in-grace licenses across all tenants (used during boot)."""

        async def _query(session: AsyncSession) -> list[LicenseRecord]:
            stmt = select(LicenseRecord).where(LicenseRecord.active_tenant_id.isnot(None))
            result = await session.execute(stmt)
            return list(result.scalars().all())

        try:
            return await self._data_store.execute_in_transaction(_query)
        except Exception as exc:
            logger.error("Failed to query all active licenses during startup: %s", exc)
            raise StorageOperationError(f"Database startup query failed: {exc}") from exc

    async def apply_activation(
        self,
        claims: LicenseTokenClaims,
        raw_token: str,
        kid: str,
        signature_hex: str,
        activated_by: str,
    ) -> tuple[LicenseRecord, bool]:
        """Atomically activate or renew a license token for a tenant.

        Guarantees at most ONE non-terminal license per tenant.
        Returns:
            tuple of (record, is_idempotent_reapplication)
        """

        async def _tx(session: AsyncSession) -> tuple[LicenseRecord, bool]:
            now = datetime.now(UTC)

            # 1. Check if license_id already exists
            stmt_id = select(LicenseRecord).where(LicenseRecord.license_id == claims.license_id)
            res_id = await session.execute(stmt_id)
            existing_by_id = res_id.scalars().first()

            if existing_by_id is not None:
                # Same license_id submitted
                if existing_by_id.raw_token == raw_token and existing_by_id.tenant_id == claims.subject_tenant_id:
                    if existing_by_id.status in ("ACTIVE", "GRACE_PERIOD"):
                        return existing_by_id, True
                    raise TerminalLicenseError(
                        f"License '{claims.license_id}' is in terminal state '{existing_by_id.status}' "
                        "and cannot be reactivated."
                    )
                raise LicenseConflictError(
                    f"License ID '{claims.license_id}' already exists with divergent claims or token bytes."
                )

            # 2. Check for current active license to supersede
            stmt_active = select(LicenseRecord).where(LicenseRecord.active_tenant_id == claims.subject_tenant_id)
            res_active = await session.execute(stmt_active)
            current_active = res_active.scalars().first()

            if current_active is not None:
                # Atomically supersede the existing license
                current_active.status = "SUPERSEDED"
                current_active.active_tenant_id = None
                current_active.updated_at = now

            # 3. Create and persist new license record
            new_record = LicenseRecord(
                id=str(uuid.uuid4()),
                license_id=claims.license_id,
                tenant_id=claims.subject_tenant_id,
                active_tenant_id=claims.subject_tenant_id,
                scope=claims.scope.value,
                tier=claims.tier.value,
                status="ACTIVE",
                raw_token=raw_token,
                kid=kid,
                signature_hex=signature_hex,
                issued_at=claims.issued_at,
                not_before=claims.not_before,
                expires_at=claims.expires_at,
                grace_period_days=claims.grace_period_days,
                features_json=json.dumps(claims.features),
                quotas_json=json.dumps(claims.quotas),
                activated_at=now,
                activated_by=activated_by,
                revoked_at=None,
                revocation_reason=None,
                highest_observed_at=now,
                grace_event_emitted=False,
            )
            session.add(new_record)
            await session.flush()
            return new_record, False

        try:
            return await self._data_store.execute_in_transaction(_tx)
        except IntegrityError as exc:
            logger.warning("Concurrent activation collision for tenant '%s': %s", claims.subject_tenant_id, exc)
            raise ConcurrentActivationError(
                f"Concurrent activation conflict for tenant '{claims.subject_tenant_id}'."
            ) from exc
        except (LicenseConflictError, TerminalLicenseError):
            raise
        except Exception as exc:
            logger.error("Failed to execute activation transaction: %s", exc)
            raise StorageOperationError(f"Database activation failed: {exc}") from exc

    async def revoke_license(
        self,
        tenant_id: str,
        reason: str,
        revoked_by: str,
    ) -> LicenseRecord | None:
        """Atomically revoke the active license for a tenant."""

        async def _tx(session: AsyncSession) -> LicenseRecord | None:
            now = datetime.now(UTC)
            stmt = select(LicenseRecord).where(LicenseRecord.active_tenant_id == tenant_id)
            res = await session.execute(stmt)
            active_rec = res.scalars().first()

            if active_rec is None:
                return None

            active_rec.status = "REVOKED"
            active_rec.active_tenant_id = None
            active_rec.revoked_at = now
            active_rec.revocation_reason = f"[{revoked_by}] {reason}"
            active_rec.updated_at = now
            await session.flush()
            return active_rec

        try:
            return await self._data_store.execute_in_transaction(_tx)
        except Exception as exc:
            logger.error("Failed to execute revocation for tenant '%s': %s", tenant_id, exc)
            raise StorageOperationError(f"Database revocation failed: {exc}") from exc

    async def mark_grace_event_emitted(self, license_id: str) -> bool:
        """Atomically flag grace_event_emitted to True if not already emitted."""

        async def _tx(session: AsyncSession) -> bool:
            stmt = select(LicenseRecord).where(LicenseRecord.license_id == license_id)
            res = await session.execute(stmt)
            rec = res.scalars().first()
            if rec is not None and not rec.grace_event_emitted:
                rec.grace_event_emitted = True
                rec.updated_at = datetime.now(UTC)
                await session.flush()
                return True
            return False

        try:
            return await self._data_store.execute_in_transaction(_tx)
        except Exception as exc:
            logger.warning("Failed to mark grace event emitted for license '%s': %s", license_id, exc)
            return False

    async def update_highest_observed_at(self, tenant_id: str, timestamp: datetime) -> None:
        """Persist updated watermark timestamp for active license."""

        async def _tx(session: AsyncSession) -> None:
            stmt = select(LicenseRecord).where(LicenseRecord.active_tenant_id == tenant_id)
            res = await session.execute(stmt)
            rec = res.scalars().first()
            if rec is not None:
                current_ts = (
                    rec.highest_observed_at.replace(tzinfo=UTC)
                    if rec.highest_observed_at.tzinfo is None
                    else rec.highest_observed_at
                )
                target_ts = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
                if target_ts > current_ts:
                    rec.highest_observed_at = target_ts
                    await session.flush()

        try:
            await self._data_store.execute_in_transaction(_tx)
        except Exception as exc:
            logger.debug("Non-fatal watermark update skipped: %s", exc)
