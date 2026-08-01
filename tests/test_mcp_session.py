import asyncio
import unittest

from zxcode.mcp.config import ServerConfig
from zxcode.mcp.errors import HANDSHAKE_FAILED, TIMEOUT, McpError
from zxcode.mcp.session import McpSession, SUPPORTED_PROTOCOL_VERSIONS
from zxcode.mcp.transports import Transport


class ScriptedTransport(Transport):
    def __init__(self, handler):
        self._handler = handler
        self.sent = []
        self.closed = False
        self.connected = False
        self._message_handler = None
        self._close_handler = None

    def set_message_handler(self, handler):
        self._message_handler = handler

    def set_close_handler(self, handler):
        self._close_handler = handler

    async def connect(self):
        self.connected = True

    async def send(self, message):
        self.sent.append(dict(message))
        replies = await self._handler(dict(message))
        for reply in replies:
            self._message_handler(reply)

    async def close(self):
        self.connected = False
        if not self.closed:
            self.closed = True
            if self._close_handler is not None:
                self._close_handler()

    def simulate_close(self):
        self.connected = False
        if self._close_handler is not None:
            self._close_handler()


def make_server(**overrides):
    calls = {"tools_list_count": 0}

    async def handler(message):
        method = message.get("method")
        if method == "initialize":
            return [
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": overrides.get(
                            "version", SUPPORTED_PROTOCOL_VERSIONS[0]
                        ),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1.0.0"},
                    },
                }
            ]
        if method == "notifications/initialized":
            return []
        if method == "tools/list":
            calls["tools_list_count"] += 1
            if overrides.get("paginate") and calls["tools_list_count"] == 1:
                return [
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "tools": [{"name": "first"}],
                            "nextCursor": "page-2",
                        },
                    }
                ]
            if overrides.get("paginate"):
                return [
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {"tools": [{"name": "second"}]},
                    }
                ]
            return [
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"tools": [{"name": "echo"}, {"name": "sum"}]},
                }
            ]
        if method == "tools/call":
            if overrides.get("no_reply"):
                return []
            if overrides.get("call_error"):
                return [
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {
                            "code": overrides["call_error"],
                            "message": "bad call",
                        },
                    }
                ]
            return [
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": overrides.get(
                        "call_result",
                        {
                            "content": [{"type": "text", "text": "hello"}],
                            "isError": False,
                        },
                    ),
                }
            ]
        if overrides.get("extra_reply"):
            return [overrides["extra_reply"]]
        return []

    return handler


def session_for(handler, **kwargs):
    transport = ScriptedTransport(handler)
    server = ServerConfig(
        name="srv",
        transport="stdio",
        command=("python", "-c", ""),
        connect_timeout_seconds=2.0,
        call_timeout_seconds=2.0,
    )
    return McpSession(server, transport=transport), transport


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_flow_and_tool_discovery(self):
        session, transport = session_for(make_server())
        await session.connect()
        self.assertEqual(transport.sent[0]["method"], "initialize")
        self.assertEqual(
            transport.sent[0]["params"]["protocolVersion"], "2025-06-18"
        )
        self.assertEqual(transport.sent[0]["params"]["clientInfo"]["name"], "ZXCode")
        self.assertEqual(transport.sent[1]["method"], "notifications/initialized")
        tools = await session.list_tools()
        self.assertEqual([tool["name"] for tool in tools], ["echo", "sum"])
        self.assertEqual(transport.sent[2]["method"], "tools/list")

    async def test_tools_list_pagination(self):
        session, transport = session_for(make_server(paginate=True))
        await session.connect()
        tools = await session.list_tools()
        self.assertEqual([tool["name"] for tool in tools], ["first", "second"])
        self.assertEqual(transport.sent[2]["method"], "tools/list")
        self.assertEqual(transport.sent[2].get("params", {}), {})
        self.assertEqual(transport.sent[3]["params"], {"cursor": "page-2"})

    async def test_call_tool_success(self):
        session, _ = session_for(make_server())
        result = await session.call_tool("echo", {"text": "hi"})
        self.assertTrue(result.success)
        self.assertEqual(result.output, "hello")
        self.assertEqual(result.metadata["server"], "srv")

    async def test_call_tool_is_error_result(self):
        session, _ = session_for(
            make_server(
                call_result={
                    "content": [{"type": "text", "text": "boom"}],
                    "isError": True,
                }
            )
        )
        result = await session.call_tool("echo", {})
        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "remote_error")

    async def test_call_tool_structured_content_fallback(self):
        session, _ = session_for(
            make_server(
                call_result={"structuredContent": {"temperature": 22.5}, "isError": False}
            )
        )
        result = await session.call_tool("echo", {})
        self.assertTrue(result.success)
        self.assertIn("22.5", result.output)

    async def test_call_tool_rpc_error_mapping(self):
        session, _ = session_for(make_server(call_error=-32602))
        result = await session.call_tool("echo", {})
        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "invalid_arguments")

    async def test_version_mismatch_fails_handshake(self):
        session, transport = session_for(make_server(version="2024-11-05"))
        with self.assertRaises(McpError) as ctx:
            await session.connect()
        self.assertEqual(ctx.exception.code, HANDSHAKE_FAILED)
        self.assertTrue(transport.closed)
        self.assertFalse(session.connected)

    async def test_initialize_error_fails_handshake(self):
        async def handler(message):
            if message.get("method") == "initialize":
                return [
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": "Unsupported protocol version"},
                    }
                ]
            return []

        session, transport = session_for(handler)
        with self.assertRaises(McpError) as ctx:
            await session.connect()
        self.assertEqual(ctx.exception.code, HANDSHAKE_FAILED)

    async def test_timeout_sends_cancel_notification(self):
        session, transport = session_for(make_server(no_reply=True))
        await session.connect()
        await session.list_tools()
        with self.assertRaises(McpError) as ctx:
            await session.call_tool("echo", {"text": "x"})
        self.assertEqual(ctx.exception.code, TIMEOUT)
        cancelled = [
            item for item in transport.sent if item["method"] == "notifications/cancelled"
        ]
        self.assertEqual(len(cancelled), 1)
        self.assertIn("requestId", cancelled[0]["params"])

    async def test_invalid_message_fails_pending_request(self):
        async def handler(message):
            if message.get("method") == "initialize":
                return [
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {"name": "fake", "version": "1"},
                        },
                    }
                ]
            if message.get("method") == "notifications/initialized":
                return []
            if message.get("method") == "tools/list":
                return [
                    {"jsonrpc": "2.0", "id": message["id"], "unexpected": True}
                ]
            return []

        session, _ = session_for(handler)
        await session.connect()
        with self.assertRaises(McpError) as ctx:
            await session.list_tools()
        self.assertEqual(ctx.exception.code, "invalid_json")

    async def test_unknown_id_response_is_ignored(self):
        session, _ = session_for(
            make_server(
                extra_reply={"jsonrpc": "2.0", "id": 999, "result": {"tools": []}}
            )
        )
        await session.connect()
        tools = await session.list_tools()
        self.assertEqual(len(tools), 2)

    async def test_transport_close_fails_pending(self):
        session, transport = session_for(make_server(no_reply=True))
        await session.connect()
        await session.list_tools()
        task = asyncio.create_task(session.call_tool("echo", {"text": "x"}))
        await asyncio.sleep(0.01)
        transport.simulate_close()
        with self.assertRaises(McpError) as ctx:
            await task
        self.assertEqual(ctx.exception.code, "connection_error")
        self.assertFalse(session.connected)

    async def test_reconnect_after_transport_close(self):
        session, transport = session_for(make_server())
        await session.connect()
        self.assertEqual(await session.call_tool("echo", {}), await session.call_tool("echo", {}))
        transport.simulate_close()
        self.assertFalse(session.connected)
        result = await session.call_tool("echo", {})
        self.assertTrue(result.success)
        self.assertEqual(transport.sent[0]["method"], "initialize")
        self.assertEqual(transport.sent[0]["params"]["protocolVersion"], "2025-06-18")

    async def test_cancelling_waiter_clears_pending_request(self):
        session, transport = session_for(make_server(no_reply=True))
        await session.connect()
        await session.list_tools()
        task = asyncio.create_task(session.call_tool("echo", {"text": "x"}))
        await asyncio.sleep(0.02)
        self.assertEqual(session._matcher.pending_count, 1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        self.assertEqual(session._matcher.pending_count, 0)


if __name__ == "__main__":
    unittest.main()
