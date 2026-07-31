"""Minimal contracts shared by ZXCode tools."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


Confirm = Callable[[str, str], Awaitable[bool | str]]


@dataclass(frozen=True)
class ToolContext:
    working_directory: Path = field(default_factory=Path.cwd)
    confirm: Confirm | None = None
    security: Any = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass
class ToolResult:
    success: bool
    output: str = ""
    error: dict[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_content(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "output": self.output,
                "error": self.error,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
        )


class Tool(ABC):
    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only = True
    timeout_seconds = 30.0

    @abstractmethod
    async def execute(
        self, arguments: Mapping[str, Any], context: ToolContext
    ) -> ToolResult:
        """Run the tool once."""

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.input_schema),
                "strict": True,
            },
        }


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]
