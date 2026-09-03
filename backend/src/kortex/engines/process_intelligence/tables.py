"""
KORTEX Process Intelligence Database Descriptors.

Declares engine-local SQLAlchemy Core Table projections for Workflow persistence
tables (workflow_instances, workflow_step_runs, approval_requests).

These descriptors decouple Process Intelligence completely from Workflow ORM
models, ensuring zero production imports of `kortex.engines.workflow.persistence`.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

# Engine-local metadata instance (completely decoupled from Workflow ORM Base)
metadata = MetaData()

t_workflow_instances = Table(
    "workflow_instances",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("definition_id", String(64), nullable=False),
    Column("definition_version", String(32), nullable=False),
    Column("state", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

t_workflow_step_runs = Table(
    "workflow_step_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("instance_id", String(36), nullable=False),
    Column("step_id", String(64), nullable=False),
    Column("attempt", Integer, nullable=False, default=1),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
)

t_approval_requests = Table(
    "approval_requests",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant_id", String(64), nullable=False),
    Column("instance_id", String(36), nullable=True),
    Column("step_id", String(64), nullable=True),
    Column("state", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
