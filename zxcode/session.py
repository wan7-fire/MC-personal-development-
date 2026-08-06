"""In-memory conversation state."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .prompts import build_environment_message, build_stable_prompt


SYSTEM_PROMPT = build_stable_prompt()


@dataclass
class ChatSession:
    model: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    instructions: list[dict[str, Any]] = field(default_factory=list)
    prompt_root: Path = field(default_factory=Path.cwd)

    @property
    def turns(self) -> int:
        return sum(message.get("role") == "user" for message in self.messages)

    def request_messages(
        self,
        user_text: str,
        dynamic_messages: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": build_stable_prompt(root=self.prompt_root)},
            build_environment_message(self.prompt_root),
            *(dict(message) for message in self.instructions),
            *(dict(message) for message in dynamic_messages),
            *self.messages,
            {"role": "user", "content": user_text},
        ]

    def prepare_request(
        self,
        user_text: str,
        compressor=None,
        dynamic_messages: Sequence[Mapping[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Run the idempotent layer-1 recheck, then assemble the request."""
        if compressor is not None:
            self.messages = compressor.recheck(self.messages)
        return self.request_messages(user_text, dynamic_messages)

    def rebuild_from_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        """Persist the loop's final history, stripping the system prefix."""
        index = 0
        while index < len(history) and history[index].get("role") == "system":
            index += 1
        self.messages = [dict(message) for message in history[index:]]

    def inject_instructions(
        self, instruction_messages: Sequence[Mapping[str, Any]]
    ) -> None:
        """Replace the session instruction messages injected into requests."""
        self.instructions = [
            dict(message)
            for message in instruction_messages
            if message.get("role") == "system" and message.get("content")
        ]

    def commit(self, user_text: str, assistant_text: str) -> None:
        self.commit_messages(
            user_text, [{"role": "assistant", "content": assistant_text}]
        )

    def commit_messages(
        self,
        user_text: str,
        assistant_messages: Sequence[Mapping[str, Any]],
    ) -> None:
        self.messages.append({"role": "user", "content": user_text})
        self.messages.extend(dict(message) for message in assistant_messages)

    def clear(self) -> None:
        self.messages.clear()

    def set_model(self, model: str) -> None:
        self.model = model
