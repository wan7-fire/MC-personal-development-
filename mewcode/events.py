"""Event stream shared by the agent loop and its consumers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


class EventType:
    """Event type literals emitted by the agent loop."""

    USER_MESSAGE = "user_message"
    THINKING = "thinking"
    TEXT = "text"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_RESULT = "tool_result"
    TURN_END = "turn_end"
    FINAL_REPLY = "final_reply"
    ERROR = "error"
    CANCELLED = "cancelled"
    LOOP_END = "loop_end"


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Event:
    type: str
    timestamp: int = field(default_factory=_now_ms)
    turn: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "turn": self.turn,
            "data": dict(self.data),
        }


class EventChannel:
    """Single-producer async event pipe; consumers iterate until close()."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def emit(self, event: Event) -> None:
        if self._closed:
            return
        await self._queue.put(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)

    def __aiter__(self) -> "EventChannel":
        return self

    async def __anext__(self) -> Event:
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        event = await self._queue.get()
        if event is None:
            raise StopAsyncIteration
        return event
