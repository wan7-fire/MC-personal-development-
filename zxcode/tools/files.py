"""UTF-8 file tools confined to the current project."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..security import resolve_project_path
from .base import Tool, ToolContext, ToolResult


MAX_FILE_BYTES = 1_048_576


def failure(code: str, message: str, **metadata: Any) -> ToolResult:
    return ToolResult(False, error={"code": code, "message": message}, metadata=metadata)


def resolve_path(root: Path, raw_path: Any) -> Path | ToolResult:
    return resolve_project_path(root, raw_path)


def read_utf8(path: Path) -> tuple[str, bytes] | ToolResult:
    if not path.exists() or not path.is_file():
        return failure("file_not_found", "file does not exist")
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return failure(
                "file_too_large", f"file exceeds {MAX_FILE_BYTES} bytes", size=len(data)
            )
        return data.decode("utf-8"), data
    except UnicodeDecodeError:
        return failure("invalid_utf8", "file is not valid UTF-8")
    except OSError:
        return failure("execution_error", "unable to read file")


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".zxcode-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _recheck_snapshot(
    root: Path, raw_path: Any, original: Path, expected_sha256: str
) -> Path | ToolResult:
    path = resolve_path(root, raw_path)
    if isinstance(path, ToolResult):
        return path
    if path != original:
        return failure("conflict", "file changed since it was read")
    loaded = read_utf8(path)
    if isinstance(loaded, ToolResult):
        return loaded
    if _sha256(loaded[1]) != expected_sha256:
        return failure("conflict", "file changed since it was read")
    return path


def _text_bytes(content: Any) -> bytes | ToolResult:
    if not isinstance(content, str):
        return failure("invalid_arguments", "invalid arguments: content must be a string")
    data = content.encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        return failure(
            "file_too_large", f"file exceeds {MAX_FILE_BYTES} bytes", size=len(data)
        )
    return data


async def _approved(context: ToolContext, title: str, detail: str) -> bool:
    return bool(context.confirm and await context.confirm(title, detail))


async def _security_guard(
    context: ToolContext, tool: str, path: Path, *, exists: bool
) -> ToolResult | None:
    security = getattr(context, "security", None)
    if security is None:
        return None
    return await security.guard_file(tool, path, context, exists=exists)


class ReadFile(Tool):
    name = "ReadFile"
    description = (
        "Read a UTF-8 project file. Line bounds are optional and inclusive; "
        "out-of-bounds ranges are clamped to the file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": ["integer", "null"], "minimum": 1},
            "end_line": {"type": ["integer", "null"], "minimum": 1},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        path = resolve_path(context.working_directory, arguments.get("path"))
        if isinstance(path, ToolResult):
            return path
        start = arguments.get("start_line")
        end = arguments.get("end_line")
        if start is not None and (not isinstance(start, int) or isinstance(start, bool) or start < 1):
            return failure("invalid_arguments", "invalid arguments: invalid start line")
        if end is not None and (not isinstance(end, int) or isinstance(end, bool) or end < 1):
            return failure("invalid_arguments", "invalid arguments: invalid end line")
        if start is not None and end is not None and start > end:
            return failure("invalid_arguments", "invalid arguments: start line exceeds end line")
        loaded = read_utf8(path)
        if isinstance(loaded, ToolResult):
            return loaded
        text, data = loaded
        lines = text.splitlines()
        total = len(lines)
        first = start or 1
        last = end if end is not None else total
        clamped = False
        if end is not None and end > total:
            last = total
            clamped = True
        if first > total:
            first = total + 1
            last = total
            clamped = True
        output = "\n".join(
            f"{number}: {lines[number - 1]}" for number in range(first, last + 1)
        )
        metadata = {
            "sha256": _sha256(data),
            "size": len(data),
            "total_lines": total,
        }
        if clamped:
            metadata["clamped"] = True
        return ToolResult(
            True,
            output,
            metadata=metadata,
        )


class WriteFile(Tool):
    name = "WriteFile"
    description = "Create or atomically overwrite a UTF-8 project file."
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "expected_sha256": {"type": ["string", "null"]},
        },
        "required": ["path", "content", "expected_sha256"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        path = resolve_path(context.working_directory, arguments.get("path"))
        if isinstance(path, ToolResult):
            return path
        data = _text_bytes(arguments.get("content"))
        if isinstance(data, ToolResult):
            return data
        if not path.parent.exists() or not path.parent.is_dir():
            return failure("invalid_arguments", "invalid arguments: parent directory does not exist")
        if path.exists():
            loaded = read_utf8(path)
            if isinstance(loaded, ToolResult):
                return loaded
            _, current = loaded
            expected = arguments.get("expected_sha256")
            if not isinstance(expected, str) or not expected:
                return failure("invalid_arguments", "invalid arguments: expected_sha256 is required")
            if expected != _sha256(current):
                return failure("conflict", "file changed since it was read")
            blocked = await _security_guard(context, self.name, path, exists=True)
            if blocked is not None:
                return blocked
            relative = path.relative_to(context.working_directory.resolve())
            if context.security is None and not await _approved(context, self.name, f"Overwrite {relative}"):
                return failure("permission_denied", "permission denied by user")
            checked = _recheck_snapshot(
                context.working_directory, arguments.get("path"), path, expected
            )
            if isinstance(checked, ToolResult):
                return checked
            path = checked
        else:
            blocked = await _security_guard(context, self.name, path, exists=False)
            if blocked is not None:
                return blocked
        try:
            atomic_write(path, data)
        except OSError:
            return failure("execution_error", "unable to write file")
        return ToolResult(True, f"wrote {path.name}", metadata={"sha256": _sha256(data)})


class EditFile(Tool):
    name = "EditFile"
    description = "Atomically apply multiple unique, non-overlapping text replacements."
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "expected_sha256": {"type": "string"},
            "edits": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {"type": "string", "minLength": 1},
                        "new_text": {"type": "string"},
                    },
                    "required": ["old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["path", "expected_sha256", "edits"],
        "additionalProperties": False,
    }

    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        path = resolve_path(context.working_directory, arguments.get("path"))
        if isinstance(path, ToolResult):
            return path
        loaded = read_utf8(path)
        if isinstance(loaded, ToolResult):
            return loaded
        text, current = loaded
        expected = arguments.get("expected_sha256")
        if not isinstance(expected, str) or expected != _sha256(current):
            return failure("conflict", "file changed since it was read")
        edits = arguments.get("edits")
        if not isinstance(edits, list) or not edits:
            return failure("invalid_arguments", "invalid arguments: edits must be non-empty")

        replacements: list[tuple[int, int, str]] = []
        for edit in edits:
            if not isinstance(edit, Mapping):
                return failure("invalid_arguments", "invalid arguments: invalid edit")
            old = edit.get("old_text")
            new = edit.get("new_text")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                return failure("invalid_arguments", "invalid arguments: invalid edit text")
            start = text.find(old)
            if start < 0 or text.find(old, start + 1) >= 0:
                return failure("edit_match_error", "old_text must match exactly once")
            replacements.append((start, start + len(old), new))

        ordered = sorted(replacements)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            return failure("edit_overlap", "edit ranges overlap")
        updated = text
        for start, end, new in reversed(ordered):
            updated = updated[:start] + new + updated[end:]
        data = _text_bytes(updated)
        if isinstance(data, ToolResult):
            return data
        blocked = await _security_guard(context, self.name, path, exists=True)
        if blocked is not None:
            return blocked
        relative = path.relative_to(context.working_directory.resolve())
        if context.security is None and not await _approved(context, self.name, f"Edit {relative}"):
            return failure("permission_denied", "permission denied by user")
        checked = _recheck_snapshot(
            context.working_directory, arguments.get("path"), path, expected
        )
        if isinstance(checked, ToolResult):
            return checked
        path = checked
        try:
            atomic_write(path, data)
        except OSError:
            return failure("execution_error", "unable to write file")
        return ToolResult(
            True,
            f"applied {len(replacements)} edits",
            metadata={"sha256": _sha256(data)},
        )
