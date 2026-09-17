"""Versioned, immutable, tenant-owned Python Actions.

This is the canonical production asset model. Inline Python is not a supported
production asset: a workflow always pins `(action_id, version)`, and that pair
resolves to a row that can never change afterwards.

Immutability is enforced in three independent places, so weakening any one of
them does not silently make actions mutable:

1. `publish_version` only ever INSERTs. There is no code path in this module
   that issues an UPDATE against `PythonActionVersionRecord`.
2. `(tenant_id, action_id, version)` is uniquely constrained in the schema, so
   a re-publish of an existing version is a database integrity error rather
   than an overwrite.
3. The stored `source_sha256` is recomputed and compared on every load, so a
   row modified out-of-band (directly in SQL, or by a restore from a tampered
   backup) fails to resolve rather than executing.

Tenant scoping follows the established KORTEX fetch-then-verify pattern: a
row is looked up by its tenant-scoped natural key, and a cross-tenant miss is
reported as `PythonActionNotFoundError` -- indistinguishable from "no such
action" -- so the API cannot be used to enumerate another tenant's actions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.python_exec.exceptions import (
    PythonActionImmutabilityError,
    PythonActionNotFoundError,
    PythonActionValidationError,
)
from kortex.engines.python_exec.models import (
    MAX_SOURCE_BYTES,
    PythonAction,
    PythonActionRecord,
    PythonActionVersion,
    PythonActionVersionRecord,
    PythonExecutionLimits,
    PythonNetworkPolicy,
    PythonTrustLevel,
)
from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engine.python_exec.actions")

# `action_id` is used as a filesystem-adjacent identifier and appears in audit
# records, so its character set is restricted rather than merely length-capped.
_ALLOWED_ID_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def compute_source_digest(source_code: str) -> str:
    """The content digest a version is pinned to."""
    return hashlib.sha256(source_code.encode("utf-8")).hexdigest()


def _validate_action_id(action_id: str) -> str:
    candidate = action_id.strip()
    if not candidate or len(candidate) > 128:
        raise PythonActionValidationError("action_id must be between 1 and 128 characters.")
    if not set(candidate) <= _ALLOWED_ID_CHARACTERS:
        raise PythonActionValidationError("action_id may contain only letters, digits, hyphen, underscore and period.")
    if candidate in (".", "..") or candidate.startswith("."):
        raise PythonActionValidationError("action_id must not be a relative path component.")
    return candidate


def _validate_source(source_code: str) -> str:
    if not source_code.strip():
        raise PythonActionValidationError("A Python Action version must contain source code.")
    if len(source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise PythonActionValidationError(f"Python Action source exceeds the {MAX_SOURCE_BYTES}-byte limit.")
    try:
        compile(source_code, "<action>", "exec")
    except SyntaxError as exc:
        # Rejected at publish time rather than at execution time: an action
        # that cannot compile is a broken asset, and discovering that during a
        # workflow run costs a workspace, a boundary, and a confusing failure.
        raise PythonActionValidationError(f"Python Action source does not compile: {exc}") from None
    return source_code


class PythonActionManager:
    """Persistence and resolution for versioned Python Actions."""

    def __init__(self, data_store: IDataStore | None = None) -> None:
        self._data_store = data_store

    def bind_data_store(self, data_store: IDataStore) -> None:
        self._data_store = data_store

    def _require_store(self) -> IDataStore:
        if self._data_store is None:
            raise PythonActionValidationError("The Python Execution Engine has no Storage Engine data store bound.")
        return self._data_store

    async def publish_version(
        self,
        *,
        tenant_id: str,
        action_id: str,
        name: str,
        source_code: str,
        description: str = "",
        entrypoint: str = "main",
        trust_level: PythonTrustLevel = PythonTrustLevel.UNTRUSTED,
        allowed_capabilities: tuple[str, ...] = (),
        network_policy: PythonNetworkPolicy = PythonNetworkPolicy.DENY,
        network_allow_list: tuple[str, ...] = (),
        limits: PythonExecutionLimits | None = None,
        requirements_lock: tuple[str, ...] = (),
        created_by: str | None = None,
    ) -> PythonActionVersion:
        """Append a new immutable version, creating the action if needed.

        The version number is allocated from the action row inside the same
        transaction that inserts the version, so two concurrent publishes
        cannot both claim the same number -- the loser hits the unique
        constraint and is reported as an immutability violation rather than
        silently overwriting.
        """
        store = self._require_store()
        resolved_id = _validate_action_id(action_id)
        validated_source = _validate_source(source_code)
        digest = compute_source_digest(validated_source)
        resolved_limits = limits or PythonExecutionLimits()

        if trust_level is PythonTrustLevel.UNTRUSTED and allowed_capabilities:
            raise PythonActionValidationError("Untrusted Python Actions cannot declare KORTEX capability access.")

        # Validated here so an invalid allowlist entry is rejected at publish
        # time, not first observed by the bridge at execution time.
        probe = PythonActionVersion(
            action_id=resolved_id,
            tenant_id=tenant_id,
            version=1,
            source_code=validated_source,
            source_sha256=digest,
            entrypoint=entrypoint,
            trust_level=trust_level,
            allowed_capabilities=allowed_capabilities,
            network_policy=network_policy,
            network_allow_list=network_allow_list,
            limits=resolved_limits,
            requirements_lock=requirements_lock,
            created_by=created_by,
        )

        async def _publish(session: AsyncSession) -> int:
            statement = select(PythonActionRecord).where(
                PythonActionRecord.tenant_id == tenant_id,
                PythonActionRecord.action_id == resolved_id,
            )
            record = (await session.execute(statement)).scalar_one_or_none()
            if record is None:
                record = PythonActionRecord(
                    id=f"{tenant_id}:{resolved_id}",
                    tenant_id=tenant_id,
                    action_id=resolved_id,
                    name=name,
                    description=description,
                    latest_version=0,
                    is_active=True,
                )
                session.add(record)
                await session.flush()

            next_version = record.latest_version + 1
            record.latest_version = next_version
            record.name = name
            record.description = description

            session.add(
                PythonActionVersionRecord(
                    id=f"{tenant_id}:{resolved_id}:{next_version}",
                    tenant_id=tenant_id,
                    action_id=resolved_id,
                    version=next_version,
                    source_code=validated_source,
                    source_sha256=digest,
                    entrypoint=probe.entrypoint,
                    trust_level=trust_level.value,
                    allowed_capabilities_json=json.dumps(list(allowed_capabilities)),
                    network_policy=network_policy.value,
                    network_allow_list_json=json.dumps(list(network_allow_list)),
                    limits_json=resolved_limits.model_dump_json(),
                    requirements_lock_json=json.dumps(list(requirements_lock)),
                    created_by=created_by,
                )
            )
            return next_version

        try:
            version_number = await store.execute_in_transaction(_publish)
        except IntegrityError as exc:
            raise PythonActionImmutabilityError(
                f"Python Action {resolved_id!r} version could not be published without "
                f"overwriting an existing immutable version.",
                details={"action_id": resolved_id},
            ) from exc

        logger.info(
            "Published Python Action %r version %d for tenant %r (trust=%s).",
            resolved_id,
            version_number,
            tenant_id,
            trust_level.value,
        )
        return probe.model_copy(update={"version": version_number})

    async def get_version(self, *, tenant_id: str, action_id: str, version: int | None = None) -> PythonActionVersion:
        """Resolve a pinned version, or the latest when `version` is None.

        The stored digest is verified against the stored source before the
        version is returned, so out-of-band modification of a published row is
        detected here rather than executed.
        """
        store = self._require_store()
        resolved_id = _validate_action_id(action_id)

        async def _load(session: AsyncSession) -> PythonActionVersionRecord | None:
            statement = select(PythonActionVersionRecord).where(
                PythonActionVersionRecord.tenant_id == tenant_id,
                PythonActionVersionRecord.action_id == resolved_id,
            )
            if version is not None:
                statement = statement.where(PythonActionVersionRecord.version == version)
            else:
                statement = statement.order_by(PythonActionVersionRecord.version.desc())
            return (await session.execute(statement.limit(1))).scalar_one_or_none()

        record = await store.execute_in_transaction(_load)
        if record is None:
            raise PythonActionNotFoundError(
                f"Python Action {resolved_id!r} was not found.",
                details={"action_id": resolved_id, "version": version},
            )

        if compute_source_digest(record.source_code) != record.source_sha256:
            raise PythonActionImmutabilityError(
                f"Python Action {resolved_id!r} version {record.version} failed its integrity check "
                f"and will not be executed.",
                details={"action_id": resolved_id, "version": record.version},
            )

        return PythonActionVersion(
            action_id=record.action_id,
            tenant_id=record.tenant_id,
            version=record.version,
            source_code=record.source_code,
            source_sha256=record.source_sha256,
            entrypoint=record.entrypoint,
            trust_level=PythonTrustLevel(record.trust_level),
            allowed_capabilities=tuple(json.loads(record.allowed_capabilities_json)),
            network_policy=PythonNetworkPolicy(record.network_policy),
            network_allow_list=tuple(json.loads(record.network_allow_list_json)),
            limits=PythonExecutionLimits.model_validate_json(record.limits_json),
            requirements_lock=tuple(json.loads(record.requirements_lock_json)),
            created_by=record.created_by,
            created_at=record.created_at,
        )

    async def get_action(self, *, tenant_id: str, action_id: str) -> PythonAction:
        store = self._require_store()
        resolved_id = _validate_action_id(action_id)

        async def _load(session: AsyncSession) -> PythonActionRecord | None:
            statement = select(PythonActionRecord).where(
                PythonActionRecord.tenant_id == tenant_id,
                PythonActionRecord.action_id == resolved_id,
            )
            return (await session.execute(statement)).scalar_one_or_none()

        record = await store.execute_in_transaction(_load)
        if record is None:
            raise PythonActionNotFoundError(
                f"Python Action {resolved_id!r} was not found.", details={"action_id": resolved_id}
            )
        return _to_action(record)

    async def list_actions(self, *, tenant_id: str, limit: int = 200) -> list[PythonAction]:
        """List the caller's own actions. Never crosses a tenant boundary."""
        store = self._require_store()

        async def _load(session: AsyncSession) -> list[PythonActionRecord]:
            statement = (
                select(PythonActionRecord)
                .where(PythonActionRecord.tenant_id == tenant_id)
                .order_by(PythonActionRecord.action_id)
                .limit(max(1, min(limit, 1000)))
            )
            return list((await session.execute(statement)).scalars().all())

        return [_to_action(record) for record in await store.execute_in_transaction(_load)]

    async def list_versions(self, *, tenant_id: str, action_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Version metadata for one action. Source code is deliberately absent.

        Listing is a discovery operation; returning every version's full source
        would make an ordinary list call an expensive bulk export of tenant
        intellectual property.
        """
        store = self._require_store()
        resolved_id = _validate_action_id(action_id)

        async def _load(session: AsyncSession) -> list[PythonActionVersionRecord]:
            statement = (
                select(PythonActionVersionRecord)
                .where(
                    PythonActionVersionRecord.tenant_id == tenant_id,
                    PythonActionVersionRecord.action_id == resolved_id,
                )
                .order_by(PythonActionVersionRecord.version.desc())
                .limit(max(1, min(limit, 500)))
            )
            return list((await session.execute(statement)).scalars().all())

        records = await store.execute_in_transaction(_load)
        return [
            {
                "action_id": record.action_id,
                "version": record.version,
                "source_sha256": record.source_sha256,
                "entrypoint": record.entrypoint,
                "trust_level": record.trust_level,
                "allowed_capabilities": json.loads(record.allowed_capabilities_json),
                "network_policy": record.network_policy,
                "created_by": record.created_by,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
            for record in records
        ]


def _to_action(record: PythonActionRecord) -> PythonAction:
    return PythonAction(
        action_id=record.action_id,
        tenant_id=record.tenant_id,
        name=record.name,
        description=record.description,
        latest_version=record.latest_version,
        is_active=record.is_active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


__all__ = ["PythonActionManager", "compute_source_digest"]
