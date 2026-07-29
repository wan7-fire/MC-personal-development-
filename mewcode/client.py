"""OpenAI-compatible streaming client."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, AuthenticationError, NotFoundError


class ConfigError(ValueError):
    """Required runtime configuration is missing."""


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    message: dict[str, Any]


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if environ is None else environ
        names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
        for name in names:
            if not values.get(name, "").strip():
                raise ConfigError(f"缺少环境变量：{name}")
        return cls(*(values[name].strip() for name in names))


class ChatClient:
    def __init__(self, settings: Settings, sdk=None) -> None:
        self.settings = settings
        self.sdk = sdk or AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=60.0,
            max_retries=2,
        )

    async def stream(
        self, messages: Sequence[Mapping[str, str]], model: str | None = None
    ) -> AsyncIterator[str]:
        response = await self.sdk.chat.completions.create(
            model=model or self.settings.model,
            messages=list(messages),
            stream=True,
        )
        try:
            async for chunk in response:
                content = chunk.choices[0].delta.content if chunk.choices else None
                if content:
                    yield content
        finally:
            await response.close()

    async def stream_events(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> AsyncIterator[TextDelta | ReasoningDelta | AssistantMessage]:
        request: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": list(messages),
        }
        if tools:
            request["tools"] = list(tools)

        async with self.sdk.chat.completions.stream(**request) as stream:
            async for event in stream:
                if event.type == "content.delta" and event.delta:
                    yield TextDelta(event.delta)
                elif event.type.startswith("reasoning") and getattr(
                    event, "delta", None
                ):
                    yield ReasoningDelta(event.delta)
            completion = await stream.get_final_completion()

        if not completion.choices:
            raise RuntimeError("model returned no choices")
        message = completion.choices[0].message
        if hasattr(message, "model_dump"):
            message = message.model_dump(mode="json", exclude_none=True)
        yield AssistantMessage(dict(message))


def friendly_error(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return "鉴权失败，请检查 LLM_API_KEY"
    if isinstance(error, NotFoundError):
        return "模型不可用，请检查 LLM_MODEL"
    return "请求失败，请稍后重试"


def friendly_error_name(name: str) -> str:
    """Same mapping as friendly_error, but keyed by exception class name.

    The agent loop turns provider failures into error events carrying only the
    class name, so the terminal cannot re-inspect the original exception.
    """
    if name == AuthenticationError.__name__:
        return "鉴权失败，请检查 LLM_API_KEY"
    if name == NotFoundError.__name__:
        return "模型不可用，请检查 LLM_MODEL"
    return "请求失败，请稍后重试"
