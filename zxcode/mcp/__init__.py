"""External MCP server client: JSON-RPC transport, session and tool adapter."""

from __future__ import annotations

from .adapter import AllowState, RemoteTool, build_tools
from .config import ConfigError, McpConfig, ServerConfig, load_config
from .errors import (
    CONNECTION_ERROR,
    HANDSHAKE_FAILED,
    TIMEOUT,
    McpError,
    map_rpc_error,
    tool_failure,
)
from .matcher import PendingRequest, ResponseMatcher
from .pool import McpManager
from .protocol import (
    INITIALIZE,
    INITIALIZED,
    TOOLS_CALL,
    TOOLS_LIST,
    CANCELLED,
    IdFactory,
    Notification,
    Request,
    Response,
    RpcError,
    RpcParseError,
    parse_json_line,
    parse_message,
)
from .security import describe_tool, is_read_only, require_confirmation
from .session import McpSession

__all__ = [
    "CONNECTION_ERROR",
    "HANDSHAKE_FAILED",
    "TIMEOUT",
    "McpError",
    "map_rpc_error",
    "tool_failure",
    "PendingRequest",
    "ResponseMatcher",
    "INITIALIZE",
    "INITIALIZED",
    "TOOLS_CALL",
    "TOOLS_LIST",
    "CANCELLED",
    "IdFactory",
    "Notification",
    "Request",
    "Response",
    "RpcError",
    "RpcParseError",
    "parse_json_line",
    "parse_message",
    "ConfigError",
    "McpConfig",
    "ServerConfig",
    "load_config",
    "McpManager",
    "McpSession",
    "AllowState",
    "RemoteTool",
    "build_tools",
    "describe_tool",
    "is_read_only",
    "require_confirmation",
]
