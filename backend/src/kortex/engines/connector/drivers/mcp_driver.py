"""Model Context Protocol (MCP) Streamable HTTP Connector Driver for KORTEX OS.

Implements McpConnectorDriver, inheriting from BaseConnectorDriver.
Exposes tenant-scoped remote MCP tools to KORTEX OS as first-class RegistryEngine capabilities.
Enforces:
- Streamable HTTP transport (mcp.client.streamable_http.streamable_http_client, protocol 2025-03-26).
- Strict pre-connect SSRF validation, IP pinning, userinfo rejection, and non-redirect policies.
- DNS rebinding protection via PinnedIPNetworkBackend with strict TLS verification.
- Two-phase atomic tools/list reconciliation against RegistryEngine with strict owner_id verification.
- Tenant isolation: verified at execution context (execution_context.tenant_id == profile.tenant_id).
- Credential isolation: resolved strictly through SecretStore using tenant ownership context.
- Exponential backoff retry semantics (max 3 retries, capped at 8s with full jitter).
- Test Connection (temporary session) vs. Runtime Session (lazy in-memory reuse).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import StreamableHTTPError, streamable_http_client
from mcp.types import CallToolResult, Tool

from kortex.engines.connector.base_driver import BaseConnectorDriver
from kortex.engines.connector.drivers.http_driver import (
    RESTRICTED_IPV4_NETWORKS,
    RESTRICTED_IPV6_NETWORKS,
    SSRFHardenedTransport,
)
from kortex.engines.connector.exceptions import (
    ConnectorConnectionError,
    ConnectorSecurityError,
    ConnectorValidationError,
    DriverExecutionError,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorCapability,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.registry.engine import CapabilityDescriptor, RegistryEngine

logger = logging.getLogger("kortex.engines.connector.drivers.mcp")


class McpActiveSession:
    """In-memory runtime session container for a tenant-scoped MCP profile."""

    def __init__(
        self,
        profile_id: str,
        tenant_id: str,
        endpoint_url: str,
        secret_token: str | None,
        transport: SSRFHardenedTransport,
        http_client: httpx.AsyncClient,
        client_gen: Any,
        session: ClientSession,
        registered_capabilities: set[str],
    ) -> None:
        self.profile_id = profile_id
        self.tenant_id = tenant_id
        self.endpoint_url = endpoint_url
        self.secret_token = secret_token
        self.transport = transport
        self.http_client = http_client
        self.client_gen = client_gen
        self.session = session
        self.registered_capabilities = registered_capabilities
        self.lock = asyncio.Lock()
        self.is_connected = True

    async def close(self) -> None:
        """Safely close active runtime session and transport."""
        self.is_connected = False
        try:
            if hasattr(self.client_gen, "aclose"):
                await self.client_gen.aclose()
        except Exception as e:
            logger.debug("Error closing MCP client generator: %s", e)
        try:
            await self.http_client.aclose()
        except Exception as e:
            logger.debug("Error closing MCP HTTP client: %s", e)


class McpConnectorDriver(BaseConnectorDriver):
    """Production Model Context Protocol (MCP) Connector Driver Plugin.

    Exclusively implements Streamable HTTP over remote MCP endpoints.
    Tenant-configured STDIO and legacy SSE are prohibited.
    """

    def __init__(
        self,
        registry_engine: RegistryEngine | None = None,
        secret_resolver: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._registry_engine = registry_engine
        self._secret_resolver = secret_resolver
        self._profile_manager: Any = None
        self._active_sessions: dict[str, McpActiveSession] = {}
        self._known_profiles: dict[str, ConnectorProfile] = {}
        self._reconciliation_locks: dict[str, asyncio.Lock] = {}

    def set_registry_engine(self, registry_engine: RegistryEngine) -> None:
        """Set or update the global RegistryEngine reference."""
        self._registry_engine = registry_engine

    def set_secret_resolver(self, secret_resolver: Callable[[str, str], Any]) -> None:
        """Set or update the tenant-scoped secret resolver callback."""
        self._secret_resolver = secret_resolver

    def set_profile_manager(self, profile_manager: Any) -> None:
        """Set or update the profile manager reference."""
        self._profile_manager = profile_manager

    @property
    def metadata(self) -> DriverMetadata:
        """Return immutable driver metadata object."""
        return DriverMetadata(
            driver_id="connector-mcp",
            display_name="Model Context Protocol (MCP) Streamable HTTP Driver",
            vendor="KORTEX",
            author="KORTEX Core Team",
            version="1.0.0",
            description="Production MCP connector driver for external Streamable HTTP endpoints.",
            license="MIT",
            is_sandboxed=True,
            supported_actions=[
                ConnectorActionType.FETCH,
                ConnectorActionType.SEND,
                ConnectorActionType.PUSH,
                ConnectorActionType.VERIFY,
            ],
            supported_capabilities=[
                ConnectorCapability.FETCH,
                ConnectorCapability.SEND,
                ConnectorCapability.PUSH,
                ConnectorCapability.VERIFY,
                ConnectorCapability.TEST_CONNECTION,
                ConnectorCapability.AUTHENTICATE,
                ConnectorCapability.STREAMING,
            ],
        )

    # -- SSRF & Safe Transport Construction ----------------------------------

    async def validate_ssrf_and_pin_ip(self, url: str) -> tuple[str, str, int]:
        """Validate URL according to strict fail-closed SSRF rules and return (hostname, pinned_ip, port).

        Rules:
        - Scheme must be strictly https://.
        - Userinfo (username / password in URL) is rejected.
        - Port must be 443 or omitted.
        - Pre-connect DNS resolution: every resolved IP address must be outside restricted ranges.
        """
        try:
            parsed = urllib.parse.urlsplit(url)
        except Exception as e:
            raise ConnectorValidationError(f"Malformed URL provided: '{url}'") from e

        scheme = (parsed.scheme or "").lower()
        if scheme != "https":
            raise ConnectorSecurityError(
                f"Invalid URL scheme '{scheme}': MCP Streamable HTTP requires strict 'https://'."
            )

        if parsed.username or parsed.password:
            raise ConnectorSecurityError("SSRF rejection: URL must not contain credentials/userinfo.")

        hostname = parsed.hostname
        if not hostname or not isinstance(hostname, str):
            raise ConnectorValidationError("Invalid URL: missing target hostname.")

        normalized_host = hostname.strip().lower()

        # Reject literal forbidden hostnames
        if (
            normalized_host in ("localhost", "metadata", "169.254.169.254")
            or normalized_host.endswith(".local")
            or normalized_host.endswith(".internal")
        ):
            raise ConnectorSecurityError(f"SSRF validation failed: access to host '{hostname}' is forbidden.")

        port = parsed.port or 443
        if port != 443:
            raise ConnectorSecurityError(f"SSRF validation failed: explicit non-443 port '{port}' is forbidden.")

        # Check explicit IP addresses
        self._check_explicit_ip(normalized_host)

        # Pre-connect DNS Resolution Check
        try:
            loop = asyncio.get_running_loop()
            addr_info = await loop.getaddrinfo(normalized_host, port, type=socket.SOCK_STREAM)
            if not addr_info:
                raise ConnectorSecurityError(f"SSRF validation failed: unable to resolve hostname '{hostname}'.")

            validated_ipv4: list[str] = []
            validated_ipv6: list[str] = []

            for _family, _socktype, _proto, _canonname, sockaddr in addr_info:
                ip_str = str(sockaddr[0])
                self._verify_ip_object(ip_str)
                ip_obj = ipaddress.ip_address(ip_str)
                if isinstance(ip_obj, ipaddress.IPv4Address):
                    validated_ipv4.append(ip_str)
                elif isinstance(ip_obj, ipaddress.IPv6Address):
                    validated_ipv6.append(ip_str)

            pinned_ip = validated_ipv4[0] if validated_ipv4 else validated_ipv6[0]
            return normalized_host, pinned_ip, port
        except ConnectorSecurityError:
            raise
        except Exception as e:
            raise ConnectorSecurityError(f"SSRF validation failed for '{hostname}': {e}") from e

    def _check_explicit_ip(self, host: str) -> None:
        """Check if host string is an explicit IP address and verify safety."""
        try:
            ip_obj = ipaddress.ip_address(host)
            self._verify_ip_object(str(ip_obj))
        except ValueError:
            pass

    def _verify_ip_object(self, ip_str: str) -> None:
        """Verify that an IP address does not fall within any restricted range."""
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise ConnectorSecurityError(f"SSRF validation failed: unparseable IP '{ip_str}'.") from e

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
            or str(ip) == "255.255.255.255"
        ):
            raise ConnectorSecurityError(f"SSRF validation failed: target IP '{ip_str}' is in a restricted range.")

        if isinstance(ip, ipaddress.IPv4Address):
            for net in RESTRICTED_IPV4_NETWORKS:
                if ip in net:
                    raise ConnectorSecurityError(
                        f"SSRF validation failed: IPv4 target '{ip_str}' belongs to restricted network '{net}'."
                    )
        elif isinstance(ip, ipaddress.IPv6Address):
            for net in RESTRICTED_IPV6_NETWORKS:
                if ip in net:
                    raise ConnectorSecurityError(
                        f"SSRF validation failed: IPv6 target '{ip_str}' belongs to restricted network '{net}'."
                    )

    def _create_hardened_client(
        self,
        pinned_ip: str,
        secret_token: str | None = None,
    ) -> tuple[SSRFHardenedTransport, httpx.AsyncClient]:
        """Construct a secure httpx.AsyncClient pinned to the validated IP with preserved TLS/SNI."""
        transport = SSRFHardenedTransport(pinned_ip=pinned_ip, verify=True)
        headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
            "User-Agent": "KORTEX-MCP-Client/1.0",
        }
        if secret_token:
            clean_token = secret_token.strip()
            if clean_token.startswith("Bearer "):
                headers["Authorization"] = clean_token
            else:
                headers["Authorization"] = f"Bearer {clean_token}"

        timeout = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
        client = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )
        return transport, client

    # -- Connection Lifecycle (Test vs Runtime) ------------------------------

    async def test_connection(self, profile: ConnectorProfile, secret_token: str | None = None) -> bool:
        """Execute a temporary TEST_CONNECTION session against the remote MCP server.

        Instantiates a transient client, initializes session, queries tools/list,
        validates schemas, and immediately closes. Does NOT persist runtime session state.
        """
        endpoint_url = profile.options.get("endpoint_url")
        if not endpoint_url or not isinstance(endpoint_url, str):
            raise ConnectorValidationError("Profile options must contain a valid 'endpoint_url' string.")

        _hostname, pinned_ip, _port = await self.validate_ssrf_and_pin_ip(endpoint_url)
        _transport, client = self._create_hardened_client(pinned_ip=pinned_ip, secret_token=secret_token)

        try:
            client_gen = streamable_http_client(endpoint_url, http_client=client)
            read_stream, write_stream, _ = await client_gen.__aenter__()
            try:
                session = ClientSession(read_stream, write_stream)
                await session.initialize()
                tools_res = await session.list_tools()
                # Validate schemas returned
                for tool in tools_res.tools:
                    if not tool.name or not isinstance(tool.name, str):
                        raise DriverExecutionError("Invalid tool: tool name must be a non-empty string.")
                    if not isinstance(tool.inputSchema, dict):
                        raise DriverExecutionError(f"Invalid tool '{tool.name}': inputSchema must be a JSON object.")
                return True
            finally:
                try:
                    await client_gen.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug("Test connection context exit error: %s", e)
        except (ConnectorSecurityError, ConnectorValidationError, DriverExecutionError):
            raise
        except Exception as e:
            logger.error("MCP test connection failed for profile '%s': %s", profile.profile_id, e)
            raise ConnectorConnectionError(f"MCP test connection failed: {e}") from e
        finally:
            await client.aclose()

    async def _get_or_create_runtime_session(
        self,
        profile: ConnectorProfile,
        secret_token: str | None = None,
    ) -> McpActiveSession:
        """Obtain or lazily establish an active profile-scoped MCP runtime session."""
        active = self._active_sessions.get(profile.profile_id)
        if active is not None and active.is_connected:
            return active

        endpoint_url = profile.options.get("endpoint_url")
        if not endpoint_url or not isinstance(endpoint_url, str):
            raise ConnectorValidationError("Profile options must contain a valid 'endpoint_url' string.")

        _hostname, pinned_ip, _port = await self.validate_ssrf_and_pin_ip(endpoint_url)
        transport, client = self._create_hardened_client(pinned_ip=pinned_ip, secret_token=secret_token)

        client_gen = streamable_http_client(endpoint_url, http_client=client)
        try:
            read_stream, write_stream, _ = await client_gen.__aenter__()
            session = ClientSession(read_stream, write_stream)
            await session.initialize()

            active = McpActiveSession(
                profile_id=profile.profile_id,
                tenant_id=profile.tenant_id,
                endpoint_url=endpoint_url,
                secret_token=secret_token,
                transport=transport,
                http_client=client,
                client_gen=client_gen,
                session=session,
                registered_capabilities=set(),
            )
            self._active_sessions[profile.profile_id] = active

            # Perform initial tools/list reconciliation
            if self._registry_engine is not None:
                await self.reconcile_tools(profile, session)

            return active
        except Exception as e:
            await client.aclose()
            logger.error("Failed to establish lazy MCP runtime session for profile '%s': %s", profile.profile_id, e)
            raise ConnectorConnectionError(f"Failed to connect to MCP server: {e}") from e

    # -- Two-Phase Tools/List Reconciliation ---------------------------------

    async def reconcile_tools(self, profile: ConnectorProfile, session: ClientSession) -> list[str]:
        """Perform two-phase atomic reconciliation between remote tools/list and RegistryEngine.

        Phase 1: Validate all remote tool schemas in-memory. If ANY fails, abort with zero mutations.
        Phase 2: Perform atomic registry mutation (add, update, remove) with strict owner_id verification.
        """
        if self._registry_engine is None:
            logger.warning("RegistryEngine not configured for McpConnectorDriver; skipping reconciliation.")
            return []

        lock = self._reconciliation_locks.setdefault(profile.profile_id, asyncio.Lock())
        async with lock:
            self._known_profiles[profile.profile_id] = profile
            tools_res = await session.list_tools()

            # Phase 1: Pure in-memory validation
            candidate_tools: dict[str, Tool] = {}
            for tool in tools_res.tools:
                if not tool.name or not isinstance(tool.name, str) or "." in tool.name:
                    # Sanitize tool name: replace dots if remote tool name contains them
                    tool_name = tool.name.replace(".", "_") if tool.name else "unnamed"
                else:
                    tool_name = tool.name

                if not isinstance(tool.inputSchema, dict):
                    raise DriverExecutionError(
                        f"Reconciliation aborted: tool '{tool.name}' has non-object inputSchema."
                    )

                candidate_tools[tool_name] = tool

            # Phase 2: Compute diff against existing capabilities owned by profile
            prefix = f"kortex.mcp.{profile.profile_id}."
            current_all_caps = self._registry_engine.list_capabilities()
            existing_owned_caps: dict[str, CapabilityDescriptor] = {
                desc.name: desc
                for desc in current_all_caps
                if getattr(desc, "owner_id", None) == profile.profile_id and desc.name.startswith(prefix)
            }

            candidate_cap_names: set[str] = {f"{prefix}{t_name}" for t_name in candidate_tools}
            existing_cap_names: set[str] = set(existing_owned_caps.keys())

            to_remove = existing_cap_names - candidate_cap_names
            to_add = candidate_cap_names - existing_cap_names
            to_check_update = candidate_cap_names & existing_cap_names

            # Remove obsolete capabilities
            for cap_name in to_remove:
                self._registry_engine.unregister_capability(cap_name, owner_id=profile.profile_id)

            # Register new capabilities
            for tool_name, tool in candidate_tools.items():
                cap_name = f"{prefix}{tool_name}"
                handler = self._create_capability_handler(profile.profile_id, profile.tenant_id, tool.name)
                desc = tool.description or f"MCP tool '{tool.name}' from profile '{profile.name}'"

                if cap_name in to_add:
                    self._registry_engine.register_capability(
                        name=cap_name,
                        description=desc,
                        provider="connector",
                        handler=handler,
                        parameters_schema=tool.inputSchema,
                        returns_schema={},
                        requires_authentication=True,
                        requires_execution_context=True,
                        security_classification="INTERNAL",
                        is_read_only=False,
                        is_idempotent=False,
                        owner_id=profile.profile_id,
                    )
                elif cap_name in to_check_update:
                    # Update existing owned capability safely
                    self._registry_engine.register_capability(
                        name=cap_name,
                        description=desc,
                        provider="connector",
                        handler=handler,
                        parameters_schema=tool.inputSchema,
                        returns_schema={},
                        requires_authentication=True,
                        requires_execution_context=True,
                        security_classification="INTERNAL",
                        is_read_only=False,
                        is_idempotent=False,
                        owner_id=profile.profile_id,
                    )

            active = self._active_sessions.get(profile.profile_id)
            if active is not None:
                active.registered_capabilities = candidate_cap_names

            logger.info(
                "MCP tools reconciled for profile '%s': %d added, %d updated, %d removed.",
                profile.profile_id,
                len(to_add),
                len(to_check_update),
                len(to_remove),
            )
            return sorted(candidate_cap_names)

    def _create_capability_handler(
        self,
        profile_id: str,
        profile_tenant_id: str,
        remote_tool_name: str,
    ) -> Callable[..., Any]:
        """Construct an invocation handler that enforces tenant isolation and delegates to invoke_tool."""

        async def _mcp_handler(*args: Any, execution_context: Any = None, **kwargs: Any) -> Any:
            # Enforce tenant isolation structurally
            caller_tenant_id = getattr(execution_context, "tenant_id", None)
            if caller_tenant_id != profile_tenant_id:
                raise ConnectorSecurityError(
                    f"Cross-tenant capability invocation rejected: caller tenant '{caller_tenant_id}' "
                    f"does not match profile tenant '{profile_tenant_id}'."
                )

            # Extract arguments
            params = kwargs.get("parameters") or kwargs
            # Filter out execution_context / principal keywords from tool arguments
            tool_args = {k: v for k, v in params.items() if k not in ("execution_context", "principal")}

            result = await self.invoke_tool_with_retry(
                profile_id=profile_id,
                tenant_id=profile_tenant_id,
                tool_name=remote_tool_name,
                arguments=tool_args,
            )
            return result

        return _mcp_handler

    # -- Tool Invocation with Deterministic Retries --------------------------

    async def invoke_tool_with_retry(
        self,
        profile_id: str,
        tenant_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke an MCP tool with deterministic exponential backoff retries.

        Max 3 retries (total 4 attempts).
        Initial delay 1.0s, multiplier 2.0, max delay 8.0s, full jitter.
        """
        # Resolve profile and secret token
        profile = self._known_profiles.get(profile_id)
        if profile is None and hasattr(self, "_profile_manager") and self._profile_manager is not None:
            profile = await self._profile_manager.get_profile(profile_id, tenant_id=tenant_id)
        elif profile is None:
            # Fallback to active session profile information
            active = self._active_sessions.get(profile_id)
            if active is not None:
                profile = ConnectorProfile(
                    profile_id=profile_id,
                    tenant_id=tenant_id,
                    name=profile_id,
                    driver_id="connector-mcp",
                    options={"endpoint_url": active.endpoint_url},
                )

        if profile is None:
            raise DriverExecutionError(f"Cannot invoke MCP tool: profile '{profile_id}' not found.")

        secret_token: str | None = None
        if profile.secret_handle and self._secret_resolver:
            secret_token = await self._secret_resolver(profile.secret_handle, tenant_id)

        max_retries = 3
        initial_delay = 1.0
        multiplier = 2.0
        max_delay = 8.0

        for attempt in range(max_retries + 1):
            try:
                active_session = await self._get_or_create_runtime_session(profile, secret_token=secret_token)
                async with active_session.lock:
                    call_result: CallToolResult = await active_session.session.call_tool(
                        name=tool_name,
                        arguments=arguments,
                    )

                    # Format structured result
                    output_content: list[dict[str, Any]] = []
                    for item in call_result.content:
                        if hasattr(item, "text"):
                            output_content.append({"type": "text", "text": item.text})
                        elif hasattr(item, "data"):
                            output_content.append({"type": "data", "data": str(item.data)})
                        else:
                            output_content.append({"type": "unknown", "raw": str(item)})

                    return {
                        "is_error": bool(call_result.isError),
                        "content": output_content,
                        "tool_name": tool_name,
                        "profile_id": profile_id,
                    }

            except (
                ConnectorSecurityError,
                ConnectorValidationError,
            ):
                # Non-retryable security / validation error: fail immediately
                raise
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                StreamableHTTPError,
                ConnectorConnectionError,
            ) as e:
                # Mark session disconnected to force fresh reconnect
                if profile_id in self._active_sessions:
                    await self._active_sessions[profile_id].close()
                    del self._active_sessions[profile_id]

                if attempt >= max_retries:
                    raise ConnectorConnectionError(
                        f"MCP tool invocation failed after {max_retries} retries: {e}"
                    ) from e

                delay = random.uniform(0, min(max_delay, initial_delay * (multiplier**attempt)))  # noqa: S311
                logger.warning(
                    "MCP transport error on tool '%s' (attempt %d/%d), retrying in %.2fs: %s",
                    tool_name,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
            except Exception as e:
                # Protocol or schema failure: non-retryable
                raise DriverExecutionError(f"MCP tool invocation failed: {e}") from e

        raise ConnectorConnectionError("MCP tool invocation exhausted retries.")

    # -- BaseConnectorDriver Implementation ----------------------------------

    async def execute_action(self, request: ActionRequest, secret_token: str | None = None) -> ActionResult:
        """Execute a connector action against target MCP profile."""
        start_time = time.perf_counter()

        if request.action_type == ConnectorActionType.VERIFY:
            # Verification / test connection action
            temp_profile = ConnectorProfile(
                profile_id=request.profile_id,
                tenant_id=request.tenant_id,
                name=request.profile_id,
                driver_id="connector-mcp",
                options=request.options or request.payload,
            )
            success = await self.test_connection(temp_profile, secret_token=secret_token)
            execution_time = (time.perf_counter() - start_time) * 1000.0
            return ActionResult(
                request_id=request.request_id,
                status="SUCCESS" if success else "FAILED",
                response_payload={"verified": success},
                execution_time_ms=execution_time,
                correlation_id=request.correlation_id,
            )

        # Standard tool execution action
        tool_name = request.payload.get("tool_name")
        if not tool_name or not isinstance(tool_name, str):
            raise DriverExecutionError("Invalid request: payload must contain a 'tool_name' string.")

        arguments = request.payload.get("arguments", {})
        if not isinstance(arguments, dict):
            raise DriverExecutionError("Invalid request: 'arguments' must be a JSON dictionary.")

        res = await self.invoke_tool_with_retry(
            profile_id=request.profile_id,
            tenant_id=request.tenant_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        execution_time = (time.perf_counter() - start_time) * 1000.0
        status = "FAILED" if res.get("is_error") else "SUCCESS"
        return ActionResult(
            request_id=request.request_id,
            status=status,
            response_payload=res,
            execution_time_ms=execution_time,
            correlation_id=request.correlation_id,
        )

    # -- Teardown / Cleanup --------------------------------------------------

    async def teardown_profile(self, profile_id: str, tenant_id: str | None = None) -> None:
        """Idempotently close runtime session and unregister all owned capabilities."""
        # 1. Close runtime session if active
        self._known_profiles.pop(profile_id, None)
        active = self._active_sessions.pop(profile_id, None)
        if active is not None:
            await active.close()

        # 2. Unregister capabilities from RegistryEngine
        if self._registry_engine is not None:
            prefix = f"kortex.mcp.{profile_id}."
            all_caps = self._registry_engine.list_capabilities()
            for cap in all_caps:
                if getattr(cap, "owner_id", None) == profile_id or cap.name.startswith(prefix):
                    try:
                        self._registry_engine.unregister_capability(cap.name, owner_id=profile_id)
                    except Exception as e:
                        logger.debug("Idempotent unregister for '%s' during teardown: %s", cap.name, e)

        logger.info("MCP profile '%s' torn down completely.", profile_id)


__all__ = ["McpActiveSession", "McpConnectorDriver"]
