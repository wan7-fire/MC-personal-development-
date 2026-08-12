import asyncio
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentLoop
from zxcode.client import AssistantMessage, TextDelta
from zxcode.config import AgentConfig
from zxcode.events import EventChannel
from zxcode.rules.engine import RuleEngine
from zxcode.workers.model import WorkerRole
from zxcode.workers.prompts import FORK_INSTRUCTION
from zxcode.workers.runner import run_worker
from zxcode.tools import (
    Grep,
    Tool,
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
                            "function": {"name": "Echo", "arguments": '{"text":"hi"}'},
                        }
                    ],
                }
            )
        else:
            yield TextDelta("finished")
            yield AssistantMessage({"role": "assistant", "content": "finished"})


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, **kwargs):
        defaults = dict(
            role=None,
            task="do it",
            fork=False,
            parent_history=(),
            client=TextClient(),
            registry=ToolRegistry([Echo()]),
            config=AgentConfig(max_turns=5),
            rule_engine=None,
            model="model-a",
        )
        defaults.update(kwargs)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            defaults["root"] = Path(directory)
            return await run_worker(**defaults), defaults

    async def test_definition_mode_uses_blank_history_with_role_and_task(self):
        role = WorkerRole(name="r", description="d", body="role body")
        client = TextClient()
        result, _ = await self._run(role=role, client=client)

        messages = client.requests[0][0]
        self.assertEqual(
            [message["content"] for message in messages],
            ["role body", "do it"],
        )
        self.assertEqual(result.text, "done")
        self.assertGreater(result.token_usage, 0)

    async def test_role_model_overrides_parent_model(self):
        role = WorkerRole(name="r", description="d", body="b", model="role-model")
        client = TextClient()
        await self._run(role=role, client=client)

        self.assertEqual(client.requests[0][1], "role-model")

    async def test_role_tool_allowlist_restricts_tool_definitions(self):
        role = WorkerRole(
            name="r",
            description="d",
            body="b",
            tools_allow=("Echo",),
        )
        registry = ToolRegistry([Echo()])
        client = TextClient()
        await self._run(role=role, client=client, registry=registry)

        names = {
            definition["function"]["name"] for definition in client.requests[0][2]
        }
        self.assertEqual(names, {"Echo"})

    async def test_fork_inherits_history_injects_instruction_and_appends_task(self):
        parent = [
            {"role": "system", "content": "stable"},
            {"role": "user", "content": "orig"},
            {"role": "assistant", "content": "answer"},
        ]
        client = TextClient()
        await self._run(fork=True, parent_history=parent, task="verify", client=client)

        messages = client.requests[0][0]
        system_prefix = [
            message["content"]
            for message in messages
            if message["role"] == "system"
        ]
        first_user = next(
            message["content"] for message in messages if message["role"] == "user"
        )
        last_user = [
            message["content"]
            for message in messages
            if message["role"] == "user"
        ][-1]
        self.assertEqual(system_prefix, ["stable"])
        self.assertTrue(first_user.startswith(FORK_INSTRUCTION))
        self.assertIn("orig", first_user)
        self.assertEqual(last_user, "verify")

    async def test_fork_reuses_parent_tool_set_without_worker_tool(self):
        client = TextClient()
        await self._run(
            fork=True,
            parent_tool_names={"Echo", "Grep", "SpawnWorker"},
            client=client,
            registry=ToolRegistry([Echo(), Grep()]),
        )

        names = {
            definition["function"]["name"] for definition in client.requests[0][2]
        }
        self.assertEqual(names, {"Echo", "Grep"})

    async def test_worker_runs_to_completion_after_tool_round(self):
        client = ToolRoundClient()
        echo = Echo()
        registry = ToolRegistry([echo])
        role = WorkerRole(
            name="r",
            description="d",
            body="b",
            tools_allow=("Echo",),
        )

        result, _ = await self._run(
            role=role, client=client, registry=registry
        )

        self.assertEqual(echo.calls, 1)
        self.assertEqual(result.text, "finished")
        self.assertEqual(len(client.requests), 2)


if __name__ == "__main__":
    unittest.main()
