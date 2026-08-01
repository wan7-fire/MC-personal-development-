"""Connection pool: lazy connect, reuse, idle reclamation and shutdown."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from ..tools import ToolRegistry
from .adapter import AllowState, build_tools
from .config import McpConfig, load_config
from .session import McpSession

logger = logging.getLogger("zxcode.mcp.pool")

SWEEP_INTERVAL_SECONDS = 30.0


class McpManager:
    """Owns every MCP session and keeps them alive across tool calls."""

    def __init__(self, config: McpConfig) -> None:
        self.config = config
        self._sessions: dict[str, McpSession] = {}
        self._allow_states: dict[str, AllowState] = {}
        self._sweep_task: asyncio.Task[None] | None = None

    @classmethod
    def from_root(cls, root: Path) -> "McpManager":
        return cls(load_config(root))

    def get_session(self, name: str) -> McpSession | None:
        return self._sessions.get(name)

    async def register_all(self, registry: ToolRegistry) -> list[dict[str, Any]]:
        """Connect every configured server, discover tools and register them."""
        if self.config.servers and self._sweep_task is None:
            self._sweep_task = asyncio.create_task(
                self._sweep_loop(), name="mcp-idle-sweep"
            )
        report: list[dict[str, Any]] = []
        for server in self.config.servers:
            try:
                session = await self._connect(server.name)
                tools = await session.list_tools()
                built = build_tools(
                    server, session, tools, self._allow_state(server.name)
                )
                for tool in built:
                    if registry.get(tool.name) is None:
                        registry.register(tool)
                report.append(
                    {"server": server.name, "ok": True, "tools": len(built)}
                )
            except Exception as error:  # noqa: BLE001 - report and keep going
                logger.warning("MCP server %r unavailable: %s", server.name, error)
                report.append(
                    {"server": server.name, "ok": False, "error": str(error)}
                )
        return report

    async def close_all(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - best effort shutdown
                logger.exception("failed to close MCP session")
        self._allow_states.clear()

    async def _connect(self, name: str) -> McpSession:
        session = self._sessions.get(name)
        if session is None:
            server = next(
                (item for item in self.config.servers if item.name == name), None
            )
            if server is None:
                raise ValueError(f"unknown MCP server: {name}")
            session = McpSession(server)
            await session.connect()
            self._sessions[name] = session
        elif not session.connected:
            await session.connect()
        return session

    def _allow_state(self, name: str) -> AllowState:
        state = self._allow_states.get(name)
        if state is None:
            state = AllowState()
            self._allow_states[name] = state
        return state

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            await self._sweep_idle()

    async def _sweep_idle(self) -> None:
        now = time.monotonic()
        for name, session in list(self._sessions.items()):
            if not session.connected:
                continue
            idle = now - session.last_used
            if idle > session.server.idle_timeout_seconds:
                logger.info("closing idle MCP server %r", name)
                await session.close()
                self._sessions.pop(name, None)
