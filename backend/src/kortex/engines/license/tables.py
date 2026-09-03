"""
SQLAlchemy ORM schema mapping the kortex_licenses table (Milestone M5.7).
"""

from __future__ import annotations

import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel


class LicenseRecord(BaseModel):
    """Durable relational representation of a license in KORTEX."""

    __tablename__ = "kortex_licenses"

    license_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    # The active_tenant_id column carries a unique constraint. When status is ACTIVE or
    # GRACE_PERIOD, active_tenant_id = tenant_id. When terminal (EXPIRED, REVOKED, SUPERSEDED),
    # active_tenant_id is NULL. This guarantees at most ONE active/grace license per tenant.
    active_tenant_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="TENANT", nullable=False)
    tier: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    raw_token: Mapped[str] = mapped_column(Text, nullable=False)
    kid: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_hex: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_before: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_period_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    features_json: Mapped[str] = mapped_column(Text, nullable=False)
    quotas_json: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    highest_observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    grace_event_emitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("idx_licenses_tenant_status", "tenant_id", "status"),
        UniqueConstraint("active_tenant_id", name="uq_active_license_per_tenant"),
    )
