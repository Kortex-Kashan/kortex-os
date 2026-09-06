"""`IOAuthProvider` — the provider-agnostic OAuth/OIDC seam (Phase A)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


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
