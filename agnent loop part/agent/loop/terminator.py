from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LoopTerminatorConfig:
    max_turns: int = 20
    repeated_observation_limit: int = 3
    repeated_error_limit: int = 2
    no_progress_limit: int = 4
    min_progress_delta: float = 0.0


@dataclass(frozen=True)
class TerminationDecision:
    should_stop: bool
    reason: str = "continue"
    detail: str = ""


class LoopTerminator:
    def __init__(self, config: LoopTerminatorConfig | None = None):
        self.config = config or LoopTerminatorConfig()
        self._last_observation_key: str | None = None
        self._observation_repeats = 0
        self._last_error_key: str | None = None
        self._error_repeats = 0
        self._best_progress: float | None = None
        self._stagnant_turns = 0

    def check(
        self,
        *,
        turn: int,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        observation: Any = None,
        error: str | None = None,
        progress_score: float | None = None,
        cancelled: bool = False,
    ) -> TerminationDecision:
        if cancelled:
            return TerminationDecision(True, "cancelled", "cancel signal received")

        if turn >= self.config.max_turns:
            return TerminationDecision(True, "max_turns", f"turn {turn} >= {self.config.max_turns}")

        if error is not None:
            decision = self._check_repeated_error(error)
            if decision.should_stop:
                return decision

        if tool_name and observation is not None:
            decision = self._check_repeated_observation(tool_name, tool_args or {}, observation)
            if decision.should_stop:
                return decision

        if progress_score is not None:
            decision = self._check_progress(progress_score)
            if decision.should_stop:
                return decision

        return TerminationDecision(False)

    def reset(self) -> None:
        self._last_observation_key = None
        self._observation_repeats = 0
        self._last_error_key = None
        self._error_repeats = 0
        self._best_progress = None
        self._stagnant_turns = 0

    def _check_repeated_observation(
        self, tool_name: str, tool_args: dict[str, Any], observation: Any
    ) -> TerminationDecision:
        key = "|".join(
            [
                tool_name,
                json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str),
                _normalize(observation),
            ]
        )
        self._observation_repeats = self._observation_repeats + 1 if key == self._last_observation_key else 1
        self._last_observation_key = key

        if self._observation_repeats >= self.config.repeated_observation_limit:
            return TerminationDecision(
                True,
                "repeated_observation",
                f"same tool/args/observation repeated {self._observation_repeats} times",
            )
        return TerminationDecision(False)

    def _check_repeated_error(self, error: str) -> TerminationDecision:
        key = _normalize(error)
        self._error_repeats = self._error_repeats + 1 if key == self._last_error_key else 1
        self._last_error_key = key

        if self._error_repeats >= self.config.repeated_error_limit:
            return TerminationDecision(
                True,
                "repeated_error",
                f"same error repeated {self._error_repeats} times",
            )
        return TerminationDecision(False)

    def _check_progress(self, progress_score: float) -> TerminationDecision:
        if self._best_progress is None or progress_score > self._best_progress + self.config.min_progress_delta:
            self._best_progress = progress_score
            self._stagnant_turns = 0
        else:
            self._stagnant_turns += 1

        if self._stagnant_turns >= self.config.no_progress_limit:
            return TerminationDecision(
                True,
                "no_progress",
                f"progress did not improve for {self._stagnant_turns} turns",
            )
        return TerminationDecision(False)


def _normalize(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return re.sub(r"\s+", " ", text).strip().lower()

