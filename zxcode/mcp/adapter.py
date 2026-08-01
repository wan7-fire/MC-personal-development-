"""Adapter turning discovered MCP tools into ZXCode Tool instances."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..tools import Tool, ToolContext, ToolResult
from .errors import McpError, tool_failure
from .security import describe_tool, is_read_only, require_confirmation

if TYPE_CHECKING:
    from .config import ServerConfig
    from .session import McpSession

logger = logging.getLogger("zxcode.mcp.adapter")

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SCHEMA_DEPTH = 8


class AllowState:
    """Per-server set of write tools the user approved for this process."""

    def __init__(self) -> None:
        self._allowed: set[str] = set()

    def is_allowed(self, name: str) -> bool:
        return name in self._allowed

    def allow(self, name: str) -> None:
        self._allowed.add(name)


class RemoteTool(Tool):
    """One tool discovered on a remote MCP server."""

    def __init__(
        self,
        server: "ServerConfig",
        session: "McpSession",
        definition: dict[str, Any],
        *,
        read_only: bool,
        allow_state: AllowState,
        timeout_seconds: float,
    ) -> None:
        self.name = f"{server.name}_{definition['name']}"
        self.description = describe_tool(server, definition)
        self.input_schema = _normalize_schema(definition.get("inputSchema"))
        self.read_only = read_only
        self.timeout_seconds = timeout_seconds
        self.server = server
        self.session = session
        self.remote_name = definition["name"]
        self.allow_state = allow_state

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if not self.read_only and not self.allow_state.is_allowed(self.name):
            blocked = await require_confirmation(
                context,
                f"MCP {self.server.name}: {self.remote_name}",
                dict(arguments),
                server=self.server.name,
                tool=self.name,
            )
            if blocked is not None:
                return blocked
            self.allow_state.allow(self.name)
        try:
            return await self.session.call_tool(self.remote_name, dict(arguments))
        except McpError as error:
            return tool_failure(
                error.code, error.message, server=self.server.name, tool=self.name
            )


def build_tools(
    server: "ServerConfig",
    session: "McpSession",
    tools: Sequence[dict[str, Any]],
    allow_state: AllowState | None = None,
) -> list[RemoteTool]:
    """Build and validate one RemoteTool per usable server tool."""
    state = allow_state or AllowState()
    built: list[RemoteTool] = []
    for raw in tools:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in server.disabled_tools:
            continue
        prefixed = f"{server.name}_{name}"
        if not _TOOL_NAME.fullmatch(prefixed):
            logger.warning("skipping remote tool %r: invalid name after prefixing", prefixed)
            continue
        built.append(
            RemoteTool(
                server,
                session,
                raw,
                read_only=is_read_only(server, raw),
                allow_state=state,
                timeout_seconds=server.call_timeout_seconds,
            )
        )
    return built


def _normalize_schema(schema: Any) -> dict[str, Any]:
    """Force a strict-mode friendly object schema with bounded nesting."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": False}
    return _normalize_object(schema, 0)


def _normalize_object(schema: dict[str, Any], depth: int) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    if depth >= MAX_SCHEMA_DEPTH:
        return normalized
    properties = schema.get("properties")
    if isinstance(properties, dict):
        normalized["properties"] = {
            str(key): _normalize_property(value, depth + 1)
            for key, value in properties.items()
            if isinstance(value, dict)
        }
    required = schema.get("required")
    if isinstance(required, list):
        valid = [
            str(key)
            for key in required
            if isinstance(key, str) and key in normalized["properties"]
        ]
        if valid:
            normalized["required"] = valid
    return normalized


def _normalize_property(schema: dict[str, Any], depth: int) -> dict[str, Any]:
    prop = dict(schema)
    prop_type = prop.get("type")
    if prop_type == "object" and depth < MAX_SCHEMA_DEPTH:
        nested = prop.get("properties")
        if isinstance(nested, dict):
            prop["properties"] = {
                str(key): _normalize_property(value, depth + 1)
                for key, value in nested.items()
                if isinstance(value, dict)
            }
        prop["additionalProperties"] = False
    elif prop_type == "array" and depth < MAX_SCHEMA_DEPTH:
        items = prop.get("items")
        if isinstance(items, dict) and items.get("type") == "object":
            prop["items"] = _normalize_object(items, depth + 1)
    return prop
