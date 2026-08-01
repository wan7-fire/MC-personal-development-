import sys
import tempfile
import unittest
from pathlib import Path

from zxcode.mcp import McpManager, load_config
from zxcode.tools import ToolRegistry
from tests.mcp_fakes import STDIO_SERVER_SCRIPT


class McpPoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "zxcode-servers.toml").write_text(
            f"""
            [[servers]]
            name = "srv"
            transport = "stdio"
            command = ['''{sys.executable}''', "-c", '''{STDIO_SERVER_SCRIPT}''']
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

    async def test_register_all_discovers_and_registers_tools(self):
        self.manager = manager = McpManager(load_config(self.root))
        registry = ToolRegistry()
        report = await manager.register_all(registry)
        self.assertTrue(report[0]["ok"])
        self.assertEqual(report[0]["tools"], 2)
        self.assertIsNotNone(registry.get("srv_echo"))
        self.assertIsNotNone(registry.get("srv_write"))
        session = manager.get_session("srv")
        self.assertTrue(session.connected)
        await manager.close_all()
        self.assertFalse(session.connected)

    async def test_register_all_reuses_existing_connection(self):
        self.manager = manager = McpManager(load_config(self.root))
        registry = ToolRegistry()
        await manager.register_all(registry)
        first = manager.get_session("srv")
        pid = first.transport.pid
        await manager.register_all(registry)
        second = manager.get_session("srv")
        self.assertIs(first, second)
        self.assertEqual(second.transport.pid, pid)
        await manager.close_all()

    async def test_idle_sweep_closes_and_drops_session(self):
        self.manager = manager = McpManager(load_config(self.root))
        await manager.register_all(ToolRegistry())
        session = manager.get_session("srv")
        session.last_used = 0.0
        await manager._sweep_idle()
        self.assertIsNone(manager.get_session("srv"))
        self.assertFalse(session.connected)

    async def test_failed_server_is_reported_but_does_not_block_others(self):
        (self.root / "zxcode-servers.toml").write_text(
            f"""
            [[servers]]
            name = "broken"
            transport = "stdio"
            command = ["definitely-not-a-real-executable-xyz"]

            [[servers]]
            name = "srv"
            transport = "stdio"
            command = ['''{sys.executable}''', "-c", '''{STDIO_SERVER_SCRIPT}''']
            read_only_tools = ["echo"]
            """,
            encoding="utf-8",
        )
        self.manager = manager = McpManager(load_config(self.root))
        registry = ToolRegistry()
        report = await manager.register_all(registry)
        by_name = {item["server"]: item for item in report}
        self.assertFalse(by_name["broken"]["ok"])
        self.assertTrue(by_name["srv"]["ok"])
        self.assertIsNotNone(registry.get("srv_echo"))
        await manager.close_all()


if __name__ == "__main__":
    unittest.main()
