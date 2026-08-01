"""Error mapping between JSON-RPC failures and ZXCode tool results."""

from __future__ import annotations

from typing import Any

from ..tools import ToolResult

RPC_ERROR_CODES: dict[int, str] = {
    -32700: "invalid_json",
    -32600: "invalid_request",
    -32601: "unknown_method",
    -32602: "invalid_arguments",
}

CONNECTION_ERROR = "connection_error"
HANDSHAKE_FAILED = "handshake_failed"
TIMEOUT = "timeout"
INVALID_JSON = "invalid_json"


def map_rpc_error(code: int) -> str:
    return RPC_ERROR_CODES.get(code, "remote_error")


class McpError(Exception):
    """Transport or protocol failure with a stable ZXCode error code."""

    def __init__(self, code: str, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def tool_failure(code: str, message: str, **metadata: Any) -> ToolResult:
    return ToolResult(
        False,
        error={"code": code, "message": message},
        metadata=metadata,
    )
