"""
KORTEX Storage Engine Core Implementation.

Acts as the facade for all 4 storage abstractions (IDataStore, IFileStore, IObjectStore, ICacheStore),
registers capabilities with the Kernel, and implements the Common Diagnostics Interface (IEngineDiagnostics).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.storage.interfaces import (
    ICacheStore,
    IDataStore,
    IEngineDiagnostics,
    IFileStore,
    IObjectStore,
)
from kortex.engines.storage.stores.cache_store import MemoryCacheStore
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.storage.stores.file_store import LocalFileStore
from kortex.engines.storage.stores.object_store import BlobObjectStore

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.storage")


class StorageEngine(BaseEngine, IEngineDiagnostics):
    """KORTEX Storage Engine Facade providing unified access to all 4 storage stores."""

    def __init__(self, base_directory: str = "storage_data") -> None:
        """Initialize StorageEngine instance.

        Args:
            base_directory: Base workspace relative directory for file and object storage.
        """
        super().__init__()
        self._base_directory = base_directory
        self._data_store: Optional[IDataStore] = None
        self._file_store: Optional[IFileStore] = None
        self._object_store: Optional[IObjectStore] = None
        self._cache_store: Optional[ICacheStore] = None
        self._registered_capabilities: List[str] = [
            "kortex.storage.data.session",
            "kortex.storage.file.store",
            "kortex.storage.object.put",
            "kortex.storage.cache.set",
        ]
        self._metrics: Dict[str, Any] = {
            "files_stored": 0,
            "objects_stored": 0,
            "cache_ops": 0,
            "errors": 0,
        }

    @property
    def name(self) -> str:
        """Unique identifier name for this engine."""
        return "storage"

    @property
    def dependencies(self) -> List[str]:
        """Names of prerequisite foundation engines."""
        return ["configuration", "registry"]

    @property
    def data(self) -> IDataStore:
        """Access the relational data store (IDataStore)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._data_store is not None
        return self._data_store

    @property
    def file(self) -> IFileStore:
        """Access the sandboxed local file store (IFileStore)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._file_store is not None
        return self._file_store

    @property
    def object(self) -> IObjectStore:
        """Access the binary object store (IObjectStore)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._object_store is not None
        return self._object_store

    @property
    def cache(self) -> ICacheStore:
        """Access the in-memory cache store (ICacheStore)."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        assert self._cache_store is not None
        return self._cache_store

    # -- Lifecycle Implementation -------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine stores and register capabilities with Kernel."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Storage Engine...")

        try:
            # Instantiate 4 storage stores
            self._data_store = RelationalDataStore(kernel.db)
            self._file_store = LocalFileStore(self._base_directory)
            self._object_store = BlobObjectStore(self._file_store)
            self._cache_store = MemoryCacheStore()

            # Register capabilities with Kernel
            kernel.register_capability(
                name="kortex.storage.data.session",
                description="Acquire relational database AsyncSession",
                provider=self.name,
                handler=self._data_store.get_session,
            )
            kernel.register_capability(
                name="kortex.storage.file.store",
                description="Store file in sandboxed file system",
                provider=self.name,
                handler=self._file_store.write_file,
            )
            kernel.register_capability(
                name="kortex.storage.object.put",
                description="Store binary blob object in container bucket",
                provider=self.name,
                handler=self._object_store.put_object,
            )
            kernel.register_capability(
                name="kortex.storage.cache.set",
                description="Store key-value entry in ephemeral cache",
                provider=self.name,
                handler=self._cache_store.set,
            )

            self._set_state(EngineState.READY)
            self.logger.info("Storage Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Storage Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Storage Engine is RUNNING.")

    async def stop(self) -> None:
        """Gracefully shut down engine stores."""
        if self._state in (EngineState.STOPPED, EngineState.UNINITIALIZED):
            return

        self._set_state(EngineState.STOPPING)
        self.logger.info("Stopping Storage Engine...")

        if self._cache_store:
            await self._cache_store.clear()

        self._set_state(EngineState.STOPPED)
        self.logger.info("Storage Engine stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Perform diagnostic health check."""
        return self.health()

    # -- Common Diagnostics Interface (IEngineDiagnostics) -------------------

    def health(self) -> Dict[str, Any]:
        """Return diagnostic health checks."""
        return {
            "engine": self.name,
            "status": self._state.value,
            "healthy": self._state in (EngineState.READY, EngineState.RUNNING),
            "stores": {
                "data_store": self._data_store is not None,
                "file_store": self._file_store is not None,
                "object_store": self._object_store is not None,
                "cache_store": self._cache_store is not None,
            },
        }

    def metrics(self) -> Dict[str, Any]:
        """Return operational runtime metrics."""
        return dict(self._metrics)

    def diagnostics(self) -> Dict[str, Any]:
        """Return detailed technical diagnostics."""
        return {
            "engine": self.name,
            "version": self.version(),
            "state": self._state.value,
            "base_directory": self._base_directory,
            "capabilities": self.capabilities(),
            "metrics": self.metrics(),
        }

    def status(self) -> str:
        """Return current operational state name string."""
        return self._state.value

    def version(self) -> str:
        """Return semantic version string."""
        return "1.0.0"

    def capabilities(self) -> List[str]:
        """Return list of capability strings registered by this engine."""
        return list(self._registered_capabilities)
