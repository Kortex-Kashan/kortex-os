"""Tests for the GitHub curated connector actions (Integration Hub M2):
descriptor catalog shape, the profile-scoped handler wrapper, and
dynamic register/unregister against a real `RegistryEngine`.

Mirrors `test_connector_actions.py`'s (F5) `_StubConnectorEngine` pattern for
handler tests — proving `_scoped_handler` never touches anything beyond
`execute_action` — but registration itself goes through a real
`RegistryEngine()` (trivially constructible standalone), since that is where
the `owner_id`-scoped dynamic register/unregister semantics actually live.
"""

from __future__ import annotations

import pytest

from kortex.engines.connector.github_actions import (
    GITHUB_ACTION_DESCRIPTORS,
    ISSUE_CREATE_ACTION,
    ISSUES_LIST_ACTION,
    REPO_GET_ACTION,
    USER_GET_ACTION,
    register_github_profile_capabilities,
    unregister_github_profile_capabilities,
)
from kortex.engines.connector.models import ActionResult, ConnectorActionType
from kortex.engines.registry.engine import RegistryEngine


class _StubConnectorEngine:
    """Mirrors `test_connector_actions.py`'s own `_StubConnectorEngine` —
    proves `_scoped_handler`/`make_action_handler` never touch anything
    beyond `execute_action`."""

    name = "connector"

    def __init__(self, outcome: ActionResult | Exception) -> None:
        self._outcome = outcome
        self.received_requests: list[object] = []

    async def execute_action(self, request: object, principal: object = None) -> ActionResult:
        self.received_requests.append(request)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


# ============================================================================
# A. Descriptor catalog shape
# ============================================================================


def test_catalog_has_exactly_the_four_agreed_semantic_actions() -> None:
    names = {d.capability_name for d in GITHUB_ACTION_DESCRIPTORS}
    assert names == {"user_get", "repo_get", "issues_list", "issue_create"}


def test_user_get_is_read_only_idempotent_fetch_with_no_required_params() -> None:
    assert USER_GET_ACTION.connector_action_type == ConnectorActionType.FETCH
    assert USER_GET_ACTION.is_read_only is True
    assert USER_GET_ACTION.is_idempotent is True
    assert USER_GET_ACTION.parameters_schema["required"] == []


def test_repo_get_requires_url() -> None:
    assert REPO_GET_ACTION.is_read_only is True
    assert REPO_GET_ACTION.is_idempotent is True
    assert "url" in REPO_GET_ACTION.parameters_schema["required"]


def test_issues_list_requires_url_only_params_optional() -> None:
    assert ISSUES_LIST_ACTION.is_read_only is True
    assert ISSUES_LIST_ACTION.parameters_schema["required"] == ["url"]
    assert "params" in ISSUES_LIST_ACTION.parameters_schema["properties"]


def test_issue_create_is_mutating_and_not_idempotent() -> None:
    assert ISSUE_CREATE_ACTION.connector_action_type == ConnectorActionType.PUSH
    assert ISSUE_CREATE_ACTION.is_read_only is False
    assert ISSUE_CREATE_ACTION.is_idempotent is False
    assert set(ISSUE_CREATE_ACTION.parameters_schema["required"]) == {"url", "body"}


# ============================================================================
# B. Profile-scoped registration (real RegistryEngine, no Kernel needed)
# ============================================================================


def test_register_creates_profile_scoped_capabilities_with_correct_owner() -> None:
    registry = RegistryEngine()
    connector_engine = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))

    register_github_profile_capabilities(registry, connector_engine, "prof-1")  # type: ignore[arg-type]

    for action_key in ("user_get", "repo_get", "issues_list", "issue_create"):
        descriptor = registry.get_capability(f"kortex.connector.prof-1.{action_key}")
        assert descriptor.owner_id == "prof-1"
        assert descriptor.provider == "connector"


def test_register_is_idempotent_for_the_same_owner() -> None:
    registry = RegistryEngine()
    connector_engine = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))

    register_github_profile_capabilities(registry, connector_engine, "prof-1")  # type: ignore[arg-type]
    # Re-registering the same profile (e.g. reactivating it) must update in
    # place, never raise `ResourceAlreadyExistsError`.
    register_github_profile_capabilities(registry, connector_engine, "prof-1")  # type: ignore[arg-type]

    assert registry.get_capability("kortex.connector.prof-1.user_get").owner_id == "prof-1"


def test_two_profiles_get_independently_named_capabilities() -> None:
    registry = RegistryEngine()
    connector_engine = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))

    register_github_profile_capabilities(registry, connector_engine, "prof-a")  # type: ignore[arg-type]
    register_github_profile_capabilities(registry, connector_engine, "prof-b")  # type: ignore[arg-type]

    assert registry.get_capability("kortex.connector.prof-a.repo_get").owner_id == "prof-a"
    assert registry.get_capability("kortex.connector.prof-b.repo_get").owner_id == "prof-b"


def test_unregister_removes_all_four_and_is_idempotent() -> None:
    registry = RegistryEngine()
    connector_engine = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))
    register_github_profile_capabilities(registry, connector_engine, "prof-1")  # type: ignore[arg-type]

    unregister_github_profile_capabilities(registry, "prof-1")

    from kortex.core.exceptions import CapabilityNotFoundError

    for action_key in ("user_get", "repo_get", "issues_list", "issue_create"):
        with pytest.raises(CapabilityNotFoundError):
            registry.get_capability(f"kortex.connector.prof-1.{action_key}")

    # Idempotent: calling it again (or on a profile that never registered)
    # must not raise.
    unregister_github_profile_capabilities(registry, "prof-1")
    unregister_github_profile_capabilities(registry, "prof-never-registered")


def test_unregister_never_touches_a_different_profiles_capabilities() -> None:
    registry = RegistryEngine()
    connector_engine = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))
    register_github_profile_capabilities(registry, connector_engine, "prof-a")  # type: ignore[arg-type]
    register_github_profile_capabilities(registry, connector_engine, "prof-b")  # type: ignore[arg-type]

    unregister_github_profile_capabilities(registry, "prof-a")

    assert registry.get_capability("kortex.connector.prof-b.repo_get").owner_id == "prof-b"


# ============================================================================
# C. Handler behavior (profile_id pre-binding, user_get's fixed url)
# ============================================================================


@pytest.mark.asyncio
async def test_user_get_handler_injects_the_fixed_url_without_caller_input() -> None:
    stub = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={"ok": True}))
    registry = RegistryEngine()
    register_github_profile_capabilities(registry, stub, "prof-1")  # type: ignore[arg-type]

    handler = registry._capability_handlers["kortex.connector.prof-1.user_get"]
    result = await handler()

    assert result == {"ok": True}
    assert len(stub.received_requests) == 1
    request = stub.received_requests[0]
    assert request.profile_id == "prof-1"  # type: ignore[attr-defined]
    assert request.payload["url"] == "https://api.github.com/user"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_repo_get_handler_forwards_caller_supplied_url() -> None:
    stub = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={"ok": True}))
    registry = RegistryEngine()
    register_github_profile_capabilities(registry, stub, "prof-1")  # type: ignore[arg-type]

    handler = registry._capability_handlers["kortex.connector.prof-1.repo_get"]
    await handler(url="https://api.github.com/repos/octocat/hello-world")

    request = stub.received_requests[0]
    assert request.profile_id == "prof-1"  # type: ignore[attr-defined]
    assert request.payload["url"] == "https://api.github.com/repos/octocat/hello-world"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_handler_raises_on_failed_action_result() -> None:
    from kortex.engines.connector.actions import ConnectorActionFailedError

    stub = _StubConnectorEngine(ActionResult(request_id="x", status="FAILED", error_details={"error": "boom"}))
    registry = RegistryEngine()
    register_github_profile_capabilities(registry, stub, "prof-1")  # type: ignore[arg-type]

    handler = registry._capability_handlers["kortex.connector.prof-1.issue_create"]
    with pytest.raises(ConnectorActionFailedError, match="boom"):
        await handler(url="https://api.github.com/repos/octocat/hello-world/issues", body={"title": "t"})
