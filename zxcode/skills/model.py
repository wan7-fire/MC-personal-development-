"""Data model for skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MODE_SHARED = "shared"
MODE_ISOLATED = "isolated"
HISTORY_ALL = "all"
HISTORY_RECENT = "recent"
HISTORY_NONE = "none"


@dataclass(frozen=True)
class SkillIssue:
    path: str
    message: str


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    mode: str
    source: Path
    level: str = ""
    model: str | None = None
    history: str = HISTORY_RECENT
    history_size: int = 10
    tools: tuple[str, ...] | None = None
