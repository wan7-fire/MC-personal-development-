"""Data model for the rule engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


EVENTS = (
    "session_start",
    "session_end",
    "turn_start",
    "turn_end",
    "pre_message",
    "post_message",
    "pre_tool_use",
    "post_tool_use",
    "system_startup",
    "system_exit",
    "system_error",
    "system_compact",
)
REJECT_EVENTS = {"pre_tool_use"}
ACTION_TYPES = ("command", "prompt", "http", "agent")
OPERATORS = ("exact", "ne", "regex", "glob")
COMBINATORS = ("all", "any")


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class ConditionGroup:
    combinator: str
    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True)
class Action:
    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    id: str
    event: str
    actions: tuple[Action, ...] = ()
    conditions: ConditionGroup | None = None
    reject: str | None = None
    once: bool = False
    async_: bool = False
    timeout_seconds: float = 30.0
