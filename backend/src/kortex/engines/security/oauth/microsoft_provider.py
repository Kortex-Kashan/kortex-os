"""Microsoft (Azure AD v2.0 / Entra ID) OAuth sign-in provider (Phase A).

Same trust model as `GoogleOAuthProvider`: the access token is exchanged and
the identity fetched directly from Microsoft Graph over HTTPS, rather than
independently verifying an ID token. Uses the `common` tenant endpoint,
supporting both personal Microsoft accounts and work/school (Azure AD)
accounts — the same default the Microsoft identity platform recommends for
a multi-tenant consumer application.
"""

from __future__ import annotations

import urllib.parse

import httpx

from kortex.engines.security.exceptions import OAuthExchangeError
from kortex.engines.security.oauth.base import OAuthUserInfo

_AUTHORIZATION_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_TOKEN_ENDPOINT = "https://login.microsoftonline.com/common/oauth2/v2.0/token"  # noqa: S105
_GRAPH_ME_ENDPOINT = "https://graph.microsoft.com/v1.0/me"
_DEFAULT_TIMEOUT_SECONDS = 15.0


class MicrosoftOAuthProvider:
    """Real Microsoft OIDC provider. Constructed only when a client
    ID/secret is configured (`SecurityEngine._build_oauth_providers`)."""

    provider_id = "microsoft"

    def __init__(self, client_id: str, client_secret: str, client: httpx.AsyncClient | None = None) -> None:
        if not client_id or not client_secret:
            raise ValueError("MicrosoftOAuthProvider requires a non-empty client_id and client_secret.")
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email User.Read",
            "state": state,
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
                    "scope": "openid email User.Read",
                },
            )
            token_response.raise_for_status()
            access_token = token_response.json().get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise OAuthExchangeError("Microsoft did not return an access token.")

            me_response = await self._client.get(
                _GRAPH_ME_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            me_response.raise_for_status()
            body = me_response.json()
        except httpx.HTTPError as exc:
            raise OAuthExchangeError("Failed to complete Microsoft sign-in.") from exc

        subject = body.get("id")
        if not isinstance(subject, str) or not subject:
            raise OAuthExchangeError("Microsoft did not return a subject identifier.")
        email = body.get("mail") or body.get("userPrincipalName")
        return OAuthUserInfo(subject=subject, email=email if isinstance(email, str) else None)
