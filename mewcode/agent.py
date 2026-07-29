"""Event-driven ReAct agent loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .cancel import CancelToken
from .client import AssistantMessage, ChatClient, ReasoningDelta, TextDelta
from .config import AgentConfig, TerminationReason
from .dispatch import ToolDispatcher
from .events import Event, EventChannel, EventType
from .state import LoopStateMachine
from .terminator import LoopTerminator, TerminationDecision
from .tools import ToolCall, ToolContext, ToolExecutor, ToolRegistry, ToolResult


PLAN_ONLY_INSTRUCTION = (
    " You are currently in plan-only mode: write tools are blocked. "
    "Do not retry blocked calls. Instead, produce a numbered, step-by-step plan "
    "describing what you would change and why, and hand it back for approval."
)


class _Cancelled(Exception):
    """Internal signal used to unwind to the cancellation cleanup path."""


@dataclass
class AgentComplete:
    text: str
    messages: list[dict[str, Any]]
    termination_reason: str = TerminationReason.END_TURN
    blocked_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        client: ChatClient,
        registry: ToolRegistry,
        executor: ToolExecutor,
        config: AgentConfig | None = None,
        context: ToolContext | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.executor = executor
        self.config = config or AgentConfig()
        self.context = context or ToolContext()

    @property
    def max_turns(self) -> int:
        return self.config.max_turns

    async def run(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        channel: EventChannel,
    ) -> AgentComplete:
        completed, turn = await self._run(messages, model, channel)
        await channel.emit(
            Event(
                type=EventType.LOOP_END,
                turn=turn,
                data={
                    "total_turns": turn + 1,
                    "termination_reason": completed.termination_reason,
                },
            )
        )
        channel.close()
        return completed

    async def _run(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str,
        channel: EventChannel,
    ) -> tuple[AgentComplete, int]:
        config = self.config
        state = LoopStateMachine()
        dispatcher = ToolDispatcher(self.registry, self.executor, channel)
        terminator = LoopTerminator(
            replace(config.terminator_config, max_turns=config.max_turns)
        )

        history = [dict(message) for message in messages]
        self._apply_plan_only_prompt(history, config)
        turn_messages: list[dict[str, Any]] = []
        blocked_calls: list[dict[str, Any]] = []
        progress: set[str] = set()
        turn = 0
        final_text = ""

        await channel.emit(
            Event(
                type=EventType.USER_MESSAGE,
                turn=0,
                data={"content": _last_user_text(history), "role": "user"},
            )
        )

        try:
            for turn in range(config.max_turns):
                self._check_cancel(config.cancel_token)
                state.transition("start" if turn == 0 else "tool_done")

                assistant, text = await self._call_model(
                    history, model, channel, turn, config
                )
                assistant.setdefault("role", "assistant")
                history.append(assistant)
                turn_messages.append(assistant)

                calls = assistant.get("tool_calls") or []
                if not calls:
                    final_text = text or str(assistant.get("content") or "")
                    await self._emit_final(channel, turn, final_text, blocked_calls)
                    await self._emit_turn_end(
                        channel, turn, TerminationReason.END_TURN
                    )
                    state.transition("terminate", TerminationReason.END_TURN)
                    return self._complete(
                        final_text,
                        turn_messages,
                        TerminationReason.END_TURN,
                        blocked_calls,
                        turn,
                    )

                self._check_cancel(config.cancel_token)
                state.transition("tool_call")

                prepared = [self._prepare(call) for call in calls]
                valid_calls = [item for item in prepared if isinstance(item, ToolCall)]
                outcome = await dispatcher.dispatch(
                    valid_calls, self.context, config, turn
                )
                blocked_calls.extend(outcome.blocked_calls)
                results = iter(outcome.results)

                decision = TerminationDecision(False)
                for call, item in zip(calls, prepared):
                    result = next(results) if isinstance(item, ToolCall) else item
                    tool_message = _tool_message(str(call.get("id", "")), result)
                    history.append(tool_message)
                    turn_messages.append(tool_message)
                    await channel.emit(
                        Event(
                            type=EventType.TOOL_RESULT,
                            turn=turn,
                            data={
                                "tool_call_id": str(call.get("id", "")),
                                "tool_name": getattr(item, "name", ""),
                                "result": result.output,
                                "is_error": not result.success,
                            },
                        )
                    )
                    checked = self._guard(terminator, turn, item, result, progress)
                    if not decision.should_stop and checked.should_stop:
                        decision = checked

                if not decision.should_stop:
                    decision = terminator.check(
                        turn=turn, progress_score=float(len(progress))
                    )
                if not decision.should_stop:
                    decision = terminator.check(turn=turn + 1)

                if decision.should_stop:
                    await self._emit_turn_end(channel, turn, decision.reason)
                    state.transition("tool_done")
                    state.transition("terminate", decision.reason)
                    return self._complete(
                        "", turn_messages, decision.reason, blocked_calls, turn
                    )

                await self._emit_turn_end(channel, turn, "continue")
                self._check_cancel(config.cancel_token)

            if state.can("tool_done"):
                state.transition("tool_done")
            await self._emit_turn_end(channel, turn, TerminationReason.MAX_TURNS)
            if state.can("terminate"):
                state.transition("terminate", TerminationReason.MAX_TURNS)
            return self._complete(
                "", turn_messages, TerminationReason.MAX_TURNS, blocked_calls, turn
            )

        except _Cancelled:
            _pair_dangling(turn_messages)
            if state.can("cancel"):
                state.transition("cancel", TerminationReason.CANCELLED)
            await channel.emit(
                Event(
                    type=EventType.CANCELLED,
                    turn=turn,
                    data={"reason": "user_cancelled"},
                )
            )
            if state.can("cleanup_done"):
                state.transition("cleanup_done")
            return self._complete(
                "", turn_messages, TerminationReason.CANCELLED, blocked_calls, turn
            )

        except Exception as error:
            _pair_dangling(turn_messages)
            await channel.emit(
                Event(
                    type=EventType.ERROR,
                    turn=turn,
                    data={
                        "message": str(error),
                        "error_type": type(error).__name__,
                        "recoverable": False,
                    },
                )
            )
            return self._complete(
                "", turn_messages, TerminationReason.ERROR, blocked_calls, turn
            )

    # ── internals ────────────────────────────────────────────────────────

    def _complete(
        self,
        text: str,
        turn_messages: list[dict[str, Any]],
        reason: str,
        blocked_calls: list[dict[str, Any]],
        turn: int,
    ) -> tuple[AgentComplete, int]:
        return (
            AgentComplete(text, turn_messages, reason, list(blocked_calls)),
            turn,
        )

    def _apply_plan_only_prompt(
        self, history: list[dict[str, Any]], config: AgentConfig
    ) -> None:
        if not config.plan_only:
            return
        for message in history:
            if message.get("role") == "system":
                message["content"] = (
                    str(message.get("content") or "") + PLAN_ONLY_INSTRUCTION
                )
                return
        history.insert(0, {"role": "system", "content": PLAN_ONLY_INSTRUCTION.strip()})

    def _check_cancel(self, token: CancelToken) -> None:
        if token.is_cancelled():
            raise _Cancelled

    async def _call_model(
        self,
        history: Sequence[Mapping[str, Any]],
        model: str,
        channel: EventChannel,
        turn: int,
        config: AgentConfig,
    ) -> tuple[dict[str, Any], str]:
        assistant: dict[str, Any] | None = None
        text = ""
        stream = self._stream_model(history, model)
        async for event in _cancellable(stream, config.cancel_token):
            if isinstance(event, TextDelta):
                text += event.text
                await channel.emit(
                    Event(
                        type=EventType.TEXT, turn=turn, data={"content": event.text}
                    )
                )
            elif isinstance(event, ReasoningDelta):
                await channel.emit(
                    Event(
                        type=EventType.THINKING,
                        turn=turn,
                        data={"content": event.text},
                    )
                )
            elif isinstance(event, AssistantMessage):
                assistant = event.message
        if assistant is None:
            raise RuntimeError("model returned no assistant message")
        return assistant, text

    async def _stream_model(
        self, history: Sequence[Mapping[str, Any]], model: str
    ) -> AsyncIterator[TextDelta | ReasoningDelta | AssistantMessage]:
        request = [dict(message) for message in history]
        stream_events = getattr(self.client, "stream_events", None)
        if stream_events is not None:
            async for event in stream_events(
                request, model, self.registry.definitions()
            ):
                yield event
            return

        text = ""
        async for part in self.client.stream(request, model):
            text += part
            yield TextDelta(part)
        yield AssistantMessage({"role": "assistant", "content": text})

    def _guard(
        self,
        terminator: LoopTerminator,
        turn: int,
        item: ToolCall | ToolResult,
        result: ToolResult,
        progress: set[str],
    ) -> TerminationDecision:
        if result.success and isinstance(item, ToolCall):
            progress.add(
                json.dumps(
                    [item.name, item.arguments, result.output],
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            )
            return terminator.check(
                turn=turn,
                tool_name=item.name,
                tool_args=dict(item.arguments),
                observation=result.output,
            )
        if result.error:
            return terminator.check(
                turn=turn, error=json.dumps(result.error, sort_keys=True)
            )
        return TerminationDecision(False)

    async def _emit_final(
        self,
        channel: EventChannel,
        turn: int,
        text: str,
        blocked_calls: list[dict[str, Any]],
    ) -> None:
        await channel.emit(
            Event(
                type=EventType.FINAL_REPLY,
                turn=turn,
                data={"content": text, "blocked_calls": list(blocked_calls)},
            )
        )

    async def _emit_turn_end(
        self, channel: EventChannel, turn: int, reason: str
    ) -> None:
        await channel.emit(
            Event(type=EventType.TURN_END, turn=turn, data={"reason": reason})
        )

    def _prepare(self, call: Mapping[str, Any]) -> ToolCall | ToolResult:
        call_id = str(call.get("id", ""))
        function = call.get("function") or {}
        name = str(function.get("name", ""))
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            return ToolResult(
                False,
                error={
                    "code": "invalid_arguments",
                    "message": "invalid arguments: expected a JSON object",
                },
                metadata={"call_id": call_id},
            )
        return ToolCall(call_id, name, arguments)


async def _cancellable(stream: AsyncIterator[Any], token: CancelToken) -> AsyncIterator[Any]:
    """Yield from an async iterator, aborting it as soon as the token fires.

    A stalled provider stream cannot notice a cooperative flag on its own, so
    each pull races against the token and the stream is closed on cancellation.
    """
    if token.is_cancelled():
        await _aclose(stream)
        raise _Cancelled
    iterator = stream.__aiter__()
    waiter = asyncio.ensure_future(token.wait())
    try:
        while True:
            pull = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait(
                {pull, waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if waiter in done:
                pull.cancel()
                # Let the cancellation settle before closing, otherwise the
                # generator is still marked as running and aclose() errors.
                await asyncio.gather(pull, return_exceptions=True)
                raise _Cancelled
            try:
                item = pull.result()
            except StopAsyncIteration:
                return
            # Checked before yielding: an item produced after the token fired
            # must not reach the consumer.
            if token.is_cancelled():
                raise _Cancelled
            yield item
    finally:
        waiter.cancel()
        await _aclose(stream)


async def _aclose(stream: Any) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        try:
            await close()
        except Exception:
            pass


def _last_user_text(history: Sequence[Mapping[str, Any]]) -> str:
    for message in reversed(history):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _tool_message(call_id: str, result: ToolResult) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": result.to_content(),
    }


def _pair_dangling(turn_messages: list[dict[str, Any]]) -> None:
    """Give every assistant tool_call a matching tool message."""
    answered = {
        str(message.get("tool_call_id", ""))
        for message in turn_messages
        if message.get("role") == "tool"
    }
    cancelled = ToolResult(
        False,
        error={"code": "cancelled", "message": "cancelled by user"},
    )
    for message in list(turn_messages):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            call_id = str(call.get("id", ""))
            if call_id and call_id not in answered:
                turn_messages.append(_tool_message(call_id, cancelled))
                answered.add(call_id)
