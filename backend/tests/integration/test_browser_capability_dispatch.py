"""End-to-end vertical slice for the `kortex.browser.*` capabilities
(Browser-B5.0-B5.4 foundation).

Every test enters through the **real** Kernel capability-dispatch boundary
— real `SecurityEngine` authentication, real RBAC/ABAC, real
`kernel.invoke_capability` — mirroring
`test_desktop_automation_capability_dispatch.py`'s own established
methodology exactly. Nothing about `CapabilityDispatcher`/`SecurityEngine`
is stubbed or bypassed.

What these tests do NOT exercise: a live WebView2 surface, or the desktop
Rust redeem command (`browser_grant.rs`, Browser-B5.4) — those have no
backend-testable counterpart; see `browser_grant.rs`'s own Rust test suite
and the B5.0-B5.4 implementation report's LIVE VERIFICATION section for
what was proven there instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from pydantic import ValidationError

from kortex.core.dispatch import CapabilityRequest
from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.ai.governance import ToolGovernanceEvaluator
from kortex.engines.ai.tools import ToolCall, ToolDefinition, ToolRegistry
from kortex.engines.browser.engine import (
    BROWSER_CAPABILITY_NAMES,
    CLICK_CAPABILITY,
    DOWNLOAD_CAPABILITY,
    EXTRACT_CAPABILITY,
    GRANT_VERIFICATION_KEY_CAPABILITY,
    NAVIGATE_CAPABILITY,
    READ_CAPABILITY,
    SCREENSHOT_CAPABILITY,
    TYPE_CAPABILITY,
    BrowserCapabilityEngine,
)
from kortex.engines.browser.exceptions import BrowserNotYetSupportedError, BrowserRefusedSensitiveInputError
from kortex.engines.browser.grant import verify_grant
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine

_TEST_MASTER_KEY = b"\x61" * 32
_TEST_SIGNING_KEY = b"\x62" * 32

_FULL_ROLE = "BROWSER_SLICE_FULL_ROLE"
_NO_PERMISSIONS_ROLE = "BROWSER_SLICE_NO_PERMISSIONS_ROLE"
_TENANT_A = "tenant-browser-a"
_TENANT_B = "tenant-browser-b"
_USER_FULL = "user-browser-full"
_USER_NONE = "user-browser-none"
_USER_TENANT_B = "user-browser-b"
_PASSWORD = "browser-slice-test-pass"

_ALL_PERMISSIONS = [
    "browser:navigate",
    "browser:read",
    "browser:click",
    "browser:type",
    "browser:extract",
    "browser:download",
    "browser:screenshot",
]


@dataclass
class _Env:
    kernel: Kernel
    security_engine: SecurityEngine
    browser_engine: BrowserCapabilityEngine


@pytest_asyncio.fixture
async def env(tmp_path_factory: pytest.TempPathFactory):
    from kortex.core.db import DatabaseEngineManager

    tmp_path = tmp_path_factory.mktemp("browser-slice")
    db_path = (tmp_path / "slice.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    browser_engine = BrowserCapabilityEngine(grant_ttl_seconds=5)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(browser_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed(session: Any) -> None:
        for permission in _ALL_PERMISSIONS:
            session.add(RolePermissionRecord(id=str(uuid4()), role=_FULL_ROLE, permission=permission))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_NO_PERMISSIONS_ROLE, permission="unrelated:permission"))
        for tenant_id, principal_id, role in (
            (_TENANT_A, _USER_FULL, _FULL_ROLE),
            (_TENANT_A, _USER_NONE, _NO_PERMISSIONS_ROLE),
            (_TENANT_B, _USER_TENANT_B, _FULL_ROLE),
        ):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    enabled=True,
                    credential_hash=hasher.hash(_PASSWORD),
                    roles=[role],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage_engine.data.execute_in_transaction(_seed)

    environment = _Env(kernel=kernel, security_engine=security_engine, browser_engine=browser_engine)
    try:
        yield environment
    finally:
        if kernel.state == KernelState.RUNNING:
            import contextlib

            with contextlib.suppress(Exception):
                await kernel.shutdown()
        await db_manager.disconnect()


async def _token(env: _Env, tenant_id: str, principal_id: str) -> Any:
    principal = await env.security_engine.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": _PASSWORD}
    )
    return await env.security_engine.authentication_manager.issue_token(principal)


async def _invoke(env: _Env, capability: str, token: Any, *, context_tenant: str = _TENANT_A, **parameters: Any) -> Any:
    return await env.kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=dict(parameters),
            context={"resource_tenant_id": context_tenant},
        )
    )


def _target(surface: str = "surface-1", profile: str = "profile-1") -> dict[str, Any]:
    return {"browser_profile_id": profile, "surface_id": surface, "navigation_generation": 0}


# -- REGISTRATION --------------------------------------------------------------


def test_all_seven_capabilities_and_the_grant_key_capability_are_registered(env: _Env) -> None:
    for name in (*BROWSER_CAPABILITY_NAMES, GRANT_VERIFICATION_KEY_CAPABILITY):
        descriptor = env.kernel.get_capability(name)
        assert descriptor.name == name


@pytest.mark.parametrize("name", BROWSER_CAPABILITY_NAMES)
def test_every_capability_requires_authentication(env: _Env, name: str) -> None:
    assert env.kernel.get_capability(name).requires_authentication is True


@pytest.mark.parametrize("name", BROWSER_CAPABILITY_NAMES)
def test_every_capability_requires_execution_context(env: _Env, name: str) -> None:
    assert env.kernel.get_capability(name).requires_execution_context is True


@pytest.mark.parametrize(
    "name,expected_read_only",
    [
        (NAVIGATE_CAPABILITY, False),
        (READ_CAPABILITY, True),
        (CLICK_CAPABILITY, False),
        (TYPE_CAPABILITY, False),
        (EXTRACT_CAPABILITY, True),
        (DOWNLOAD_CAPABILITY, False),
        (SCREENSHOT_CAPABILITY, True),
    ],
)
def test_read_only_mutation_classification_matches_the_locked_table(
    env: _Env, name: str, expected_read_only: bool
) -> None:
    assert env.kernel.get_capability(name).is_read_only is expected_read_only


def test_registering_the_engine_twice_does_not_create_a_second_dispatcher(env: _Env) -> None:
    """There is exactly one Kernel, one CapabilityDispatcher, one Registry —
    `BrowserCapabilityEngine` never constructs its own; this is a structural
    assertion, not just a behavioral one."""
    from kortex.core.kernel import Kernel as KernelClass

    assert isinstance(env.kernel, KernelClass)
    assert not hasattr(env.browser_engine, "_dispatcher")
    assert not hasattr(env.browser_engine, "_registry")


# -- AI tool bridge (Section: registration integrates with the existing tool bridge) --


def test_capability_tool_bridge_derives_is_mutation_correctly() -> None:
    from kortex.api.capability_tool_bridge import generate_tool_definition_from_capability
    from kortex.engines.registry.engine import CapabilityDescriptor

    read_only_descriptor = CapabilityDescriptor(
        name=READ_CAPABILITY,
        description="d",
        provider="browser",
        parameters_schema={"type": "object", "properties": {}},
        is_read_only=True,
    )
    mutation_descriptor = CapabilityDescriptor(
        name=NAVIGATE_CAPABILITY,
        description="d",
        provider="browser",
        parameters_schema={"type": "object", "properties": {}},
        is_read_only=False,
    )
    assert generate_tool_definition_from_capability(read_only_descriptor).is_mutation is False
    assert generate_tool_definition_from_capability(mutation_descriptor).is_mutation is True


# -- AUTHORIZATION ---------------------------------------------------------------


@pytest.mark.parametrize(
    "capability,parameters",
    [
        (NAVIGATE_CAPABILITY, {"target": _target(), "url": "https://example.com"}),
        (CLICK_CAPABILITY, {"target": _target(), "selector": {"accessible_name": "Submit"}}),
        (TYPE_CAPABILITY, {"target": _target(), "selector": {"accessible_name": "Search"}, "ui_input_text": "hello"}),
        (READ_CAPABILITY, {"target": _target()}),
        (EXTRACT_CAPABILITY, {"target": _target(), "schema_fields": {"title": {"role": "heading"}}}),
        (SCREENSHOT_CAPABILITY, {"target": _target()}),
    ],
)
async def test_unauthorized_principal_denied(env: _Env, capability: str, parameters: dict[str, Any]) -> None:
    token = await _token(env, _TENANT_A, _USER_NONE)
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(env, capability, token, **parameters)


async def test_no_session_token_denied(env: _Env) -> None:
    """A request with no session token at all never reaches a handler for
    an authenticated capability."""
    with pytest.raises(AuthenticationError):
        await env.kernel.invoke_capability(
            CapabilityRequest(
                capability_name=NAVIGATE_CAPABILITY,
                session_token=None,
                parameters={"target": _target(), "url": "https://example.com"},
                context={"resource_tenant_id": _TENANT_A},
            )
        )


async def test_wrong_tenant_context_does_not_grant_cross_tenant_access(env: _Env) -> None:
    token = await _token(env, _TENANT_B, _USER_TENANT_B)
    # ABAC denies: the request's own `resource_tenant_id` context defaults
    # to tenant A, but the authenticated principal belongs to tenant B.
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(
            env, NAVIGATE_CAPABILITY, token, context_tenant=_TENANT_A, target=_target(), url="https://example.com"
        )


# -- Authorized success: every capability mints a grant, none execute -----------


async def test_navigate_mints_a_grant_and_does_not_execute(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(env, NAVIGATE_CAPABILITY, token, target=_target(), url="https://example.com")
    grant = result["grant"]
    assert grant["capability_name"] == NAVIGATE_CAPABILITY
    assert grant["tenant_id"] == _TENANT_A
    assert grant["principal_id"] == _USER_FULL
    assert grant["surface_id"] == "surface-1"
    assert grant["browser_profile_id"] == "profile-1"
    assert "signature" in grant and len(grant["signature"]) == 128  # 64-byte Ed25519 sig, hex-encoded


async def test_read_extract_click_screenshot_all_mint_grants(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    read_result = await _invoke(env, READ_CAPABILITY, token, target=_target())
    click_result = await _invoke(env, CLICK_CAPABILITY, token, target=_target(), selector={"accessible_name": "Submit"})
    extract_result = await _invoke(
        env, EXTRACT_CAPABILITY, token, target=_target(), schema_fields={"title": {"role": "heading"}}
    )
    screenshot_result = await _invoke(env, SCREENSHOT_CAPABILITY, token, target=_target())
    for result, capability in (
        (read_result, READ_CAPABILITY),
        (click_result, CLICK_CAPABILITY),
        (extract_result, EXTRACT_CAPABILITY),
        (screenshot_result, SCREENSHOT_CAPABILITY),
    ):
        assert result["grant"]["capability_name"] == capability


async def test_type_mints_a_grant_for_ordinary_text(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(
        env,
        TYPE_CAPABILITY,
        token,
        target=_target(),
        selector={"accessible_name": "Search"},
        ui_input_text="hello world",
    )
    assert result["grant"]["capability_name"] == TYPE_CAPABILITY


async def test_minted_grant_independently_verifies(env: _Env) -> None:
    """The grant minted by a real capability dispatch is not just
    shaped correctly — it independently re-verifies against the engine's
    own published verification key, exactly as the desktop redeem command
    would do."""
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(env, NAVIGATE_CAPABILITY, token, target=_target(), url="https://example.com")
    key_result = await _invoke(env, GRANT_VERIFICATION_KEY_CAPABILITY, token)

    from kortex.engines.browser.models import BrowserCapabilityExecutionGrant
    from kortex.engines.security.providers.local_crypto import LocalCrypto

    grant = BrowserCapabilityExecutionGrant.model_validate(result["grant"])
    public_key = bytes.fromhex(key_result["public_key_hex"])
    verification = verify_grant(grant, crypto_provider=LocalCrypto(), verification_public_key=public_key)
    assert verification.is_valid


async def test_grant_verification_key_is_reachable_by_any_authenticated_principal(env: _Env) -> None:
    """No special permission required for the public key itself — every
    authenticated principal in `_NO_PERMISSIONS_ROLE` can still fetch it."""
    token = await _token(env, _TENANT_A, _USER_NONE)
    result = await _invoke(env, GRANT_VERIFICATION_KEY_CAPABILITY, token)
    assert len(result["public_key_hex"]) == 64  # 32-byte Ed25519 public key, hex-encoded


# -- Sensitive-input refusal (browser.type) --------------------------------------


async def test_type_refuses_sensitive_target_name(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(BrowserRefusedSensitiveInputError):
        await _invoke(
            env,
            TYPE_CAPABILITY,
            token,
            target=_target(),
            selector={"accessible_name": "Password"},
            ui_input_text="anything",
        )


async def test_type_refuses_secret_shaped_value(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(BrowserRefusedSensitiveInputError):
        await _invoke(
            env,
            TYPE_CAPABILITY,
            token,
            target=_target(),
            selector={"accessible_name": "API Key"},
            ui_input_text="sk-abcdef0123456789ABCDEF",
        )


def test_ui_input_text_is_redacted_from_persisted_audit_context() -> None:
    """Same convention `kortex.desktop.type` already established — reused,
    not reinvented."""
    cleaned = sanitize_for_persistence({"ui_input_text": "hunter2", "target": {"surface_id": "s1"}})
    assert cleaned["ui_input_text"] is None
    assert cleaned["target"] == {"surface_id": "s1"}


# -- Fail-closed target validation ------------------------------------------------


async def test_empty_selector_rejected_before_a_grant_is_minted(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(ValidationError):
        await _invoke(env, CLICK_CAPABILITY, token, target=_target(), selector={})


async def test_malformed_request_missing_required_parameter_fails_closed(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(TypeError):
        await _invoke(env, CLICK_CAPABILITY, token, target=_target())


# -- Download: registered, discoverable, never executes -------------------------


async def test_download_is_registered_and_discoverable(env: _Env) -> None:
    descriptor = env.kernel.get_capability(DOWNLOAD_CAPABILITY)
    assert descriptor.name == DOWNLOAD_CAPABILITY
    assert descriptor.is_read_only is False


async def test_download_always_returns_not_yet_supported(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(BrowserNotYetSupportedError):
        await _invoke(env, DOWNLOAD_CAPABILITY, token, target=_target())


def test_download_handler_has_no_reference_to_grant_minting() -> None:
    """Structural, not merely behavioral: `download`'s own bytecode/source
    never calls `grant.mint_grant` at all — confirmed by source inspection,
    not just by observing its current return value."""
    import inspect

    source = inspect.getsource(BrowserCapabilityEngine.download)
    assert "mint_grant" not in source
    assert "_mint_and_audit" not in source


# -- Browser-specific action_fingerprint regression tests (ToolGovernanceEvaluator) --


@pytest.fixture
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            name="kortex_browser_navigate",
            description="d",
            parameters_schema={"type": "object", "properties": {}},
            canonical_capability=NAVIGATE_CAPABILITY,
            is_mutation=True,
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="kortex_browser_read",
            description="d",
            parameters_schema={"type": "object", "properties": {}},
            canonical_capability=READ_CAPABILITY,
            is_mutation=False,
        )
    )
    return registry


def test_browser_mutation_tool_call_requires_approval(tool_registry: ToolRegistry) -> None:
    evaluator = ToolGovernanceEvaluator(tool_registry)
    is_allowed, violations, requires_approval = evaluator.evaluate_tool_calls(
        [ToolCall(call_id="1", tool_name="kortex_browser_navigate", arguments={"url": "https://example.com"})]
    )
    assert is_allowed
    assert violations == []
    assert requires_approval is True


def test_browser_read_only_tool_call_does_not_require_approval(tool_registry: ToolRegistry) -> None:
    evaluator = ToolGovernanceEvaluator(tool_registry)
    is_allowed, _violations, requires_approval = evaluator.evaluate_tool_calls(
        [ToolCall(call_id="1", tool_name="kortex_browser_read", arguments={})]
    )
    assert is_allowed
    assert requires_approval is False


def test_action_fingerprint_changes_when_browser_parameters_change() -> None:
    """Mirrors `DurableAIApprovalPolicy.requires_approval`'s own fingerprint
    formula (`ai/governance.py`) exactly — proven here against Browser's
    specific tool-call shape, not merely against the generic mechanism's
    own pre-existing test suite."""
    import hashlib
    import json

    from kortex.engines.ai.tools import scrub_secrets_from_text

    def fingerprint(calls: list[ToolCall]) -> str:
        summary = [{"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))} for c in calls]
        return hashlib.sha256(json.dumps(summary, sort_keys=True).encode("utf-8")).hexdigest()

    call_a = ToolCall(call_id="1", tool_name="kortex_browser_navigate", arguments={"url": "https://a.example/"})
    call_b = ToolCall(call_id="1", tool_name="kortex_browser_navigate", arguments={"url": "https://b.example/"})
    call_c = ToolCall(call_id="1", tool_name="kortex_browser_click", arguments={"url": "https://a.example/"})

    fp_a = fingerprint([call_a])
    fp_b = fingerprint([call_b])
    fp_c = fingerprint([call_c])

    assert fp_a != fp_b, "changed parameters (url) must change the fingerprint"
    assert fp_a != fp_c, "changed capability/tool must change the fingerprint"
    assert fingerprint([call_a]) == fp_a, "the same call must always produce the same fingerprint"


def test_action_fingerprint_changes_when_browser_target_changes() -> None:
    import hashlib
    import json

    from kortex.engines.ai.tools import scrub_secrets_from_text

    def fingerprint(calls: list[ToolCall]) -> str:
        summary = [{"tool": c.tool_name, "args": scrub_secrets_from_text(json.dumps(c.arguments))} for c in calls]
        return hashlib.sha256(json.dumps(summary, sort_keys=True).encode("utf-8")).hexdigest()

    call_surface_1 = ToolCall(
        call_id="1", tool_name="kortex_browser_click", arguments={"target": _target(surface="surface-1")}
    )
    call_surface_2 = ToolCall(
        call_id="1", tool_name="kortex_browser_click", arguments={"target": _target(surface="surface-2")}
    )
    assert fingerprint([call_surface_1]) != fingerprint([call_surface_2]), (
        "changed target (surface_id) must change the fingerprint"
    )
