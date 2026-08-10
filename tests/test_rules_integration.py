import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentLoop
from zxcode.client import AssistantMessage, TextDelta
from zxcode.config import AgentConfig
from zxcode.dispatch import ToolDispatcher
from zxcode.events import EventChannel
from zxcode.rules.engine import RuleEngine
from zxcode.rules.model import Action, Condition, ConditionGroup, Rule
from zxcode.tools import (
    Tool,
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)


class Echo(Tool):
    name = "Echo"
    description = "echo"
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
        return ToolResult(True, arguments.get("text", ""))


def reject_rule(rule_id, reason, tool="Echo"):
    return Rule(
        id=rule_id,
        event="pre_tool_use",
        reject=reason,
        conditions=ConditionGroup(
            "all", (Condition("tool", "exact", tool),)
        ),
    )


def command_rule(rule_id, event, command, **kwargs):
    timeout = kwargs.pop("timeout", 5)
    return Rule(
        id=rule_id,
        event=event,
        actions=(Action("command", {"command": command}),),
        timeout_seconds=timeout,
        **kwargs,
    )


class ToolDispatcherHookTests(unittest.IsolatedAsyncioTestCase):
    async def _dispatch(self, rules, tool, root, call):
        registry = ToolRegistry([tool])
        channel = EventChannel()
        dispatcher = ToolDispatcher(
            registry,
            ToolExecutor(registry),
            channel,
            rule_engine=RuleEngine(rules, root=root),
        )
        outcome = await dispatcher.dispatch(
            [call], ToolContext(root), AgentConfig(), 0
        )
        channel.close()
        return outcome

    async def test_reject_skips_tool_and_returns_reason(self):
        tool = Echo()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            outcome = await self._dispatch(
                [reject_rule("block", "no echo")],
                tool,
                root,
                ToolCall("call-1", "Echo", {"text": "hi"}),
            )

        self.assertEqual(tool.calls, 0)
        self.assertFalse(outcome.results[0].success)
        self.assertEqual(outcome.results[0].error["code"], "rule_rejected")
        self.assertEqual(outcome.results[0].error["message"], "no echo")
        self.assertEqual(outcome.blocked_calls, [])

    async def test_post_tool_use_fires_after_success(self):
        tool = Echo()
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules = [
                command_rule(
                    "after",
                    "post_tool_use",
                    "Set-Content marker.txt done",
                )
            ]
            outcome = await self._dispatch(
                rules,
                tool,
                root,
                ToolCall("call-1", "Echo", {"text": "hi"}),
            )

            self.assertTrue(outcome.results[0].success)
            self.assertEqual(tool.calls, 1)
            self.assertEqual(
                (root / "marker.txt").read_text(encoding="utf-8").strip(), "done"
            )


class ToolCallClient:
    def __init__(self):
        self.requests = []
        self.calls = 0

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, list(tools or [])))
        self.calls += 1
        if self.calls == 1:
            yield AssistantMessage(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "Echo", "arguments": "{}"},
                        }
                    ],
                }
            )
        else:
            yield TextDelta("done")
            yield AssistantMessage({"role": "assistant", "content": "done"})


class AgentLoopHookTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, rules, root, client=None):
        client = client or ToolCallClient()
        registry = ToolRegistry([Echo()])
        engine = RuleEngine(rules, root=root)
        agent = AgentLoop(
            client,
            registry,
            ToolExecutor(registry),
            config=AgentConfig(max_turns=5),
            context=ToolContext(root),
            rule_engine=engine,
        )
        channel = EventChannel()
        runner = asyncio.create_task(
            agent.run(
                [{"role": "user", "content": "hi"}], "demo", channel
            )
        )
        async for _ in channel:
            pass
        completed = await runner
        await engine.drain()
        return client, completed, engine

    async def test_pre_message_injection_appears_in_request(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules = [
                Rule(
                    id="inject",
                    event="pre_message",
                    actions=(
                        Action("prompt", {"prompt": "rule-on-{{message}}"}),
                    ),
                )
            ]
            client, _, _ = await self._run(rules, root, client=ToolCallClient())

        contents = [
            str(message.get("content", ""))
            for message in client.requests[0][0]
        ]
        self.assertIn("rule-on-hi", contents)

    async def test_turn_and_post_message_hooks_fire(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules = [
                command_rule("start", "turn_start", "Set-Content turn.txt start"),
                command_rule("end", "turn_end", "Set-Content turn.txt end"),
                command_rule("reply", "post_message", "Set-Content reply.txt done"),
            ]
            await self._run(rules, root)

            self.assertEqual(
                (root / "turn.txt").read_text(encoding="utf-8").strip(), "end"
            )
            self.assertEqual(
                (root / "reply.txt").read_text(encoding="utf-8").strip(), "done"
            )

    async def test_reject_feedback_reaches_llm_history(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules = [reject_rule("block", "no echo")]
            client, completed, _ = await self._run(rules, root)

        second_request = client.requests[1][0]
        tool_messages = [
            str(message.get("content", ""))
            for message in second_request
            if message.get("role") == "tool"
        ]
        self.assertTrue(any("rule_rejected" in text for text in tool_messages))
        self.assertTrue(any("no echo" in text for text in tool_messages))
        self.assertIn("done", completed.text)

    async def test_once_turn_rule_fires_once_across_tool_round(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules = [
                command_rule(
                    "once",
                    "turn_start",
                    "Set-Content once.txt x",
                    once=True,
                )
            ]
            await self._run(rules, root)

            self.assertEqual(
                (root / "once.txt").read_text(encoding="utf-8").strip(), "x"
            )


if __name__ == "__main__":
    unittest.main()
