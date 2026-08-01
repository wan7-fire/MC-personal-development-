import asyncio
import unittest
from pathlib import Path

from zxcode.mcp.adapter import AllowState, RemoteTool, build_tools
from zxcode.mcp.config import ServerConfig
from zxcode.mcp.errors import McpError
from zxcode.tools import ToolContext, ToolExecutor, ToolRegistry, ToolResult


class StubSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(True, f"ran {name}", metadata={"server": "srv"})


def server(**overrides):
    values = {
        "name": "srv",
        "transport": "stdio",
        "command": ("python", "-c", ""),
        "trusted": False,
        "read_only_tools": (),
        "disabled_tools": (),
        "call_timeout_seconds": 60.0,
    }
    values.update(overrides)
    return ServerConfig(**values)


def tool_def(name, **extra):
    definition = {
        "name": name,
        "description": f"tool {name}",
        "inputSchema": {"type": "object", "properties": {}},
    }
    definition.update(extra)
    return definition


def confirm_context(choice="once"):
    async def confirm(title, detail):
        return choice

    return ToolContext(working_directory=Path.cwd(), confirm=confirm)


class BuildToolsTests(unittest.TestCase):
    def test_prefixes_names_and_registers(self):
        session = StubSession()
        tools = build_tools(
            server(),
            session,
            [tool_def("echo"), tool_def("sum")],
        )
        self.assertEqual([tool.name for tool in tools], ["srv_echo", "srv_sum"])
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        self.assertIsNotNone(registry.get("srv_echo"))
        self.assertIsNotNone(registry.get("srv_sum"))

    def test_disabled_tools_are_skipped(self):
        session = StubSession()
        tools = build_tools(
            server(disabled_tools=("delete",)),
            session,
            [tool_def("echo"), tool_def("delete")],
        )
        self.assertEqual([tool.name for tool in tools], ["srv_echo"])

    def test_invalid_prefixed_name_is_skipped(self):
        session = StubSession()
        tools = build_tools(
            server(),
            session,
            [tool_def("echo"), tool_def("has space")],
        )
        self.assertEqual([tool.name for tool in tools], ["srv_echo"])

    def test_read_only_classification(self):
        session = StubSession()
        # Explicit list wins without trusting the server.
        explicit = build_tools(
            server(read_only_tools=("query",)),
            session,
            [tool_def("query")],
        )[0]
        self.assertTrue(explicit.read_only)
        # Hint only counts on a trusted server.
        untrusted = build_tools(
            server(),
            session,
            [tool_def("lookup", annotations={"readOnlyHint": True})],
        )[0]
        self.assertFalse(untrusted.read_only)
        trusted = build_tools(
            server(trusted=True),
            session,
            [tool_def("lookup", annotations={"readOnlyHint": True})],
        )[0]
        self.assertTrue(trusted.read_only)
        # Default is a write tool.
        plain = build_tools(server(), session, [tool_def("do")])[0]
        self.assertFalse(plain.read_only)

    def test_schema_normalization(self):
        session = StubSession()
        tool = build_tools(
            server(),
            session,
            [
                tool_def(
                    "complex",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "meta": {
                                "type": "object",
                                "properties": {"flag": {"type": "boolean"}},
                            },
                        },
                        "required": ["name", "missing_prop"],
                    },
                )
            ],
        )[0]
        schema = tool.input_schema
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["name"])
        self.assertIs(schema["properties"]["meta"]["additionalProperties"], False)


class RemoteToolExecuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_tool_skips_confirmation(self):
        session = StubSession()
        tool = RemoteTool(
            server(read_only_tools=("query",)),
            session,
            tool_def("query"),
            read_only=True,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        result = await tool.execute({"x": 1}, confirm_context())
        self.assertTrue(result.success)
        self.assertEqual(session.calls, [("query", {"x": 1})])

    async def test_write_tool_requires_confirmation(self):
        session = StubSession()
        tool = RemoteTool(
            server(),
            session,
            tool_def("do"),
            read_only=False,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        denied = await tool.execute({}, confirm_context("deny"))
        self.assertFalse(denied.success)
        self.assertEqual(denied.error["code"], "permission_denied")
        self.assertEqual(session.calls, [])

    async def test_write_tool_confirm_once_then_skips(self):
        session = StubSession()
        tool = RemoteTool(
            server(),
            session,
            tool_def("do"),
            read_only=False,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        first = await tool.execute({}, confirm_context("once"))
        self.assertTrue(first.success)
        second = await tool.execute({}, confirm_context("deny"))
        self.assertTrue(second.success)
        self.assertEqual(len(session.calls), 2)

    async def test_write_tool_without_confirm_callback_is_denied(self):
        session = StubSession()
        tool = RemoteTool(
            server(),
            session,
            tool_def("do"),
            read_only=False,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        result = await tool.execute({}, ToolContext(working_directory=Path.cwd()))
        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "permission_denied")

    async def test_write_tool_confirm_title_mentions_server_and_tool(self):
        session = StubSession()
        captured = {}

        async def confirm(title, detail):
            captured["title"] = title
            captured["detail"] = detail
            return "once"

        tool = RemoteTool(
            server(),
            session,
            tool_def("do"),
            read_only=False,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        context = ToolContext(working_directory=Path.cwd(), confirm=confirm)
        await tool.execute({"x": 1}, context)
        self.assertIn("srv", captured["title"])
        self.assertIn("do", captured["title"])
        self.assertIn("x", captured["detail"])

    async def test_executor_truncates_large_remote_output(self):
        class BigSession:
            async def call_tool(self, name, arguments):
                return ToolResult(True, "x" * 100_000)

        tool = RemoteTool(
            server(read_only_tools=("big",)),
            BigSession(),
            tool_def("big"),
            read_only=True,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        registry = ToolRegistry([tool])
        result = await ToolExecutor(registry).execute("c1", "srv_big", {})
        self.assertTrue(result.success)
        self.assertTrue(result.metadata.get("truncated"))
        self.assertLessEqual(len(result.output.encode("utf-8")), 65_536)

    async def test_mcp_error_is_mapped_to_tool_failure(self):
        class FailingSession:
            async def call_tool(self, name, arguments):
                raise McpError("connection_error", "server died")

        tool = RemoteTool(
            server(read_only_tools=("query",)),
            FailingSession(),
            tool_def("query"),
            read_only=True,
            allow_state=AllowState(),
            timeout_seconds=60.0,
        )
        result = await tool.execute({}, confirm_context())
        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "connection_error")


if __name__ == "__main__":
    unittest.main()
