"""KORTEX Security Engine — OAuth sign-in provider abstraction (Phase A).

`IOAuthProvider` is the provider-agnostic seam; `GoogleOAuthProvider`/
`MicrosoftOAuthProvider` are real OIDC implementations, constructed only
when their client ID/secret are configured. OAuth is a second *sign-in
method* for an existing KORTEX account, never a self-registration path —
see `AuthenticationManager.resolve_oauth_link`/`link_oauth_identity`.
"""
