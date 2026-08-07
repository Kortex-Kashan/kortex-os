"""
KORTEX Storage Engine Package.

Provides four distinct storage abstractions (IDataStore, IFileStore, IObjectStore, ICacheStore)
for relational data, local file system operations, object blob storage, and key-value caching.
"""

from __future__ import annotations

from kortex.engines.storage.interfaces import (
    ICacheStore,
    IDataStore,
    IEngineDiagnostics,
    IFileStore,
    IObjectStore,
)
from kortex.engines.storage.models import BucketConfig, FileMetadata, ObjectMetadata

__all__ = [
    "IDataStore",
    "IFileStore",
    "IObjectStore",
    "ICacheStore",
    "IEngineDiagnostics",
    "ObjectMetadata",
    "FileMetadata",
    "BucketConfig",
]
