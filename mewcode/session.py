"""In-memory conversation state."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


SYSTEM_PROMPT = (
    "You are MewCode, a terminal AI programming assistant. "
    "Provide concise, accurate, and actionable coding help."
)


@dataclass
class ChatSession:
    model: str
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def turns(self) -> int:
        return sum(message.get("role") == "user" for message in self.messages)

    def request_messages(self, user_text: str) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self.messages,
            {"role": "user", "content": user_text},
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
