"""MCP session: initialize handshake, tool discovery and tool calls."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from typing import Any

from ..tools import ToolResult
from .config import ServerConfig
from .errors import (
    CONNECTION_ERROR,
    HANDSHAKE_FAILED,
    INVALID_JSON,
    TIMEOUT,
    McpError,
    map_rpc_error,
    tool_failure,
)
from .matcher import ResponseMatcher
from .protocol import (
    CANCELLED,
    INITIALIZE,
    INITIALIZED,
    TOOLS_CALL,
    TOOLS_LIST,
    IdFactory,
    Notification,
    Request,
    Response,
    RpcParseError,
    parse_message,
)
from .transports import Transport, build_transport

logger = logging.getLogger("zxcode.mcp.session")

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18",)
CLIENT_INFO = {"name": "ZXCode", "version": "0.1.0"}


class McpSession:
    """One connected MCP server: handshake, discovery and tool calls."""

    def __init__(
        self, server: ServerConfig, transport: Transport | None = None
    ) -> None:
        self.server = server
        self.transport: Transport = transport or build_transport(server)
        self.transport.set_message_handler(self._handle_message)
        self.transport.set_close_handler(self._on_transport_closed)
        self._matcher = ResponseMatcher()
        self._ids = IdFactory()
        self._connected = False
        self._tools: list[dict[str, Any]] | None = None
        self.protocol_version: str | None = None
        self.last_used = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._connected:
            return
        await self.transport.connect()
        self._connected = True
        try:
            await self._initialize()
        except Exception:
            self._connected = False
            await self.transport.close()
            raise

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.ensure_connected()
        if self._tools is None:
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                params = {"cursor": cursor} if cursor else {}
                response = await self._send_request(
                    TOOLS_LIST, params, self.server.call_timeout_seconds
                )
                result = self._result_or_raise(response, "tools/list")
                items = result.get("tools")
                if not isinstance(items, list):
                    raise McpError(INVALID_JSON, "tools/list returned no tools array")
                tools.extend(item for item in items if isinstance(item, dict))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            self._tools = tools
        return self._tools

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult:
        await self.ensure_connected()
        response = await self._send_request(
            TOOLS_CALL,
            {"name": name, "arguments": dict(arguments)},
            self.server.call_timeout_seconds,
        )
        if response.is_error:
            return tool_failure(
                map_rpc_error(response.error.code),
                response.error.message or f"remote tool {name} failed",
                server=self.server.name,
                tool=name,
            )
        result = response.result if isinstance(response.result, dict) else {}
        output, is_error = _render_tool_result(result)
        if is_error:
            return tool_failure(
                "remote_error",
                output or f"remote tool {name} returned an error",
                server=self.server.name,
                tool=name,
            )
        return ToolResult(
            True, output, metadata={"server": self.server.name, "tool": name}
        )

    async def close(self) -> None:
        self._connected = False
        self._matcher.fail_all(CONNECTION_ERROR, "connection closed")
        await self.transport.close()

    # ------------------------------------------------------------------ #

    async def _initialize(self) -> None:
        params = {
            "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }
        response = await self._send_request(
            INITIALIZE, params, self.server.connect_timeout_seconds
        )
        if response.is_error:
            message = response.error.message or "initialize failed"
            if response.error.code in (-32602, -32601):
                raise McpError(HANDSHAKE_FAILED, f"handshake failed: {message}")
            raise McpError(
                map_rpc_error(response.error.code), f"initialize failed: {message}"
            )
        result = response.result if isinstance(response.result, dict) else {}
        version = result.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise McpError(
                HANDSHAKE_FAILED, f"unsupported protocol version: {version!r}"
            )
        self.protocol_version = version
        set_session = getattr(self.transport, "set_session", None)
        if set_session is not None:
            session_id = getattr(self.transport, "session_id", None)
            set_session(session_id, version)
        await self.transport.send(Notification(INITIALIZED).to_dict())

    async def _send_request(
        self, method: str, params: dict[str, Any], timeout: float
    ) -> Response:
        await self.ensure_connected()
        self.last_used = time.monotonic()
        request = Request(self._ids.next(), method, params)
        future = self._matcher.register(request.id, method)
        try:
            await self.transport.send(request.to_dict())
        except McpError:
            self._matcher.abandon(request.id)
            raise
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self._matcher.abandon(request.id)
            await self._send_cancelled(request.id)
            raise McpError(TIMEOUT, f"{method} timed out after {timeout:g}s")
        except asyncio.CancelledError:
            self._matcher.abandon(request.id)
            raise

    async def _send_cancelled(self, request_id: int | str) -> None:
        try:
            await self.transport.send(
                Notification(CANCELLED, {"requestId": request_id}).to_dict()
            )
        except McpError:
            logger.debug("failed to send cancellation notification")

    def _handle_message(self, value: dict[str, Any]) -> None:
        try:
            message = parse_message(value)
        except RpcParseError:
            request_id = value.get("id")
            if isinstance(request_id, (int, str)) and not isinstance(request_id, bool):
                self._matcher.fail(
                    request_id, INVALID_JSON, "invalid message from server"
                )
            else:
                logger.warning(
                    "ignoring invalid message from server: %r", str(value)[:200]
                )
            return
        if isinstance(message, Response):
            self._matcher.resolve(message)
        elif isinstance(message, Notification):
            logger.debug("ignoring server notification: %s", message.method)
        else:
            logger.debug("ignoring server request: %s", message.method)

    def _on_transport_closed(self) -> None:
        if not self._connected:
            return
        self._connected = False
        self._matcher.fail_all(CONNECTION_ERROR, "connection closed by server")

    def _result_or_raise(self, response: Response, method: str) -> dict[str, Any]:
        if response.is_error:
            raise McpError(
                map_rpc_error(response.error.code),
                f"{method} failed: {response.error.message or 'server error'}",
            )
        result = response.result
        if not isinstance(result, dict):
            raise McpError(INVALID_JSON, f"{method} returned a non-object result")
        return result


def _render_tool_result(result: dict[str, Any]) -> tuple[str, bool]:
    texts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                texts.append(item["text"])
    output = "\n".join(texts)
    if not output and result.get("structuredContent") is not None:
        output = json.dumps(result["structuredContent"], ensure_ascii=False)
    return output, bool(result.get("isError"))
