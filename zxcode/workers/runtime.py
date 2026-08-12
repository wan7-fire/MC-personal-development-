"""Per-task runtime state for sub-workers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkerRuntime:
    history: list[dict] = field(default_factory=list)
    security: object | None = None
    token_usage: int = 0
