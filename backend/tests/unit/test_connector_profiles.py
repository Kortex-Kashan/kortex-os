"""Unit tests for ConnectorProfileManager (Milestone 5).

Target: 100% pass rate, 100% line coverage for profiles.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from kortex.engines.connector.exceptions import (
    ConnectorProfileNotFoundError,
    ConnectorValidationError,
)
from kortex.engines.connector.interfaces import IConnectorProfileManager
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.storage.stores.cache_store import MemoryCacheStore


def test_protocol_compliance() -> None:
    """Test that ConnectorProfileManager satisfies IConnectorProfileManager protocol."""
    mgr = ConnectorProfileManager()
    assert isinstance(mgr, IConnectorProfileManager)


def test_profile_model_immutability() -> None:
    """Verify ConnectorProfile model immutability."""
    prof = ConnectorProfile(profile_id="p-imm", name="Imm", driver_id="drv-dummy")
    with pytest.raises(ValidationError):
        prof.name = "Mutated"  # type: ignore[misc]


def test_validate_profile_rules() -> None:
    """Test profile field validation rules raising ConnectorValidationError."""

    # Empty profile_id
    with pytest.raises(ConnectorValidationError) as exc1:
        ConnectorProfileManager.validate_profile(
            ConnectorProfile(profile_id="   ", name="N", driver_id="D")
        )
    assert "profile_id" in exc1.value.message

    # Empty name
    with pytest.raises(ConnectorValidationError) as exc2:
        ConnectorProfileManager.validate_profile(
            ConnectorProfile(profile_id="P", name="   ", driver_id="D")
        )
    assert "name" in exc2.value.message

    # Empty driver_id
    with pytest.raises(ConnectorValidationError) as exc3:
        ConnectorProfileManager.validate_profile(
            ConnectorProfile(profile_id="P", name="N", driver_id="   ")
        )
    assert "driver_id" in exc3.value.message

    # Non-positive rate_limit_per_sec
    with pytest.raises(ConnectorValidationError) as exc4:
        ConnectorProfileManager.validate_profile(
            ConnectorProfile(profile_id="P", name="N", driver_id="D", rate_limit_per_sec=0.0)
        )
    assert "rate_limit_per_sec" in exc4.value.message

    # Negative max_retries
    with pytest.raises(ConnectorValidationError) as exc5:
        ConnectorProfileManager.validate_profile(
            ConnectorProfile(profile_id="P", name="N", driver_id="D", max_retries=-1)
        )
    assert "max_retries" in exc5.value.message


@pytest.mark.asyncio
async def test_register_and_get_profile() -> None:
    """Test profile registration, timestamp generation, and lookup."""
    mgr = ConnectorProfileManager()

    prof = ConnectorProfile(
        profile_id="prof-1",
        name="Test Profile 1",
        driver_id="connector-dummy",
        secret_handle="sec-handle-123",
    )

    await mgr.register_profile(prof)

    retrieved = await mgr.get_profile("prof-1")
    assert retrieved.profile_id == "prof-1"
    assert retrieved.name == "Test Profile 1"
    assert retrieved.driver_id == "connector-dummy"
    assert retrieved.secret_handle == "sec-handle-123"
    assert retrieved.created_at is not None
    assert retrieved.updated_at is not None
    assert retrieved.created_at == retrieved.updated_at


@pytest.mark.asyncio
async def test_update_profile_preserves_created_at() -> None:
    """Test updating existing profile preserves original created_at timestamp."""
    mgr = ConnectorProfileManager()

    prof1 = ConnectorProfile(profile_id="p-upd", name="Original Name", driver_id="drv-dummy")
    await mgr.register_profile(prof1)

    first = await mgr.get_profile("p-upd")
    original_created = first.created_at

    await asyncio.sleep(0.01)

    prof2 = ConnectorProfile(profile_id="p-upd", name="Updated Name", driver_id="drv-dummy")
    await mgr.register_profile(prof2)

    second = await mgr.get_profile("p-upd")
    assert second.name == "Updated Name"
    assert second.created_at == original_created
    assert second.updated_at >= original_created


@pytest.mark.asyncio
async def test_get_profile_errors() -> None:
    """Test get_profile validation and missing profile exceptions."""
    mgr = ConnectorProfileManager()

    with pytest.raises(ConnectorValidationError):
        await mgr.get_profile("   ")

    with pytest.raises(ConnectorProfileNotFoundError) as exc_info:
        await mgr.get_profile("non-existent-profile")
    assert "non-existent-profile" in exc_info.value.message


@pytest.mark.asyncio
async def test_list_profiles_and_filtering() -> None:
    """Test list_profiles with driver_id and active_only filtering."""
    mgr = ConnectorProfileManager()

    await mgr.register_profile(
        ConnectorProfile(profile_id="p1", name="P1", driver_id="drv-a", is_active=True)
    )
    await mgr.register_profile(
        ConnectorProfile(profile_id="p2", name="P2", driver_id="drv-a", is_active=False)
    )
    await mgr.register_profile(
        ConnectorProfile(profile_id="p3", name="P3", driver_id="drv-b", is_active=True)
    )

    all_profiles = await mgr.list_profiles()
    assert len(all_profiles) == 3

    drv_a_profiles = await mgr.list_profiles(driver_id="drv-a")
    assert len(drv_a_profiles) == 2
    assert {p.profile_id for p in drv_a_profiles} == {"p1", "p2"}

    active_profiles = await mgr.list_profiles(active_only=True)
    assert len(active_profiles) == 2
    assert {p.profile_id for p in active_profiles} == {"p1", "p3"}

    active_drv_a = await mgr.list_profiles(driver_id="drv-a", active_only=True)
    assert len(active_drv_a) == 1
    assert active_drv_a[0].profile_id == "p1"


@pytest.mark.asyncio
async def test_delete_profile() -> None:
    """Test delete_profile removal and invalid/missing handling."""
    mgr = ConnectorProfileManager()

    await mgr.register_profile(ConnectorProfile(profile_id="p-del", name="Del", driver_id="drv-dummy"))

    assert await mgr.delete_profile("p-del") is True
    assert await mgr.delete_profile("p-del") is False
    assert await mgr.delete_profile("   ") is False

    with pytest.raises(ConnectorProfileNotFoundError):
        await mgr.get_profile("p-del")


@pytest.mark.asyncio
async def test_cache_store_integration() -> None:
    """Test ConnectorProfileManager with MemoryCacheStore backend."""
    cache = MemoryCacheStore()
    mgr = ConnectorProfileManager(cache_store=cache)

    prof = ConnectorProfile(profile_id="p-cache", name="Cached", driver_id="drv-dummy")
    await mgr.register_profile(prof)

    # Verify cached in MemoryCacheStore
    cached_dict = await cache.get("connector:profile:p-cache")
    assert isinstance(cached_dict, dict)
    assert cached_dict["name"] == "Cached"

    # Retrieval uses cache store
    retrieved = await mgr.get_profile("p-cache")
    assert retrieved.profile_id == "p-cache"

    # Delete purges from cache store
    assert await mgr.delete_profile("p-cache") is True
    assert await cache.get("connector:profile:p-cache") is None


@pytest.mark.asyncio
async def test_cache_store_error_resilience() -> None:
    """Test resilience when ICacheStore raises exceptions."""

    class BrokenCacheStore:
        async def get(self, key: str) -> Any:
            raise RuntimeError("Cache get error")

        async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
            raise RuntimeError("Cache set error")

        async def delete(self, key: str) -> bool:
            raise RuntimeError("Cache delete error")

        async def clear(self) -> bool:
            return True

    broken_cache = BrokenCacheStore()
    mgr = ConnectorProfileManager(cache_store=broken_cache)  # type: ignore[arg-type]

    prof = ConnectorProfile(profile_id="p-broken", name="BrokenCache", driver_id="drv-dummy")
    await mgr.register_profile(prof)

    retrieved = await mgr.get_profile("p-broken")
    assert retrieved.name == "BrokenCache"

    assert await mgr.delete_profile("p-broken") is True


@pytest.mark.asyncio
async def test_concurrent_profile_operations() -> None:
    """Test concurrent profile registrations using asyncio.gather."""
    mgr = ConnectorProfileManager()

    async def register_worker(index: int) -> None:
        prof = ConnectorProfile(
            profile_id=f"prof-conc-{index}",
            name=f"Concurrent {index}",
            driver_id="drv-dummy",
        )
        await mgr.register_profile(prof)

    tasks = [register_worker(i) for i in range(20)]
    await asyncio.gather(*tasks)

    profiles = await mgr.list_profiles()
    assert len(profiles) == 20
