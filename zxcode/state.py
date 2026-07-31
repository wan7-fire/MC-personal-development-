"""Explicit state machine for the agent loop."""

from __future__ import annotations

from enum import Enum


class LoopState(Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    TOOL_EXECUTING = "TOOL_EXECUTING"
    PLAN_ONLY = "PLAN_ONLY"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"


class IllegalStateTransition(RuntimeError):
    """A transition was requested that the state machine does not allow."""


_TRANSITIONS: dict[tuple[LoopState, str], LoopState] = {
    (LoopState.IDLE, "start"): LoopState.RUNNING,
    (LoopState.RUNNING, "tool_call"): LoopState.TOOL_EXECUTING,
    (LoopState.TOOL_EXECUTING, "tool_done"): LoopState.RUNNING,
    (LoopState.RUNNING, "terminate"): LoopState.TERMINATED,
    (LoopState.RUNNING, "cancel"): LoopState.CANCELLED,
    (LoopState.TOOL_EXECUTING, "cancel"): LoopState.CANCELLED,
    (LoopState.CANCELLED, "cleanup_done"): LoopState.TERMINATED,
    (LoopState.RUNNING, "enter_plan_only"): LoopState.PLAN_ONLY,
    (LoopState.PLAN_ONLY, "exit_plan_only"): LoopState.RUNNING,
}


class LoopStateMachine:
    def __init__(self) -> None:
        self._state = LoopState.IDLE
        self.termination_reason: str | None = None

    @property
    def state(self) -> LoopState:
        return self._state

    def transition(self, action: str, reason: str | None = None) -> LoopState:
        target = _TRANSITIONS.get((self._state, action))
        if target is None:
            raise IllegalStateTransition(
                f"illegal transition: {self._state.name} --{action}-->"
            )
        self._state = target
        if reason is not None:
            self.termination_reason = reason
        return target

    def can(self, action: str) -> bool:
        return (self._state, action) in _TRANSITIONS
