"""Google OAuth 2.0 / OIDC sign-in provider (Phase A).

Uses the standard authorization-code flow and Google's own userinfo
endpoint (not independent ID-token/JWKS verification) — the access token
is exchanged and the identity fetched directly over HTTPS from Google's own
servers, so TLS server authentication is the trust anchor, exactly as a
normal OAuth "login with Google" integration relies on. This intentionally
avoids adding a JWT/JWKS-verification dependency for a single, narrow use.
"""

from __future__ import annotations

import urllib.parse

import httpx

from kortex.engines.security.exceptions import OAuthExchangeError
from kortex.engines.security.oauth.base import OAuthUserInfo

_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
_DEFAULT_TIMEOUT_SECONDS = 15.0


class GoogleOAuthProvider:
    """Real Google OIDC provider. Constructed only when a client ID/secret
    is configured (`SecurityEngine._build_oauth_providers`)."""

    provider_id = "google"

    def __init__(self, client_id: str, client_secret: str, client: httpx.AsyncClient | None = None) -> None:
        if not client_id or not client_secret:
            raise ValueError("GoogleOAuthProvider requires a non-empty client_id and client_secret.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "access_type": "online",
        }
        return f"{_AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthUserInfo:
        try:
            token_response = await self._client.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise OAuthExchangeError("Google did not return an access token.")

            userinfo_response = await self._client.get(
                _USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            userinfo_response.raise_for_status()
            body = userinfo_response.json()
        except httpx.HTTPError as exc:
            raise OAuthExchangeError("Failed to complete Google sign-in.") from exc

        subject = body.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OAuthExchangeError("Google did not return a subject identifier.")
        email = body.get("email") if isinstance(body.get("email"), str) else None
        return OAuthUserInfo(subject=subject, email=email)
