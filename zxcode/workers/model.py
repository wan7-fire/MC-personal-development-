"""Data model for worker roles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerRole:
    name: str
    description: str
    body: str
    tools_allow: tuple[str, ...] | None = None
    tools_deny: tuple[str, ...] = ()
    model: str | None = None
    max_turns: int = 20
    permission_mode: str = "default"
