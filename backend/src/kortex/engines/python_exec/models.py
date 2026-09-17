"""Domain models and persistence records for the KORTEX Python Execution Gateway.

Two distinct model families live here, following the existing
`engines/connector/models.py` convention exactly:

* Pydantic v2 domain models -- the in-process contract between the Gateway,
  the execution boundaries, and the capability handlers.
* SQLAlchemy ORM records -- persisted through the existing Storage Engine
  `IDataStore`. No second artifact store, no second registry.

Python Actions are the canonical production asset model: tenant-owned,
versioned, and immutable. A "save" never mutates a published version; it
appends a new one. A workflow execution pins `(action_id, version)`.
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel as SQLAlchemyBaseModel

# The hard ceiling on a single action's source size. Not a security boundary
# on its own -- it bounds the persistence/transport cost of an asset that is
# copied into every ephemeral workspace.
MAX_SOURCE_BYTES = 512 * 1024


class PythonTrustLevel(str, enum.Enum):
    """The two Python execution classes the frozen architecture defines.

    TRUSTED: KORTEX-administrator authored, official recipe/action, or an
    explicitly trusted versioned Python Action. May invoke an explicitly
    permitted set of KORTEX capabilities through the governed IPC bridge --
    never the Kernel, database, or SecretStore directly.

    UNTRUSTED: user-authored, LLM-authored, or imported. No capability
    access, no host filesystem, no secrets, no network, disposable
    environment, strict limits, fail closed. Supported on Linux only.
    """

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class PythonNetworkPolicy(str, enum.Enum):
    """Network policy for one execution. Default-deny, always."""

    DENY = "DENY"
    ALLOW_LIST = "ALLOW_LIST"


class PythonExecutionStatus(str, enum.Enum):
    """Terminal outcome of one Python execution."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


class PythonBoundaryKind(str, enum.Enum):
    """Which governed execution boundary actually ran the code.

    Recorded on every execution so an audit reader never has to infer, from
    the host platform alone, which enforcement mechanism was in force.
    """

    WINDOWS_RESTRICTED_TOKEN_JOB = "WINDOWS_RESTRICTED_TOKEN_JOB"  # noqa: S105 - a boundary name, not a credential
    LINUX_NSJAIL = "LINUX_NSJAIL"


class PythonExecutionLimits(BaseModel):
    """Resource limits enforced by the platform boundary, not by Python itself.

    A limit of `None` means "not configured", and a boundary must then apply
    no limit for that dimension rather than silently inventing one -- so a
    configured limit is always a real, enforced limit and never a comment.
    `timeout_seconds` is the one dimension that is never optional: every
    execution has a deterministic termination budget.
    """

    model_config = ConfigDict(frozen=True)

    timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    memory_bytes: int | None = Field(default=512 * 1024 * 1024, gt=0)
    cpu_seconds: int | None = Field(default=60, gt=0)
    max_processes: int | None = Field(default=1, ge=1)
    max_output_bytes: int = Field(default=4 * 1024 * 1024, gt=0)


class PythonActionVersion(BaseModel):
    """One immutable, published version of a tenant-owned Python Action."""

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    source_code: str
    source_sha256: str
    entrypoint: str = Field(default="main", min_length=1, max_length=128)
    trust_level: PythonTrustLevel = PythonTrustLevel.UNTRUSTED
    allowed_capabilities: tuple[str, ...] = ()
    network_policy: PythonNetworkPolicy = PythonNetworkPolicy.DENY
    network_allow_list: tuple[str, ...] = ()
    limits: PythonExecutionLimits = Field(default_factory=PythonExecutionLimits)
    requirements_lock: tuple[str, ...] = ()
    created_by: str | None = None
    created_at: datetime.datetime | None = None

    @field_validator("allowed_capabilities")
    @classmethod
    def _capabilities_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """An allowlist entry must be a fully-qualified `kortex.*` capability
        name. No wildcards, no prefixes: an allowlist that can be widened by
        pattern is not an allowlist, and the bridge must never have to decide
        what a pattern means at request time."""
        for name in value:
            if not name.startswith("kortex.") or "*" in name or name != name.strip():
                raise ValueError(f"Invalid capability allowlist entry: {name!r}")
        return value


class PythonAction(BaseModel):
    """A tenant-owned Python Action: stable identity plus its latest version."""

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    latest_version: int = Field(default=0, ge=0)
    is_active: bool = True
    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None


class PythonExecutionRequest(BaseModel):
    """One governed execution of a pinned Action version.

    `tenant_id`, `workflow_id` and `execution_id` are never caller-suppliable
    at the capability boundary: `kortex.python.execute` derives them from the
    dispatcher-built `CapabilityExecutionContext`. They appear here because
    this is the Gateway's own internal contract, constructed only by the
    capability handler after that derivation has happened.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    workflow_id: str | None = None
    execution_id: str | None = None
    correlation_id: str | None = None
    principal_id: str | None = None


class PythonExecutionResult(BaseModel):
    """Structured outcome of one Python execution.

    `stderr_excerpt` is diagnostics only and is truncated. It is never parsed
    for control flow, and it is scrubbed of the execution token before it is
    ever persisted or logged.
    """

    model_config = ConfigDict(frozen=True)

    status: PythonExecutionStatus
    boundary: PythonBoundaryKind | None = None
    output: Any = None
    error: str | None = None
    exit_code: int | None = None
    duration_ms: float = 0.0
    timed_out: bool = False
    stderr_excerpt: str = ""
    capability_calls: int = 0
    action_id: str | None = None
    version: int | None = None
    trust_level: PythonTrustLevel | None = None


@dataclass(frozen=True)
class BoundaryResult:
    """What a platform boundary observed about one execution.

    Deliberately carries no interpretation: it reports the raw exit code and
    streams, and the Gateway alone decides what they mean. Defined here rather
    than in either boundary module so that importing the Linux boundary never
    drags in the Windows one -- `winapi` loads `WinDLL` at module scope and
    cannot be imported off-Windows at all.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_ms: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False


# -- SQLAlchemy ORM Records ---------------------------------------------------


class PythonActionRecord(SQLAlchemyBaseModel):
    """Stable identity row for a tenant-owned Python Action.

    `id` (from `BaseModel`) is the surrogate key; `(tenant_id, action_id)` is
    the tenant-scoped natural key and is uniquely constrained so two tenants
    may independently use the same `action_id` without colliding, while one
    tenant can never hold two actions under the same id.
    """

    __tablename__ = "python_actions"
    __table_args__ = (UniqueConstraint("tenant_id", "action_id", name="uq_python_actions_tenant_action"),)

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PythonActionVersionRecord(SQLAlchemyBaseModel):
    """An immutable published version of a Python Action.

    Rows in this table are append-only by contract: `PythonActionManager`
    never issues an UPDATE against one, and `(tenant_id, action_id, version)`
    is uniquely constrained so a re-publish of an existing version is a
    database-level integrity failure rather than a silent overwrite.
    """

    __tablename__ = "python_action_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "action_id", "version", name="uq_python_action_versions_identity"),)

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(128), default="main", nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    allowed_capabilities_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    network_policy: Mapped[str] = mapped_column(String(16), default="DENY", nullable=False)
    network_allow_list_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    limits_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    requirements_lock_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PythonExecutionRecordModel(SQLAlchemyBaseModel):
    """Sanitized execution history for one Python Action invocation.

    Deliberately stores no stdout payload and no environment: the structured
    result is returned to the workflow and audited through the existing
    `AuditManager`; this row is the durable, queryable execution lineage.
    """

    __tablename__ = "python_execution_records"

    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False)
    boundary: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    capability_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    principal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "MAX_SOURCE_BYTES",
    "BoundaryResult",
    "PythonAction",
    "PythonActionRecord",
    "PythonActionVersion",
    "PythonActionVersionRecord",
    "PythonBoundaryKind",
    "PythonExecutionLimits",
    "PythonExecutionRecordModel",
    "PythonExecutionRequest",
    "PythonExecutionResult",
    "PythonExecutionStatus",
    "PythonNetworkPolicy",
    "PythonTrustLevel",
]
