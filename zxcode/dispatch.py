"""Tool batch scheduling with event emission and pre/post hooks."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from .config import AgentConfig
from .events import Event, EventChannel, EventType
from .tools import ToolCall, ToolContext, ToolExecutor, ToolRegistry, ToolResult


PLAN_ONLY_MESSAGE = (
    "当前为 plan-only 模式，写类工具已被拦截。"
    "请使用 /plan 关闭该模式后再执行写操作。"
)


@dataclass
class DispatchOutcome:
    results: list[ToolResult] = field(default_factory=list)
    blocked_calls: list[dict[str, Any]] = field(default_factory=list)


class ToolDispatcher:
    """Runs a batch of tool calls: reads concurrently, then writes serially."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        channel: EventChannel,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.channel = channel

    async def dispatch(
        self,
        calls: Sequence[ToolCall],
        context: ToolContext,
        config: AgentConfig,
        turn: int = 0,
    ) -> DispatchOutcome:
        outcome = DispatchOutcome(results=[None] * len(calls))  # type: ignore[list-item]
        reads: list[tuple[int, ToolCall]] = []
        writes: list[tuple[int, ToolCall]] = []
        for index, call in enumerate(calls):
            tool = self.registry.get(call.name)
            (reads if tool is None or tool.read_only else writes).append((index, call))

        if reads:
            read_results = await asyncio.gather(
                *(
                    self._run_one(call, context, config, turn, outcome)
                    for _, call in reads
                )
            )
            for (index, _), result in zip(reads, read_results):
                outcome.results[index] = result

        for index, call in writes:
            outcome.results[index] = await self._run_one(
                call, context, config, turn, outcome
            )

        outcome.results = [result for result in outcome.results if result is not None]
        return outcome

    # ── hooks ────────────────────────────────────────────────────────────

    async def _pre_hook(
        self,
        call: ToolCall,
        context: ToolContext,
        config: AgentConfig,
        outcome: DispatchOutcome,
    ) -> ToolResult | None:
        """Return a result to short-circuit execution, or None to proceed."""
        tool = self.registry.get(call.name)
        if config.plan_only and tool is not None and not tool.read_only:
            outcome.blocked_calls.append(
                {
                    "tool_name": call.name,
                    "arguments": dict(call.arguments),
                    "reason": PLAN_ONLY_MESSAGE,
                }
            )
            return ToolResult(
                False,
                error={"code": "plan_only_blocked", "message": PLAN_ONLY_MESSAGE},
                metadata={"call_id": call.id},
            )
        security = getattr(context, "security", None)
        if security is not None:
            blocked = await security.guard_call(
                call.name, call.arguments, context, prompt=call.name == "Bash"
            )
            if blocked is not None:
                reason = (
                    blocked.error.get("message", "security blocked")
                    if blocked.error
                    else "security blocked"
                )
                outcome.blocked_calls.append(
                    {
                        "tool_name": call.name,
                        "arguments": dict(call.arguments),
                        "reason": reason,
                    }
                )
                return blocked
        # 权限检查位（permission hook）：本章不实现具体规则，保留调用点。
        return None

    def _post_hook(self, call: ToolCall, result: ToolResult) -> ToolResult:
        """审计日志位（audit hook）：本章留空，直接透传结果。"""
        return result

    # ── single call ──────────────────────────────────────────────────────

    async def _run_one(
        self,
        call: ToolCall,
        context: ToolContext,
        config: AgentConfig,
        turn: int,
        outcome: DispatchOutcome,
    ) -> ToolResult:
        tool = self.registry.get(call.name)
        tool_type = "read" if tool is None or tool.read_only else "write"

        blocked = await self._pre_hook(call, context, config, outcome)
        if blocked is not None:
            await self._emit_start(call, tool_type, turn)
            await self._emit_end(call, turn, 0, "error")
            return self._post_hook(call, blocked)

        await self._emit_start(call, tool_type, turn)
        started = monotonic()
        result = await self.executor.execute(
            call.id, call.name, call.arguments, context
        )
        duration_ms = int((monotonic() - started) * 1000)
        await self._emit_end(call, turn, duration_ms, _status(result))
        return self._post_hook(call, result)

    async def _emit_start(self, call: ToolCall, tool_type: str, turn: int) -> None:
        await self.channel.emit(
            Event(
                type=EventType.TOOL_CALL_START,
                turn=turn,
                data={
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "arguments": dict(call.arguments),
                    "tool_type": tool_type,
                },
            )
        )

    async def _emit_end(
        self, call: ToolCall, turn: int, duration_ms: int, status: str
    ) -> None:
        await self.channel.emit(
            Event(
                type=EventType.TOOL_CALL_END,
                turn=turn,
                data={
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "duration_ms": duration_ms,
                    "status": status,
                },
            )
        )


def _status(result: ToolResult) -> str:
    if result.success:
        return "success"
    if result.error and result.error.get("code") == "timeout":
        return "timeout"
    return "error"
