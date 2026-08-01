import asyncio
import json
import sys
import unittest

from zxcode.mcp.errors import CONNECTION_ERROR, McpError
from zxcode.mcp.transports.stdio import StdioTransport


FAKE_SERVER = r"""
import json, sys

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0.0"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": []}})
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "hello from stdio"}],
            "isError": False,
        }})
    else:
        send({"jsonrpc": "2.0", "id": msg["id"], "error": {
            "code": -32601, "message": "unknown method"}})
"""


class StdioTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = StdioTransport([sys.executable, "-c", FAKE_SERVER])
        self.received = []
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()

        def on_message(message):
            self.received.append(message)
            self.ready.set()

        self.transport.set_message_handler(on_message)
        self.transport.set_close_handler(self.closed.set)
        await self.transport.connect()

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_request_response_round_trip(self):
        await self.transport.send(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        await asyncio.wait_for(self.ready.wait(), 5)
        self.assertEqual(self.received[0]["id"], 1)
        self.assertEqual(self.received[0]["result"]["tools"], [])

    async def test_malformed_json_line_is_ignored(self):
        # Raw line injection is not part of the public API; write directly to
        # the stdin pipe to simulate a server emitting garbage.
        self.transport._process.stdin.write(b"{not json\n")
        await self.transport._process.stdin.drain()
        await self.transport.send(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        await asyncio.wait_for(self.ready.wait(), 5)
        self.assertEqual(self.received[0]["id"], 2)

    async def test_close_terminates_child(self):
        pid = self.transport.pid
        self.assertIsNotNone(pid)
        await self.transport.close()
        process = self.transport._process
        self.assertIsNone(process)
        await asyncio.wait_for(self.closed.wait(), 5)
        with self.assertRaises(McpError) as ctx:
            await self.transport.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(ctx.exception.code, CONNECTION_ERROR)

    async def test_stderr_is_captured_not_protocol(self):
        await self.transport.send(
            {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}}
        )
        await asyncio.wait_for(self.ready.wait(), 5)
        self.assertEqual(self.received[0]["result"]["protocolVersion"], "2025-06-18")


if __name__ == "__main__":
    unittest.main()
