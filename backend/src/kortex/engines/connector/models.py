"""Pydantic v2 data models and enums for the KORTEX OS Connector Engine.

This module contains domain data models, enums, metadata wrappers, and configuration
schemas defined in the Connector Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectorActionType(str, Enum):
    """Supported high-level connector action types."""

    SEND = "SEND"
    RECEIVE = "RECEIVE"
    FETCH = "FETCH"
    PUSH = "PUSH"
    VERIFY = "VERIFY"


class ConnectorCapability(str, Enum):
    """Fine-grained capabilities advertised by connector drivers."""

    SEND = "SEND"
    RECEIVE = "RECEIVE"
    FETCH = "FETCH"
    PUSH = "PUSH"
    VERIFY = "VERIFY"
    TEST_CONNECTION = "TEST_CONNECTION"
    AUTHENTICATE = "AUTHENTICATE"
    WEBHOOK = "WEBHOOK"
    STREAMING = "STREAMING"


class ConnectorStatus(str, Enum):
    """Operational health status for connector profiles and drivers."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    DISCONNECTED = "DISCONNECTED"


class DriverMetadata(BaseModel):
    """Immutable metadata for a Connector Driver plugin."""

    model_config = ConfigDict(frozen=True)

    driver_id: str
    display_name: str
    vendor: str
    author: str
    version: str
    description: str
    supported_actions: list[ConnectorActionType] = Field(default_factory=list)
    supported_capabilities: list[ConnectorCapability] = Field(default_factory=list)
    is_sandboxed: bool = True
    homepage: str | None = None
    license: str = "MIT"


class ConnectorProfile(BaseModel):
    """Declarative channel configuration profile decoupling driver implementation from settings."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    name: str
    driver_id: str
    secret_handle: str | None = None
    rate_limit_per_sec: float = 10.0
    max_retries: int = 3
    options: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class ActionRequest(BaseModel):
    """Request payload for executing an action through a Connector Profile."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    profile_id: str
    action_type: ConnectorActionType
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = "default"


class ActionResult(BaseModel):
    """Result payload returned after executing a connector action."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    status: str
    response_payload: dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: float = 0.0
    error_details: dict[str, Any] | None = None
    correlation_id: str | None = None


class PipelineStage(BaseModel):
    """Specification of an individual stage within a Connector Pipeline."""

    model_config = ConfigDict(frozen=True)

    stage_id: str
    stage_type: str
    is_optional: bool = False
    stage_options: dict[str, Any] = Field(default_factory=dict)


class ConnectorPipelineDefinition(BaseModel):
    """Definition of a multi-stage Connector Pipeline."""

    model_config = ConfigDict(frozen=True)

    pipeline_id: str
    profile_id: str
    stages: list[PipelineStage] = Field(default_factory=list)


# -- SQLAlchemy ORM Models for IDataStore Persistence -------------------------

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel as SQLAlchemyBaseModel


class ConnectorProfileModel(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for persisting Connector Profile records via IDataStore."""

    __tablename__ = "connector_profiles"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    driver_id: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rate_limit_per_sec: Mapped[float] = mapped_column(Float, default=10.0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ConnectorActionHistoryModel(SQLAlchemyBaseModel):
    """SQLAlchemy ORM model for persisting sanitized action execution history via IDataStore."""

    __tablename__ = "connector_action_history"

    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_time_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "ActionRequest",
    "ActionResult",
    "ConnectorActionHistoryModel",
    "ConnectorActionType",
    "ConnectorCapability",
    "ConnectorPipelineDefinition",
    "ConnectorProfile",
    "ConnectorProfileModel",
    "ConnectorStatus",
    "DriverMetadata",
    "PipelineStage",
]
