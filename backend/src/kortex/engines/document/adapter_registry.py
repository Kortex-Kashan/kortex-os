"""Document Adapter Registry for KORTEX OS Document Engine.

This module implements DocumentAdapterRegistry, which manages discovery, registration,
lookup, validation, capability discovery, versioning, and lifecycle management for Document Adapters,
in accordance with Section 14 of the Document Engine Implementation Specification (Version 3.0.0).
"""

from __future__ import annotations

from typing import Any, cast

from kortex.engines.document.base_adapter import BaseDocumentAdapter
from kortex.engines.document.exceptions import AdapterNotFoundError, DocumentAdapterError
from kortex.engines.document.models import (
    AdapterCapability,
    AdapterMetadata,
    BindingContext,
    DocumentOperationType,
    TemplateSchema,
)
from kortex.engines.document.template_library import parse_semver
from kortex.engines.storage.interfaces import ICacheStore


class MetadataAdapterWrapper(BaseDocumentAdapter):
    """Internal lightweight adapter wrapper used when registering AdapterMetadata alone."""

    def __init__(self, metadata_obj: AdapterMetadata) -> None:
        self._metadata = metadata_obj

    @property
    def metadata(self) -> AdapterMetadata:
        return self._metadata

    async def execute(
        self,
        operation_type: DocumentOperationType,
        binding_context: BindingContext,
        options: dict[str, Any],
    ) -> bytes:
        raise NotImplementedError("Metadata-only adapter registration cannot be executed.")

    def validate_schema(self, schema: TemplateSchema) -> bool:
        return True


class DocumentAdapterRegistry:
    """Registry for managing BaseDocumentAdapter implementations and metadata.

    Responsibilities:
    1. Registering BaseDocumentAdapter implementations and AdapterMetadata models.
    2. Validating adapter contract and metadata completeness prior to registration.
    3. SemVer 2.0.0 resolution for exact and latest version lookup.
    4. Fine-grained capability and operation based discovery.
    5. Enforcing immutable version rules and duplicate registration rejection.
    6. Thread-safe, deterministic, offline-first adapter resolution.
    """

    # Adapter Discovery Cache: single-key cache of list_adapters_cached()'s result.
    # Correctness does not depend on this TTL at all (see _mark_discovery_cache_dirty) — it
    # is kept only as a general hygiene backstop, since adapter registration is a rare,
    # boot-time event rather than a high-frequency mutation.
    DISCOVERY_CACHE_KEY = "doc_engine:adapters:discovery:all"
    ADAPTER_CACHE_TTL_SECONDS = 60

    def __init__(self, cache_store: ICacheStore | None = None) -> None:
        """Initialize empty in-memory adapter catalog.

        Args:
            cache_store: Optional ICacheStore backing the additive Adapter Discovery Cache
                         (see list_adapters_cached()). Adapters are process-global, not
                         tenant-scoped data (established since Milestone 4), so no tenant_id
                         is threaded into the cache key.
        """
        # Map: adapter_id -> dict of (version_str -> BaseDocumentAdapter)
        self._adapters: dict[str, dict[str, BaseDocumentAdapter]] = {}
        self._cache_store = cache_store
        # Pending-invalidation flag (see _mark_discovery_cache_dirty / list_adapters_cached).
        self._discovery_cache_dirty = False

    @property
    def cache_store(self) -> ICacheStore | None:
        """Return the configured ICacheStore backing the Adapter Discovery Cache, or None if uncached."""
        return self._cache_store

    def _mark_discovery_cache_dirty(self) -> None:
        """Mark the Adapter Discovery Cache as stale after a registration change.

        register_adapter/unregister_adapter are synchronous — an established, tested contract
        relied on by DocumentAdapterLoader.load_and_register_all() and other sync call sites —
        while ICacheStore is async-only, so the actual cache entry cannot be deleted from
        within these methods without making them async. Rather than scheduling a background
        task (which left a real stale-read window between registration and the task actually
        running — the cache entry was not guaranteed to be gone by the time the very next
        read happened), this sets a plain synchronous flag with no event-loop dependency at
        all. list_adapters_cached() — the sole reader of this cache key — checks this flag
        FIRST, before ever consulting the cache, and applies the pending deletion synchronously
        with respect to that read. This guarantees no caller can ever observe a stale entry:
        correctness comes from ordering at the read site, not from timing of when the flag was
        set. This single code path covers both automatic discovery (DocumentAdapterLoader
        calls register_adapter for each discovered adapter) and explicit registration.
        """
        if self._cache_store is not None:
            self._discovery_cache_dirty = True

    def validate_adapter_metadata(self, metadata: AdapterMetadata) -> None:
        """Validate AdapterMetadata completeness and SemVer format.

        Args:
            metadata: AdapterMetadata instance to validate.

        Raises:
            DocumentAdapterError: If any metadata validation rule is violated.
        """
        if not metadata.adapter_id or not metadata.adapter_id.strip():
            raise DocumentAdapterError("Missing required metadata field: 'adapter_id' cannot be empty.")

        if not metadata.display_name or not metadata.display_name.strip():
            raise DocumentAdapterError("Missing required metadata field: 'display_name' cannot be empty.")

        if not metadata.vendor or not metadata.vendor.strip():
            raise DocumentAdapterError("Missing required metadata field: 'vendor' cannot be empty.")

        if not metadata.author or not metadata.author.strip():
            raise DocumentAdapterError("Missing required metadata field: 'author' cannot be empty.")

        if not metadata.license or not metadata.license.strip():
            raise DocumentAdapterError("Missing required metadata field: 'license' cannot be empty.")

        if not metadata.description or not metadata.description.strip():
            raise DocumentAdapterError("Missing required metadata field: 'description' cannot be empty.")

        # Validate SemVer version format
        try:
            parse_semver(metadata.version)
        except Exception as err:
            raise DocumentAdapterError(f"Invalid adapter version: {err}") from err

    def register_adapter(self, adapter: BaseDocumentAdapter | AdapterMetadata) -> BaseDocumentAdapter:
        """Register a BaseDocumentAdapter or AdapterMetadata instance.

        Args:
            adapter: BaseDocumentAdapter subclass instance or AdapterMetadata.

        Returns:
            The registered BaseDocumentAdapter instance.

        Raises:
            DocumentAdapterError: If contract validation fails or version is duplicate.
        """
        if isinstance(adapter, AdapterMetadata):
            adapter_obj: BaseDocumentAdapter = MetadataAdapterWrapper(adapter)
        elif isinstance(adapter, BaseDocumentAdapter):
            adapter_obj = adapter
        else:
            raise DocumentAdapterError(
                "Invalid adapter object: must inherit from BaseDocumentAdapter or be AdapterMetadata."
            )

        try:
            meta = adapter_obj.metadata
        except Exception as err:
            raise DocumentAdapterError(f"Failed to access adapter metadata: {err}") from err

        if not isinstance(meta, AdapterMetadata):
            raise DocumentAdapterError("Adapter metadata property must return an AdapterMetadata instance.")

        self.validate_adapter_metadata(meta)

        adapter_id = meta.adapter_id.strip()
        version = meta.version.strip()

        if adapter_id in self._adapters and version in self._adapters[adapter_id]:
            raise DocumentAdapterError(
                f"Duplicate adapter registration: '{adapter_id}' version '{version}' is already registered."
            )

        if adapter_id not in self._adapters:
            self._adapters[adapter_id] = {}

        self._adapters[adapter_id][version] = adapter_obj
        self._mark_discovery_cache_dirty()
        return adapter_obj

    def unregister_adapter(self, adapter_id: str, version: str | None = None) -> bool:
        """Unregister an adapter or specific adapter version from the registry.

        Args:
            adapter_id: Canonical adapter identifier string.
            version: Optional SemVer string. If None, unregisters all versions.

        Returns:
            True if unregistration succeeded, False if adapter_id/version was not found.
        """
        adapter_id = adapter_id.strip()
        if adapter_id not in self._adapters or not self._adapters[adapter_id]:
            return False

        if version is not None:
            version = version.strip()
            if version not in self._adapters[adapter_id]:
                return False
            del self._adapters[adapter_id][version]
            if not self._adapters[adapter_id]:
                del self._adapters[adapter_id]
            self._mark_discovery_cache_dirty()
            return True

        del self._adapters[adapter_id]
        self._mark_discovery_cache_dirty()
        return True

    def remove_adapter(self, adapter_id: str, version: str | None = None) -> bool:
        """Alias for unregister_adapter."""
        return self.unregister_adapter(adapter_id, version=version)

    def get_adapter(
        self,
        identifier_or_capability: str | AdapterCapability,
        version: str | None = None,
    ) -> BaseDocumentAdapter:
        """Retrieve a registered adapter by adapter_id or capability.

        If identifier_or_capability is an AdapterCapability (or string matching a capability enum),
        returns the latest adapter supporting that capability.
        If identifier_or_capability is an adapter_id string, returns that adapter (latest version if version is None).

        Args:
            identifier_or_capability: Adapter ID string or AdapterCapability.
            version: Optional SemVer string.

        Returns:
            BaseDocumentAdapter instance.

        Raises:
            AdapterNotFoundError: If no matching adapter is found.
        """
        # Check if identifier_or_capability is an AdapterCapability enum
        if isinstance(identifier_or_capability, AdapterCapability):
            return self.get_adapter_by_capability(identifier_or_capability)

        # Check if string matches an AdapterCapability value
        if isinstance(identifier_or_capability, str):
            cap_clean = identifier_or_capability.strip()
            try:
                cap_enum = AdapterCapability(cap_clean)
                matching = self.find_by_capability(cap_enum)
                if matching:
                    return matching[0]
            except ValueError:
                pass

            return self.get_adapter_by_id(cap_clean, version=version)

        raise AdapterNotFoundError(f"Invalid lookup target: {identifier_or_capability}")

    def get_adapter_by_id(self, adapter_id: str, version: str | None = None) -> BaseDocumentAdapter:
        """Retrieve a registered adapter by adapter_id and optional version.

        Args:
            adapter_id: Canonical adapter identifier string.
            version: Optional SemVer string. Resolves latest version if None.

        Returns:
            BaseDocumentAdapter instance.

        Raises:
            AdapterNotFoundError: If adapter_id or specified version is not found.
        """
        adapter_id = adapter_id.strip()
        if adapter_id not in self._adapters or not self._adapters[adapter_id]:
            raise AdapterNotFoundError(f"Adapter '{adapter_id}' not found in registry.")

        if version is not None:
            version = version.strip()
            if version not in self._adapters[adapter_id]:
                raise AdapterNotFoundError(f"Adapter '{adapter_id}' version '{version}' not found in registry.")
            return self._adapters[adapter_id][version]

        return self.get_latest_version(adapter_id)

    def get_adapter_by_capability(self, capability: AdapterCapability | str) -> BaseDocumentAdapter:
        """Retrieve the latest registered adapter supporting a specific capability.

        Args:
            capability: AdapterCapability enum or string.

        Returns:
            BaseDocumentAdapter instance.

        Raises:
            AdapterNotFoundError: If no adapter advertising the capability is found.
        """
        adapters = self.find_by_capability(capability)
        if not adapters:
            cap_name = capability.value if isinstance(capability, AdapterCapability) else capability
            raise AdapterNotFoundError(f"No registered adapter supports capability '{cap_name}'.")
        return adapters[0]

    def get_latest_version(self, adapter_id: str) -> BaseDocumentAdapter:
        """Retrieve the latest registered version of an adapter using SemVer resolution.

        Args:
            adapter_id: Canonical adapter identifier string.

        Returns:
            Latest BaseDocumentAdapter instance.

        Raises:
            AdapterNotFoundError: If adapter_id is not found.
        """
        adapter_id = adapter_id.strip()
        if adapter_id not in self._adapters or not self._adapters[adapter_id]:
            raise AdapterNotFoundError(f"Adapter '{adapter_id}' not found in registry.")

        versions = list(self._adapters[adapter_id].keys())
        sorted_versions = sorted(versions, key=lambda v: parse_semver(v))
        latest_ver = sorted_versions[-1]
        return self._adapters[adapter_id][latest_ver]

    def get_specific_version(self, adapter_id: str, version: str) -> BaseDocumentAdapter:
        """Retrieve a specific registered version of an adapter.

        Args:
            adapter_id: Canonical adapter identifier string.
            version: Specific SemVer string.

        Returns:
            BaseDocumentAdapter instance.

        Raises:
            AdapterNotFoundError: If adapter or version is not found.
        """
        return self.get_adapter_by_id(adapter_id, version=version)

    def get_adapter_metadata(self, adapter_id: str, version: str | None = None) -> AdapterMetadata:
        """Retrieve AdapterMetadata for a registered adapter.

        Args:
            adapter_id: Canonical adapter identifier string.
            version: Optional SemVer string.

        Returns:
            AdapterMetadata object.

        Raises:
            AdapterNotFoundError: If adapter is not found.
        """
        adapter_obj = self.get_adapter_by_id(adapter_id, version=version)
        return adapter_obj.metadata

    def find_by_capability(self, capability: AdapterCapability | str) -> list[BaseDocumentAdapter]:
        """Find all registered adapters advertising support for a specific capability.

        Returns latest version for each matching adapter ID.

        Args:
            capability: AdapterCapability enum or string.

        Returns:
            List of matching BaseDocumentAdapter instances.
        """
        cap_enum: AdapterCapability | None = None
        if isinstance(capability, AdapterCapability):
            cap_enum = capability
        elif isinstance(capability, str):
            try:
                cap_enum = AdapterCapability(capability.strip())
            except ValueError:
                return []

        if cap_enum is None:
            return []

        result: list[BaseDocumentAdapter] = []
        for adapter_id in self._adapters:
            latest = self.get_latest_version(adapter_id)
            if latest.supports_capability(cap_enum):
                result.append(latest)

        return result

    def find_by_operation(self, operation: DocumentOperationType | str) -> list[BaseDocumentAdapter]:
        """Find all registered adapters advertising support for a specific operation type.

        Args:
            operation: DocumentOperationType enum or string.

        Returns:
            List of matching BaseDocumentAdapter instances.
        """
        op_enum: DocumentOperationType | None = None
        if isinstance(operation, DocumentOperationType):
            op_enum = operation
        elif isinstance(operation, str):
            try:
                op_enum = DocumentOperationType(operation.strip())
            except ValueError:
                return []

        if op_enum is None:
            return []

        result: list[BaseDocumentAdapter] = []
        for adapter_id in self._adapters:
            latest = self.get_latest_version(adapter_id)
            if op_enum in latest.metadata.supported_operations:
                result.append(latest)

        return result

    def list_adapters(self) -> list[AdapterMetadata]:
        """Return AdapterMetadata objects for all registered adapters (IDocumentAdapterRegistry protocol).

        Returns latest version for each registered adapter ID.

        Returns:
            List of AdapterMetadata objects.
        """
        result: list[AdapterMetadata] = []
        for adapter_id in self._adapters:
            latest = self.get_latest_version(adapter_id)
            result.append(latest.metadata)
        return result

    async def list_adapters_cached(self) -> list[AdapterMetadata]:
        """Read-through cached variant of list_adapters(), backed by the Adapter Discovery Cache.

        Purely additive — does not replace or alter list_adapters(), which remains synchronous
        and uncached for existing callers. When cache_store is None, this simply delegates to
        list_adapters() with no caching behavior at all.

        Any pending invalidation from a prior register_adapter/unregister_adapter call (see
        _mark_discovery_cache_dirty) is applied here, before the cache is consulted — this is
        what actually guarantees a stale result can never be returned, regardless of how much
        time has passed since the registration change that triggered it.

        Returns:
            List of AdapterMetadata objects (same contents as list_adapters()).
        """
        if self._cache_store is None:
            return self.list_adapters()

        if self._discovery_cache_dirty:
            await self._cache_store.delete(self.DISCOVERY_CACHE_KEY)
            self._discovery_cache_dirty = False

        cached = await self._cache_store.get(self.DISCOVERY_CACHE_KEY)
        if cached is not None:
            return cast(list[AdapterMetadata], cached)

        result = self.list_adapters()
        await self._cache_store.set(self.DISCOVERY_CACHE_KEY, result, ttl_seconds=self.ADAPTER_CACHE_TTL_SECONDS)
        return result

    def list_all_adapter_versions(self) -> list[AdapterMetadata]:
        """Return AdapterMetadata objects for all registered versions of all adapters.

        Returns:
            List of AdapterMetadata objects.
        """
        result: list[AdapterMetadata] = []
        for _adapter_id, versions_map in self._adapters.items():
            for _ver_str, adapter_obj in versions_map.items():
                result.append(adapter_obj.metadata)
        return result


__all__ = ["DocumentAdapterRegistry", "MetadataAdapterWrapper"]
