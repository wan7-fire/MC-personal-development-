import asyncio
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentLoop
from zxcode.client import AssistantMessage, TextDelta
from zxcode.config import AgentConfig
from zxcode.events import EventChannel
from zxcode.rules.engine import RuleEngine
from zxcode.rules.loader import load_rules
from zxcode.tools import Tool, ToolContext, ToolExecutor, ToolRegistry, ToolResult


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


RULES_YAML = """
rules:
  - id: inject
    event: pre_message
    actions:
      - type: prompt
        prompt: "rule-on-{{message}}"
  - id: block-echo
    event: pre_tool_use
    when:
      all:
        - field: tool
          op: exact
          value: Echo
    reject: "禁止 {{tool}}"
  - id: once-mark
    event: turn_start
    once: true
    actions:
      - type: command
        command: "Set-Content once.txt x"
  - id: bg-mark
    event: turn_start
    async: true
    actions:
      - type: command
        command: "Start-Sleep -Milliseconds 100; Set-Content async.txt done"
  - id: slow
    event: turn_start
    timeout_seconds: 0.2
    actions:
      - type: command
        command: "Start-Sleep -Seconds 10"
"""


class RuleE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_full_loop_with_yaml_rules(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rules_dir = root / ".zxcode" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "rules.yaml").write_text(RULES_YAML, encoding="utf-8")
            rules = load_rules(root)
            engine = RuleEngine(rules, root=root)
            client = ToolCallClient()
            echo = Echo()
            registry = ToolRegistry([echo])
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
                agent.run([{"role": "user", "content": "hi"}], "demo", channel)
            )
            async for _ in channel:
                pass
            completed = await runner
            await engine.drain()
            await asyncio.sleep(0.3)

            first_contents = [
                str(message.get("content", ""))
                for message in client.requests[0][0]
            ]
            tool_messages = [
                str(message.get("content", ""))
                for message in client.requests[1][0]
                if message.get("role") == "tool"
            ]

            self.assertEqual(echo.calls, 0)
            self.assertIn("rule-on-hi", first_contents)
            self.assertTrue(any("rule_rejected" in text for text in tool_messages))
            self.assertTrue(any("禁止 Echo" in text for text in tool_messages))
            self.assertEqual(
                (root / "once.txt").read_text(encoding="utf-8").strip(), "x"
            )
            self.assertEqual(
                (root / "async.txt").read_text(encoding="utf-8").strip(), "done"
            )
            self.assertIn("done", completed.text)


if __name__ == "__main__":
    unittest.main()
