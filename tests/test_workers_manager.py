import asyncio
import tempfile
import unittest
from pathlib import Path

from zxcode.client import AssistantMessage, TextDelta
from zxcode.config import AgentConfig
from zxcode.rules.engine import RuleEngine
from zxcode.workers.manager import STATUS_CANCELLED, STATUS_RUNNING, TaskManager
from zxcode.workers.model import WorkerRole
from zxcode.workers.prompts import FORK_INSTRUCTION
from zxcode.workers.tool import SpawnWorker
from zxcode.tools import Tool, ToolContext, ToolRegistry, ToolResult


class Echo(Tool):
    name = "Echo"
    description = "echo"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(True, arguments.get("text", ""))


class TextClient:
    def __init__(self, text="done", delay=0.0):
        self.text = text
        self.delay = delay
        self.requests = []
        self.calls = 0

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, list(tools or [])))
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        yield TextDelta(self.text)
        yield AssistantMessage({"role": "assistant", "content": self.text})


def make_manager(
    directory,
    *,
    roles=None,
    client=None,
    on_complete=None,
    foreground_timeout=5.0,
):
    root = Path(directory)
    registry = ToolRegistry([Echo()])
    return TaskManager(
        roles=roles or {
            "r": WorkerRole(name="r", description="d", body="role body")
        },
        root=root,
        client=client or TextClient(),
        registry=registry,
        config=AgentConfig(max_turns=5),
        rule_engine=RuleEngine([], root=root),
        history_provider=lambda: [{"role": "user", "content": "parent"}],
        model_provider=lambda: "model-a",
        parent_tool_names_provider=lambda: {"Echo"},
        on_complete=on_complete,
        foreground_timeout=foreground_timeout,
    )


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreground_task_returns_result(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)

            task = await manager.start(role_name="r", task="do it")

            self.assertEqual(task.status, "succeeded")
            self.assertEqual(task.result, "done")
            self.assertGreater(task.token_usage, 0)
            self.assertFalse(task.background)

    async def test_background_task_returns_immediately_and_completes(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)

            task = await manager.start(role_name="r", task="do it", background=True)

            self.assertEqual(task.status, STATUS_RUNNING)
            self.assertTrue(task.background)
            await manager.wait(task.id)
            self.assertEqual(task.status, "succeeded")

    async def test_timeout_transfers_to_background_without_restart(self):
        client = TextClient(delay=0.8)
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory, client=client, foreground_timeout=0.1)

            task = await manager.start(role_name="r", task="do it")

            self.assertTrue(task.background)
            self.assertEqual(client.calls, 1)
            await manager.wait(task.id)
            self.assertEqual(task.status, "succeeded")
            self.assertEqual(client.calls, 1)

    async def test_fork_is_forced_background(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)

            task = await manager.start(task="verify", role_name=None)

            self.assertEqual(task.mode, "fork")
            self.assertTrue(task.background)
            await manager.wait(task.id)
            self.assertEqual(task.status, "succeeded")
            first_user = next(
                message["content"]
                for message in manager.client.requests[0][0]
                if message["role"] == "user"
            )
            self.assertTrue(first_user.startswith(FORK_INSTRUCTION))

    async def test_kill_cancels_without_completion_notification(self):
        notifications = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(
                directory,
                client=TextClient(delay=5),
                on_complete=lambda task: notifications.append(task),
            )
            task = await manager.start(role_name="r", task="do it", background=True)

            killed = manager.kill(task.id)

            self.assertTrue(killed)
            self.assertEqual(task.status, STATUS_CANCELLED)
            await asyncio.sleep(0.1)
            self.assertEqual(notifications, [])

    async def test_completion_notification_on_success(self):
        notifications = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(
                directory,
                on_complete=lambda task: notifications.append(task),
            )
            task = await manager.start(role_name="r", task="do it", background=True)
            await manager.wait(task.id)

        self.assertEqual([item.id for item in notifications], [task.id])
        self.assertEqual(notifications[0].status, "succeeded")

    async def test_unknown_role_raises(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)

            with self.assertRaises(ValueError):
                await manager.start(role_name="missing", task="do it")


class WorkerToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_runs_definition_worker(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)
            tool = SpawnWorker(manager)

            result = await tool.execute(
                {"role": "r", "task": "do it"}, ToolContext(Path(directory))
            )

        self.assertTrue(result.success)
        self.assertIn("done", result.output)

    async def test_tool_returns_task_id_for_background(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)
            tool = SpawnWorker(manager)

            result = await tool.execute(
                {"role": "r", "task": "do it", "background": True},
                ToolContext(Path(directory)),
            )
            task = manager.get(result.metadata["task_id"])
            await manager.wait(task.id)

        self.assertTrue(result.success)
        self.assertIn(task.id, result.output)
        self.assertEqual(task.status, "succeeded")

    async def test_tool_reports_unknown_role(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            manager = make_manager(directory)
            tool = SpawnWorker(manager)

            result = await tool.execute(
                {"role": "missing", "task": "do it"}, ToolContext(Path(directory))
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "unknown_role")


if __name__ == "__main__":
    unittest.main()
