"""
Level-2 local HTTP end-to-end harness for connector-backed workflow tests.

Purpose: let an integration test drive the *real* production path

    Kernel capability -> ConnectorEngine -> ConnectorPipeline
    -> HttpRestConnectorDriver -> httpx -> SSRFHardenedTransport
    -> PinnedIPNetworkBackend -> real TCP socket

all the way across an actual HTTP transport boundary, terminating in a local
server owned by the test rather than in a simulated driver. Nothing in the
production dispatch chain is mocked, stubbed or bypassed: the only thing this
module supplies is the far side of the socket.

The server is deliberately built on `asyncio.start_server` alone -- stdlib only,
no new dependency, no thread, no subprocess, no fixed port and no sleep-based
synchronisation. It binds `127.0.0.1` on port 0 (the OS assigns a free port),
runs on the same event loop as the test it serves, and is closed inside the
test's own lifecycle, so it cannot leak a thread, a process or a port.

`loopback_ssrf_allowance` is the one, deliberately narrow concession the harness
needs. `HttpRestConnectorDriver._verify_ip_object` correctly refuses every
loopback/private/link-local address, which is exactly the policy production
wants and exactly what makes a local test server unreachable. The allowance
swaps that single policy predicate for one that permits *only* the harness's own
loopback address and delegates every other address to the original, unmodified
implementation -- so SSRF protection stays fully enforced for every address the
test does not itself own, and is restored on exit. No production module is
modified; the driver's request building, header handling, body encoding, method
resolution, DNS pre-resolution, IP pinning, transport, streaming response read,
size limits and response-header allowlisting all execute unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver

LOOPBACK_HOST = "127.0.0.1"

_STATUS_REASONS: dict[int, str] = {
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    500: "Internal Server Error",
}


@dataclass(frozen=True)
class RecordedHttpRequest:
    """One HTTP request as it was actually received off the wire by the harness."""

    method: str
    target: str
    headers: dict[str, str]
    body: bytes

    @property
    def text_body(self) -> str:
        return self.body.decode("utf-8")

    @property
    def json_body(self) -> Any:
        return json.loads(self.text_body)


ResponseFactory = Callable[[RecordedHttpRequest], tuple[int, Any]]


def _default_response_factory(request: RecordedHttpRequest) -> tuple[int, Any]:
    return 200, {"received": True, "target": request.target}


class LocalHttpTestServer:
    """A minimal, deterministic HTTP/1.1 server bound to an ephemeral loopback port.

    Records every request it receives (method, target, headers, raw body) and answers with a
    JSON response produced by `response_factory`, so a test can assert on the *observable*
    request that crossed the transport boundary rather than on an in-process call record.
    """

    def __init__(self, response_factory: ResponseFactory | None = None) -> None:
        self._response_factory: ResponseFactory = response_factory or _default_response_factory
        self._server: asyncio.AbstractServer | None = None
        self._port = 0
        self.requests: list[RecordedHttpRequest] = []

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("LocalHttpTestServer has not been started.")
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK_HOST}:{self.port}"

    def url(self, path: str) -> str:
        suffix = path.lstrip("/")
        return f"{self.base_url}/{suffix}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, LOOPBACK_HOST, 0)
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("LocalHttpTestServer failed to bind a loopback socket.")
        self._port = int(sockets[0].getsockname()[1])

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            recorded = await self._read_request(reader)
            self.requests.append(recorded)
            status, payload = self._response_factory(recorded)
            writer.write(self._encode_response(status, payload))
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            # Client hung up mid-request (e.g. connection-pool teardown). Nothing to record.
            return
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, asyncio.IncompleteReadError):
                await writer.wait_closed()

    @staticmethod
    async def _read_request(reader: asyncio.StreamReader) -> RecordedHttpRequest:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _version = lines[0].split(" ", 2)

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(content_length) if content_length > 0 else b""
        return RecordedHttpRequest(method=method, target=target, headers=headers, body=body)

    @staticmethod
    def _encode_response(status: int, payload: Any) -> bytes:
        body = json.dumps(payload).encode("utf-8")
        reason = _STATUS_REASONS.get(status, "Unknown")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("latin-1")
        return head + body


@contextlib.contextmanager
def loopback_ssrf_allowance(allowed_ip: str = LOOPBACK_HOST) -> Iterator[None]:
    """Permit `HttpRestConnectorDriver` to reach exactly one loopback address, and nothing else.

    Every other address continues through the driver's original, unmodified SSRF verification,
    and the original predicate is restored on exit.
    """
    original = HttpRestConnectorDriver._verify_ip_object

    def _verify_ip_object(self: HttpRestConnectorDriver, ip_str: str) -> None:
        if ip_str == allowed_ip:
            return
        original(self, ip_str)

    HttpRestConnectorDriver._verify_ip_object = _verify_ip_object  # type: ignore[method-assign]
    try:
        yield
    finally:
        HttpRestConnectorDriver._verify_ip_object = original  # type: ignore[method-assign]


@contextlib.asynccontextmanager
async def local_http_endpoint(
    response_factory: ResponseFactory | None = None,
) -> AsyncIterator[LocalHttpTestServer]:
    """Start a loopback HTTP server and allow the connector driver to reach it, for the block's
    duration only. Both the server and the SSRF allowance are torn down on exit, including on
    failure."""
    server = LocalHttpTestServer(response_factory)
    await server.start()
    try:
        with loopback_ssrf_allowance():
            yield server
    finally:
        await server.stop()


__all__ = [
    "LOOPBACK_HOST",
    "LocalHttpTestServer",
    "RecordedHttpRequest",
    "ResponseFactory",
    "local_http_endpoint",
    "loopback_ssrf_allowance",
]
