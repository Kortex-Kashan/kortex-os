"""
KORTEX Security Engine administrative CLI (Phase 5).

Provides the two explicitly-administrative PKI operations defined by
`docs/architecture/phase5_locked_architecture_spec.md` S11-S12:

    python -m kortex.engines.security.cli pki init
    python -m kortex.engines.security.cli pki issue-server --hostname <hostname>

Both are deliberately manual. Neither is invoked by the application's boot
path: a CA that materialized automatically on first start would mean an
operator could never tell a genuine first-time initialization apart from a
silent re-initialization that had just orphaned every issued certificate.

This module prints certificate subjects, fingerprints, and validity windows.
It never prints a private key, and the raw enrollment tokens handled elsewhere
in Phase 5 are not produced here at all.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from kortex.core.kernel import Kernel
from kortex.engines.security.exceptions import CaAlreadyExistsError, PkiError
from kortex.engines.security.pki import (
    DEFAULT_CA_CERTIFICATE_PATH,
    KortexPki,
    certificate_fingerprint,
)
from kortex.engines.security.providers.local_crypto import LocalCrypto
from kortex.engines.security.secrets import SecretStore
from kortex.engines.storage.engine import StorageEngine

_MASTER_KEY_ENV = "KORTEX_MASTER_KEY"
_STORAGE_ROOT_ENV = "KORTEX_STORAGE_ROOT"


async def _build_pki() -> tuple[Kernel, KortexPki]:
    """Construct the same SecretStore-backed PKI the running backend uses.

    Resolves the master key from `KORTEX_MASTER_KEY` exactly as
    `SecurityEngine` does, so the CLI writes secrets the server can later read
    — a CLI with its own key derivation would silently produce a CA the
    Gateway could never decrypt.
    """
    raw_key = os.environ.get(_MASTER_KEY_ENV)
    if not raw_key:
        raise SystemExit(f"{_MASTER_KEY_ENV} is not set. The PKI CLI cannot open the SecretStore without it.")
    master_key = SecretStore.decode_master_key(raw_key)

    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=os.environ.get(_STORAGE_ROOT_ENV) or "storage_data")
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()

    secret_store = SecretStore(data_store=storage_engine.data, crypto_provider=LocalCrypto(), master_key=master_key)
    return kernel, KortexPki(secret_store)


async def _pki_init(certificate_path: Path | None) -> int:
    kernel, pki = await _build_pki()
    try:
        certificate = await pki.initialize_ca(certificate_path=certificate_path)
    except CaAlreadyExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except PkiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        await kernel.db.disconnect()

    target = certificate_path or DEFAULT_CA_CERTIFICATE_PATH
    print("KORTEX Internal Root CA initialized.")
    print(f"  Subject:      {certificate.subject.rfc4514_string()}")
    print(f"  Serial:       {certificate.serial_number:x}")
    print(f"  SHA-256:      {certificate_fingerprint(certificate)}")
    print(f"  Not before:   {certificate.not_valid_before_utc.isoformat()}")
    print(f"  Not after:    {certificate.not_valid_after_utc.isoformat()}")
    print(f"  Public cert:  {target}")
    print("  Private key:  stored in SecretStore (system/pki/ca_private_key); not written to disk.")
    return 0


async def _pki_issue_server(hostname: str, ip_address: str | None) -> int:
    kernel, pki = await _build_pki()
    try:
        certificate, _pem = await pki.issue_server_certificate(hostname, ip_address=ip_address)
    except PkiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        await kernel.db.disconnect()

    print("KORTEX Gateway server certificate issued.")
    print(f"  Subject:      {certificate.subject.rfc4514_string()}")
    print(f"  SAN DNS:      {hostname}")
    if ip_address:
        print(f"  SAN IP:       {ip_address}")
    print(f"  Serial:       {certificate.serial_number:x}")
    print(f"  SHA-256:      {certificate_fingerprint(certificate)}")
    print(f"  Not after:    {certificate.not_valid_after_utc.isoformat()}")
    print("  Private key:  stored in SecretStore (system/pki/gateway_server_private_key).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kortex.engines.security.cli",
        description="KORTEX Security Engine administrative commands.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    pki_parser = subcommands.add_parser("pki", help="Internal PKI administration.")
    pki_subcommands = pki_parser.add_subparsers(dest="pki_command", required=True)

    init_parser = pki_subcommands.add_parser("init", help="Generate the KORTEX Internal Root CA.")
    init_parser.add_argument(
        "--certificate-path",
        type=Path,
        default=None,
        help=f"Where to write the CA public certificate (default: {DEFAULT_CA_CERTIFICATE_PATH}).",
    )

    issue_parser = pki_subcommands.add_parser("issue-server", help="Issue the Gateway server certificate.")
    issue_parser.add_argument("--hostname", required=True, help="Gateway hostname for the Subject CN and SAN.")
    issue_parser.add_argument("--ip", default=None, help="Optional IP address to add to the SAN.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "pki":
        if args.pki_command == "init":
            return asyncio.run(_pki_init(args.certificate_path))
        if args.pki_command == "issue-server":
            return asyncio.run(_pki_issue_server(args.hostname, args.ip))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
