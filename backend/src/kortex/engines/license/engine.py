"""
KORTEX License Engine Implementation (Milestone M5.7).

Provides commercial license token cryptographic verification, lifecycle state
management, and in-memory entitlement evaluation adhering to Clean Architecture.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.license.config import (
    CANONICAL_COMMUNITY_FEATURES,
    CANONICAL_COMMUNITY_QUOTAS,
    CLOCK_SKEW_TOLERANCE_SECONDS,
    COMPILED_VENDOR_ROOT_KEYS,
)
from kortex.engines.license.crypto import LicenseCryptoEngine
from kortex.engines.license.exceptions import (
    LicenseExpiredError,
    LicenseNotYetValidError,
    SecurityConfigurationError,
    TenantMismatchError,
    UnsupportedScopeError,
)
from kortex.engines.license.interfaces import ILicenseProvider, ILicenseRepository
from kortex.engines.license.models import (
    EntitlementSnapshot,
    LicenseScopeEnum,
    LicenseStatusEnum,
    LicenseStatusResponse,
    LicenseTier,
    TokenVerifyResponse,
)
from kortex.engines.license.repository import TenantScopedLicenseRepository
from kortex.engines.license.tables import LicenseRecord
from kortex.engines.security.exceptions import AuthenticationError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.event.engine import EventEngine

logger = logging.getLogger("kortex.engines.license")


class LicenseEngine(BaseEngine, ILicenseProvider):
    """Core runtime engine for license token validation and entitlement evaluation."""

    def __init__(
        self,
        trusted_root_keys: dict[str, bytes] | None = None,
        crypto_engine: LicenseCryptoEngine | None = None,
        repository: ILicenseRepository | None = None,
        is_production: bool = False,
    ) -> None:
        super().__init__()
        if is_production and trusted_root_keys is not None and trusted_root_keys != COMPILED_VENDOR_ROOT_KEYS:
            raise SecurityConfigurationError("Custom root keys are strictly forbidden in production mode.")

        self._crypto_engine = crypto_engine or LicenseCryptoEngine(trusted_root_keys=trusted_root_keys)
        self._repository = repository
        self._event_engine: EventEngine | None = None

        # In-memory fast path caches
        self._cached_records: dict[str, LicenseRecord] = {}
        self._highest_observed_at: dict[str, datetime] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()

        self._registered_capabilities: list[str] = [
            "kortex.license.token.verify",
            "kortex.license.activation.apply",
            "kortex.license.activation.revoke",
            "kortex.license.status.get",
        ]

    @property
    def name(self) -> str:
        return "license"

    @property
    def dependencies(self) -> list[str]:
        return ["configuration", "registry", "storage", "security"]

    @property
    def crypto_engine(self) -> LicenseCryptoEngine:
        return self._crypto_engine

    @property
    def repository(self) -> ILicenseRepository | None:
        return self._repository

    # -- Lifecycle Implementation -------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine resources, load active licenses, and register capabilities."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX License Engine (M5.7)...")

        try:
            # 1. Resolve StorageEngine and IDataStore repository
            if self._repository is None:
                storage_engine = kernel.get_engine("storage")
                data_store = getattr(storage_engine, "data", None)
                if data_store is None:
                    raise RuntimeError("Storage Engine has no relational data store available.")
                self._repository = TenantScopedLicenseRepository(data_store)

            # 2. Resolve EventEngine (optional best-effort)
            try:
                self._event_engine = kernel.get_engine("event")  # type: ignore[assignment]
            except Exception:
                self._event_engine = None

            # 3. Load existing active licenses into in-memory cache (cold start)
            try:
                active_records = await self._repository.get_all_active_licenses()
                for rec in active_records:
                    self._cached_records[rec.tenant_id] = rec
                    self._highest_observed_at[rec.tenant_id] = rec.highest_observed_at
                self.logger.info("Loaded %d active license records into cache.", len(active_records))
            except Exception as exc:
                self.logger.warning(
                    "Could not load existing licenses from storage at startup (%s); defaulting to Community fallback.",
                    exc,
                )

            # 4. Register Capabilities
            kernel.register_capability(
                name="kortex.license.token.verify",
                description="Cryptographically verify a raw license token and inspect claims without activation.",
                provider=self.name,
                handler=self.verify_token,
                requires_authentication=True,
                requires_execution_context=False,
                required_permissions=["license:read"],
                security_classification="INTERNAL",
            )

            kernel.register_capability(
                name="kortex.license.activation.apply",
                description="Activate or renew a cryptographically signed license token for the caller's tenant.",
                provider=self.name,
                handler=self.apply_activation,
                requires_authentication=True,
                requires_execution_context=True,
                required_permissions=["license:manage"],
                security_classification="RESTRICTED",
            )

            kernel.register_capability(
                name="kortex.license.activation.revoke",
                description="Revoke the currently active license for the caller's tenant.",
                provider=self.name,
                handler=self.revoke_activation,
                requires_authentication=True,
                requires_execution_context=True,
                required_permissions=["license:manage"],
                security_classification="RESTRICTED",
            )

            kernel.register_capability(
                name="kortex.license.status.get",
                description="Retrieve current license status and active entitlements for the caller's tenant.",
                provider=self.name,
                handler=self.get_status,
                requires_authentication=True,
                requires_execution_context=True,
                required_permissions=["license:read"],
                security_classification="INTERNAL",
            )

            self._set_state(EngineState.READY)
            self.logger.info("License Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize License Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start engine services."""
        self.ensure_state(EngineState.READY, EngineState.STOPPED)
        self._set_state(EngineState.RUNNING)
        self.logger.info("License Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully stop engine services."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        self._cached_records.clear()
        self._highest_observed_at.clear()
        self._set_state(EngineState.STOPPED)
        self.logger.info("License Engine stopped.")

    async def health_check(self) -> dict[str, Any]:
        """Diagnostic health check (BaseEngine async contract)."""
        return self.health()

    def health(self) -> dict[str, Any]:
        """Synchronous operational health report (IEngineDiagnostics)."""
        return {
            "engine": self.name,
            "status": "healthy" if self._state in (EngineState.READY, EngineState.RUNNING) else "unhealthy",
            "state": self._state.value,
            "cached_active_licenses": len(self._cached_records),
            "clock_tamper_tenants": sum(
                1
                for t, h in self._highest_observed_at.items()
                if (h - datetime.now(UTC)).total_seconds() > CLOCK_SKEW_TOLERANCE_SECONDS
            ),
        }

    # -- Internal ILicenseProvider Contract ---------------------------------

    def get_entitlements(self, tenant_id: str) -> EntitlementSnapshot:
        """Return the immutable entitlement snapshot for a tenant."""
        now = datetime.now(UTC)

        # 1. Monotonic clock rollback check
        highest = self._highest_observed_at.get(tenant_id, now)
        if now >= highest:
            self._highest_observed_at[tenant_id] = now
        elif (highest - now).total_seconds() > CLOCK_SKEW_TOLERANCE_SECONDS:
            drift = (highest - now).total_seconds()
            logger.warning("Clock rollback detected for tenant '%s' (drift: %ss)", tenant_id, drift)
            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier.COMMUNITY,
                status=LicenseStatusEnum.ACTIVE if tenant_id in self._cached_records else LicenseStatusEnum.UNLICENSED,
                features=CANONICAL_COMMUNITY_FEATURES,
                quotas=dict(CANONICAL_COMMUNITY_QUOTAS),
                expires_at=None,
                is_degraded=True,
                clock_tamper_detected=True,
            )

        # 2. Check cached record
        rec = self._cached_records.get(tenant_id)
        if rec is None:
            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier.COMMUNITY,
                status=LicenseStatusEnum.UNLICENSED,
                features=CANONICAL_COMMUNITY_FEATURES,
                quotas=dict(CANONICAL_COMMUNITY_QUOTAS),
                expires_at=None,
                is_degraded=False,
                clock_tamper_detected=False,
            )

        # 3. Parse features and quotas from JSON
        try:
            features = frozenset(json.loads(rec.features_json))
            quotas = dict(json.loads(rec.quotas_json))
        except Exception:
            features = CANONICAL_COMMUNITY_FEATURES
            quotas = dict(CANONICAL_COMMUNITY_QUOTAS)

        # 4. Evaluate time boundaries
        not_before = rec.not_before
        if not_before.tzinfo is None:
            not_before = not_before.replace(tzinfo=UTC)

        if now < not_before:
            # Not yet valid
            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier.COMMUNITY,
                status=LicenseStatusEnum.UNLICENSED,
                features=CANONICAL_COMMUNITY_FEATURES,
                quotas=dict(CANONICAL_COMMUNITY_QUOTAS),
                expires_at=rec.expires_at,
                is_degraded=True,
                clock_tamper_detected=False,
            )

        if rec.expires_at is None:
            # Perpetual active license
            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier(rec.tier),
                status=LicenseStatusEnum.ACTIVE,
                features=features,
                quotas=quotas,
                expires_at=None,
                is_degraded=False,
                clock_tamper_detected=False,
            )

        expires_at = rec.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if now <= expires_at:
            # Active
            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier(rec.tier),
                status=LicenseStatusEnum.ACTIVE,
                features=features,
                quotas=quotas,
                expires_at=expires_at,
                is_degraded=False,
                clock_tamper_detected=False,
            )

        grace_seconds = rec.grace_period_days * 86400
        if (now - expires_at).total_seconds() <= grace_seconds:
            # In Grace Period
            if not rec.grace_event_emitted:
                self._schedule_task(self._emit_grace_event_safe(rec))

            return EntitlementSnapshot(
                tenant_id=tenant_id,
                tier=LicenseTier(rec.tier),
                status=LicenseStatusEnum.GRACE_PERIOD,
                features=features,
                quotas=quotas,
                expires_at=expires_at,
                is_degraded=True,
                clock_tamper_detected=False,
            )

        # Expired (grace exhausted)
        return EntitlementSnapshot(
            tenant_id=tenant_id,
            tier=LicenseTier.COMMUNITY,
            status=LicenseStatusEnum.EXPIRED,
            features=CANONICAL_COMMUNITY_FEATURES,
            quotas=dict(CANONICAL_COMMUNITY_QUOTAS),
            expires_at=expires_at,
            is_degraded=True,
            clock_tamper_detected=False,
        )

    def is_feature_enabled(self, tenant_id: str, feature_key: str) -> bool:
        """Fast-path boolean check: return True if feature is granted in effective entitlements."""
        return feature_key in self.get_entitlements(tenant_id).features

    def get_quota(self, tenant_id: str, quota_key: str) -> int | None:
        """Return integer quota limit, or None if unconstrained."""
        return self.get_entitlements(tenant_id).quotas.get(quota_key)

    def get_tier(self, tenant_id: str) -> LicenseTier:
        """Return effective commercial tier."""
        return self.get_entitlements(tenant_id).tier

    # -- Capability Handlers ------------------------------------------------

    def verify_token(self, token: str) -> TokenVerifyResponse:
        """Stateless capability handler for kortex.license.token.verify."""
        _header, claims, _kid, _sig = self._crypto_engine.decode_and_verify_token(token)
        return TokenVerifyResponse(is_valid=True, claims=claims)

    async def apply_activation(
        self,
        token: str,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> LicenseStatusResponse:
        """Stateful capability handler for kortex.license.activation.apply."""
        if execution_context is None:
            raise AuthenticationError("Authentication required: execution context is missing.")

        caller_tenant_id = execution_context.tenant_id

        # 1. Decode and cryptographically verify token
        _header, claims, kid, sig_hex = self._crypto_engine.decode_and_verify_token(token)

        # 2. Scope & Tenant confinement
        if claims.scope != LicenseScopeEnum.TENANT:
            raise UnsupportedScopeError(f"Scope '{claims.scope}' is not supported in M5.7.")

        if claims.subject_tenant_id != caller_tenant_id:
            raise TenantMismatchError(
                f"License token subject_tenant_id '{claims.subject_tenant_id}' does not match "
                f"caller tenant '{caller_tenant_id}'."
            )

        # 3. Check time validity at activation time
        now = datetime.now(UTC)
        if claims.not_before > now:
            raise LicenseNotYetValidError(f"License is not yet valid (not_before: {claims.not_before.isoformat()}).")

        if claims.expires_at is not None:
            grace_seconds = claims.grace_period_days * 86400
            if (now - claims.expires_at).total_seconds() > grace_seconds:
                raise LicenseExpiredError("Cannot activate an expired license whose grace period has elapsed.")

        # 4. Durable persistence
        if self._repository is None:
            raise RuntimeError("License repository is not initialized.")

        principal_id = execution_context.principal.principal_id if execution_context.principal else "system"
        record, is_reapplication = await self._repository.apply_activation(
            claims=claims,
            raw_token=token,
            kid=kid,
            signature_hex=sig_hex,
            activated_by=principal_id,
        )

        # 5. Update in-memory cache
        async with self._lock:
            self._cached_records[caller_tenant_id] = record
            self._highest_observed_at[caller_tenant_id] = now

        # 6. Publish domain event if newly activated
        if not is_reapplication and self._event_engine is not None:
            self._schedule_task(
                self._emit_event_safe(
                    topic="license.activated",
                    payload={
                        "tenant_id": caller_tenant_id,
                        "license_id": claims.license_id,
                        "tier": claims.tier.value,
                        "expires_at": claims.expires_at.isoformat() if claims.expires_at else None,
                    },
                )
            )

        return self._build_status_response(caller_tenant_id)

    async def revoke_activation(
        self,
        reason: str,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> LicenseStatusResponse:
        """Stateful capability handler for kortex.license.activation.revoke."""
        if execution_context is None:
            raise AuthenticationError("Authentication required: execution context is missing.")

        caller_tenant_id = execution_context.tenant_id
        if self._repository is None:
            raise RuntimeError("License repository is not initialized.")

        principal_id = execution_context.principal.principal_id if execution_context.principal else "system"
        revoked_record = await self._repository.revoke_license(
            tenant_id=caller_tenant_id,
            reason=reason,
            revoked_by=principal_id,
        )

        async with self._lock:
            self._cached_records.pop(caller_tenant_id, None)

        if revoked_record is not None and self._event_engine is not None:
            self._schedule_task(
                self._emit_event_safe(
                    topic="license.revoked",
                    payload={
                        "tenant_id": caller_tenant_id,
                        "license_id": revoked_record.license_id,
                        "reason": reason,
                    },
                )
            )

        return self._build_status_response(caller_tenant_id)

    def get_status(
        self,
        execution_context: CapabilityExecutionContext | None = None,
    ) -> LicenseStatusResponse:
        """Stateful read-only capability handler for kortex.license.status.get."""
        if execution_context is None:
            raise AuthenticationError("Authentication required: execution context is missing.")

        caller_tenant_id = execution_context.tenant_id
        return self._build_status_response(caller_tenant_id)

    # -- Private Helpers ----------------------------------------------------

    def _build_status_response(self, tenant_id: str) -> LicenseStatusResponse:
        """Build status response model from current effective entitlements."""
        snapshot = self.get_entitlements(tenant_id)
        rec = self._cached_records.get(tenant_id)

        grace_days_remaining = None
        if snapshot.status == LicenseStatusEnum.GRACE_PERIOD and rec is not None and rec.expires_at is not None:
            now = datetime.now(UTC)
            exp = rec.expires_at if rec.expires_at.tzinfo else rec.expires_at.replace(tzinfo=UTC)
            elapsed_days = (now - exp).total_seconds() / 86400
            grace_days_remaining = max(0, int(rec.grace_period_days - elapsed_days))

        return LicenseStatusResponse(
            tenant_id=tenant_id,
            tier=snapshot.tier.value,
            status=snapshot.status.value,
            expires_at=snapshot.expires_at.isoformat() if snapshot.expires_at else None,
            features=sorted(snapshot.features),
            quotas=dict(snapshot.quotas),
            grace_period_remaining_days=grace_days_remaining,
            is_degraded=snapshot.is_degraded,
            clock_tamper_detected=snapshot.clock_tamper_detected,
        )

    async def _emit_grace_event_safe(self, rec: LicenseRecord) -> None:
        """Safely emit license.grace_period_entered event once and flag database record."""
        if self._repository is not None:
            was_marked = await self._repository.mark_grace_event_emitted(rec.license_id)
            if was_marked:
                rec.grace_event_emitted = True
                if self._event_engine is not None:
                    await self._emit_event_safe(
                        topic="license.grace_period_entered",
                        payload={
                            "tenant_id": rec.tenant_id,
                            "license_id": rec.license_id,
                            "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
                            "grace_period_days": rec.grace_period_days,
                        },
                    )

    async def _emit_event_safe(self, topic: str, payload: dict[str, Any]) -> None:
        """Emit domain event to EventEngine with exception suppression."""
        if self._event_engine is not None:
            try:
                await self._event_engine.publish(
                    topic=topic,
                    payload=payload,
                    sender=self.name,
                )
            except Exception as exc:
                logger.warning("Failed to publish event '%s': %s", topic, exc)

    def _schedule_task(self, coro: Any) -> None:
        """Schedule a background task and track its reference to avoid premature garbage collection."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            if asyncio.iscoroutine(coro):
                coro.close()
