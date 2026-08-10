"""Conservative Windows PowerShell tool."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..security import command_path_policy, is_read_only_command
from .base import Tool, ToolContext, ToolResult
from .files import failure


_READ_ONLY = {
    "get-childitem",
    "get-content",
    "get-item",
    "get-location",
    "select-string",
    "test-path",
}
_READ_ONLY_GIT = {"status", "diff", "log", "show", "branch"}
_BACKGROUND = re.compile(r"\b(?:Start-Job|Start-Process)\b", re.IGNORECASE)
_DYNAMIC = re.compile(r"[;|><`(){}\r\n]|\$|\&")
_WORDS = re.compile(r'''[^\s'\"]+|'[^']*'|\"[^\"]*\"''')
_ABSOLUTE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/][^\s'\"]*|\\\\[^\s'\"]+|(?<!\S)[\\/][^\s'\"]+)"
)
_PARENT = re.compile(r"(?:^|[\\/\s'\"])\.\.(?:[\\/\s'\"]|$)")
_HOME = re.compile(r"(?:^|\s|['\"])~(?:[\\/]|\s|['\"]|$)")
_PROVIDER = re.compile(
    r"(?:^|\s|['\"])(?:Env|Variable|Function|Alias|Registry|HKLM|HKCU|Cert|WSMan)::?",
    re.IGNORECASE,
)


class Bash(Tool):
    name = "Bash"
    description = (
        "Run one non-interactive Windows PowerShell command in the project. "
        "Simple read-only commands run directly; non-read-only or unclear commands "
        "require confirmation."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string", "minLength": 1}},
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return failure("invalid_arguments", "invalid arguments: command must be non-empty")
        command = command.strip()
        secured = context.security is not None
        if secured:
            blocked = await context.security.guard_shell(command, context)
            if blocked is not None:
                return blocked
        if _BACKGROUND.search(command):
            return failure(
                "background_process_not_allowed", "persistent background processes are not allowed"
            )
        path_policy = _path_policy(command, context.working_directory)
        if path_policy == "outside":
            return failure("path_outside_root", "path is outside working directory")
        if not secured and (not _is_read_only(command) or path_policy == "absolute"):
            if not context.confirm or not await context.confirm(
                self.name,
                "Command is not a simple project-local read-only operation.\n"
                f"Run PowerShell command:\n{command}",
            ):
                return failure("permission_denied", "permission denied by user")
        return await _run(command, context.working_directory)


def _is_read_only(command: str) -> bool:
    return is_read_only_command(command)


def _path_policy(command: str, root: Path) -> str | None:
    return command_path_policy(command, root)


async def _run(command: str, root: Path) -> ToolResult:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        return failure("execution_error", "PowerShell is not available")
    utf8 = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        "$OutputEncoding=[Console]::OutputEncoding;"
    )
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        utf8 + command,
        cwd=root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=flags,
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        await _kill_tree(process)
        raise
    out = stdout.decode("utf-8", errors="replace").rstrip()
    err = stderr.decode("utf-8", errors="replace").rstrip()
    output = out + (f"\n[stderr]\n{err}" if err else "")
    metadata = {"return_code": process.returncode}
    if process.returncode:
        return ToolResult(
            False,
            output,
            error={
                "code": "execution_error",
                "message": f"command exited with code {process.returncode}",
            },
            metadata=metadata,
        )
    return ToolResult(True, output, metadata=metadata)


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
        try:
            await asyncio.wait_for(process.wait(), 1)
        except TimeoutError:
            pass
