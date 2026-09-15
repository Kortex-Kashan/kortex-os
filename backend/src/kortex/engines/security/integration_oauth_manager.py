"""Integration OAuth Manager (Integration Hub M2).

Owns the full lifecycle of a connected third-party API integration's OAuth
credential — GitHub today — on behalf of `SecurityEngine`: authorization
begin/complete, `SecretStore` write/refresh coordination, disconnect, and
credential-status lookup. Deliberately does **not** know about
`RegistryEngine`/`ConnectorEngine` capability lifecycle — that stays a
`ConnectorEngine` concern (`kortex.connector.integration.disconnect`),
consistent with the M2 architecture correction's engine-ownership split.

Reuses, rather than duplicates:
- `AuthenticationManager.issue_oauth_state`/`verify_oauth_state` for the
  signed `state`'s cryptographic integrity/expiry (Ed25519, same signing key
  as every other OAuth flow on this platform).
- `oauth_state_codec.encode_oauth_state`/`decode_oauth_state` for the wire
  format, shared with `SecurityEngine`'s own Phase A flows.
- `SecretStore.get_secret`/`put_secret` for the encrypted credential
  material itself.

What this module adds that did not exist before (Integration Hub M2 is new
work, not a refactor of the above):
- A persisted, atomically single-use `state` nonce ledger
  (`OAuthStateNonceRecord`) — `verify_oauth_state` alone only proves a
  `state` is authentic and unexpired, not that it hasn't already been
  redeemed once.
- `(tenant_id, profile_id)` as the authoritative credential identity
  boundary (`OAuthIntegrationCredentialRecord`), with an opaque
  `SecretStore` handle as a mere lookup key, never itself trusted as proof
  of ownership.
- GitHub's expiring-token lifecycle: proactive expiry checks, refresh with
  mandatory refresh-token rotation, per-`(tenant_id, profile_id)` refresh
  synchronization, and an explicit `REAUTHORIZATION_REQUIRED` status when a
  refresh token is missing, expired, or rejected by the provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets as secrets_module
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.exceptions import (
    IntegrationCredentialNotFoundError,
    IntegrationOAuthReplayError,
    OAuthProviderNotConfiguredError,
    OAuthRefreshInvalidError,
    OAuthStateError,
)
from kortex.engines.security.models import (
    IntegrationCredentialStatus,
    IntegrationTokenSet,
    OAuthIntegrationCredentialRecord,
    OAuthStateNonceRecord,
)
from kortex.engines.security.oauth_state_codec import OAuthStateDecodeError, decode_oauth_state, encode_oauth_state

if TYPE_CHECKING:
    from kortex.engines.security.auth import AuthenticationManager
    from kortex.engines.security.oauth.base import IIntegrationOAuthProvider
    from kortex.engines.security.secrets import SecretStore
    from kortex.engines.storage.interfaces import IDataStore

logger = logging.getLogger("kortex.engines.security.integration_oauth")

_STATE_INTENT = "connector_link"
_OAUTH_JSON_MARKER = "kortex_integration_oauth_v1"
_EXPIRY_SAFETY_MARGIN = timedelta(seconds=60)


def _fingerprint(refresh_token: str) -> str:
    """Non-secret, non-reversible fingerprint of a refresh token, used only
    for the optimistic compare-and-swap on rotation. Never usable to
    reconstruct or authenticate with the token itself."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def _token_set_to_json(token_set: IntegrationTokenSet, provider: str) -> str:
    return json.dumps(
        {
            "marker": _OAUTH_JSON_MARKER,
            "provider": provider,
            "access_token": token_set.access_token,
            "refresh_token": token_set.refresh_token,
            "access_token_expires_at": (
                token_set.access_token_expires_at.isoformat() if token_set.access_token_expires_at else None
            ),
            "refresh_token_expires_at": (
                token_set.refresh_token_expires_at.isoformat() if token_set.refresh_token_expires_at else None
            ),
            "scope": token_set.scope,
            "token_type": token_set.token_type,
        },
        separators=(",", ":"),
    )


def _token_set_from_json(raw: str) -> IntegrationTokenSet | None:
    """Returns `None` (never raises) for anything that isn't one of this
    module's own OAuth-managed JSON blobs — the caller's contract is "pass a
    non-OAuth secret through unchanged", not "fail on it"."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("marker") != _OAUTH_JSON_MARKER:
        return None
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    return IntegrationTokenSet(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        access_token_expires_at=(
            datetime.fromisoformat(payload["access_token_expires_at"])
            if payload.get("access_token_expires_at")
            else None
        ),
        refresh_token_expires_at=(
            datetime.fromisoformat(payload["refresh_token_expires_at"])
            if payload.get("refresh_token_expires_at")
            else None
        ),
        scope=payload.get("scope"),
        token_type=payload.get("token_type"),
    )


class IntegrationOAuthManager:
    """Owns Integration Hub M2's OAuth credential lifecycle. See module docstring."""

    def __init__(
        self,
        data_store: IDataStore,
        secret_store: SecretStore,
        auth_manager: AuthenticationManager,
        providers: dict[str, IIntegrationOAuthProvider],
    ) -> None:
        self._data_store = data_store
        self._secret_store = secret_store
        self._auth_manager = auth_manager
        self._providers = providers
        # Primary refresh-synchronization guard (KORTEX is local-first,
        # single-backend-process — ADR #001). Keyed by (tenant_id,
        # profile_id); created lazily, never removed (bounded by the number
        # of ever-connected profiles, matching the existing precedent of
        # small long-lived per-key state elsewhere in this codebase).
        self._refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # -- Provider / lock resolution -------------------------------------------

    def _require_provider(self, provider: str) -> IIntegrationOAuthProvider:
        instance = self._providers.get(provider)
        if instance is None:
            raise OAuthProviderNotConfiguredError(f"The '{provider}' integration is not configured.")
        return instance

    def _lock_for(self, tenant_id: str, profile_id: str) -> asyncio.Lock:
        key = (tenant_id, profile_id)
        lock = self._refresh_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._refresh_locks[key] = lock
        return lock

    # -- Begin / complete authorization ---------------------------------------

    async def begin_authorization(
        self,
        provider: str,
        tenant_id: str,
        profile_id: str,
        redirect_uri: str,
        principal_id: str | None = None,
        principal_type: str | None = None,
    ) -> dict[str, str]:
        """Start linking `provider` to `profile_id` for `tenant_id`.

        `principal_id`/`principal_type` (the calling `SecurityPrincipal`,
        supplied by `SecurityEngine`'s capability handler from the
        dispatcher-injected execution context — never a caller-supplied
        parameter) are signed into the `state` alongside `tenant_id`/
        `profile_id`/`provider`, mirroring Phase A's `oauth_link_begin`
        precedent exactly: the completing call must present the identical
        identity, or `complete_authorization` refuses it outright (see
        there for why this is a hard requirement here, not best-effort).
        """
        provider_instance = self._require_provider(provider)
        state = await self._auth_manager.issue_oauth_state(
            provider=provider,
            intent=_STATE_INTENT,
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_type=principal_type,
            profile_id=profile_id,
        )

        async def _persist_nonce(session: AsyncSession) -> None:
            session.add(
                OAuthStateNonceRecord(
                    id=str(uuid.uuid4()),
                    nonce=state.nonce,
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    provider=provider,
                    expires_at=state.expires_at_utc,
                    consumed_at=None,
                )
            )
            await session.flush()

        await self._data_store.execute_in_transaction(_persist_nonce)

        encoded_state = encode_oauth_state(state)
        authorization_url = provider_instance.authorization_url(state=encoded_state, redirect_uri=redirect_uri)
        return {"authorization_url": authorization_url, "state": encoded_state}

    async def complete_authorization(
        self,
        provider: str,
        profile_id: str,
        code: str,
        state: str,
        redirect_uri: str,
        tenant_id: str,
        principal_id: str | None = None,
        principal_type: str | None = None,
    ) -> dict[str, Any]:
        """Complete linking `provider` to `profile_id`.

        `tenant_id`/`principal_id`/`principal_type` are the CALLER's own
        verified identity (from the dispatcher-injected execution context,
        exactly like `begin_authorization` above) — required to match the
        signed `state`'s own embedded claims exactly, hard-failing
        (`OAuthStateError`) otherwise. This is not "best-effort": it is the
        same hard binding `SecurityEngine.oauth_link_complete_capability`
        already enforces for Phase A's `intent="link"` over the identical
        desktop deep-link transport, so there is direct, already-shipped
        precedent that this platform's session context survives that
        round-trip reliably — no reason to weaken the check for this
        `intent="connector_link"` sibling.
        """
        try:
            decoded_state = decode_oauth_state(state)
        except OAuthStateDecodeError as exc:
            raise OAuthStateError("This authorization attempt is invalid or has expired.") from exc

        verified_state = await self._auth_manager.verify_oauth_state(decoded_state)
        if (
            verified_state.intent != _STATE_INTENT
            or verified_state.provider != provider
            or verified_state.profile_id != profile_id
            or verified_state.tenant_id != tenant_id
            or verified_state.principal_id != principal_id
            or verified_state.principal_type != principal_type
        ):
            raise OAuthStateError("This authorization attempt is invalid or has expired.")

        # Atomic, replay-proof single-use consumption. `verify_oauth_state`
        # above only proves the claims are authentic and unexpired; this is
        # the separate guarantee that this exact `state` has never been
        # redeemed before. A single `UPDATE ... WHERE consumed_at IS NULL`
        # is atomic under ordinary DB row-update semantics (SQLite and
        # Postgres both serialize a single-row UPDATE), so of two concurrent
        # callback deliveries for the same `state`, exactly one `UPDATE`
        # affects a row and wins — there is no separate check-then-mark step
        # for a race to land between.
        async def _consume_nonce(session: AsyncSession) -> int:
            stmt = (
                update(OAuthStateNonceRecord)
                .where(
                    OAuthStateNonceRecord.nonce == verified_state.nonce,
                    OAuthStateNonceRecord.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.now(UTC))
            )
            result = await session.execute(stmt)
            await session.flush()
            return result.rowcount or 0

        consumed_count = await self._data_store.execute_in_transaction(_consume_nonce)
        if consumed_count != 1:
            raise IntegrationOAuthReplayError("This authorization attempt has already been used or is invalid.")

        provider_instance = self._require_provider(provider)
        token_set = await provider_instance.exchange_code(code=code, redirect_uri=redirect_uri)

        secret_handle = f"integration-oauth:{secrets_module.token_urlsafe(24)}"
        await self._secret_store.put_secret(secret_handle, tenant_id, _token_set_to_json(token_set, provider))

        connected_at = datetime.now(UTC)
        refresh_fingerprint = _fingerprint(token_set.refresh_token) if token_set.refresh_token else None

        async def _upsert_record(session: AsyncSession) -> None:
            stmt = select(OAuthIntegrationCredentialRecord).where(
                OAuthIntegrationCredentialRecord.tenant_id == tenant_id,
                OAuthIntegrationCredentialRecord.profile_id == profile_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                existing.provider = provider
                existing.secret_handle = secret_handle
                existing.status = IntegrationCredentialStatus.CONNECTED
                existing.scope = token_set.scope
                existing.connected_at = connected_at
                existing.access_token_expires_at = token_set.access_token_expires_at
                existing.refresh_token_expires_at = token_set.refresh_token_expires_at
                existing.refresh_token_fingerprint = refresh_fingerprint
            else:
                session.add(
                    OAuthIntegrationCredentialRecord(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        profile_id=profile_id,
                        provider=provider,
                        secret_handle=secret_handle,
                        status=IntegrationCredentialStatus.CONNECTED,
                        scope=token_set.scope,
                        connected_at=connected_at,
                        access_token_expires_at=token_set.access_token_expires_at,
                        refresh_token_expires_at=token_set.refresh_token_expires_at,
                        refresh_token_fingerprint=refresh_fingerprint,
                    )
                )
            await session.flush()

        await self._data_store.execute_in_transaction(_upsert_record)

        return {"connected": True, "secret_handle": secret_handle}

    # -- Resolution (wired as ConnectorEngine's secret_resolver) --------------

    async def _load_record(self, tenant_id: str, profile_id: str) -> OAuthIntegrationCredentialRecord | None:
        async def _action(session: AsyncSession) -> OAuthIntegrationCredentialRecord | None:
            stmt = select(OAuthIntegrationCredentialRecord).where(
                OAuthIntegrationCredentialRecord.tenant_id == tenant_id,
                OAuthIntegrationCredentialRecord.profile_id == profile_id,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

        return await self._data_store.execute_in_transaction(_action)

    @staticmethod
    def _is_expired(expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        now = datetime.now(UTC)
        expires_at_aware = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
        return now >= (expires_at_aware - _EXPIRY_SAFETY_MARGIN)

    async def _mark_reauthorization_required(self, tenant_id: str, profile_id: str) -> None:
        async def _action(session: AsyncSession) -> None:
            stmt = select(OAuthIntegrationCredentialRecord).where(
                OAuthIntegrationCredentialRecord.tenant_id == tenant_id,
                OAuthIntegrationCredentialRecord.profile_id == profile_id,
            )
            record = (await session.execute(stmt)).scalar_one_or_none()
            if record is not None:
                record.status = IntegrationCredentialStatus.REAUTHORIZATION_REQUIRED
            await session.flush()

        await self._data_store.execute_in_transaction(_action)

    async def resolve_access_token(self, secret_handle: str, tenant_id: str, profile_id: str) -> str | None:
        """Resolve a `ConnectorProfile.secret_handle` to a usable bearer
        token — wired as `ConnectorEngine`'s `secret_resolver`.

        Binds resolution to `(tenant_id, profile_id)`, never to possession
        of `secret_handle` alone: a plain, non-OAuth secret (e.g. MCP) is
        returned unchanged (this manager has no opinion on it); an
        OAuth-managed secret is only ever returned after confirming the
        looked-up `OAuthIntegrationCredentialRecord` for this exact
        `(tenant_id, profile_id)` actually owns `secret_handle` — never the
        reverse (trusting whatever handle the caller happened to pass).
        """
        # A missing/undecryptable secret raises (`SecretNotFoundError`/
        # `SecretDecryptionError`) exactly as the default, non-OAuth
        # resolver (`SecurityEngine.get_secret`) already does — unchanged,
        # not this manager's concern to reinterpret.
        raw = await self._secret_store.get_secret(secret_handle, tenant_id)

        token_set = _token_set_from_json(raw)
        if token_set is None:
            return raw  # Plain non-OAuth secret (MCP, etc.) — pass through unchanged.

        record = await self._load_record(tenant_id, profile_id)
        if record is None or record.secret_handle != secret_handle:
            logger.warning(
                "Integration credential binding mismatch for profile %s in tenant %s.", profile_id, tenant_id
            )
            return None

        if record.status != IntegrationCredentialStatus.CONNECTED:
            return None

        if not self._is_expired(record.access_token_expires_at):
            return token_set.access_token

        async with self._lock_for(tenant_id, profile_id):
            # Double-checked: a concurrent caller may have already refreshed
            # (or the expiring token may have been a stale read) while this
            # one waited for the lock.
            record = await self._load_record(tenant_id, profile_id)
            if record is None or record.status != IntegrationCredentialStatus.CONNECTED:
                return None
            if not self._is_expired(record.access_token_expires_at):
                current_raw = await self._secret_store.get_secret(record.secret_handle, tenant_id)
                current_token_set = _token_set_from_json(current_raw)
                return current_token_set.access_token if current_token_set else None

            if record.refresh_token_expires_at is not None and self._is_expired(record.refresh_token_expires_at):
                await self._mark_reauthorization_required(tenant_id, profile_id)
                return None

            current_raw = await self._secret_store.get_secret(record.secret_handle, tenant_id)
            current_token_set = _token_set_from_json(current_raw)
            if current_token_set is None or not current_token_set.refresh_token:
                await self._mark_reauthorization_required(tenant_id, profile_id)
                return None

            provider_instance = self._require_provider(record.provider)
            used_fingerprint = _fingerprint(current_token_set.refresh_token)
            try:
                new_token_set = await provider_instance.refresh(current_token_set.refresh_token)
            except OAuthRefreshInvalidError:
                await self._mark_reauthorization_required(tenant_id, profile_id)
                return None

            new_fingerprint = _fingerprint(new_token_set.refresh_token) if new_token_set.refresh_token else None

            async def _persist_rotation(session: AsyncSession) -> int:
                stmt = (
                    update(OAuthIntegrationCredentialRecord)
                    .where(
                        OAuthIntegrationCredentialRecord.tenant_id == tenant_id,
                        OAuthIntegrationCredentialRecord.profile_id == profile_id,
                        OAuthIntegrationCredentialRecord.refresh_token_fingerprint == used_fingerprint,
                    )
                    .values(
                        access_token_expires_at=new_token_set.access_token_expires_at,
                        refresh_token_expires_at=new_token_set.refresh_token_expires_at,
                        refresh_token_fingerprint=new_fingerprint,
                    )
                )
                result = await session.execute(stmt)
                await session.flush()
                return result.rowcount or 0

            rows_updated = await self._data_store.execute_in_transaction(_persist_rotation)
            if rows_updated == 1:
                # This call's rotation won — persist the new secret material
                # (old refresh token is now dead server-side, per GitHub's
                # own rotation contract; never retried or reused).
                await self._secret_store.put_secret(
                    record.secret_handle, tenant_id, _token_set_to_json(new_token_set, record.provider)
                )
                return new_token_set.access_token

            # Another writer's rotation landed first (multi-process
            # defense-in-depth path — the in-process lock above already
            # prevents this within a single process). Re-read and return
            # whatever is now current rather than overwriting a newer
            # rotation with this call's now-stale result.
            refreshed_record = await self._load_record(tenant_id, profile_id)
            if refreshed_record is None:
                return None
            refreshed_raw = await self._secret_store.get_secret(refreshed_record.secret_handle, tenant_id)
            refreshed_token_set = _token_set_from_json(refreshed_raw)
            return refreshed_token_set.access_token if refreshed_token_set else None

    # -- Status / disconnect ---------------------------------------------------

    async def get_status(self, provider: str, tenant_id: str, profile_id: str) -> dict[str, Any]:
        record = await self._load_record(tenant_id, profile_id)
        if record is None or record.provider != provider:
            raise IntegrationCredentialNotFoundError(
                f"No '{provider}' integration is connected for this profile."
            )
        return {
            "connected": record.status == IntegrationCredentialStatus.CONNECTED,
            "status": record.status,
            "scopes": record.scope,
            "connected_at": record.connected_at,
        }

    async def disconnect_credential(self, tenant_id: str, profile_id: str, provider: str) -> bool:
        """Revoke and remove the stored credential for `(tenant_id,
        profile_id)`. Idempotent: returns `False` (no-op) if nothing was
        connected, rather than raising — matching `SecretStore.delete_secret`'s
        own "no entry" precedent. Called from `ConnectorEngine`'s
        `kortex.connector.integration.disconnect` handler (§F of the M2
        plan) strictly *after* that handler has already unregistered the
        profile's RegistryEngine capabilities — this method only ever
        touches credential state, never capability lifecycle.
        """
        record = await self._load_record(tenant_id, profile_id)
        if record is None or record.provider != provider:
            return False

        await self._secret_store.delete_secret(record.secret_handle, tenant_id)

        async def _mark_revoked(session: AsyncSession) -> None:
            stmt = select(OAuthIntegrationCredentialRecord).where(
                OAuthIntegrationCredentialRecord.tenant_id == tenant_id,
                OAuthIntegrationCredentialRecord.profile_id == profile_id,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                existing.status = IntegrationCredentialStatus.REVOKED
            await session.flush()

        await self._data_store.execute_in_transaction(_mark_revoked)
        return True
