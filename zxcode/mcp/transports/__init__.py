"""Transport abstraction for MCP client connections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ServerConfig

MessageHandler = Callable[[dict[str, Any]], None]
CloseHandler = Callable[[], None]


class Transport(ABC):
    """Byte-level connection to one MCP server.

    Implementations deliver every parsed JSON-RPC message to the message
    handler and notify the close handler when the connection dies or is
    closed, so the session layer never cares about framing details.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Open the underlying connection and start receiving messages."""

    @abstractmethod
    async def send(self, message: Mapping[str, Any]) -> None:
        """Serialize and send one JSON-RPC message."""

    @abstractmethod
    async def close(self) -> None:
        """Shut the connection down; must be safe to call twice."""

    def set_message_handler(self, handler: MessageHandler) -> None:
        self._message_handler = handler

    def set_close_handler(self, handler: CloseHandler) -> None:
        self._close_handler = handler


def build_transport(server: ServerConfig) -> Transport:
    from .http import HttpTransport
    from .stdio import StdioTransport

    if server.transport == "http":
        return HttpTransport(
            server.url,
            headers=server.headers,
            timeout=server.call_timeout_seconds,
            connect_timeout=server.connect_timeout_seconds,
        )
    return StdioTransport(
        server.command,
        env=server.env,
        cwd=server.cwd,
    )
