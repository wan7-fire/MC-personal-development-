import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentLoop
from zxcode.app import ZXCodeApp
from zxcode.client import Settings
from zxcode.config import AgentConfig
from zxcode.events import EventChannel
from zxcode.mcp import McpManager, load_config
from zxcode.tools import ToolContext, ToolExecutor, ToolRegistry
from tests.mcp_fakes import (
    McpHttpHandler,
    RemoteToolCallingClient,
    STDIO_SERVER_SCRIPT,
    start_http_server,
)
from tests.test_app import FakeClient


class McpEndToEndTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.http_server, cls.http_port = start_http_server()

    @classmethod
    def tearDownClass(cls):
        cls.http_server.shutdown()
        cls.http_server.server_close()

    def setUp(self):
        McpHttpHandler.received = []
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "zxcode-servers.toml").write_text(
            f"""
            [[servers]]
            name = "srv"
            transport = "stdio"
            command = ['''{sys.executable}''', "-c", '''{STDIO_SERVER_SCRIPT}''']
            read_only_tools = ["echo"]

            [[servers]]
            name = "http"
            transport = "http"
            url = "http://127.0.0.1:{self.http_port}/mcp"
            read_only_tools = ["echo"]
            """,
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.close_all()

    async def test_full_agent_loop_with_remote_tools(self):
        self.manager = manager = McpManager(load_config(self.root))
        registry = ToolRegistry()
        report = await manager.register_all(registry)
        self.assertEqual(
            sorted((item["server"], item["ok"]) for item in report),
            [("http", True), ("srv", True)],
        )
        names = {definition["function"]["name"] for definition in registry.definitions()}
        self.assertIn("srv_echo", names)
        self.assertIn("http_echo", names)
        self.assertIn("srv_write", names)

        client = RemoteToolCallingClient("srv_echo")
        agent = AgentLoop(
            client,
            registry,
            ToolExecutor(registry),
            config=AgentConfig(),
            context=ToolContext(self.root),
        )
        completed = await agent.run(
            [{"role": "user", "content": "ping"}], "model-a", EventChannel()
        )
        self.assertIn("hello from stdio:ping", completed.text)
        tool_defs = client.requests[0][2]
        self.assertIn("srv_echo", {definition["function"]["name"] for definition in tool_defs})

        # Second run reuses the same stdio process (connection pooling).
        stdio_session = manager.get_session("srv")
        pid = stdio_session.transport.pid
        await agent.run(
            [{"role": "user", "content": "ping"}], "model-a", EventChannel()
        )
        self.assertEqual(stdio_session.transport.pid, pid)

        # HTTP transport negotiated the protocol version and session id.
        http_messages = [
            item for item in McpHttpHandler.received
            if item["message"].get("method") == "tools/list"
        ]
        self.assertEqual(len(http_messages), 1)
        headers = http_messages[0]["headers"]
        self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
        self.assertEqual(headers["Mcp-Session-Id"], "e2e-session")

        await manager.close_all()
        self.assertFalse(stdio_session.connected)

    async def test_write_tool_is_blocked_by_plan_only(self):
        self.manager = manager = McpManager(load_config(self.root))
        registry = ToolRegistry()
        await manager.register_all(registry)
        agent = AgentLoop(
            RemoteToolCallingClient("srv_write"),
            registry,
            ToolExecutor(registry),
            config=AgentConfig(plan_only=True),
            context=ToolContext(self.root),
        )
        completed = await agent.run(
            [{"role": "user", "content": "ping"}], "model-a", EventChannel()
        )
        self.assertEqual(len(completed.blocked_calls), 1)
        self.assertEqual(completed.blocked_calls[0]["tool_name"], "srv_write")
        self.assertIn("plan-only", str(completed.blocked_calls[0]["reason"]))
        await manager.close_all()

    async def test_app_registers_remote_tools_on_mount(self):
        self.manager = manager = McpManager(load_config(self.root))
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            FakeClient(),
            mcp_manager=manager,
        )
        async with app.run_test():
            for _ in range(100):
                if (
                    app.registry.get("srv_echo") is not None
                    and app.registry.get("http_echo") is not None
                ):
                    break
                await asyncio.sleep(0.05)
        self.assertIsNotNone(app.registry.get("srv_echo"))
        self.assertIsNotNone(app.registry.get("http_echo"))
        self.assertIsNone(manager.get_session("srv"))


if __name__ == "__main__":
    unittest.main()
