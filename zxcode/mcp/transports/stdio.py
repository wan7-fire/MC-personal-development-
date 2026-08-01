"""stdio transport: a local subprocess exchanging newline-delimited JSON."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..errors import CONNECTION_ERROR, McpError
from . import Transport

logger = logging.getLogger("zxcode.mcp.stdio")

EXIT_WAIT_SECONDS = 5.0


class StdioTransport(Transport):
    """Runs ``command`` and speaks newline-delimited JSON over stdio.

    stderr is captured as a diagnostic tail only; it never carries protocol
    messages.  Shutdown follows the MCP lifecycle: close stdin, wait for the
    child to exit, then terminate the process tree on Windows.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        encoding: str = "utf-8",
    ) -> None:
        self.command = list(command)
        self.env = dict(env or {})
        self.cwd = cwd
        self.encoding = encoding
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._close_notified = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr_tail)

    async def connect(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        env = dict(os.environ)
        env.update(self.env)
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                env=env,
                creationflags=flags,
            )
        except OSError as error:
            raise McpError(
                CONNECTION_ERROR, f"failed to start stdio server: {error}"
            ) from error
        self._reader_task = asyncio.create_task(self._read_loop(), name="mcp-stdio-read")
        self._stderr_task = asyncio.create_task(self._stderr_loop(), name="mcp-stdio-err")

    async def send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            raise McpError(CONNECTION_ERROR, "stdio server is not running")
        data = (json.dumps(dict(message), ensure_ascii=False) + "\n").encode(
            self.encoding
        )
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise McpError(CONNECTION_ERROR, "stdio server closed its input") from error

    async def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            await asyncio.wait_for(process.wait(), EXIT_WAIT_SECONDS)
        except TimeoutError:
            await _kill_tree(process)
        tasks = [
            task
            for task in (self._reader_task, self._stderr_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._notify_close()

    async def _read_loop(self) -> None:
        process = self._process
        try:
            while process is not None and process.returncode is None:
                line = await process.stdout.readline()
                if not line:
                    break
                self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("stdio reader failed")
        finally:
            self._notify_close()

    def _handle_line(self, line: bytes) -> None:
        text = line.decode(self.encoding, errors="replace").strip()
        if not text:
            return
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            # Malformed JSON cannot be matched to a request; log and move on
            # so the connection stays usable.
            logger.warning("ignoring malformed stdio message: %r", text[:200])
            return
        handler = getattr(self, "_message_handler", None)
        if handler is not None:
            handler(value)

    async def _stderr_loop(self) -> None:
        process = self._process
        try:
            while process is not None:
                line = await process.stderr.readline()
                if not line:
                    return
                text = line.decode(self.encoding, errors="replace").rstrip()
                if text:
                    self._stderr_tail.append(text)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("stdio stderr reader failed")

    def _notify_close(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        handler = getattr(self, "_close_handler", None)
        if handler is not None:
            handler()


async def _kill_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.communicate()
    else:
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), 1)
    except TimeoutError:
        process.kill()
