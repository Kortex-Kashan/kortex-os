"""
KORTEX License Engine Interfaces and Protocols (Milestone M5.7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from kortex.engines.license.models import EntitlementSnapshot, LicenseTier, LicenseTokenClaims
from kortex.engines.license.tables import LicenseRecord


class ILicenseProvider(Protocol):
    """Internal in-process interface for querying license entitlements."""

    def get_entitlements(self, tenant_id: str) -> EntitlementSnapshot:
        """Return the immutable entitlement snapshot for a tenant."""
        ...

    def is_feature_enabled(self, tenant_id: str, feature_key: str) -> bool:
        """Fast-path boolean check: return True if feature is granted and active."""
        ...

    def get_quota(self, tenant_id: str, quota_key: str) -> int | None:
        """Return integer quota limit, or None if unconstrained."""
        ...

    def get_tier(self, tenant_id: str) -> LicenseTier:
        """Return effective commercial tier (COMMUNITY, PROFESSIONAL, ENTERPRISE)."""
        ...


class ILicenseRepository(Protocol):
    """Data access protocol for managing license persistence."""

    async def get_active_license(self, tenant_id: str) -> LicenseRecord | None:
        """Retrieve the current active/grace license for a tenant, if any."""
        ...

    async def get_all_active_licenses(self) -> list[LicenseRecord]:
        """Retrieve all currently active/grace licenses across all tenants."""
        ...

    async def apply_activation(
        self,
        claims: LicenseTokenClaims,
        raw_token: str,
        kid: str,
        signature_hex: str,
        activated_by: str,
    ) -> tuple[LicenseRecord, bool]:
        """Atomically activate or renew a license token for a tenant.

        Returns:
            tuple of (record, is_idempotent_reapplication)
        """
        ...

    async def revoke_license(
        self,
        tenant_id: str,
        reason: str,
        revoked_by: str,
    ) -> LicenseRecord | None:
        """Atomically revoke the active license for a tenant."""
        ...

    async def mark_grace_event_emitted(self, license_id: str) -> bool:
        """Atomically flag grace_event_emitted to True if currently False."""
        ...

    async def update_highest_observed_at(self, tenant_id: str, timestamp: datetime) -> None:
        """Update the monotonic watermark for a tenant's active license."""
        ...
