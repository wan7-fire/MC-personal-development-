"""Runtime configuration and termination reasons for the agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .cancel import CancelToken
from .terminator import LoopTerminatorConfig


class TerminationReason:
    """Reasons the loop can stop, including the reused loop guards."""

    END_TURN = "end_turn"
    NO_TOOL_CALLS = "no_tool_calls"
    MAX_TURNS = "max_turns"
    CANCELLED = "cancelled"
    ERROR = "error"
    REPEATED_OBSERVATION = "repeated_observation"
    REPEATED_ERROR = "repeated_error"
    NO_PROGRESS = "no_progress"


ALL_REASONS = (
    TerminationReason.END_TURN,
    TerminationReason.NO_TOOL_CALLS,
    TerminationReason.MAX_TURNS,
    TerminationReason.CANCELLED,
    TerminationReason.ERROR,
    TerminationReason.REPEATED_OBSERVATION,
    TerminationReason.REPEATED_ERROR,
    TerminationReason.NO_PROGRESS,
)


@dataclass(frozen=True)
class AgentConfig:
    max_turns: int = 20
    plan_only: bool = False
    security_mode: str = "default"
    llm_timeout_seconds: float = 120.0
    cancel_token: CancelToken = field(default_factory=CancelToken)
    terminator_config: LoopTerminatorConfig = field(
        default_factory=LoopTerminatorConfig
    )

    def with_plan_only(self, plan_only: bool) -> "AgentConfig":
        return replace(self, plan_only=plan_only)
