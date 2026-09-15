"""`IOAuthProvider` — the provider-agnostic OAuth/OIDC seam (Phase A)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from kortex.engines.security.models import IntegrationTokenSet


class OAuthUserInfo(BaseModel):
    """The minimum identity a provider's userinfo endpoint must supply.

    `subject` is the provider's own stable, unique identifier for the
    authenticated external account (Google's `sub`, Microsoft Graph's
    `id`) — never the email, which some providers allow a user to change.
    """

    subject: str = Field(min_length=1)
    email: str | None = None


@runtime_checkable
class IOAuthProvider(Protocol):
    """Provider-agnostic OAuth 2.0 authorization-code flow abstraction."""

    provider_id: str

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Build the provider's authorization redirect URL for `state`/`redirect_uri`."""
        ...

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthUserInfo:
        """Exchange an authorization `code` for the authenticated external
        identity. Raises on any exchange/verification failure — never
        returns a partially-populated or guessed identity."""
        ...


@runtime_checkable
class IIntegrationOAuthProvider(Protocol):
    """Provider-agnostic OAuth 2.0 authorization-code flow abstraction for a
    connected third-party *integration* (Integration Hub M2) — distinct from
    `IOAuthProvider` above, which authenticates a KORTEX sign-in and returns
    only an identity (`OAuthUserInfo`). An integration needs a full,
    refreshable token set to make later API calls on the tenant's behalf,
    returned as `IntegrationTokenSet` (`kortex.engines.security.models`).

    Implementations (e.g. `GitHubIntegrationOAuthProvider`) are owned by
    `IntegrationOAuthManager`, never by `ConnectorEngine` — see the M2
    architecture correction requiring `IntegrationOAuthManager` to live under
    the `SecurityEngine` boundary.
    """

    provider_id: str

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Build the provider's authorization redirect URL for `state`/`redirect_uri`."""
        ...

    async def exchange_code(self, code: str, redirect_uri: str) -> IntegrationTokenSet:
        """Exchange an authorization `code` for a full token set. Raises
        `OAuthExchangeError` on any exchange failure — never returns a
        partially-populated or guessed token set."""
        ...

    async def refresh(self, refresh_token: str) -> IntegrationTokenSet:
        """Exchange a `refresh_token` for a new token set.

        Raises `OAuthRefreshInvalidError` specifically when the provider
        rejects the refresh token itself (e.g. GitHub's `bad_refresh_token`)
        — the caller (`IntegrationOAuthManager`) relies on this exact
        exception type to distinguish "reauthorization is required" from a
        transient network/provider failure, which is not fatal to the
        credential and should simply be retried later.
        """
        ...
