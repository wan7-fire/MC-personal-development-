import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentComplete, AgentLoop
from zxcode.client import AssistantMessage, ReasoningDelta, TextDelta
from zxcode.compress import (
    BEGIN_SUMMARY,
    BOUNDARY_MESSAGE,
    CompressionConfig,
    CompressionManager,
    END_SUMMARY,
)
from zxcode.config import AgentConfig
from zxcode.events import EventChannel, EventType
from zxcode.state import LoopState
from zxcode.tools import Tool, ToolExecutor, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "Echo"
    description = "Return the supplied text."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(success=True, output=arguments["text"])


class WriteTool(Tool):
    name = "Write"
    description = "Pretend to write something."
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def __init__(self):
        self.calls = 0

    async def execute(self, arguments, context):
        self.calls += 1
        return ToolResult(success=True, output="written")


class FakeClient:
    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, tools))
        for event in next(self.turns):
            yield event


class CallSpyExecutor:
    """Stands in for ToolExecutor, recording per-call invocations."""

    def __init__(self):
        self.calls = []

    async def execute(self, call_id, name, arguments, context=None):
        self.calls.append(call_id)
        return ToolResult(True, str(arguments.get("text", "")))


class ChangingErrorExecutor:
    def __init__(self):
        self.count = 0

    async def execute(self, call_id, name, arguments, context=None):
        self.count += 1
        return ToolResult(
            False, error={"code": f"error_{self.count}", "message": "failed"}
        )


def tool_call(number, name="Echo", arguments=None):
    return {
        "id": f"call-{number}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments if arguments is not None else {}),
        },
    }


def tool_turn(number, arguments):
    return [AssistantMessage({"role": "assistant", "tool_calls": [tool_call(number, arguments=arguments)]})]


async def drive(agent, messages, model="demo"):
    """Run the loop, returning (AgentComplete, [Event])."""
    channel = EventChannel()
    events = []
    runner = asyncio.create_task(agent.run(messages, model, channel))
    async for event in channel:
        events.append(event)
    return await runner, events


def types_of(events):
    return [event.type for event in events]


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_is_returned_to_model_before_final_text(self):
        client = FakeClient(
            [
                [
                    AssistantMessage(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tool_call(1, arguments={"text": "hello"})],
                        }
                    )
                ],
                [
                    TextDelta("完成"),
                    AssistantMessage({"role": "assistant", "content": "完成"}),
                ],
            ]
        )
        registry = ToolRegistry([EchoTool()])
        agent = AgentLoop(client, registry, ToolExecutor(registry))

        completed, events = await drive(
            agent, [{"role": "user", "content": "echo hello"}]
        )

        second_request = client.requests[1][0]
        self.assertEqual(second_request[-1]["role"], "tool")
        self.assertEqual(second_request[-1]["tool_call_id"], "call-1")
        self.assertEqual(json.loads(second_request[-1]["content"])["output"], "hello")
        self.assertEqual(completed.text, "完成")
        self.assertEqual(completed.termination_reason, "end_turn")
        self.assertEqual(completed.messages[-1]["content"], "完成")

    async def test_event_sequence_covers_a_full_tool_round(self):
        client = FakeClient(
            [
                [
                    TextDelta("先看看"),
                    AssistantMessage(
                        {
                            "role": "assistant",
                            "tool_calls": [tool_call(1, arguments={"text": "hi"})],
                        }
                    ),
                ],
                [
                    TextDelta("完成"),
                    AssistantMessage({"role": "assistant", "content": "完成"}),
                ],
            ]
        )
        registry = ToolRegistry([EchoTool()])
        _, events = await drive(AgentLoop(client, registry, ToolExecutor(registry)), [])

        self.assertEqual(
            types_of(events),
            [
                EventType.USER_MESSAGE,
                EventType.TEXT,
                EventType.TOOL_CALL_START,
                EventType.TOOL_CALL_END,
                EventType.TOOL_RESULT,
                EventType.TURN_END,
                EventType.TEXT,
                EventType.FINAL_REPLY,
                EventType.TURN_END,
                EventType.LOOP_END,
            ],
        )
        self.assertEqual(events[0].turn, 0)
        self.assertEqual([e.turn for e in events], sorted(e.turn for e in events))

    async def test_first_event_is_the_last_user_message(self):
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "ok"})]])
        registry = ToolRegistry([EchoTool()])
        _, events = await drive(
            AgentLoop(client, registry, ToolExecutor(registry)),
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "第一条"},
                {"role": "assistant", "content": "回应"},
                {"role": "user", "content": "第二条"},
            ],
        )

        self.assertEqual(events[0].type, EventType.USER_MESSAGE)
        self.assertEqual(events[0].data["content"], "第二条")
        self.assertEqual(types_of(events).count(EventType.USER_MESSAGE), 1)

    async def test_text_events_are_emitted_per_delta(self):
        deltas = ["a", "b", "c", "d", "e"]
        client = FakeClient(
            [
                [*(TextDelta(part) for part in deltas),
                 AssistantMessage({"role": "assistant", "content": "abcde"})]
            ]
        )
        registry = ToolRegistry([EchoTool()])
        _, events = await drive(AgentLoop(client, registry, ToolExecutor(registry)), [])

        texts = [e for e in events if e.type == EventType.TEXT]
        self.assertEqual(len(texts), 5)
        self.assertEqual("".join(e.data["content"] for e in texts), "abcde")

    async def test_thinking_events_follow_reasoning_deltas(self):
        client = FakeClient(
            [
                [
                    ReasoningDelta("想一下"),
                    TextDelta("好"),
                    AssistantMessage({"role": "assistant", "content": "好"}),
                ]
            ]
        )
        registry = ToolRegistry([EchoTool()])
        _, events = await drive(AgentLoop(client, registry, ToolExecutor(registry)), [])
        self.assertEqual(types_of(events).count(EventType.THINKING), 1)

    async def test_no_reasoning_means_no_thinking_events(self):
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "好"})]])
        registry = ToolRegistry([EchoTool()])
        _, events = await drive(AgentLoop(client, registry, ToolExecutor(registry)), [])
        self.assertEqual(types_of(events).count(EventType.THINKING), 0)

    async def test_tool_result_count_matches_tool_calls(self):
        calls = [tool_call(n, arguments={"text": str(n)}) for n in (1, 2)]
        client = FakeClient(
            [
                [AssistantMessage({"role": "assistant", "tool_calls": calls})],
                [AssistantMessage({"role": "assistant", "content": "done"})],
            ]
        )
        registry = ToolRegistry([EchoTool()])
        executor = CallSpyExecutor()
        _, events = await drive(AgentLoop(client, registry, executor), [])

        self.assertEqual(executor.calls, ["call-1", "call-2"])
        self.assertEqual(types_of(events).count(EventType.TOOL_RESULT), 2)
        tool_messages = [
            message for message in client.requests[1][0] if message["role"] == "tool"
        ]
        self.assertEqual(
            [message["tool_call_id"] for message in tool_messages],
            ["call-1", "call-2"],
        )

    async def test_invalid_streamed_arguments_return_structured_tool_error(self):
        call = {
            "id": "call-bad",
            "type": "function",
            "function": {"name": "Echo", "arguments": "{not-json"},
        }
        client = FakeClient(
            [
                [AssistantMessage({"role": "assistant", "tool_calls": [call]})],
                [AssistantMessage({"role": "assistant", "content": "fixed"})],
            ]
        )
        registry = ToolRegistry([EchoTool()])
        executor = CallSpyExecutor()
        await drive(AgentLoop(client, registry, executor), [])

        self.assertEqual(executor.calls, [])
        tool_message = client.requests[1][0][-1]
        self.assertEqual(tool_message["tool_call_id"], "call-bad")
        self.assertEqual(
            json.loads(tool_message["content"])["error"]["code"], "invalid_arguments"
        )


class TerminationTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, client, executor=None, **config_kwargs):
        registry = ToolRegistry([EchoTool()])
        agent = AgentLoop(
            client,
            registry,
            executor or ToolExecutor(registry),
            config=AgentConfig(**config_kwargs),
        )
        return await drive(agent, [])

    async def test_end_turn(self):
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "ok"})]])
        completed, events = await self._run(client)
        self.assertEqual(completed.termination_reason, "end_turn")
        self.assertEqual(events[-1].data["termination_reason"], "end_turn")
        turn_ends = [e for e in events if e.type == EventType.TURN_END]
        self.assertEqual(turn_ends[-1].data["reason"], "end_turn")

    async def test_max_turns(self):
        client = FakeClient([tool_turn(i, {"text": str(i)}) for i in range(5)])
        completed, events = await self._run(client, max_turns=3)
        self.assertEqual(completed.termination_reason, "max_turns")
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(events[-1].data["total_turns"], 3)

    async def test_repeated_observation(self):
        client = FakeClient([tool_turn(i, {"text": "same"}) for i in range(4)])
        completed, _ = await self._run(client)
        self.assertEqual(completed.termination_reason, "repeated_observation")
        self.assertEqual(len(client.requests), 3)

    async def test_repeated_error(self):
        client = FakeClient([tool_turn(i, {}) for i in range(3)])
        completed, _ = await self._run(client)
        self.assertEqual(completed.termination_reason, "repeated_error")
        self.assertEqual(len(client.requests), 2)

    async def test_no_progress(self):
        client = FakeClient([tool_turn(i, {"text": str(i)}) for i in range(6)])
        completed, _ = await self._run(client, executor=ChangingErrorExecutor())
        self.assertEqual(completed.termination_reason, "no_progress")
        self.assertEqual(len(client.requests), 5)

    async def test_error_terminates_with_error_event(self):
        class BoomClient:
            requests = []

            async def stream_events(self, messages, model=None, tools=None):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        completed, events = await self._run(BoomClient())
        self.assertEqual(completed.termination_reason, "error")
        errors = [e for e in events if e.type == EventType.ERROR]
        self.assertEqual(len(errors), 1)
        self.assertIs(errors[0].data["recoverable"], False)

    async def test_exactly_one_loop_end_and_channel_closes(self):
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "ok"})]])
        _, events = await self._run(client)
        self.assertEqual(types_of(events).count(EventType.LOOP_END), 1)
        self.assertEqual(types_of(events)[-1], EventType.LOOP_END)


class PlanOnlyTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        return FakeClient(
            [
                [
                    AssistantMessage(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                tool_call(1, name="Write", arguments={"text": "x"})
                            ],
                        }
                    )
                ],
                [
                    TextDelta("这是计划"),
                    AssistantMessage({"role": "assistant", "content": "这是计划"}),
                ],
            ]
        )

    async def test_write_tools_are_blocked_and_listed(self):
        writer = WriteTool()
        registry = ToolRegistry([EchoTool(), writer])
        client = self._client()
        agent = AgentLoop(
            client,
            registry,
            ToolExecutor(registry),
            config=AgentConfig(plan_only=True),
        )
        completed, events = await drive(agent, [])

        self.assertEqual(writer.calls, 0)
        self.assertEqual(len(completed.blocked_calls), 1)
        self.assertEqual(completed.blocked_calls[0]["tool_name"], "Write")
        self.assertEqual(completed.blocked_calls[0]["arguments"], {"text": "x"})
        self.assertIn("reason", completed.blocked_calls[0])

        tool_message = client.requests[1][0][-1]
        error = json.loads(tool_message["content"])["error"]
        self.assertEqual(error["code"], "plan_only_blocked")
        self.assertEqual(
            error["message"],
            "当前为 plan-only 模式，写类工具已被拦截。"
            "请使用 /plan 关闭该模式后再执行写操作。",
        )

        final = [e for e in events if e.type == EventType.FINAL_REPLY][0]
        self.assertIn("content", final.data)
        self.assertIn("blocked_calls", final.data)

    async def test_read_tools_still_run_under_plan_only(self):
        registry = ToolRegistry([EchoTool(), WriteTool()])
        client = FakeClient(
            [
                [
                    AssistantMessage(
                        {
                            "role": "assistant",
                            "tool_calls": [tool_call(1, arguments={"text": "hi"})],
                        }
                    )
                ],
                [AssistantMessage({"role": "assistant", "content": "ok"})],
            ]
        )
        executor = CallSpyExecutor()
        agent = AgentLoop(
            client, registry, executor, config=AgentConfig(plan_only=True)
        )
        completed, _ = await drive(agent, [])
        self.assertEqual(executor.calls, ["call-1"])
        self.assertEqual(completed.blocked_calls, [])

    async def test_plan_only_keeps_the_stable_system_prompt_unchanged(self):
        registry = ToolRegistry([EchoTool(), WriteTool()])
        base = [
            {"role": "system", "content": "stable"},
            {"role": "system", "content": "Runtime environment: test"},
            {"role": "user", "content": "hi"},
        ]

        plain = FakeClient([[AssistantMessage({"role": "assistant", "content": "a"})]])
        await drive(AgentLoop(plain, registry, ToolExecutor(registry)), base)

        planning = FakeClient([[AssistantMessage({"role": "assistant", "content": "a"})]])
        await drive(
            AgentLoop(
                planning,
                registry,
                ToolExecutor(registry),
                config=AgentConfig(plan_only=True),
            ),
            base,
        )

        self.assertEqual(planning.requests[0][0][0], plain.requests[0][0][0])
        self.assertIn("plan-only", planning.requests[0][0][2]["content"])
        self.assertEqual(planning.requests[0][0][3], base[-1])

    async def test_blocked_calls_accumulate_across_turns(self):
        registry = ToolRegistry([EchoTool(), WriteTool()])
        write_turn = [
            AssistantMessage(
                {
                    "role": "assistant",
                    "tool_calls": [tool_call(1, name="Write", arguments={"text": "x"})],
                }
            )
        ]
        write_turn_2 = [
            AssistantMessage(
                {
                    "role": "assistant",
                    "tool_calls": [tool_call(2, name="Write", arguments={"text": "y"})],
                }
            )
        ]
        client = FakeClient(
            [
                write_turn,
                write_turn_2,
                [AssistantMessage({"role": "assistant", "content": "计划"})],
            ]
        )
        agent = AgentLoop(
            client,
            registry,
            ToolExecutor(registry),
            config=AgentConfig(plan_only=True),
        )
        completed, _ = await drive(agent, [])
        self.assertEqual(len(completed.blocked_calls), 2)


class CancelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_tools_pairs_every_call(self):
        finished = []

        class SlowTool(Tool):
            name = "Slow"
            description = "sleeps"
            input_schema = {"type": "object", "properties": {}, "required": []}

            async def execute(self, arguments, context):
                await asyncio.sleep(0.05)
                finished.append(True)
                return ToolResult(True, "slow")

        config = AgentConfig()
        registry = ToolRegistry([SlowTool()])
        client = FakeClient(
            [
                [
                    AssistantMessage(
                        {
                            "role": "assistant",
                            "tool_calls": [tool_call(1, name="Slow")],
                        }
                    )
                ],
                [AssistantMessage({"role": "assistant", "content": "never"})],
            ]
        )
        agent = AgentLoop(client, registry, ToolExecutor(registry), config=config)

        channel = EventChannel()
        runner = asyncio.create_task(agent.run([], "demo", channel))
        events = []

        async def consume():
            async for event in channel:
                events.append(event)
                if event.type == EventType.TOOL_CALL_START:
                    config.cancel_token.cancel()

        await consume()
        completed = await runner

        self.assertEqual(completed.termination_reason, "cancelled")
        self.assertEqual(finished, [True])
        self.assertEqual(types_of(events).count(EventType.CANCELLED), 1)
        self.assertEqual(
            [e for e in events if e.type == EventType.CANCELLED][0].data["reason"],
            "user_cancelled",
        )
        self.assertNoDanglingCalls(completed.messages)

    async def test_cancel_before_start_returns_cancelled(self):
        config = AgentConfig()
        config.cancel_token.cancel()
        registry = ToolRegistry([EchoTool()])
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "x"})]])
        agent = AgentLoop(client, registry, ToolExecutor(registry), config=config)
        completed, events = await drive(agent, [])

        self.assertEqual(completed.termination_reason, "cancelled")
        self.assertEqual(len(client.requests), 0)
        self.assertEqual(types_of(events).count(EventType.CANCELLED), 1)

    async def test_double_cancel_emits_one_event(self):
        config = AgentConfig()
        config.cancel_token.cancel()
        config.cancel_token.cancel()
        registry = ToolRegistry([EchoTool()])
        client = FakeClient([[AssistantMessage({"role": "assistant", "content": "x"})]])
        agent = AgentLoop(client, registry, ToolExecutor(registry), config=config)
        _, events = await drive(agent, [])
        self.assertEqual(types_of(events).count(EventType.CANCELLED), 1)

    async def test_cancelled_messages_replay_into_the_next_request(self):
        config = AgentConfig()

        class MidStreamClient:
            def __init__(self):
                self.requests = []

            async def stream_events(self, messages, model=None, tools=None):
                self.requests.append((list(messages), model, tools))
                yield TextDelta("部分")
                config.cancel_token.cancel()
                yield TextDelta("不该出现")
                yield AssistantMessage({"role": "assistant", "content": "部分"})

        registry = ToolRegistry([EchoTool()])
        client = MidStreamClient()
        agent = AgentLoop(client, registry, ToolExecutor(registry), config=config)
        completed, events = await drive(agent, [])

        self.assertEqual(completed.termination_reason, "cancelled")
        self.assertEqual(types_of(events).count(EventType.TEXT), 1)
        self.assertNoDanglingCalls(completed.messages)

    def assertNoDanglingCalls(self, messages):
        answered = {
            m.get("tool_call_id") for m in messages if m.get("role") == "tool"
        }
        for message in messages:
            for call in message.get("tool_calls") or []:
                self.assertIn(call["id"], answered)


class StateTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_state_enum_is_complete(self):
        self.assertEqual(
            {member.name for member in LoopState},
            {
                "IDLE",
                "RUNNING",
                "TOOL_EXECUTING",
                "PLAN_ONLY",
                "CANCELLED",
                "TERMINATED",
            },
        )


class CompressionLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_tool_result_is_spooled_before_history(self):
        with tempfile.TemporaryDirectory() as directory:
            compressor = CompressionManager(Path(directory))
            client = FakeClient(
                [
                    [
                        AssistantMessage(
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    tool_call(1, arguments={"text": "z" * 9000})
                                ],
                            }
                        )
                    ],
                    [AssistantMessage({"role": "assistant", "content": "完成"})],
                ]
            )
            registry = ToolRegistry([EchoTool()])
            agent = AgentLoop(
                client,
                registry,
                ToolExecutor(registry),
                compressor=compressor,
            )

            completed, _ = await drive(
                agent, [{"role": "user", "content": "go"}]
            )

            self.assertIsNotNone(completed.final_history)
            tool_messages = [
                message
                for message in completed.final_history
                if message.get("role") == "tool"
            ]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("已溢出", tool_messages[0]["content"])
            self.assertIn(".zxcode/spool/", tool_messages[0]["content"])
            self.assertEqual(
                len(list((Path(directory) / ".zxcode/spool").glob("*.txt"))), 1
            )

    async def test_history_is_compressed_before_request_when_over_trigger(self):
        class SplitClient:
            def __init__(self):
                self.requests = []

            async def stream_events(self, messages, model=None, tools=None):
                self.requests.append((list(messages), model, tools))
                if not tools:
                    yield TextDelta("草稿")
                    yield TextDelta(
                        f"{BEGIN_SUMMARY}\n## 主要请求\n汇总\n{END_SUMMARY}"
                    )
                    yield AssistantMessage(
                        {"role": "assistant", "content": "草稿"}
                    )
                    return
                yield AssistantMessage({"role": "assistant", "content": "完成"})

        with tempfile.TemporaryDirectory() as directory:
            compressor = CompressionManager(
                Path(directory),
                CompressionConfig(context_window=2000),
                client=SplitClient(),
            )
            registry = ToolRegistry([EchoTool()])
            agent = AgentLoop(
                compressor.client,
                registry,
                ToolExecutor(registry),
                compressor=compressor,
            )
            messages = [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a" * 7000},
                {"role": "user", "content": "u2"},
            ]

            completed, _ = await drive(agent, messages)

            self.assertEqual(compressor.client.requests[0][2], ())
            self.assertIsNotNone(completed.final_history)
            contents = [
                message.get("content") for message in completed.final_history
            ]
            self.assertIn(BOUNDARY_MESSAGE, contents)
            self.assertTrue(
                any("## 主要请求" in (content or "") for content in contents)
            )
            self.assertIn(
                {"role": "user", "content": "u2"}, completed.final_history
            )


if __name__ == "__main__":
    unittest.main()
