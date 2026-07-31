"""Cooperative cancellation token."""

from __future__ import annotations

import asyncio
import threading


class CancelToken:
    """Thread-safe flag with an awaitable form for async waiters."""

    def __init__(self) -> None:
        self._cancelled = False
        self._lock = threading.Lock()
        self._event = asyncio.Event()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
        self._event.set()

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def reset(self) -> None:
        with self._lock:
            self._cancelled = False
        self._event.clear()

    async def wait(self) -> None:
        await self._event.wait()
