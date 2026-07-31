"""Bounded project search tools."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .base import Tool, ToolContext, ToolResult
from .files import MAX_FILE_BYTES, failure, resolve_path


GLOB_IGNORED = {".git", "node_modules", "vendor", ".idea", ".venv", "__pycache__"}
GREP_IGNORED = {".git", ".venv", "__pycache__"}


def _search_root(context: ToolContext, raw: Any) -> Path | ToolResult:
    root = resolve_path(context.working_directory, raw)
    if isinstance(root, ToolResult):
        return root
    if not root.exists() or not root.is_dir():
        return failure("invalid_arguments", "invalid arguments: search root is not a directory")
    return root


def _safe_file(project: Path, path: Path) -> bool:
    try:
        return path.resolve(strict=True).is_relative_to(project.resolve(strict=True))
    except (OSError, RuntimeError):
        return False


def _matches(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.match(pattern) or (
        pattern.startswith("**/") and candidate.match(pattern[3:])
    )


class Glob(Tool):
    name = "Glob"
    description = (
        "Find project files by a root-relative glob pattern, newest first. "
        "Read-only search; automatically skips .git, node_modules, vendor, .idea, "
        ".venv, and __pycache__."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "root": {"type": "string"},
            "pattern": {"type": "string"},
        },
        "required": ["root", "pattern"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        root = _search_root(context, arguments.get("root"))
        if isinstance(root, ToolResult):
            return root
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return failure("invalid_arguments", "invalid arguments: pattern must be a string")
        parsed = Path(pattern)
        if parsed.is_absolute() or ".." in parsed.parts:
            return failure("path_outside_root", "path is outside working directory")
        return await asyncio.to_thread(self._scan, context.working_directory, root, pattern)

    @staticmethod
    def _scan(project: Path, root: Path, pattern: str) -> ToolResult:
        matches: list[tuple[int, str]] = []
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in GLOB_IGNORED and not (Path(directory) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(directory) / name
                if not _safe_file(project, path):
                    continue
                relative = path.relative_to(root).as_posix()
                if _matches(relative, pattern):
                    try:
                        matches.append((path.stat().st_mtime_ns, relative))
                    except OSError:
                        continue
        matches.sort(key=lambda item: (-item[0], item[1]))
        truncated = len(matches) > 200
        selected = matches[:200]
        return ToolResult(
            True,
            "\n".join(path for _, path in selected),
            metadata={
                "read_only": True,
                "destructive": False,
                "category": "search",
                "truncated": truncated,
                "total_matches": len(matches),
            },
        )


class Grep(Tool):
    name = "Grep"
    description = "Search UTF-8 project files with a Python regular expression."
    input_schema = {
        "type": "object",
        "properties": {
            "root": {"type": "string"},
            "pattern": {"type": "string"},
        },
        "required": ["root", "pattern"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        root = _search_root(context, arguments.get("root"))
        if isinstance(root, ToolResult):
            return root
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str):
            return failure("invalid_arguments", "invalid arguments: pattern must be a string")
        try:
            expression = re.compile(pattern)
        except re.error as error:
            return failure("invalid_arguments", f"invalid arguments: invalid regex: {error}")
        return await asyncio.to_thread(self._scan, context.working_directory, root, expression)

    @staticmethod
    def _scan(project: Path, root: Path, expression: re.Pattern[str]) -> ToolResult:
        matches: list[str] = []
        skipped_large = 0
        skipped_utf8 = 0
        truncated = False
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in GREP_IGNORED and not (Path(directory) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(directory) / name
                if not _safe_file(project, path):
                    continue
                try:
                    with path.open("rb") as stream:
                        data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES:
                        skipped_large += 1
                        continue
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    skipped_utf8 += 1
                    continue
                except OSError:
                    continue
                relative = path.relative_to(root).as_posix()
                for number, line in enumerate(text.splitlines(), 1):
                    if expression.search(line):
                        if len(matches) == 1_000:
                            truncated = True
                            break
                        matches.append(f"{relative}:{number}:{line}")
                if truncated:
                    break
            if truncated:
                break
        return ToolResult(
            True,
            "\n".join(matches),
            metadata={
                "read_only": True,
                "destructive": False,
                "category": "search",
                "truncated": truncated,
                "skipped_large": skipped_large,
                "skipped_invalid_utf8": skipped_utf8,
            },
        )
