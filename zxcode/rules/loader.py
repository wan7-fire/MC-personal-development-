"""Rule loading and validation from project .zxcode/rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .model import (
    ACTION_TYPES,
    COMBINATORS,
    EVENTS,
    OPERATORS,
    REJECT_EVENTS,
    Action,
    Condition,
    ConditionGroup,
    Rule,
)


class RuleLoadError(ValueError):
    def __init__(self, path: Path, rule_id: str, message: str) -> None:
        self.path = str(path)
        self.rule_id = rule_id
        self.message = message
        super().__init__(f"{self.path} [{rule_id}]: {message}")


_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)$")
_LIST_ITEM = re.compile(r"^-\s*(.*)$")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] in ("'", '"'):
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


def _mapping_start(rest: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", rest))


def _parse_lines(lines: list[tuple[int, str]]) -> tuple[Any, int]:
    def node(index: int, indent: int):
        content = lines[index][1]
        if content == "-" or content.startswith("- "):
            return _list(index, indent)
        return _mapping(index, indent)

    def _list(index: int, indent: int):
        items: list[Any] = []
        while (
            index < len(lines)
            and lines[index][0] == indent
            and (lines[index][1] == "-" or lines[index][1].startswith("- "))
        ):
            match = _LIST_ITEM.match(lines[index][1])
            rest = (match.group(1) or "").strip()
            if not rest:
                items.append(None)
                index += 1
                continue
            if _mapping_start(rest):
                key, _, value = rest.partition(":")
                key = key.strip()
                value = value.strip()
                item: dict[str, Any] = {}
                item[key] = _parse_scalar(value) if value else None
                index += 1
                if index < len(lines) and lines[index][0] > indent:
                    sub_indent = lines[index][0]
                    sub, index = node(index, sub_indent)
                    if isinstance(sub, dict):
                        item.update(sub)
                    elif item.get(key) is None:
                        item[key] = sub
                items.append(item)
            else:
                items.append(_parse_scalar(rest))
                index += 1
        return items, index

    def _mapping(index: int, indent: int):
        result: dict[str, Any] = {}
        while index < len(lines):
            line_indent, content = lines[index]
            if line_indent < indent:
                break
            if line_indent != indent:
                raise ValueError("invalid indentation")
            match = _KEY.match(content)
            if match is None:
                raise ValueError(f"cannot parse line: {content!r}")
            key = match.group(1)
            raw = match.group(2).strip()
            if raw:
                result[key] = _parse_scalar(raw)
                index += 1
                continue
            if index + 1 < len(lines) and lines[index + 1][0] > indent:
                result[key], index = node(index + 1, lines[index + 1][0])
            else:
                result[key] = None
                index += 1
        return result, index

    return node(0, lines[0][0])


def _parse_yaml(text: str) -> Any:
    prepared: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        prepared.append((indent, line.strip()))
    if not prepared:
        return None
    value, _ = _parse_lines(prepared)
    return value


def _parse_condition(raw: Any, path: Path, rule_id: str) -> Condition:
    if not isinstance(raw, dict):
        raise RuleLoadError(path, rule_id, "condition must be a mapping")
    field = raw.get("field")
    op = raw.get("op")
    if not isinstance(field, str) or not field:
        raise RuleLoadError(path, rule_id, "condition missing field")
    if op not in OPERATORS:
        raise RuleLoadError(path, rule_id, f"unknown condition operator: {op!r}")
    if "value" not in raw:
        raise RuleLoadError(path, rule_id, "condition missing value")
    return Condition(field, op, raw["value"])


def _parse_conditions(raw: Any, path: Path, rule_id: str) -> ConditionGroup | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuleLoadError(path, rule_id, "when must be a mapping")
    keys = [key for key in COMBINATORS if key in raw]
    if len(keys) != 1:
        raise RuleLoadError(path, rule_id, "when must contain exactly one of all/any")
    combinator = keys[0]
    items = raw[combinator]
    if not isinstance(items, list):
        raise RuleLoadError(path, rule_id, f"{combinator} must be a list")
    return ConditionGroup(
        combinator, tuple(_parse_condition(item, path, rule_id) for item in items)
    )


_REQUIRED = {
    "command": "command",
    "prompt": "prompt",
    "http": "url",
    "agent": "name",
}


def _parse_action(raw: Any, path: Path, rule_id: str) -> Action:
    if not isinstance(raw, dict):
        raise RuleLoadError(path, rule_id, "action must be a mapping")
    action_type = raw.get("type")
    if action_type not in ACTION_TYPES:
        raise RuleLoadError(
            path, rule_id, f"unknown action type: {action_type!r}"
        )
    required = _REQUIRED[action_type]
    value = raw.get(required)
    if not isinstance(value, str) or not value.strip():
        raise RuleLoadError(
            path, rule_id, f"action {action_type} requires {required}"
        )
    payload = {key: item for key, item in raw.items() if key != "type"}
    return Action(action_type, payload)


def _parse_rule(raw: Any, path: Path, seen: set[str]) -> Rule:
    if not isinstance(raw, dict):
        raise RuleLoadError(path, "?", "rule must be a mapping")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RuleLoadError(path, str(rule_id or "?"), "rule missing id")
    rule_id = rule_id.strip()
    if rule_id in seen:
        raise RuleLoadError(path, rule_id, "duplicate rule id")
    seen.add(rule_id)
    event = raw.get("event")
    if event not in EVENTS:
        raise RuleLoadError(path, rule_id, f"unknown event: {event!r}")
    reject = raw.get("reject")
    if reject is not None and not isinstance(reject, str):
        raise RuleLoadError(path, rule_id, "reject must be a string")
    if reject is not None and event not in REJECT_EVENTS:
        raise RuleLoadError(
            path, rule_id, "reject is only allowed on pre_tool_use"
        )
    async_ = raw.get("async", False)
    if not isinstance(async_, bool):
        raise RuleLoadError(path, rule_id, "async must be a boolean")
    if async_ and event == "pre_tool_use":
        raise RuleLoadError(path, rule_id, "async is not allowed on pre_tool_use")
    once = raw.get("once", False)
    if not isinstance(once, bool):
        raise RuleLoadError(path, rule_id, "once must be a boolean")
    timeout = raw.get("timeout_seconds", 30.0)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise RuleLoadError(path, rule_id, "timeout_seconds must be a positive number")
    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raise RuleLoadError(path, rule_id, "actions must be a list")
    if not raw_actions and reject is None:
        raise RuleLoadError(path, rule_id, "rule needs actions or reject")
    actions = tuple(_parse_action(item, path, rule_id) for item in raw_actions)
    conditions = _parse_conditions(raw.get("when"), path, rule_id)
    return Rule(
        id=rule_id,
        event=event,
        actions=actions,
        conditions=conditions,
        reject=reject,
        once=once,
        async_=async_,
        timeout_seconds=float(timeout),
    )


def parse_rule_file(path: Path) -> list[Rule]:
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = _parse_yaml(text)
    except (OSError, UnicodeError, ValueError) as error:
        raise RuleLoadError(Path(path), "?", f"cannot parse rule file: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuleLoadError(Path(path), "?", "rule file must contain a rules list")
    seen: set[str] = set()
    return [_parse_rule(item, Path(path), seen) for item in data["rules"]]


def load_rules(project_root: Path | str) -> list[Rule]:
    rules_dir = Path(project_root) / ".zxcode" / "rules"
    if not rules_dir.is_dir():
        return []
    paths = sorted(
        [*rules_dir.glob("*.yaml"), *rules_dir.glob("*.yml")],
        key=lambda item: item.name,
    )
    rules: list[Rule] = []
    for path in paths:
        rules.extend(parse_rule_file(path))
    return rules
