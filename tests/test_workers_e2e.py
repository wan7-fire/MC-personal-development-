import asyncio
import tempfile
import unittest
from pathlib import Path

from zxcode.client import AssistantMessage, TextDelta
from zxcode.config import AgentConfig
from zxcode.rules.engine import RuleEngine
from zxcode.rules.model import Condition, ConditionGroup, Rule
from zxcode.workers.loader import load_roles
from zxcode.workers.manager import TaskManager
from zxcode.workers.prompts import FORK_INSTRUCTION
from zxcode.tools import Tool, ToolRegistry, ToolResult


BUILTIN = Path(__file__).resolve().parents[1] / "zxcode" / "workers" / "builtin"


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


class WorkerToolStub(Tool):
    name = "SpawnWorker"
    description = "stub"
    input_schema = {
        "type": "object",
        "properties": {"task": {"type": "string"}},
        "required": ["task"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(False, error={"code": "nested_blocked", "message": "nested"})


class TextClient:
    def __init__(self, text="done"):
        self.text = text
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, list(tools or [])))
        yield TextDelta(self.text)
        yield AssistantMessage({"role": "assistant", "content": self.text})


class ToolRoundClient:
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
            yield TextDelta("finished")
            yield AssistantMessage({"role": "assistant", "content": "finished"})


def make_manager(directory, client, roles, rule_engine, on_complete):
    return TaskManager(
        roles=roles,
        root=Path(directory),
        client=client,
        registry=ToolRegistry([Echo(), WorkerToolStub()]),
        config=AgentConfig(max_turns=5),
        rule_engine=rule_engine,
        history_provider=lambda: [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "parent"},
        ],
        model_provider=lambda: "model-a",
        parent_tool_names_provider=lambda: {"Echo", "WorkerToolStub"},
        on_complete=on_complete,
        foreground_timeout=10.0,
    )


class WorkerE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_definition_worker_runs_to_completion_with_hook_and_no_nesting(self):
        notifications = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            roles = load_roles(Path(directory), builtin_root=BUILTIN)
            root = Path(directory)
            engine = RuleEngine(
                [
                    Rule(
                        id="block-echo",
                        event="pre_tool_use",
                        reject="禁止 Echo",
                        conditions=ConditionGroup(
                            "all", (Condition("tool", "exact", "Echo"),)
                        ),
                    )
                ],
                root=root,
            )
            client = ToolRoundClient()
            echo = Echo()
            manager = TaskManager(
                roles=roles,
                root=root,
                client=client,
                registry=ToolRegistry([echo, WorkerToolStub()]),
                config=AgentConfig(max_turns=5),
                rule_engine=engine,
                history_provider=lambda: [],
                model_provider=lambda: "model-a",
                parent_tool_names_provider=None,
                on_complete=lambda task: notifications.append(task),
                foreground_timeout=10.0,
            )

            task = await manager.start(role_name="general", task="verify")

            tool_names = {
                definition["function"]["name"]
                for definition in client.requests[0][2]
            }
            tool_texts = [
                str(message.get("content", ""))
                for message in client.requests[1][0]
                if message.get("role") == "tool"
            ]

            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.result, "finished")
            self.assertEqual(echo.calls, 0)
            self.assertNotIn("SpawnWorker", tool_names)
            self.assertTrue(any("rule_rejected" in text for text in tool_texts))
            self.assertEqual(len(notifications), 1)

    async def test_fork_worker_forced_background_with_instruction(self):
        notifications = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            roles = load_roles(Path(directory), builtin_root=BUILTIN)
            client = TextClient()
            manager = make_manager(
                directory, client, roles, RuleEngine([], root=Path(directory)),
                on_complete=lambda task: notifications.append(task),
            )

            task = await manager.start(task="verify")
            await manager.wait(task.id)

            first_user = next(
                message["content"]
                for message in client.requests[0][0]
                if message["role"] == "user"
            )
            self.assertTrue(task.background)
            self.assertEqual(task.mode, "fork")
            self.assertEqual(task.status, "succeeded")
            self.assertTrue(first_user.startswith(FORK_INSTRUCTION))
            self.assertIn("parent", first_user)
            self.assertEqual(len(notifications), 1)


if __name__ == "__main__":
    unittest.main()
