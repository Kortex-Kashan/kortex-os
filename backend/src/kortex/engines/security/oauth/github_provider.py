"""GitHub OAuth App integration provider (Integration Hub M2).

Implements `IIntegrationOAuthProvider`. Distinct from a hypothetical
"GitHub sign-in" `IOAuthProvider` — this provider exists to obtain and
refresh a token set used to call the GitHub REST API on the tenant's behalf
via `HttpRestConnectorDriver`, not to authenticate a KORTEX sign-in.

Token lifecycle, explicit (per the M2 architecture correction):
- `offline_access` is requested alongside the API scopes this milestone's
  curated actions need (`repo`, `read:user`) — GitHub's documented runtime
  opt-in for expiring OAuth App tokens. Without it, or if the org hasn't
  enabled expiring tokens, GitHub issues a classic, non-expiring token
  instead (no `expires_in`/`refresh_token` in the response) — handled
  explicitly below, never assumed away.
- GitHub **rotates** the refresh token on every successful refresh: the
  response's `refresh_token` is a new value and the old one is invalidated
  server-side. `refresh()` returns the full new set; the caller
  (`IntegrationOAuthManager`) is responsible for persisting it and never
  reusing the old refresh token.
- GitHub's OAuth token endpoint returns HTTP 200 with an `error` field in
  the JSON body for failures (not a 4xx status) — both `exchange_code` and
  `refresh` check for this field explicitly rather than relying on
  `raise_for_status()`.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from kortex.engines.security.exceptions import OAuthExchangeError, OAuthRefreshInvalidError
from kortex.engines.security.models import IntegrationTokenSet

_AUTHORIZATION_ENDPOINT = "https://github.com/login/oauth/authorize"
_TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"  # noqa: S105
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_SCOPES = "repo read:user offline_access"
_BAD_REFRESH_TOKEN_ERRORS = frozenset({"bad_refresh_token", "bad_verification_code"})


def _token_set_from_response(body: dict[str, Any]) -> IntegrationTokenSet:
    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthExchangeError("GitHub did not return an access token.")

    now = datetime.now(UTC)
    expires_in = body.get("expires_in")
    access_token_expires_at = now + timedelta(seconds=int(expires_in)) if isinstance(expires_in, (int, float)) else None

    refresh_token = body.get("refresh_token") if isinstance(body.get("refresh_token"), str) else None
    refresh_expires_in = body.get("refresh_token_expires_in")
    refresh_token_expires_at = (
        now + timedelta(seconds=int(refresh_expires_in)) if isinstance(refresh_expires_in, (int, float)) else None
    )

    scope = body.get("scope") if isinstance(body.get("scope"), str) else None
    token_type = body.get("token_type") if isinstance(body.get("token_type"), str) else None

    return IntegrationTokenSet(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_at=access_token_expires_at,
        refresh_token_expires_at=refresh_token_expires_at,
        scope=scope,
        token_type=token_type,
    )


class GitHubIntegrationOAuthProvider:
    """Real GitHub OAuth App provider. Constructed only when a client
    ID/secret is configured (`SecurityEngine._build_integration_oauth_providers`)."""

    provider_id = "github"

    def __init__(self, client_id: str, client_secret: str, client: httpx.AsyncClient | None = None) -> None:
        if not client_id or not client_secret:
            raise ValueError("GitHubIntegrationOAuthProvider requires a non-empty client_id and client_secret.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": _DEFAULT_SCOPES,
            "state": state,
        }
        return f"{_AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IntegrationTokenSet:
        try:
            response = await self._client.post(
                _TOKEN_ENDPOINT,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise OAuthExchangeError("Failed to complete GitHub integration authorization.") from exc

        error = body.get("error") if isinstance(body, dict) else None
        if error:
            raise OAuthExchangeError(f"GitHub rejected the authorization code: {error}.")

        return _token_set_from_response(body)

    async def refresh(self, refresh_token: str) -> IntegrationTokenSet:
        try:
            response = await self._client.post(
                _TOKEN_ENDPOINT,
                headers={"Accept": "application/json"},
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise OAuthExchangeError("Failed to refresh the GitHub integration token.") from exc

        error = body.get("error") if isinstance(body, dict) else None
        if error:
            if error in _BAD_REFRESH_TOKEN_ERRORS:
                raise OAuthRefreshInvalidError("GitHub rejected the refresh token; reauthorization is required.")
            raise OAuthExchangeError(f"GitHub rejected the refresh request: {error}.")

        return _token_set_from_response(body)
