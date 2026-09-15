"""
KORTEX GitHub Connector Curated Actions (Integration Hub M2).

A **static descriptor catalog only** (`GITHUB_ACTION_DESCRIPTORS`) plus the
functions that turn it into **per-profile, dynamically registered** Kernel
capabilities — `kortex.connector.<profile_id>.<action>`, `owner_id =
profile_id` — mirroring `mcp_driver.py`'s own dynamic register/unregister
pattern (Integration Hub M1) exactly, rather than F5's boot-time-fixed
`ConnectorActionBootstrapEngine` path: a GitHub capability only exists once a
specific `ConnectorProfile` has actually connected GitHub, and disappears
again on disconnect (`unregister_github_profile_capabilities`, called from
`ConnectorEngine.delete_profile()`/the deactivation branch of
`register_profile()`, and from `kortex.connector.integration.disconnect`).

Reuses `actions.py`'s existing `ConnectorActionDescriptor` model and
`make_action_handler()` **unmodified** — no changes to `actions.py`. The
only new logic here is `_scoped_handler()`, a thin closure that pre-binds
`profile_id` (so the registered capability's own schema never needs a
`profile_id` property — identity is already fixed by which capability was
called) and, for `user_get` only, a fixed `url` the caller never supplies.

Payload shape mirrors `HttpRestConnectorDriver`'s own contract exactly, per
the same rationale `reference_actions.py` documents: `ConnectorProfile.
options.base_url` is never merged onto an `ActionRequest` by the pipeline
for this registration path, so every `url` here is the complete, absolute
GitHub REST API URL — not a path relative to some configured base.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from kortex.engines.connector.actions import ConnectorActionDescriptor, make_action_handler
from kortex.engines.connector.models import ConnectorActionType

if TYPE_CHECKING:
    from kortex.engines.connector.engine import ConnectorEngine
    from kortex.engines.registry.engine import RegistryEngine

_GITHUB_API_BASE = "https://api.github.com"

_HTTP_RESPONSE_RETURNS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status_code": {"type": "integer", "description": "The HTTP response status code."},
        "headers": {"type": "object", "description": "Allow-listed, non-sensitive response headers."},
        "body": {"description": "The parsed JSON response body, or raw text if not JSON."},
    },
    "required": ["status_code"],
}

USER_GET_ACTION = ConnectorActionDescriptor(
    capability_name="user_get",
    description="Get the GitHub user this integration is authorized as. Read-only, idempotent.",
    connector_action_type=ConnectorActionType.FETCH,
    parameters_schema={"properties": {}, "required": []},
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=True,
    is_idempotent=True,
    resource="user",
    action="get",
)

REPO_GET_ACTION = ConnectorActionDescriptor(
    capability_name="repo_get",
    description="Get a GitHub repository's details. Read-only, idempotent.",
    connector_action_type=ConnectorActionType.FETCH,
    parameters_schema={
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "The complete GitHub API repository URL, e.g. 'https://api.github.com/repos/owner/repo'.",
            },
        },
        "required": ["url"],
    },
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=True,
    is_idempotent=True,
    resource="repo",
    action="get",
)

ISSUES_LIST_ACTION = ConnectorActionDescriptor(
    capability_name="issues_list",
    description="List issues on a GitHub repository. Read-only, idempotent.",
    connector_action_type=ConnectorActionType.FETCH,
    parameters_schema={
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The complete GitHub API issues-list URL, "
                    "e.g. 'https://api.github.com/repos/owner/repo/issues'."
                ),
            },
            "params": {
                "type": "object",
                "description": "Optional query parameters, e.g. {\"state\": \"open\"}.",
            },
        },
        "required": ["url"],
    },
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=True,
    is_idempotent=True,
    resource="issues",
    action="list",
)

ISSUE_CREATE_ACTION = ConnectorActionDescriptor(
    capability_name="issue_create",
    description="Create an issue on a GitHub repository. Mutates external state; not idempotent.",
    connector_action_type=ConnectorActionType.PUSH,
    parameters_schema={
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The complete GitHub API issues-list URL to post to, "
                    "e.g. 'https://api.github.com/repos/owner/repo/issues'."
                ),
            },
            "body": {
                "type": "object",
                "description": "The issue payload, e.g. {\"title\": \"...\", \"body\": \"...\"}.",
            },
        },
        "required": ["url", "body"],
    },
    returns_schema=_HTTP_RESPONSE_RETURNS_SCHEMA,
    required_permissions=["connector:execute"],
    is_read_only=False,
    is_idempotent=False,
    resource="issue",
    action="create",
)

GITHUB_ACTION_DESCRIPTORS: list[ConnectorActionDescriptor] = [
    USER_GET_ACTION,
    REPO_GET_ACTION,
    ISSUES_LIST_ACTION,
    ISSUE_CREATE_ACTION,
]

# Fixed payload values a caller never supplies, merged in ahead of theirs
# (Integration Hub M2's own, minimal per-action convenience — not a change
# to `actions.py`'s shared, driver-agnostic payload contract). Only
# `user_get` needs one today: it always targets the same fixed endpoint.
_FIXED_PAYLOAD: dict[str, dict[str, Any]] = {
    "user_get": {"url": f"{_GITHUB_API_BASE}/user"},
}


def _capability_name(profile_id: str, action_key: str) -> str:
    return f"kortex.connector.{profile_id}.{action_key}"


def _scoped_handler(
    descriptor: ConnectorActionDescriptor, connector_engine: ConnectorEngine, profile_id: str
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Pre-bind `profile_id` (and, for `user_get`, its fixed `url`) onto
    `actions.py`'s existing, unmodified `make_action_handler()` — the
    registered capability's own schema never includes `profile_id`, since
    identity is already fixed by which per-profile capability was called."""
    handler = make_action_handler(descriptor, connector_engine)
    fixed = _FIXED_PAYLOAD.get(descriptor.capability_name, {})

    async def _handler(**kwargs: Any) -> dict[str, Any]:
        return await handler(profile_id=profile_id, **{**fixed, **kwargs})

    _handler.__name__ = f"github_action_handler__{profile_id}__{descriptor.capability_name}"
    return _handler


def register_github_profile_capabilities(
    registry_engine: RegistryEngine, connector_engine: ConnectorEngine, profile_id: str
) -> None:
    """Register all four curated GitHub capabilities for a connected
    `ConnectorProfile`, `owner_id=profile_id` — called from
    `ConnectorEngine.register_profile()`'s GitHub branch when the profile is
    active.

    Calls `RegistryEngine.register_capability()` **directly** (never via
    `Kernel.register_capability`) — the Kernel wrapper rejects registration
    once booted (`actions.py:257-259`), but a profile connects at runtime,
    post-boot; this mirrors `mcp_driver.py`'s own dynamic registration for
    exactly the same reason. `owner_id`-based semantics already make this
    idempotent: re-registering the same `(name, owner_id)` pair updates
    rather than raises (`registry/engine.py:676-681`).
    """
    for descriptor in GITHUB_ACTION_DESCRIPTORS:
        registry_engine.register_capability(
            name=_capability_name(profile_id, descriptor.capability_name),
            description=descriptor.description,
            provider=connector_engine.name,
            handler=_scoped_handler(descriptor, connector_engine, profile_id),
            parameters_schema=descriptor.parameters_schema,
            returns_schema=descriptor.returns_schema,
            required_permissions=list(descriptor.required_permissions),
            security_classification=descriptor.security_classification,
            requires_execution_context=True,
            is_read_only=descriptor.is_read_only,
            is_idempotent=descriptor.is_idempotent,
            owner_id=profile_id,
        )


def unregister_github_profile_capabilities(registry_engine: RegistryEngine, profile_id: str) -> None:
    """Unregister all four curated GitHub capabilities for `profile_id`.

    Idempotent per-entry: `RegistryEngine.unregister_capability()` already
    returns `False` (never raises) for a capability that was never
    registered or already removed (`registry/engine.py:749-750`) — calling
    this twice, or on a profile that never fully connected, is always safe.
    Called from `ConnectorEngine.delete_profile()`, the deactivation branch
    of `register_profile()`, and `kortex.connector.integration.disconnect`
    (which calls it first, before touching any credential state — see that
    capability's docstring).
    """
    for descriptor in GITHUB_ACTION_DESCRIPTORS:
        registry_engine.unregister_capability(_capability_name(profile_id, descriptor.capability_name), profile_id)
