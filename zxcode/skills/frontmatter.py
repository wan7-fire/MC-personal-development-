"""Minimal YAML-frontmatter parser for flat skill metadata.

The supported subset covers scalars, a single ``tools`` list, and JSON values
such as ``input_schema``. It intentionally avoids a YAML dependency.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import (
    HISTORY_ALL,
    HISTORY_NONE,
    HISTORY_RECENT,
    MODE_ISOLATED,
    MODE_SHARED,
    SkillMeta,
)


class SkillParseError(ValueError):
    def __init__(self, path: Path | str, message: str) -> None:
        self.path = str(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


def _parse_value(raw: str):
    raw = raw.strip()
    if not raw:
        raise ValueError("empty value")
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON value: {error.msg}") from error
    if raw[0] in "\"'":
        if len(raw) < 2 or raw[-1] != raw[0]:
            raise ValueError("unterminated string")
        return raw[1:-1]
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw


def parse_frontmatter(text: str) -> dict:
    data: dict = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", line):
            raise ValueError(f"cannot parse line: {line!r}")
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not raw:
            index += 1
            items = []
            while index < len(lines) and re.match(r"^\s*-\s+", lines[index]):
                items.append(_parse_value(lines[index].strip()[2:]))
                index += 1
            data[key] = items
            continue
        data[key] = _parse_value(raw)
        index += 1
    return data


def _required_string(data: dict, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillParseError(path, f"missing required {key}")
    return value.strip()


def parse_skill_file(path: Path, level: str = "") -> tuple[SkillMeta, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SkillParseError(path, f"cannot read file: {error}") from error
    if not text.startswith("---"):
        raise SkillParseError(path, "file must start with ---")
    end = text.find("\n---", 3)
    if end < 0:
        raise SkillParseError(path, "missing closing ---")
    try:
        data = parse_frontmatter(text[3:end])
    except ValueError as error:
        raise SkillParseError(path, str(error)) from error

    name = _required_string(data, "name", path)
    description = _required_string(data, "description", path)
    mode = _required_string(data, "mode", path)
    if mode not in (MODE_SHARED, MODE_ISOLATED):
        raise SkillParseError(path, f"mode must be shared or isolated, got {mode!r}")
    history = data.get("history", HISTORY_RECENT)
    if history not in (HISTORY_ALL, HISTORY_RECENT, HISTORY_NONE):
        raise SkillParseError(path, f"invalid history: {history!r}")
    history_size = data.get("history_size", 10)
    if not isinstance(history_size, int) or isinstance(history_size, bool) or history_size <= 0:
        raise SkillParseError(path, "history_size must be a positive integer")
    model = data.get("model")
    if model is not None and not isinstance(model, str):
        raise SkillParseError(path, "model must be a string")
    tools = data.get("tools")
    if tools is None:
        tools_tuple = None
    elif isinstance(tools, list) and all(isinstance(item, str) and item for item in tools):
        tools_tuple = tuple(tools)
    else:
        raise SkillParseError(path, "tools must be a list of non-empty strings")
    meta = SkillMeta(
        name=name,
        description=description,
        mode=mode,
        source=Path(path),
        level=level,
        model=model,
        history=history,
        history_size=history_size,
        tools=tools_tuple,
    )
    body = text[end + 4 :].strip()
    return meta, body
