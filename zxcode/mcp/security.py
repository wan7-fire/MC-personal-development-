"""Security integration for remote MCP tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..tools import ToolContext, ToolResult
from .errors import tool_failure

if TYPE_CHECKING:
    from .config import ServerConfig


def is_read_only(server: "ServerConfig", definition: dict[str, Any]) -> bool:
    """Conservative read-only classification for one remote tool.

    A tool is read-only only when the config explicitly lists it, or when
    the server is trusted and declares a readOnlyHint.  Everything else is a
    write tool and goes through the confirmation flow.
    """
    name = definition.get("name")
    if isinstance(name, str) and name in server.read_only_tools:
        return True
    annotations = definition.get("annotations")
    read_only_hint = (
        isinstance(annotations, dict) and annotations.get("readOnlyHint") is True
    )
    return bool(server.trusted and read_only_hint)


def describe_tool(server: "ServerConfig", definition: dict[str, Any]) -> str:
    description = definition.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    title = definition.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return f"Remote tool '{definition.get('name', '')}' provided by server {server.name}"


async def require_confirmation(
    context: ToolContext,
    title: str,
    arguments: dict[str, Any],
    *,
    server: str,
    tool: str,
) -> ToolResult | None:
    """Return a failure result when the user denies; ``None`` to proceed."""
    if context.confirm is None:
        return tool_failure(
            "permission_denied",
            "security check requires confirmation",
            server=server,
            tool=tool,
        )
    detail = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    choice = await context.confirm(title, detail)
    if choice == "deny":
        return tool_failure(
            "permission_denied",
            "permission denied by user",
            server=server,
            tool=tool,
        )
    return None
