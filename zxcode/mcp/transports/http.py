"""Streamable HTTP transport for MCP servers."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from typing import Any

import httpx

from ..errors import CONNECTION_ERROR, INVALID_JSON, TIMEOUT, McpError
from . import Transport

logger = logging.getLogger("zxcode.mcp.http")

ACCEPT_HEADER = "application/json, text/event-stream"


class HttpTransport(Transport):
    """POSTs JSON-RPC messages to a single endpoint.

    The server may answer with plain JSON or with an SSE stream; the session
    id returned during initialize is echoed on every later request together
    with the negotiated protocol version.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
    ) -> None:
        self.url = url
        self.base_headers = dict(headers or {})
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._client: httpx.AsyncClient | None = None

    def set_session(self, session_id: str | None, protocol_version: str | None) -> None:
        self.session_id = session_id
        self.protocol_version = protocol_version

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=self.connect_timeout)
        )

    async def send(self, message: Mapping[str, Any]) -> None:
        client = self._client
        if client is None:
            raise McpError(CONNECTION_ERROR, "http transport is not connected")
        headers = {
            "Accept": ACCEPT_HEADER,
            "Content-Type": "application/json",
            **self.base_headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        try:
            response = await client.post(self.url, json=dict(message), headers=headers)
        except httpx.TimeoutException as error:
            raise McpError(TIMEOUT, "http request timed out") from error
        except httpx.HTTPError as error:
            raise McpError(CONNECTION_ERROR, f"http request failed: {type(error).__name__}") from error

        incoming_session = response.headers.get("mcp-session-id")
        if incoming_session:
            self.session_id = incoming_session
        if response.status_code == 202:
            if "id" in message:
                raise McpError(
                    TIMEOUT, "server accepted the request without a response body"
                )
            return
        if response.status_code >= 400:
            raise McpError(
                CONNECTION_ERROR, f"http server returned status {response.status_code}"
            )
        content_type = response.headers.get("content-type", "")
        body = response.text
        if "text/event-stream" in content_type.lower():
            for payload in _iter_sse_payloads(body):
                self._deliver_payload(payload)
            return
        if not body.strip():
            raise McpError(TIMEOUT, "server returned an empty response body")
        self._deliver_payload(body)

    def _deliver_payload(self, payload: str) -> None:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise McpError(INVALID_JSON, f"invalid JSON in http response: {error.msg}") from error
        handler = getattr(self, "_message_handler", None)
        if handler is not None:
            handler(value)

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()


def _iter_sse_payloads(text: str) -> Iterator[str]:
    """Yield the JSON payload of each SSE event in ``text``."""
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "" and data_lines:
            yield "\n".join(data_lines)
            data_lines = []
    if data_lines:
        yield "\n".join(data_lines)
