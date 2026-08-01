"""JSON-RPC 2.0 message model used by the MCP client.

Only the message shapes needed by the ZXCode client are modeled: requests,
responses (result or error) and notifications.  Params are kept as plain
dicts so protocol messages round-trip without schema drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP methods used by this client.
INITIALIZE = "initialize"
INITIALIZED = "notifications/initialized"
TOOLS_LIST = "tools/list"
TOOLS_CALL = "tools/call"
CANCELLED = "notifications/cancelled"


class RpcParseError(ValueError):
    """Raised when a decoded value is not a valid JSON-RPC message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RpcError:
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


@dataclass(frozen=True)
class Request:
    id: int | str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self.id,
            "method": self.method,
        }
        if self.params:
            payload["params"] = self.params
        return payload


@dataclass(frozen=True)
class Response:
    id: int | str
    result: Any = None
    error: RpcError | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": self.id}
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = self.result
        return payload


@dataclass(frozen=True)
class Notification:
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": self.method}
        if self.params:
            payload["params"] = self.params
        return payload


Message = Request | Response | Notification


def _valid_id(value: Any) -> bool:
    return isinstance(value, (int, str)) and not isinstance(value, bool)


def parse_message(value: Any) -> Message:
    """Validate one decoded JSON value as a JSON-RPC message."""
    if not isinstance(value, dict):
        raise RpcParseError(INVALID_REQUEST, "message must be a JSON object")
    if value.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcParseError(INVALID_REQUEST, "jsonrpc must be \"2.0\"")
    if "id" not in value:
        method = value.get("method")
        if not isinstance(method, str) or not method:
            raise RpcParseError(INVALID_REQUEST, "notification requires a method")
        raw_params = value.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            raise RpcParseError(INVALID_REQUEST, "params must be an object")
        params = raw_params or {}
        return Notification(method, params)
    request_id = value["id"]
    if not _valid_id(request_id):
        raise RpcParseError(INVALID_REQUEST, "id must be a string or number")
    method = value.get("method")
    if isinstance(method, str) and "error" not in value and "result" not in value:
        raw_params = value.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            raise RpcParseError(INVALID_REQUEST, "params must be an object")
        params = raw_params or {}
        return Request(request_id, method, params)
    has_result = "result" in value
    has_error = "error" in value
    if has_result == has_error:
        raise RpcParseError(INVALID_REQUEST, "response must have exactly one of result or error")
    if has_error:
        raw = value["error"]
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("code"), int)
            or not isinstance(raw.get("message"), str)
        ):
            raise RpcParseError(INVALID_REQUEST, "error must contain code and message")
        return Response(request_id, None, RpcError(raw["code"], raw["message"], raw.get("data")))
    return Response(request_id, value["result"], None)


def parse_json_line(line: str) -> tuple[Message | None, RpcParseError | None]:
    """Parse one JSON line into a message.

    Returns ``(message, None)`` on success and ``(None, error)`` when the
    line is not a valid JSON-RPC message.  A completely unparseable JSON
    payload yields a parse error carrying ``PARSE_ERROR``.
    """
    text = line.strip()
    if not text:
        return None, None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return None, RpcParseError(PARSE_ERROR, f"invalid JSON: {error.msg}")
    try:
        return parse_message(value), None
    except RpcParseError as error:
        return None, error


class IdFactory:
    """Monotonic request id generator, one instance per session."""

    def __init__(self) -> None:
        self._next = 1

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value
