"""Unit tests for `kortex.api.errors` — the exception -> `IpcErrorCategory`
mapping table this M3 adapter uses instead of rewriting the dispatcher."""

from __future__ import annotations

from kortex.api.errors import map_exception
from kortex.core.exceptions import CapabilityNotFoundError
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
