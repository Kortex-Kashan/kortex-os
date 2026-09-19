"""Unit tests for `kortex.api.errors` — the exception -> `IpcErrorCategory`
mapping table this M3 adapter uses instead of rewriting the dispatcher."""

from __future__ import annotations

from kortex.api.errors import map_exception
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.engines.agent_gateway.exceptions import (
    DesktopAgentAmbiguousError,
    DesktopAgentUnavailableError,
    DesktopCommandTimeoutError,
    DesktopSessionDisconnectedError,
)
from kortex.engines.desktop_automation.exceptions import (
    DesktopApplicationNotAllowedError,
    DesktopElementAmbiguousError,
    DesktopElementNotFoundError,
    DesktopInvalidSelectorError,
    DesktopLaunchFailedError,
    DesktopWindowNotFoundError,
)
from kortex.engines.security.exceptions import (
    AuthenticationError,
    AuthorizationDeniedError,
    InvalidTokenError,
    SecurityEngineError,
    SigningKeyError,
    TokenExpiredError,
)


class TestMapException:
    def test_capability_not_found_maps_to_404(self) -> None:
        mapping = map_exception(CapabilityNotFoundError("kortex.x.y.z"))
        assert mapping.category == "CAPABILITY_NOT_FOUND"
        assert mapping.http_status == 404

    def test_authorization_denied_maps_to_permission_denied_403(self) -> None:
        mapping = map_exception(AuthorizationDeniedError("no."))
        assert mapping.category == "PERMISSION_DENIED"
        assert mapping.http_status == 403

    def test_authentication_error_maps_to_permission_denied_401(self) -> None:
        mapping = map_exception(AuthenticationError("no token."))
        assert mapping.category == "PERMISSION_DENIED"
        assert mapping.http_status == 401

    def test_authentication_error_subtypes_map_the_same_as_their_base(self) -> None:
        for exc in (InvalidTokenError("bad"), TokenExpiredError("stale")):
            mapping = map_exception(exc)
            assert mapping.category == "PERMISSION_DENIED"
            assert mapping.http_status == 401

    def test_unrelated_security_engine_error_maps_to_execution_failed_500(self) -> None:
        mapping = map_exception(SigningKeyError("bad key"))
        assert mapping.category == "EXECUTION_FAILED"
        assert mapping.http_status == 500

    def test_generic_security_engine_error_maps_to_execution_failed(self) -> None:
        mapping = map_exception(SecurityEngineError("boom"))
        assert mapping.category == "EXECUTION_FAILED"

    def test_arbitrary_exception_maps_to_execution_failed_500(self) -> None:
        mapping = map_exception(RuntimeError("unexpected"))
        assert mapping.category == "EXECUTION_FAILED"
        assert mapping.http_status == 500

    def test_desktop_command_timeout_maps_to_timeout_exceeded_408(self) -> None:
        mapping = map_exception(DesktopCommandTimeoutError("no reply"))
        assert mapping.category == "TIMEOUT_EXCEEDED"
        assert mapping.http_status == 408

    def test_desktop_agent_unavailable_maps_to_service_unavailable_503(self) -> None:
        mapping = map_exception(DesktopAgentUnavailableError("no agent"))
        assert mapping.category == "SERVICE_UNAVAILABLE"
        assert mapping.http_status == 503

    def test_desktop_session_disconnected_maps_to_service_unavailable_503(self) -> None:
        mapping = map_exception(DesktopSessionDisconnectedError("closed"))
        assert mapping.category == "SERVICE_UNAVAILABLE"
        assert mapping.http_status == 503

    def test_desktop_agent_ambiguous_maps_to_validation_failed_422(self) -> None:
        mapping = map_exception(DesktopAgentAmbiguousError("pick one"))
        assert mapping.category == "VALIDATION_FAILED"
        assert mapping.http_status == 422

    def test_desktop_application_not_allowed_maps_to_permission_denied_403(self) -> None:
        mapping = map_exception(DesktopApplicationNotAllowedError("no.", error_code="APPLICATION_NOT_ALLOWED"))
        assert mapping.category == "PERMISSION_DENIED"
        assert mapping.http_status == 403

    def test_desktop_ui_targeting_errors_map_to_validation_failed_422(self) -> None:
        for exc in (
            DesktopInvalidSelectorError("empty selector"),
            DesktopWindowNotFoundError("stale", error_code="WINDOW_NOT_FOUND"),
            DesktopElementNotFoundError("none", error_code="ELEMENT_NOT_FOUND"),
            DesktopElementAmbiguousError("many", error_code="ELEMENT_AMBIGUOUS"),
        ):
            mapping = map_exception(exc)
            assert mapping.category == "VALIDATION_FAILED"
            assert mapping.http_status == 422

    def test_desktop_launch_failed_maps_to_execution_failed_500(self) -> None:
        mapping = map_exception(DesktopLaunchFailedError("boom", error_code="LAUNCH_FAILED"))
        assert mapping.category == "EXECUTION_FAILED"
        assert mapping.http_status == 500
