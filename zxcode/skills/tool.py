"""Directory-skill tool loading and subprocess execution."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from ..tools import Tool, ToolContext, ToolResult
from .frontmatter import parse_frontmatter
from .loader import is_within


async def _terminate_process(process) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass
    try:
        await asyncio.wait_for(process.communicate(), 5)
    except (TimeoutError, ProcessLookupError, OSError):
        pass


class ScriptTool(Tool):
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        script_path: Path,
        *,
        read_only: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.script_path = Path(script_path)
        self.read_only = read_only
        self.timeout_seconds = timeout_seconds

    async def execute(
        self, arguments: dict, context: ToolContext
    ) -> ToolResult:
        security = getattr(context, "security", None)
        if security is not None:
            blocked = await security.guard_script(
                self.name, self.script_path, context
            )
            if blocked is not None:
                return blocked
        elif not self.read_only:
            if context.confirm is None:
                return ToolResult(
                    False,
                    error={
                        "code": "permission_denied",
                        "message": "security check requires confirmation",
                    },
                )
            choice = await context.confirm(
                f"Skill tool: {self.name}",
                json.dumps(arguments, ensure_ascii=False),
            )
            if choice not in ("once", "session", "permanent", True):
                return ToolResult(
                    False,
                    error={
                        "code": "permission_denied",
                        "message": "permission denied by user",
                    },
                )
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(self.script_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(context.working_directory),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(arguments).encode("utf-8")),
                self.timeout_seconds,
            )
        except TimeoutError:
            await _terminate_process(process)
            return ToolResult(
                False, error={"code": "timeout", "message": "tool timed out"}
            )
        except OSError as error:
            await _terminate_process(process)
            return ToolResult(
                False,
                error={"code": "execution_error", "message": str(error)},
            )
        except BaseException:
            await _terminate_process(process)
            raise
        if process.returncode != 0:
            return ToolResult(
                False,
                error={
                    "code": "execution_error",
                    "message": stderr.decode("utf-8", errors="replace")[:500],
                },
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return ToolResult(
                False,
                error={"code": "execution_error", "message": "invalid tool output"},
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
            return ToolResult(
                False,
                error={"code": "execution_error", "message": "invalid tool output"},
            )
        error = payload.get("error")
        return ToolResult(
            payload["success"],
            output=str(payload.get("output", "")),
            error=error if isinstance(error, dict) else None,
        )


def load_skill_tools(skill_dir: Path) -> list[Tool]:
    tools_dir = Path(skill_dir) / "tools"
    if not tools_dir.is_dir():
        return []
    tools: list[Tool] = []
    for spec in sorted(tools_dir.glob("*.md")):
        script = spec.with_suffix(".py")
        if not script.exists():
            continue
        if not is_within(skill_dir, script):
            continue
        try:
            raw = spec.read_text(encoding="utf-8")
            if not raw.startswith("---"):
                continue
            end = raw.find("\n---", 3)
            if end < 0:
                continue
            data = parse_frontmatter(raw[3:end])
        except (OSError, UnicodeError, ValueError):
            continue
        name = data.get("name", spec.stem)
        if not isinstance(name, str) or not name:
            continue
        description = data.get("description", "")
        input_schema = data.get("input_schema")
        if not isinstance(input_schema, dict):
            input_schema = {
                "type": "object",
                "properties": {},
                "required": [],
            }
        read_only = bool(data.get("read_only", True))
        timeout = data.get("timeout_seconds", 30)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30.0
        tools.append(
            ScriptTool(
                name,
                str(description),
                input_schema,
                script,
                read_only=read_only,
                timeout_seconds=max(0.1, timeout),
            )
        )
    return tools
