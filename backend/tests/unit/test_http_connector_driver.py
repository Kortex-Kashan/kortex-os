"""Unit tests for Production REST/HTTP Connector Driver (Sub-Milestone 10.2).

Verifies driver contract compliance, HTTP method dispatching, request/response payload mapping,
pre-connect SSRF security checks, non-redirect policies, body size limits, credential isolation,
and 100% statement coverage for HttpRestConnectorDriver.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver
from kortex.engines.connector.exceptions import ConnectorSecurityError, DriverExecutionError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
)


class MockStreamResponse:
    """Mock httpx streaming response context manager for testing incremental chunk reading."""

    def __init__(self, chunks: list[bytes], status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.closed = False
        self.consumed_chunks_count = 0

    async def aiter_bytes(self):
        for chunk in self.chunks:
            if self.closed:
                break
            self.consumed_chunks_count += 1
            yield chunk

    async def aclose(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()


@pytest.fixture
def http_driver() -> HttpRestConnectorDriver:
    return HttpRestConnectorDriver()


# -- A. Driver Contract Tests -------------------------------------------------

def test_driver_metadata_and_properties(http_driver: HttpRestConnectorDriver) -> None:
    """A1. Verify driver metadata, ID, vendor, supported actions, and capabilities."""
    assert http_driver.driver_id == "connector-http-rest"
    meta = http_driver.metadata
    assert meta.display_name == "Production REST/HTTP Connector Driver"
    assert meta.vendor == "KORTEX"
    assert meta.version == "1.0.0"
    assert meta.is_sandboxed is True
    assert ConnectorActionType.FETCH in http_driver.supported_actions
    assert ConnectorActionType.PUSH in http_driver.supported_actions
    assert ConnectorActionType.SEND in http_driver.supported_actions
    assert http_driver.supports_action(ConnectorActionType.FETCH) is True
    assert http_driver.supports_action(ConnectorActionType.VERIFY) is False


@pytest.mark.asyncio
async def test_unsupported_action_raises_driver_execution_error(http_driver: HttpRestConnectorDriver) -> None:
    """A2. Test executing unsupported action raises DriverExecutionError."""
    req = ActionRequest(
        request_id="req-unsupp",
        profile_id="prof-1",
        action_type=ConnectorActionType.VERIFY,
        payload={"url": "https://api.example.com"},
    )
    with pytest.raises(DriverExecutionError) as exc_info:
        await http_driver.execute_action(req)

    assert "not supported" in str(exc_info.value)


# -- B & C. Request Construction & HTTP Method Dispatching --------------------

@pytest.mark.asyncio
async def test_execute_get_request(http_driver: HttpRestConnectorDriver) -> None:
    """B1. Test executing HTTP GET request with query params and base_url."""
    req = ActionRequest(
        request_id="req-get-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "/users", "params": {"page": "1"}},
        options={"base_url": "https://api.example.com"},
    )

    resp_bytes = json.dumps({"users": [{"id": 1, "name": "Alice"}]}).encode("utf-8")

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([resp_bytes], status_code=200)
            res = await http_driver.execute_action(req)

            assert res.status == "SUCCESS"
            assert res.response_payload["status_code"] == 200
            assert res.response_payload["body"] == {"users": [{"id": 1, "name": "Alice"}]}
            mock_stream.assert_called_once_with(
                method="GET",
                url="https://api.example.com/users",
                params={"page": "1"},
                headers={},
                content=None,
            )


@pytest.mark.asyncio
async def test_execute_post_json_request(http_driver: HttpRestConnectorDriver) -> None:
    """C1. Test executing HTTP POST request with JSON body."""
    req = ActionRequest(
        request_id="req-post-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.PUSH,
        payload={"url": "https://api.example.com/items", "body": {"name": "Widget", "qty": 10}},
    )

    resp_bytes = json.dumps({"id": "item-100", "created": True}).encode("utf-8")

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([resp_bytes], status_code=201)
            res = await http_driver.execute_action(req)

            assert res.status == "SUCCESS"
            assert res.response_payload["status_code"] == 201
            assert res.response_payload["body"] == {"id": "item-100", "created": True}


@pytest.mark.asyncio
async def test_execute_put_and_delete_methods(http_driver: HttpRestConnectorDriver) -> None:
    """B2. Test executing PUT and DELETE via explicit method override."""
    req_put = ActionRequest(
        request_id="req-put-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.SEND,
        payload={"url": "https://api.example.com/items/1", "method": "PUT", "body": "updated text"},
    )
    req_del = ActionRequest(
        request_id="req-del-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.SEND,
        payload={"url": "https://api.example.com/items/1", "method": "DELETE"},
    )

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b"OK"])
            res_put = await http_driver.execute_action(req_put)
            assert res_put.status == "SUCCESS"
            assert res_put.response_payload["body"] == "OK"

            mock_stream.return_value = MockStreamResponse([b"OK"])
            res_del = await http_driver.execute_action(req_del)
            assert res_del.status == "SUCCESS"


@pytest.mark.asyncio
async def test_header_and_query_param_merging(http_driver: HttpRestConnectorDriver) -> None:
    """C2. Test header and query parameter merging precedence."""
    req = ActionRequest(
        request_id="req-hdr-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={
            "url": "https://api.example.com/data",
            "headers": {"X-Custom": "request-val", "Accept": "text/html"},
            "params": {"p1": "req-p1"},
        },
        options={
            "headers": {"X-Custom": "profile-val", "X-Profile": "yes"},
            "default_params": {"p1": "prof-p1", "p2": "prof-p2"},
        },
    )

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b'{"ok": true}'])
            await http_driver.execute_action(req)

            sent_headers = mock_stream.call_args[1]["headers"]
            sent_params = mock_stream.call_args[1]["params"]
            assert sent_headers["X-Custom"] == "request-val"
            assert sent_headers["X-Profile"] == "yes"
            assert sent_headers["Accept"] == "text/html"
            assert sent_params["p1"] == "req-p1"
            assert sent_params["p2"] == "prof-p2"


@pytest.mark.asyncio
async def test_body_types_bytes_and_scalar(http_driver: HttpRestConnectorDriver) -> None:
    """C3. Test body handling for bytes, raw strings, and scalar numbers."""
    req_bytes = ActionRequest(
        request_id="req-bytes",
        profile_id="prof-1",
        action_type=ConnectorActionType.PUSH,
        payload={"url": "https://api.example.com/raw", "body": b"binary_data"},
    )
    req_scalar = ActionRequest(
        request_id="req-scalar",
        profile_id="prof-1",
        action_type=ConnectorActionType.PUSH,
        payload={"url": "https://api.example.com/scalar", "body": 12345},
    )

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b"OK"])
            res1 = await http_driver.execute_action(req_bytes)
            assert res1.status == "SUCCESS"
            assert mock_stream.call_args[1]["content"] == b"binary_data"

            mock_stream.return_value = MockStreamResponse([b"OK"])
            res2 = await http_driver.execute_action(req_scalar)
            assert res2.status == "SUCCESS"
            assert mock_stream.call_args[1]["content"] == b"12345"


@pytest.mark.asyncio
async def test_invalid_http_method_override_raises_error(http_driver: HttpRestConnectorDriver) -> None:
    """C4. Test invalid HTTP method string raises DriverExecutionError."""
    req = ActionRequest(
        request_id="req-inv-method",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com", "method": "TRACE"},
    )
    with pytest.raises(DriverExecutionError) as exc_info:
        await http_driver.execute_action(req)

    assert "Unsupported HTTP method" in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_url_payload_raises_error(http_driver: HttpRestConnectorDriver) -> None:
    """C5. Test missing or non-string URL raises DriverExecutionError."""
    req = ActionRequest(
        request_id="req-no-url",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={},
    )
    with pytest.raises(DriverExecutionError) as exc_info:
        await http_driver.execute_action(req)

    assert "non-empty 'url' string" in str(exc_info.value)


# -- D & G. HTTP Response Mapping & Non-Redirect Policy -----------------------

@pytest.mark.asyncio
async def test_http_error_responses(http_driver: HttpRestConnectorDriver) -> None:
    """D1. Test HTTP 400, 401, 403, 404, 422, 429, 500 status code response mapping."""
    for status_code in [400, 401, 403, 404, 422, 429, 500, 502, 503, 504]:
        req = ActionRequest(
            request_id=f"req-err-{status_code}",
            profile_id="prof-1",
            action_type=ConnectorActionType.FETCH,
            payload={"url": "https://api.example.com/status"},
        )
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with patch.object(httpx.AsyncClient, "stream") as mock_stream:
                mock_stream.return_value = MockStreamResponse([b"Error Response"], status_code=status_code)
                res = await http_driver.execute_action(req)
                assert res.status == "FAILED"
                assert res.error_details is not None
                assert res.error_details["status_code"] == status_code


@pytest.mark.asyncio
async def test_redirect_3xx_is_not_followed(http_driver: HttpRestConnectorDriver) -> None:
    """G1. Test HTTP 302 and 307 redirects are returned as FAILED and not followed."""
    for status_code in [301, 302, 303, 307, 308]:
        req = ActionRequest(
            request_id=f"req-redir-{status_code}",
            profile_id="prof-1",
            action_type=ConnectorActionType.FETCH,
            payload={"url": "https://api.example.com/redirect"},
        )
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with patch.object(httpx.AsyncClient, "stream") as mock_stream:
                mock_stream.return_value = MockStreamResponse([], status_code=status_code, headers={"location": "https://other.com"})
                res = await http_driver.execute_action(req)
                assert res.status == "FAILED"


# -- E. Network Failure Exception Handling ------------------------------------

@pytest.mark.asyncio
async def test_network_timeouts_and_connection_failures(http_driver: HttpRestConnectorDriver) -> None:
    """E1. Test handling of timeouts and socket connection errors."""
    req = ActionRequest(
        request_id="req-timeout-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com/timeout"},
    )

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.side_effect = httpx.TimeoutException("Connection timed out")
            with pytest.raises(DriverExecutionError) as exc_info:
                await http_driver.execute_action(req)

            assert "timed out" in str(exc_info.value)

            mock_stream.side_effect = httpx.ConnectError("Failed to connect")
            with pytest.raises(DriverExecutionError) as exc_info2:
                await http_driver.execute_action(req)

            assert "failed" in str(exc_info2.value)


# -- F. SSRF Security Hardening Tests -----------------------------------------

@pytest.mark.asyncio
async def test_ssrf_forbidden_hostnames_and_ip_ranges(http_driver: HttpRestConnectorDriver) -> None:
    """F1. Test SSRF protection blocking localhost, metadata 169.254.169.254, and private IPv4/IPv6 networks."""
    forbidden_urls = [
        "http://localhost/admin",
        "http://127.0.0.1:8080/metrics",
        "http://0.0.0.0/status",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://172.16.0.1/secret",
        "http://192.168.1.1/router",
        "http://[::1]/local",
        "http://[fe80::1]/linklocal",
        "http://[fc00::1]/privatev6",
    ]

    for url in forbidden_urls:
        req = ActionRequest(
            request_id="req-ssrf",
            profile_id="prof-1",
            action_type=ConnectorActionType.FETCH,
            payload={"url": url},
        )
        with pytest.raises(ConnectorSecurityError) as exc_info:
            await http_driver.execute_action(req)

        assert "SSRF validation failed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ssrf_invalid_schemes_and_obfuscated_ips(http_driver: HttpRestConnectorDriver) -> None:
    """F2. Test SSRF protection blocking invalid schemes, raw octal/hex/int IPs, and DNS failures."""
    # Invalid schemes
    for invalid_url in ["file:///etc/passwd", "ftp://example.com", "gopher://example.com"]:
        req = ActionRequest(
            request_id="req-scheme",
            profile_id="prof-1",
            action_type=ConnectorActionType.FETCH,
            payload={"url": invalid_url},
        )
        with pytest.raises(DriverExecutionError) as exc_info:
            await http_driver.execute_action(req)
        assert "Invalid URL scheme" in str(exc_info.value)

    # Obfuscated integer IP (2130706433 = 127.0.0.1)
    req_obf_int = ActionRequest(
        request_id="req-obf-int",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://2130706433/admin"},
    )
    with pytest.raises(ConnectorSecurityError):
        await http_driver.execute_action(req_obf_int)

    # Obfuscated hex IP (0x7f000001 = 127.0.0.1)
    req_obf_hex = ActionRequest(
        request_id="req-obf-hex",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://0x7f000001/admin"},
    )
    with pytest.raises(ConnectorSecurityError):
        await http_driver.execute_action(req_obf_hex)

    # DNS rebinding: public domain resolves to private IP (10.0.0.5)
    req_dns = ActionRequest(
        request_id="req-dns-rebind",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://malicious-domain.com/data"},
    )
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
        with pytest.raises(ConnectorSecurityError) as exc_info:
            await http_driver.execute_action(req_dns)
        assert "SSRF validation failed" in str(exc_info.value)

    # DNS resolution failure
    with patch("socket.getaddrinfo", side_effect=Exception("DNS resolution failed")):
        with pytest.raises(ConnectorSecurityError) as exc_info2:
            await http_driver.execute_action(req_dns)
        assert "DNS resolution error" in str(exc_info2.value)


@pytest.mark.asyncio
async def test_ssrf_ipv6_mapped_and_invalid_ip_verification(http_driver: HttpRestConnectorDriver) -> None:
    """F3. Test SSRF verification for IPv6 mapped IPv4 and invalid IP strings."""
    # IPv6 mapped IPv4 restricted address (::ffff:127.0.0.1)
    req_v6_mapped = ActionRequest(
        request_id="req-v6-mapped",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://[::ffff:127.0.0.1]/admin"},
    )
    with pytest.raises(ConnectorSecurityError):
        await http_driver.execute_action(req_v6_mapped)

    # Invalid IP format passed to _verify_ip_object
    with pytest.raises(ConnectorSecurityError) as exc_info:
        http_driver._verify_ip_object("invalid-ip-str")
    assert "invalid IP address format" in str(exc_info.value)


# -- H. Body Size Limit & Timeout Override Tests ------------------------------

@pytest.mark.asyncio
async def test_request_body_size_limits_exact_and_oversized(http_driver: HttpRestConnectorDriver) -> None:
    """H1. Test enforcing 10MB limits on request bodies (exact vs oversized)."""
    # Exactly 10MB request body (allowed)
    exact_req_body = "x" * (10 * 1024 * 1024)
    req_exact = ActionRequest(
        request_id="req-exact-body",
        profile_id="prof-1",
        action_type=ConnectorActionType.PUSH,
        payload={"url": "https://api.example.com/upload", "body": exact_req_body},
    )
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b"OK"])
            res = await http_driver.execute_action(req_exact)
            assert res.status == "SUCCESS"

    # Oversized Request Body (10MB + 1 byte) -> rejected before network dispatch
    large_req_body = "x" * (10 * 1024 * 1024 + 1)
    req_large = ActionRequest(
        request_id="req-large-body",
        profile_id="prof-1",
        action_type=ConnectorActionType.PUSH,
        payload={"url": "https://api.example.com/upload", "body": large_req_body},
    )
    with patch.object(httpx.AsyncClient, "stream") as mock_stream2:
        with pytest.raises(DriverExecutionError) as exc_info:
            await http_driver.execute_action(req_large)

        assert "exceeds maximum allowed limit" in str(exc_info.value)
        # Network dispatch never occurred
        mock_stream2.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_response_body_size_limits(http_driver: HttpRestConnectorDriver) -> None:
    """H2. Test true streaming response body limit enforcement (10MB exact vs 10MB+1, single & multi-chunk)."""
    # 1. Exactly 10MB response in single chunk -> SUCCESS
    exact_10mb_chunk = b"a" * (10 * 1024 * 1024)
    req_exact_res = ActionRequest(
        request_id="req-res-exact",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com/download"},
    )
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([exact_10mb_chunk])
            res_exact = await http_driver.execute_action(req_exact_res)
            assert res_exact.status == "SUCCESS"

    # 2. Oversized 10MB + 1 byte single chunk -> DriverExecutionError & stream closed
    oversized_chunk = b"a" * (10 * 1024 * 1024 + 1)
    mock_over_resp = MockStreamResponse([oversized_chunk])
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream2:
            mock_stream2.return_value = mock_over_resp
            with pytest.raises(DriverExecutionError) as exc_info1:
                await http_driver.execute_action(req_exact_res)

            assert "Response body size exceeds maximum" in str(exc_info1.value)
            assert mock_over_resp.closed is True

    # 3. Multiple chunks reaching exactly 10MB (5x 2MB chunks) -> SUCCESS
    chunk_2mb = b"b" * (2 * 1024 * 1024)
    mock_multi_5 = MockStreamResponse([chunk_2mb] * 5)
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream3:
            mock_stream3.return_value = mock_multi_5
            res_multi = await http_driver.execute_action(req_exact_res)
            assert res_multi.status == "SUCCESS"
            assert mock_multi_5.consumed_chunks_count == 5

    # 4. Multiple chunks exceeding 10MB (6x 2MB chunks = 12MB) -> DriverExecutionError & stops reading
    mock_multi_6 = MockStreamResponse([chunk_2mb] * 6)
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream4:
            mock_stream4.return_value = mock_multi_6
            with pytest.raises(DriverExecutionError) as exc_info2:
                await http_driver.execute_action(req_exact_res)

            assert "Response body size exceeds maximum" in str(exc_info2.value)
            assert mock_multi_6.closed is True
            # Stopped after chunk 6 (when cumulative size exceeded 10MB)
            assert mock_multi_6.consumed_chunks_count == 6


@pytest.mark.asyncio
async def test_timeout_overrides_and_validation(http_driver: HttpRestConnectorDriver) -> None:
    """H3. Test custom timeout overrides and timeout bounds validation."""
    # Valid timeout override (10.0s)
    req_valid = ActionRequest(
        request_id="req-tout-val",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com/fast", "timeout": 10.0},
    )
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b'{"ok": true}'])
            res = await http_driver.execute_action(req_valid)
            assert res.status == "SUCCESS"

    # Invalid timeout bounds (<0.1 or >60.0)
    for invalid_tout in [0.01, 100.0, "invalid"]:
        req_invalid = ActionRequest(
            request_id="req-tout-inv",
            profile_id="prof-1",
            action_type=ConnectorActionType.FETCH,
            payload={"url": "https://api.example.com/fast", "timeout": invalid_tout},
        )
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            with pytest.raises(DriverExecutionError):
                await http_driver.execute_action(req_invalid)


# -- I. Credential Isolation Tests --------------------------------------------

@pytest.mark.asyncio
async def test_credential_secret_token_injection_and_privacy(http_driver: HttpRestConnectorDriver) -> None:
    """I1. Test secret token header injection and secret privacy isolation in results and errors."""
    secret = "SUPER_SECRET_BEARER_TOKEN_999"
    req = ActionRequest(
        request_id="req-sec-1",
        profile_id="prof-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "https://api.example.com/protected"},
    )

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b'{"auth": "ok"}'])
            res = await http_driver.execute_action(req, secret_token=secret)

            # Check header was injected with Bearer prefix
            sent_headers = mock_stream.call_args[1]["headers"]
            assert sent_headers["Authorization"] == f"Bearer {secret}"

            # Verify secret token NEVER leaks in ActionResult
            res_str = str(res.model_dump())
            assert secret not in res_str

            # Test secret token already having Bearer prefix
            mock_stream.return_value = MockStreamResponse([b'{"auth": "ok"}'])
            await http_driver.execute_action(req, secret_token="Bearer already_prefixed_token")
            sent_headers_prefixed = mock_stream.call_args[1]["headers"]
            assert sent_headers_prefixed["Authorization"] == "Bearer already_prefixed_token"

            # Test custom secret header (e.g. X-API-Key)
            req_custom_hdr = ActionRequest(
                request_id="req-sec-custom",
                profile_id="prof-1",
                action_type=ConnectorActionType.FETCH,
                payload={"url": "https://api.example.com/protected"},
                options={"secret_header": "X-API-Key"},
            )
            mock_stream.return_value = MockStreamResponse([b'{"auth": "ok"}'])
            await http_driver.execute_action(req_custom_hdr, secret_token="api_key_xyz")
            sent_headers_custom = mock_stream.call_args[1]["headers"]
            assert sent_headers_custom["X-API-Key"] == "api_key_xyz"

            # Test failure response privacy
            mock_stream.side_effect = httpx.ConnectError("Failure")
            with pytest.raises(DriverExecutionError) as exc_info:
                await http_driver.execute_action(req, secret_token=secret)

            assert secret not in str(exc_info.value)


# -- J. Test Connection Method ------------------------------------------------

@pytest.mark.asyncio
async def test_test_connection_method(http_driver: HttpRestConnectorDriver) -> None:
    """J1. Test test_connection method success and failure cases."""
    profile_valid = ConnectorProfile(
        profile_id="prof-test-1",
        name="Test Profile",
        driver_id="connector-http-rest",
        options={"base_url": "https://api.example.com"},
    )
    profile_missing_url = ConnectorProfile(
        profile_id="prof-test-2",
        name="Invalid Profile",
        driver_id="connector-http-rest",
        options={},
    )
    profile_forbidden_url = ConnectorProfile(
        profile_id="prof-test-3",
        name="Forbidden Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://127.0.0.1"},
    )

    assert await http_driver.test_connection(profile_missing_url) is False
    assert await http_driver.test_connection(profile_forbidden_url) is False

    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
        with patch.object(httpx.AsyncClient, "stream") as mock_stream:
            mock_stream.return_value = MockStreamResponse([b"OK"], status_code=200)
            assert await http_driver.test_connection(profile_valid) is True

            mock_stream.return_value = MockStreamResponse([b"Error"], status_code=500)
            assert await http_driver.test_connection(profile_valid) is False


# -- K. Additional Direct Helper Unit Tests for 100% Line Coverage -------------

def test_resolve_http_method_direct(http_driver: HttpRestConnectorDriver) -> None:
    """K1. Direct unit test for _resolve_http_method helper covering fallback error paths."""
    assert http_driver._resolve_http_method(ConnectorActionType.FETCH, {}) == "GET"
    assert http_driver._resolve_http_method(ConnectorActionType.PUSH, {}) == "POST"
    assert http_driver._resolve_http_method(ConnectorActionType.SEND, {}) == "POST"

    with pytest.raises(DriverExecutionError) as exc_info:
        http_driver._resolve_http_method(ConnectorActionType.VERIFY, {})
    assert "maps to no valid default HTTP method" in str(exc_info.value)


def test_build_headers_direct(http_driver: HttpRestConnectorDriver) -> None:
    """K2. Direct unit test for _build_headers covering non-dict profile/payload headers and secret headers."""
    headers = http_driver._build_headers(
        options={"headers": "not-a-dict", "secret_header": 12345},
        payload={"headers": 999},
        secret_token="my_token",
    )
    assert headers["Authorization"] == "Bearer my_token"


def test_build_request_body_direct(http_driver: HttpRestConnectorDriver) -> None:
    """K3. Direct unit test for _build_request_body covering scalar numbers and bytes."""
    assert http_driver._build_request_body({"body": None}) is None
    assert http_driver._build_request_body({"body": b"raw_bytes"}) == b"raw_bytes"
    assert http_driver._build_request_body({"body": 98765}) == b"98765"


@pytest.mark.asyncio
async def test_validate_ssrf_security_direct_edge_cases(http_driver: HttpRestConnectorDriver) -> None:
    """K4. Direct unit test for _validate_ssrf_security covering empty DNS and malformed URLs."""
    # Missing hostname (e.g. "http://")
    with pytest.raises(DriverExecutionError) as exc_info1:
        await http_driver._validate_ssrf_security("http://")
    assert "missing target hostname" in str(exc_info1.value)

    # Empty DNS addr_info
    with patch("socket.getaddrinfo", return_value=[]):
        with pytest.raises(ConnectorSecurityError) as exc_info2:
            await http_driver._validate_ssrf_security("https://api.example.com")
        assert "unable to resolve hostname" in str(exc_info2.value)

    # Malformed URL where urlparse raises an exception
    with patch("urllib.parse.urlparse", side_effect=Exception("URL parse error")):
        with pytest.raises(DriverExecutionError) as exc_info3:
            await http_driver._validate_ssrf_security("https://api.example.com")
        assert "Malformed URL provided" in str(exc_info3.value)


def test_check_explicit_ip_hex_and_verify_ip_object(http_driver: HttpRestConnectorDriver) -> None:
    """K5. Direct unit test for _check_explicit_ip and _verify_ip_object covering hex IPs and IPv6 ranges."""
    # Public Uppercase Hex IP (0X08080808 = 8.8.8.8) -> returns None safely
    http_driver._check_explicit_ip("0X08080808")

    # Public Standard IP (8.8.8.8) -> returns None safely
    http_driver._check_explicit_ip("8.8.8.8")

    # Public Integer IP (134744072 = 8.8.8.8) -> returns None safely
    http_driver._check_explicit_ip("134744072")

    # Invalid Integer IP (digits out of IP range) -> passes to next check safely
    http_driver._check_explicit_ip("9999999999999999999999999999999999999999")

    # Invalid Hex IP (0xinvalidhex) -> passes to next check safely
    http_driver._check_explicit_ip("0xinvalidhex")

    # Private Uppercase Hex IP (0X7F000001 = 127.0.0.1) -> raises ConnectorSecurityError
    with pytest.raises(ConnectorSecurityError):
        http_driver._check_explicit_ip("0X7F000001")

    # IPv4 loopback (127.0.0.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("127.0.0.1")

    # IPv4 private (10.0.0.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("10.0.0.1")

    # IPv4 link-local (169.254.1.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("169.254.1.1")

    # IPv4 multicast (224.0.0.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("224.0.0.1")

    # IPv4 reserved (255.255.255.255)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("255.255.255.255")

    # IPv4 CGNAT restricted network (100.64.0.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("100.64.0.1")

    # IPv6 loopback (::1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("::1")

    # IPv6 link-local (fe80::1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("fe80::1")

    # IPv6 unique local (fc00::1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("fc00::1")

    # IPv6 multicast (ff02::1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("ff02::1")

    # IPv6 unspecified (::)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("::")

    # IPv6 mapped IPv4 private (::ffff:192.168.1.1)
    with pytest.raises(ConnectorSecurityError):
        http_driver._verify_ip_object("::ffff:192.168.1.1")

    # IPv6 mapped IPv4 public (::ffff:8.8.8.8)
    http_driver._verify_ip_object("::ffff:8.8.8.8")

    # IPv6 valid public IP (e.g., 2001:4860:4860::8888)
    http_driver._verify_ip_object("2001:4860:4860::8888")
