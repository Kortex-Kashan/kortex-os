"""Connector Profile Manager for KORTEX OS Connector Engine.

This module implements ConnectorProfileManager, managing declarative channel configuration
profiles, validation rules, timestamp lifecycle, and thread/async-safe storage in accordance
with the Connector Engine Specification.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import json
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.connector.exceptions import (
    ConnectorProfileNotFoundError,
    ConnectorValidationError,
)
from kortex.engines.connector.interfaces import IConnectorProfileManager
from kortex.engines.connector.models import ConnectorProfile, ConnectorProfileModel
from kortex.engines.storage.interfaces import ICacheStore, IDataStore


class ConnectorProfileManager(IConnectorProfileManager):
    """Thread-safe manager for creating, validating, updating, listing, and resolving Connector Profiles.

    Integrates with Storage Engine IDataStore and ICacheStore abstractions with fallback
    to in-memory tracking.
    """

    def __init__(
        self,
        data_store: IDataStore | None = None,
        cache_store: ICacheStore | None = None,
    ) -> None:
        """Initialize ConnectorProfileManager.

        Args:
            data_store: Optional IDataStore instance from Storage Engine.
            cache_store: Optional ICacheStore instance from Storage Engine.
        """
        self._data_store = data_store
        self._cache_store = cache_store
        self._profiles: dict[str, ConnectorProfile] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def validate_profile(profile: ConnectorProfile) -> None:
        """Validate ConnectorProfile field constraints.

        Args:
            profile: ConnectorProfile instance to validate.

        Raises:
            ConnectorValidationError: If any profile field validation rule is violated.
        """
        if not profile.profile_id or not profile.profile_id.strip():
            raise ConnectorValidationError(
                "Invalid profile: 'profile_id' cannot be empty or whitespace."
            )

        if not profile.name or not profile.name.strip():
            raise ConnectorValidationError(
                "Invalid profile: 'name' cannot be empty or whitespace."
            )

        if not profile.driver_id or not profile.driver_id.strip():
            raise ConnectorValidationError(
                "Invalid profile: 'driver_id' cannot be empty or whitespace."
            )

        if profile.rate_limit_per_sec <= 0.0:
            raise ConnectorValidationError(
                f"Invalid profile: 'rate_limit_per_sec' must be strictly positive (> 0.0), got {profile.rate_limit_per_sec}."
            )

        if profile.max_retries < 0:
            raise ConnectorValidationError(
                f"Invalid profile: 'max_retries' cannot be negative (< 0), got {profile.max_retries}."
            )

    def _get_cache_key(self, profile_id: str) -> str:
        """Format canonical cache key string."""
        return f"connector:profile:{profile_id.strip()}"

    async def register_profile(self, profile: ConnectorProfile) -> None:
        """Register or update a Connector Profile across Storage Engine and local memory.

        Args:
            profile: ConnectorProfile model instance.

        Raises:
            ConnectorValidationError: If profile validation fails.
        """
        self.validate_profile(profile)

        profile_id = profile.profile_id.strip()
        now_iso = datetime.now(timezone.utc).isoformat()

        async with self._lock:
            existing = self._profiles.get(profile_id)
            if existing is not None:
                created_at = existing.created_at or profile.created_at or now_iso
            else:
                created_at = profile.created_at or now_iso

            updated_profile = profile.model_copy(
                update={
                    "profile_id": profile_id,
                    "name": profile.name.strip(),
                    "driver_id": profile.driver_id.strip(),
                    "created_at": created_at,
                    "updated_at": now_iso,
                }
            )

            self._profiles[profile_id] = updated_profile

            # 1. Durable persistence via Storage Engine IDataStore
            if self._data_store is not None:
                async def _save_to_db(session: AsyncSession) -> None:
                    stmt = select(ConnectorProfileModel).where(ConnectorProfileModel.id == profile_id)
                    res = await session.execute(stmt)
                    model = res.scalar_one_or_none()
                    options_str = json.dumps(updated_profile.options) if updated_profile.options else None

                    if model is not None:
                        model.name = updated_profile.name
                        model.driver_id = updated_profile.driver_id
                        model.secret_handle = updated_profile.secret_handle
                        model.rate_limit_per_sec = updated_profile.rate_limit_per_sec
                        model.max_retries = updated_profile.max_retries
                        model.options_json = options_str
                        model.is_active = updated_profile.is_active
                    else:
                        model = ConnectorProfileModel(
                            id=profile_id,
                            name=updated_profile.name,
                            driver_id=updated_profile.driver_id,
                            secret_handle=updated_profile.secret_handle,
                            rate_limit_per_sec=updated_profile.rate_limit_per_sec,
                            max_retries=updated_profile.max_retries,
                            options_json=options_str,
                            is_active=updated_profile.is_active,
                        )
                        session.add(model)

                try:
                    await self._data_store.execute_in_transaction(_save_to_db)
                except Exception:
                    pass  # Resilience: continue on DB exception with local fallback

            # 2. Ephemeral caching via Storage Engine ICacheStore
            if self._cache_store is not None:
                try:
                    await self._cache_store.set(
                        self._get_cache_key(profile_id),
                        updated_profile.model_dump(),
                        ttl_seconds=3600,
                    )
                except Exception:
                    pass  # Resilience: continue on cache failure

    async def get_profile(self, profile_id: str) -> ConnectorProfile:
        """Retrieve Connector Profile by profile ID across CacheStore, DataStore, and local state.

        Args:
            profile_id: Identifier string of target profile.

        Returns:
            ConnectorProfile instance.

        Raises:
            ConnectorValidationError: If profile_id is empty or whitespace.
            ConnectorProfileNotFoundError: If profile is not registered.
        """
        if not profile_id or not profile_id.strip():
            raise ConnectorValidationError("profile_id cannot be empty or whitespace.")

        pid = profile_id.strip()

        # 1. Check ICacheStore
        if self._cache_store is not None:
            try:
                cached = await self._cache_store.get(self._get_cache_key(pid))
                if isinstance(cached, dict):
                    return ConnectorProfile.model_validate(cached)
            except Exception:
                pass  # Fall through on cache error

        # 2. Check IDataStore
        if self._data_store is not None:
            async def _read_from_db(session: AsyncSession) -> ConnectorProfile | None:
                stmt = select(ConnectorProfileModel).where(ConnectorProfileModel.id == pid)
                res = await session.execute(stmt)
                model = res.scalar_one_or_none()
                if model is not None:
                    opts = json.loads(model.options_json) if model.options_json else {}
                    return ConnectorProfile(
                        profile_id=model.id,
                        name=model.name,
                        driver_id=model.driver_id,
                        secret_handle=model.secret_handle,
                        rate_limit_per_sec=model.rate_limit_per_sec,
                        max_retries=model.max_retries,
                        options=opts,
                        is_active=model.is_active,
                        created_at=model.created_at.isoformat() if model.created_at else None,
                        updated_at=model.updated_at.isoformat() if model.updated_at else None,
                    )
                return None

            try:
                db_profile = await self._data_store.execute_in_transaction(_read_from_db)
                if db_profile is not None:
                    # Update local state cache
                    async with self._lock:
                        self._profiles[pid] = db_profile
                    return db_profile
            except Exception:
                pass

        # 3. Check local in-memory fallback
        async with self._lock:
            if pid in self._profiles:
                return self._profiles[pid]

        raise ConnectorProfileNotFoundError(
            f"Connector Profile '{pid}' not found.",
            details={"profile_id": pid},
        )

    async def list_profiles(
        self, driver_id: str | None = None, active_only: bool = False
    ) -> list[ConnectorProfile]:
        """Return all registered Connector Profiles from Storage Engine or local state.

        Args:
            driver_id: Optional driver ID filter string.
            active_only: If True, returns only profiles where is_active is True.

        Returns:
            List of matching ConnectorProfile instances.
        """
        profiles: list[ConnectorProfile] = []

        # 1. Query IDataStore if available
        if self._data_store is not None:
            async def _list_from_db(session: AsyncSession) -> list[ConnectorProfile]:
                stmt = select(ConnectorProfileModel)
                res = await session.execute(stmt)
                models = res.scalars().all()
                results = []
                for m in models:
                    opts = json.loads(m.options_json) if m.options_json else {}
                    results.append(
                        ConnectorProfile(
                            profile_id=m.id,
                            name=m.name,
                            driver_id=m.driver_id,
                            secret_handle=m.secret_handle,
                            rate_limit_per_sec=m.rate_limit_per_sec,
                            max_retries=m.max_retries,
                            options=opts,
                            is_active=m.is_active,
                            created_at=m.created_at.isoformat() if m.created_at else None,
                            updated_at=m.updated_at.isoformat() if m.updated_at else None,
                        )
                    )
                return results

            try:
                profiles = await self._data_store.execute_in_transaction(_list_from_db)
            except Exception:
                profiles = []

        if not profiles:
            async with self._lock:
                profiles = list(self._profiles.values())

        if driver_id is not None:
            target_driver = driver_id.strip()
            profiles = [p for p in profiles if p.driver_id == target_driver]

        if active_only:
            profiles = [p for p in profiles if p.is_active is True]

        return profiles

    async def delete_profile(self, profile_id: str) -> bool:
        """Unregister and delete a Connector Profile across DataStore, CacheStore, and local state.

        Args:
            profile_id: Identifier string of target profile.

        Returns:
            True if profile was deleted, False if profile was not found.
        """
        if not profile_id or not profile_id.strip():
            return False

        pid = profile_id.strip()
        deleted = False

        # 1. Delete from IDataStore if available
        if self._data_store is not None:
            async def _delete_from_db(session: AsyncSession) -> bool:
                stmt = delete(ConnectorProfileModel).where(ConnectorProfileModel.id == pid)
                res = await session.execute(stmt)
                return (res.rowcount or 0) > 0

            try:
                deleted = await self._data_store.execute_in_transaction(_delete_from_db)
            except Exception:
                pass

        # 2. Delete from local state
        async with self._lock:
            if pid in self._profiles:
                del self._profiles[pid]
                deleted = True

        # 3. Delete from ICacheStore
        if self._cache_store is not None:
            try:
                await self._cache_store.delete(self._get_cache_key(pid))
            except Exception:
                pass

        return deleted


__all__ = ["ConnectorProfileManager"]
