"""Pending-request table mapping JSON-RPC ids to awaiting coroutines."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .errors import McpError
from .protocol import Response


@dataclass
class PendingRequest:
    request_id: int | str
    method: str
    future: asyncio.Future[Response]


class ResponseMatcher:
    """Associates outbound request ids with inbound responses.

    All methods must run on the session's event loop.  Late or unknown
    responses are dropped so a timed-out request can never corrupt a later
    one that happens to reuse the id.
    """

    def __init__(self) -> None:
        self._pending: dict[int | str, PendingRequest] = {}

    def register(self, request_id: int | str, method: str) -> asyncio.Future[Response]:
        if request_id in self._pending:
            raise ValueError(f"duplicate pending request id: {request_id!r}")
        future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = PendingRequest(request_id, method, future)
        return future

    def resolve(self, response: Response) -> bool:
        pending = self._pending.pop(response.id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(response)
        return True

    def fail(self, request_id: int | str, code: str, message: str) -> bool:
        pending = self._pending.pop(request_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_exception(McpError(code, message))
        return True

    def abandon(self, request_id: int | str) -> None:
        """Forget a request without resolving it (timeout or cancellation)."""
        self._pending.pop(request_id, None)

    def fail_all(self, code: str, message: str) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(McpError(code, message))

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def ids(self) -> list[Any]:
        return list(self._pending)
