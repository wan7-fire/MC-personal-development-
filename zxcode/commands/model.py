"""Command metadata and invocation models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence


class CommandType(Enum):
    """Execution category of a command."""

    LOCAL = "local"
    UI_STATE = "ui_state"
    AI_FLOW = "ai_flow"


@dataclass(frozen=True)
class CommandMeta:
    """Registered metadata for one command."""

    name: str
    description: str
    usage: str
    command_type: CommandType
    handler: Callable[..., Any]
    aliases: tuple[str, ...] = ()
    param_hint: str = ""
    hidden: bool = False

    @property
    def display_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class CommandInvocation:
    """A parsed command line: command name and raw argument string."""

    name: str
    args: str = ""


@dataclass(frozen=True)
class AIPrompt:
    """A+B payload for AI-flow commands.

    ``user_text`` is the visible user message (enters the archive); the
    ``system_parts`` are one-shot system instructions for this request only
    and are never persisted.
    """

    user_text: str
    system_parts: Sequence[str] = ()
