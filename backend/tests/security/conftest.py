"""Shared fixtures for the Phase 5 security tests.

Every fixture here builds a genuinely isolated stack: its own SQLite database
file under `tmp_path`, its own `StorageEngine` root, and its own `SecretStore`
master key. `Kernel()` otherwise resolves `DatabaseEngineManager()` to the
process-wide default database (the developer's real local `kortex_local.db`),
which would let one test's CA leak into the next test's "no CA present"
assertion — and would let a test suite write to real user data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kortex.core.kernel import Kernel
from kortex.engines.security.pki import KortexPki
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.engine import StorageEngine

TEST_MASTER_KEY = b"\x31" * 32

# Generating a fresh RSA-4096 CA key costs seconds, and every test that needs
# a CA pays it. One genuine RSA-4096 key is generated per test session and
# reused as the CA key across tests. This is a speed measure only: it is a
# real 4096-bit key produced by the real generator, so assertions about the
# CA's algorithm, size, extensions and signatures remain meaningful. Leaf keys
# are deliberately NOT cached — tests that rely on two CSRs being different
# would silently stop testing anything if they were.
_CACHED_CA_KEY: rsa.RSAPrivateKey | None = None


@pytest.fixture(autouse=True)
def _fast_ca_keygen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reuse one real RSA-4096 key for CA generation across the session."""
    real_generate = rsa.generate_private_key

    def _generate(public_exponent: int, key_size: int, backend: object = None) -> rsa.RSAPrivateKey:
        global _CACHED_CA_KEY
        if key_size != 4096:
            return real_generate(public_exponent=public_exponent, key_size=key_size)
        if _CACHED_CA_KEY is None:
            _CACHED_CA_KEY = real_generate(public_exponent=public_exponent, key_size=key_size)
        return _CACHED_CA_KEY

    monkeypatch.setattr("kortex.engines.security.pki.rsa.generate_private_key", _generate)


@dataclass
class Phase5Stack:
    """A fully isolated Security Engine stack for one test."""

    kernel: Kernel
    storage_engine: StorageEngine
    secret_store: SecretStore
    pki: KortexPki
    tmp_path: Path

    @property
    def data_store(self) -> object:
        return self.storage_engine.data


async def build_phase5_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "phase5") -> Phase5Stack:
    """Build an isolated Kernel + StorageEngine + SecretStore + PKI."""
    database_path = tmp_path / f"{name}.db"
    monkeypatch.setenv("KORTEX_DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}")

    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / f"{name}_storage"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()

    secret_store = SecretStore(
        data_store=storage_engine.data, crypto_provider=LocalCrypto(), master_key=TEST_MASTER_KEY
    )
    return Phase5Stack(
        kernel=kernel,
        storage_engine=storage_engine,
        secret_store=secret_store,
        pki=KortexPki(secret_store),
        tmp_path=tmp_path,
    )


@pytest.fixture
async def phase5_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Phase5Stack]:
    """An isolated Phase 5 stack, torn down cleanly after the test."""
    stack = await build_phase5_stack(tmp_path, monkeypatch)
    try:
        yield stack
    finally:
        await stack.kernel.db.disconnect()
