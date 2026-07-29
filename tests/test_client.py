import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import AuthenticationError, NotFoundError

from mewcode.client import (
    AssistantMessage,
    ChatClient,
    ConfigError,
    Settings,
    TextDelta,
    friendly_error,
)


class FakeStream:
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    def __aiter__(self):
        self._parts = iter(self.parts)
        return self

    async def __anext__(self):
        try:
            part = next(self._parts)
        except StopIteration:
            raise StopAsyncIteration
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=part))]
        )

    async def close(self):
        self.closed = True


class FakeCompletions:
    def __init__(self, stream):
        self.response_stream = stream
        self.request = None
        self.event_stream = None
        self.event_request = None

    async def create(self, **kwargs):
        self.request = kwargs
        return self.response_stream

    def stream(self, **kwargs):
        self.event_request = kwargs
        return self.event_stream


class FakeEventStream:
    def __init__(self, events, message):
        self.events = events
        self.message = message
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.exited = True

    def __aiter__(self):
        self._events = iter(self.events)
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration

    async def get_final_completion(self):
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)])


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_reports_each_missing_variable(self):
        cases = (
            ({}, "LLM_API_KEY"),
            ({"LLM_API_KEY": "secret"}, "LLM_BASE_URL"),
            (
                {"LLM_API_KEY": "secret", "LLM_BASE_URL": "https://example.test/v1"},
                "LLM_MODEL",
            ),
        )
        for environ, missing in cases:
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ConfigError, f"缺少环境变量：{missing}"):
                    Settings.from_env(environ)

    def test_sdk_uses_confirmed_timeout_and_retry_values(self):
        settings = Settings("secret", "https://example.test/v1", "demo")
        with patch("mewcode.client.AsyncOpenAI") as sdk:
            ChatClient(settings)
        sdk.assert_called_once_with(
            api_key="secret",
            base_url="https://example.test/v1",
            timeout=60.0,
            max_retries=2,
        )

    async def test_stream_yields_text_and_closes_response(self):
        stream = FakeStream(["你", None, "好", "！"])
        completions = FakeCompletions(stream)
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        client = ChatClient(Settings("secret", "https://example.test/v1", "demo"), sdk)

        parts = [part async for part in client.stream([{"role": "user", "content": "hi"}])]

        self.assertEqual(parts, ["你", "好", "！"])
        self.assertEqual(completions.request["model"], "demo")
        self.assertTrue(completions.request["stream"])
        self.assertTrue(stream.closed)

    async def test_stream_events_yields_text_and_complete_tool_calls(self):
        message = SimpleNamespace(
            model_dump=lambda **_: {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "Echo",
                            "arguments": '{"text":"hello"}',
                        },
                    }
                ],
            }
        )
        event_stream = FakeEventStream(
            [
                SimpleNamespace(type="content.delta", delta="先"),
                SimpleNamespace(type="tool_calls.function.arguments.delta"),
            ],
            message,
        )
        completions = FakeCompletions(FakeStream([]))
        completions.event_stream = event_stream
        client = ChatClient(
            Settings("secret", "https://example.test/v1", "demo"),
            SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "Echo",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            }
        ]

        events = [
            event
            async for event in client.stream_events(
                [{"role": "user", "content": "hi"}], tools=tools
            )
        ]

        self.assertEqual(events[0], TextDelta("先"))
        self.assertIsInstance(events[1], AssistantMessage)
        self.assertEqual(events[1].message["tool_calls"][0]["id"], "call-1")
        self.assertEqual(completions.event_request["tools"], tools)
        self.assertTrue(event_stream.exited)

    def test_friendly_error_hides_provider_details(self):
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        auth = AuthenticationError(
            "provider secret detail",
            response=httpx.Response(401, request=request),
            body=None,
        )
        missing = NotFoundError(
            "provider model detail",
            response=httpx.Response(404, request=request),
            body=None,
        )

        self.assertEqual(friendly_error(auth), "鉴权失败，请检查 LLM_API_KEY")
        self.assertEqual(friendly_error(missing), "模型不可用，请检查 LLM_MODEL")
        self.assertEqual(friendly_error(RuntimeError("secret")), "请求失败，请稍后重试")


if __name__ == "__main__":
    unittest.main()
