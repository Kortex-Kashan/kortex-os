"""Production REST/HTTP Connector Driver Plugin for KORTEX OS Connector Engine.

Implements HttpRestConnectorDriver, inheriting from BaseConnectorDriver.
Supports HTTP_GET, HTTP_POST, HTTP_PUT, and HTTP_DELETE over ActionRequest/ActionResult contracts.
Includes pre-connect SSRF security validation, IP normalization, body size enforcement,
strict credential isolation, and httpx non-redirect dispatching.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import time
import urllib.parse
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
from httpcore import SOCKET_OPTION
from httpcore._backends.anyio import AnyIOBackend

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.exceptions import ConnectorSecurityError, DriverExecutionError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
    DriverMetadata,
)

logger = logging.getLogger("kortex.engines.connector.drivers.http")


# Restricted IPv4 and IPv6 Networks for SSRF Validation
RESTRICTED_IPV4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
]

RESTRICTED_IPV6_NETWORKS = [
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("::ffff:0:0/96"),
]

MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_RESPONSE_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

# Explicit allowlist for HTTP response headers included in ActionResult.
# All response headers NOT in this set are stripped before ActionResult construction.
# This prevents credential-bearing headers (Set-Cookie, Authorization, Proxy-Authorization,
# X-Api-Key, Api-Key, WWW-Authenticate, etc.) from leaking into workflow step_outputs,
# WorkflowContext persistence, connector events, or logs.
ALLOWED_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "etag",
        "last-modified",
        "date",
        "cache-control",
        "x-request-id",
        "x-correlation-id",
    }
)


class PinnedIPNetworkBackend(httpcore.AsyncNetworkBackend):
    """Custom httpcore network backend that forces TCP socket creation directly to a pre-validated IP address."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        # Annotated with the public base class: `httpcore._backends.anyio` is a
        # private, untyped module, so both delegating calls below would
        # otherwise resolve to `Any`.
        self._default_backend: httpcore.AsyncNetworkBackend = AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Force socket connection directly to self._pinned_ip, preventing time-of-use DNS re-resolution."""
        return await self._default_backend.connect_tcp(
            host=self._pinned_ip,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> httpcore.AsyncNetworkStream:
        return await self._default_backend.connect_unix_socket(*args, **kwargs)

    async def sleep(self, seconds: float) -> None:
        await self._default_backend.sleep(seconds)


class SSRFHardenedTransport(httpx.AsyncHTTPTransport):
    """Custom httpx AsyncHTTPTransport that pins TCP connections to a pre-validated IP address.

    This transport does NOT call super().__init__() to avoid creating an orphaned
    default connection pool. Instead, it directly constructs a single
    httpcore.AsyncConnectionPool with PinnedIPNetworkBackend, ensuring:
    - Exactly one active connection pool per transport instance
    - No orphaned pool or resource leak
    - Proper async close behavior via inherited aclose() → self._pool.aclose()
    - All TCP connections are physically routed to the pinned IP
    """

    def __init__(self, pinned_ip: str, verify: bool = True) -> None:
        # Intentionally skip super().__init__() to avoid creating an orphaned pool.
        # AsyncHTTPTransport.__init__() would create self._pool as a default
        # httpcore.AsyncConnectionPool without our PinnedIPNetworkBackend,
        # and we'd then have to overwrite it, leaving the original pool un-closed.
        ssl_context = httpx.create_ssl_context(verify=verify)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            network_backend=PinnedIPNetworkBackend(pinned_ip),
        )


class HttpRestConnectorDriver(BaseConnectorDriver):
    """Production REST/HTTP Connector Driver Plugin.

    Supports asynchronous HTTP requests (GET, POST, PUT, DELETE) over external REST APIs.
    Enforces pre-connection SSRF IP checks, exact IP pinning, non-redirect policies,
    request/response size limits, and secret token isolation.
    """

    @property
    def metadata(self) -> DriverMetadata:
        """Return immutable driver metadata object."""
        return DriverMetadata(
            driver_id="connector-http-rest",
            display_name="Production REST/HTTP Connector Driver",
            vendor="KORTEX",
            author="KORTEX Core Team",
            version="1.0.0",
            description=("Production REST/HTTP connector driver plugin for external web API dispatches."),
            license="MIT",
            is_sandboxed=True,
            supported_actions=[
                ConnectorActionType.FETCH,
                ConnectorActionType.PUSH,
                ConnectorActionType.SEND,
            ],
            supported_capabilities=[
                ConnectorCapability.FETCH,
                ConnectorCapability.PUSH,
                ConnectorCapability.SEND,
                ConnectorCapability.TEST_CONNECTION,
            ],
        )

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        """Execute a REST/HTTP connector action and return the response result.

        Args:
            request: ActionRequest payload.
            secret_token: Optional resolved authentication secret token.

        Returns:
            ActionResult object containing execution status and response payload.

        Raises:
            DriverExecutionError: If request parameters or execution limits fail.
            ConnectorSecurityError: If SSRF security checks fail.
        """
        start_time = time.perf_counter()

        if not self.supports_action(request.action_type):
            raise DriverExecutionError(
                f"Action '{request.action_type.value}' is not supported by driver '{self.driver_id}'.",
                details={
                    "action_type": request.action_type.value,
                    "driver_id": self.driver_id,
                    "request_id": request.request_id,
                },
            )

        payload = request.payload or {}
        options = request.options or {}

        # Reject custom proxy options explicitly
        if options.get("proxy") or payload.get("proxy"):
            raise ConnectorSecurityError("Custom proxy configuration is not supported.")

        # 1. Determine HTTP method
        method = self._resolve_http_method(request.action_type, payload)

        # 2. Validate URL string presence
        raw_url = payload.get("url")
        if not raw_url or not isinstance(raw_url, str):
            raise DriverExecutionError(
                "Invalid request: payload must contain a non-empty 'url' string.",
                details={"request_id": request.request_id},
            )

        # 3. Build and validate request body BEFORE socket/network dispatch
        body_bytes = self._build_request_body(payload)

        # 4. Configure and validate timeout settings BEFORE socket/network dispatch
        timeout_config = self._build_timeout_config(options, payload)

        # 5. Construct final URL
        base_url = options.get("base_url") or payload.get("base_url")
        if base_url and isinstance(base_url, str):
            final_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", raw_url.lstrip("/"))
        else:
            final_url = raw_url

        # 6. Perform pre-connect SSRF security validation & select pinned IP
        pinned_ip = await self._validate_ssrf_security(final_url)

        # 7. Construct headers and inject secret token safely
        headers = self._build_headers(options, payload, secret_token)

        # 8. Build query parameters
        params = self._build_query_params(options, payload)

        # 9. Execute HTTP request via SSRFHardenedTransport (follow_redirects=False, trust_env=False)
        transport = SSRFHardenedTransport(pinned_ip=pinned_ip)
        try:
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=False,
                    trust_env=False,
                    timeout=timeout_config,
                ) as client,
                client.stream(
                    method=method,
                    url=final_url,
                    params=params if params else None,
                    headers=headers,
                    content=body_bytes,
                ) as response,
            ):
                body_chunks: list[bytes] = []
                total_bytes = 0

                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_RESPONSE_BODY_SIZE:
                        await response.aclose()
                        raise DriverExecutionError(
                            "Response body size exceeds maximum allowed limit of 10MB (10485760 bytes).",
                            details={"request_id": request.request_id},
                        )
                    body_chunks.append(chunk)

                response_bytes = b"".join(body_chunks)
                exec_time_ms = (time.perf_counter() - start_time) * 1000.0
                status_code = response.status_code

                if 200 <= status_code <= 299:
                    # Attempt JSON parsing, fall back to UTF-8 text string
                    try:
                        res_body: Any = json.loads(response_bytes.decode("utf-8"))
                    except Exception:
                        res_body = response_bytes.decode("utf-8", errors="replace")

                    return ActionResult(
                        request_id=request.request_id,
                        status="SUCCESS",
                        response_payload={
                            "status_code": status_code,
                            "headers": self._sanitize_response_headers(response.headers),
                            "body": res_body,
                        },
                        execution_time_ms=exec_time_ms,
                        correlation_id=request.correlation_id,
                    )
                else:
                    return ActionResult(
                        request_id=request.request_id,
                        status="FAILED",
                        response_payload={},
                        execution_time_ms=exec_time_ms,
                        error_details={
                            "error": f"HTTP status error {status_code}",
                            "status_code": status_code,
                        },
                        correlation_id=request.correlation_id,
                    )

        except (DriverExecutionError, ConnectorSecurityError):
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise DriverExecutionError(
                "HTTP request execution timed out.",
                details={
                    "request_id": request.request_id,
                    "driver_id": self.driver_id,
                },
            ) from exc
        except (OSError, httpx.RequestError) as e:
            # Sanitize network exception details to prevent credential/URL leak
            raise DriverExecutionError(
                "HTTP network connection request failed.",
                details={
                    "request_id": request.request_id,
                    "driver_id": self.driver_id,
                },
            ) from e

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        """Test connectivity and endpoint validity for target connector profile.

        Args:
            profile: ConnectorProfile definition.
            secret_token: Optional resolved authentication secret token.

        Returns:
            True if connection test returns 2xx status, False otherwise.
        """
        options = profile.options or {}
        test_url = options.get("base_url") or options.get("url")
        if not test_url or not isinstance(test_url, str):
            return False

        try:
            request = ActionRequest(
                request_id=f"test-{profile.profile_id}",
                profile_id=profile.profile_id,
                action_type=ConnectorActionType.FETCH,
                payload={"url": test_url, "method": "GET"},
                options=options,
            )
            result = await self.execute_action(request, secret_token=secret_token)
            return result.status == "SUCCESS"
        except Exception:
            return False

    # -- Internal Helper Implementations ------------------------------------

    def _resolve_http_method(self, action_type: ConnectorActionType, payload: dict[str, Any]) -> str:
        """Resolve requested HTTP method string from payload or action_type."""
        explicit_method = payload.get("method")
        if explicit_method and isinstance(explicit_method, str):
            method = explicit_method.strip().upper()
            if method not in ("GET", "POST", "PUT", "DELETE"):
                raise DriverExecutionError(
                    f"Unsupported HTTP method '{method}'. Must be GET, POST, PUT, or DELETE.",
                )
            return method

        if action_type == ConnectorActionType.FETCH:
            return "GET"
        if action_type in (ConnectorActionType.PUSH, ConnectorActionType.SEND):
            return "POST"

        raise DriverExecutionError(
            f"Action '{action_type.value}' maps to no valid default HTTP method.",
        )

    def _build_headers(
        self,
        options: dict[str, Any],
        payload: dict[str, Any],
        secret_token: str | None,
    ) -> dict[str, str]:
        """Build merged header dictionary and inject secret token securely."""
        merged_headers: dict[str, str] = {}

        profile_headers = options.get("headers")
        if isinstance(profile_headers, dict):
            for k, v in profile_headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    merged_headers[k] = v

        payload_headers = payload.get("headers")
        if isinstance(payload_headers, dict):
            for k, v in payload_headers.items():
                if isinstance(k, str) and isinstance(v, str):
                    merged_headers[k] = v

        if secret_token and isinstance(secret_token, str):
            # S105 false positive: an HTTP *header name*, not a secret value.
            secret_header_key = options.get("secret_header") or payload.get("secret_header") or "Authorization"
            if not isinstance(secret_header_key, str):
                secret_header_key = "Authorization"  # noqa: S105

            if secret_header_key.lower() == "authorization":
                if secret_token.startswith("Bearer ") or secret_token.startswith("Basic "):
                    merged_headers[secret_header_key] = secret_token
                else:
                    merged_headers[secret_header_key] = f"Bearer {secret_token}"
            else:
                merged_headers[secret_header_key] = secret_token

        return merged_headers

    def _build_query_params(self, options: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Combine default profile query parameters with request payload parameters."""
        params: dict[str, Any] = {}

        profile_params = options.get("default_params")
        if isinstance(profile_params, dict):
            params.update(profile_params)

        payload_params = payload.get("params")
        if isinstance(payload_params, dict):
            params.update(payload_params)

        return params

    @staticmethod
    def _sanitize_response_headers(headers: httpx.Headers) -> dict[str, str]:
        """Filter HTTP response headers to an explicit allowlist.

        Only headers whose lowercased name is in ALLOWED_RESPONSE_HEADERS are
        included in the returned dictionary.  All credential-bearing, internal,
        and debug headers are silently excluded so they never propagate into
        ActionResult, workflow step_outputs, events, persistence, or logs.
        """
        return {k: v for k, v in headers.items() if k.lower() in ALLOWED_RESPONSE_HEADERS}

    def _build_request_body(self, payload: dict[str, Any]) -> bytes | None:
        """Serialize and validate request payload body size."""
        body = payload.get("body")
        if body is None:
            return None

        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body_bytes = body.encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = str(body).encode("utf-8")

        if len(body_bytes) > MAX_REQUEST_BODY_SIZE:
            raise DriverExecutionError(
                "Request body size exceeds maximum allowed limit of 10MB (10485760 bytes).",
            )

        return body_bytes

    def _build_timeout_config(self, options: dict[str, Any], payload: dict[str, Any]) -> httpx.Timeout:
        """Construct httpx.Timeout object with default or overridden request limits."""
        req_timeout = payload.get("timeout") or options.get("timeout")
        if req_timeout is not None:
            try:
                total_t = float(req_timeout)
                if total_t < 0.1 or total_t > 60.0:
                    raise DriverExecutionError("Timeout override must be between 0.1 and 60.0 seconds.")
                return httpx.Timeout(connect=5.0, read=total_t, write=15.0, pool=30.0)
            except (ValueError, TypeError) as e:
                raise DriverExecutionError("Invalid timeout value provided.") from e

        return httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=30.0)

    async def _validate_ssrf_security(self, url: str) -> str:
        """Validate URL scheme, parse host/IP, pre-resolve DNS, enforce Reject-All on all resolved IPs,
        and return the deterministically selected pinned IP address (IPv4 preference over IPv6).

        Security Note:
        After successful t0 validation, the TCP connection is pinned to the exact validated IP
        and does not perform a second DNS resolution at time t1.
        """
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as e:
            raise DriverExecutionError(f"Malformed URL provided: '{url}'") from e

        scheme = parsed.scheme.lower() if parsed.scheme else ""
        if scheme not in ("http", "https"):
            raise DriverExecutionError(f"Invalid URL scheme '{scheme}': scheme must be http or https.")

        hostname = parsed.hostname
        if not hostname or not isinstance(hostname, str):
            raise DriverExecutionError("Invalid URL: missing target hostname.")

        normalized_host = hostname.strip().lower()

        # Reject literal forbidden hostnames
        if normalized_host in ("localhost", "metadata", "169.254.169.254") or normalized_host.endswith(".local"):
            raise ConnectorSecurityError(f"SSRF validation failed: access to target host '{hostname}' is forbidden.")

        port = parsed.port or (443 if scheme == "https" else 80)

        # Parse and check explicit IP addresses (handles standard, octal, hex, or integer IPs)
        self._check_explicit_ip(normalized_host)

        # Pre-connect DNS Resolution Check (Reject-All Rule & Deterministic Selection)
        try:
            loop = asyncio.get_running_loop()
            addr_info = await loop.getaddrinfo(normalized_host, port, type=socket.SOCK_STREAM)
            if not addr_info:
                raise ConnectorSecurityError(f"SSRF validation failed: unable to resolve hostname '{hostname}'.")

            validated_ipv4: list[str] = []
            validated_ipv6: list[str] = []

            # Reject-All Rule: Validate EVERY resolved address. If ANY address is restricted, fail the request.
            for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
                ip_str = str(sockaddr[0])
                self._verify_ip_object(ip_str)
                ip_obj = ipaddress.ip_address(ip_str)
                if isinstance(ip_obj, ipaddress.IPv4Address):
                    validated_ipv4.append(ip_str)
                elif isinstance(ip_obj, ipaddress.IPv6Address):
                    validated_ipv6.append(ip_str)

            if validated_ipv4:
                return validated_ipv4[0]
            return validated_ipv6[0]
        except ConnectorSecurityError:
            raise
        except Exception as e:
            raise ConnectorSecurityError(f"SSRF validation failed: DNS resolution error for '{hostname}': {e}") from e

    def _check_explicit_ip(self, host: str) -> None:
        """Check if host string is a literal IPv4/IPv6 or obfuscated integer/hex IP."""
        # Check standard IP parsing
        try:
            ip_obj = ipaddress.ip_address(host)
            self._verify_ip_object(str(ip_obj))
            return
        except ValueError:
            pass

        # Reject raw integer or octal IP string representations (e.g. 2130706433 or 0177.0.0.1)
        if host.isdigit():
            try:
                ip_obj = ipaddress.ip_address(int(host))
                self._verify_ip_object(str(ip_obj))
                return
            except ValueError:
                pass

        if host.startswith("0x") or host.startswith("0X"):
            try:
                ip_obj = ipaddress.ip_address(int(host, 0))
                self._verify_ip_object(str(ip_obj))
                return
            except ValueError:
                pass

    def _verify_ip_object(self, ip_str: str) -> None:
        """Verify that an IP address string does not fall in restricted network ranges."""
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise ConnectorSecurityError(f"SSRF validation failed: invalid IP address format '{ip_str}'.") from e

        if (
            ip_obj.is_loopback
            or ip_obj.is_private
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_unspecified
            or ip_obj.is_reserved
        ):
            raise ConnectorSecurityError(
                f"SSRF validation failed: access to restricted IP address '{ip_str}' is forbidden."
            )

        if isinstance(ip_obj, ipaddress.IPv4Address):
            for net in RESTRICTED_IPV4_NETWORKS:
                if ip_obj in net:
                    raise ConnectorSecurityError(
                        f"SSRF validation failed: access to restricted IPv4 network '{net}' is forbidden."
                    )
        elif isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            self._verify_ip_object(str(ip_obj.ipv4_mapped))


__all__ = ["ALLOWED_RESPONSE_HEADERS", "HttpRestConnectorDriver", "PinnedIPNetworkBackend", "SSRFHardenedTransport"]
