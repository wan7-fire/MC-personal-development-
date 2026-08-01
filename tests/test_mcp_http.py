import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from zxcode.mcp.errors import CONNECTION_ERROR, TIMEOUT, McpError
from zxcode.mcp.transports.http import HttpTransport, _iter_sse_payloads


class FakeHandler(BaseHTTPRequestHandler):
    received_headers = []
    delay = 0.0
    status = 200
    content_type = "application/json"
    sse = False
    session_header = "sess-abc"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        msg = json.loads(body)
        FakeHandler.received_headers.append(dict(self.headers))
        if FakeHandler.delay:
            import time

            time.sleep(FakeHandler.delay)
        response = {"jsonrpc": "2.0", "id": msg.get("id")}
        method = msg.get("method")
        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "1.0.0"},
            }
        elif method == "tools/list":
            response["result"] = {"tools": [{"name": "echo"}]}
        else:
            response["error"] = {"code": -32601, "message": "unknown"}
        self.send_response(FakeHandler.status)
        self.send_header("Content-Type", FakeHandler.content_type)
        if FakeHandler.session_header:
            self.send_header("Mcp-Session-Id", FakeHandler.session_header)
        self.end_headers()
        if FakeHandler.sse:
            payload = "data: " + json.dumps(response) + "\n\n"
            self.wfile.write(payload.encode("utf-8"))
        else:
            self.wfile.write(json.dumps(response).encode("utf-8"))


class HttpTransportTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        FakeHandler.received_headers = []
        FakeHandler.delay = 0.0
        FakeHandler.status = 200
        FakeHandler.sse = False
        FakeHandler.session_header = "sess-abc"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    async def asyncSetUp(self):
        self.transport = HttpTransport(f"http://127.0.0.1:{self.port}/mcp")
        self.received = []
        self.transport.set_message_handler(self.received.append)
        await self.transport.connect()

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_json_response_and_session_id_capture(self):
        await self.transport.send(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(self.received[0]["id"], 1)
        self.assertEqual(self.transport.session_id, "sess-abc")

    async def test_sse_response(self):
        FakeHandler.sse = True
        FakeHandler.content_type = "text/event-stream"
        try:
            await self.transport.send(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
        finally:
            FakeHandler.sse = False
            FakeHandler.content_type = "application/json"
        self.assertEqual(self.received[0]["result"]["tools"][0]["name"], "echo")

    async def test_headers_after_session_set(self):
        self.transport.set_session("sess-abc", "2025-06-18")
        await self.transport.send(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        headers = FakeHandler.received_headers[-1]
        self.assertEqual(headers["Mcp-Session-Id"], "sess-abc")
        self.assertEqual(headers["MCP-Protocol-Version"], "2025-06-18")
        self.assertEqual(
            headers["Accept"], "application/json, text/event-stream"
        )

    async def test_error_status_raises_connection_error(self):
        FakeHandler.status = 500
        try:
            with self.assertRaises(McpError) as ctx:
                await self.transport.send(
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}
                )
        finally:
            FakeHandler.status = 200
        self.assertEqual(ctx.exception.code, CONNECTION_ERROR)

    async def test_timeout_raises_timeout_error(self):
        self.transport.timeout = 0.05
        self.transport._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.transport.timeout)
        )
        FakeHandler.delay = 0.5
        try:
            with self.assertRaises(McpError) as ctx:
                await self.transport.send(
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}}
                )
        finally:
            FakeHandler.delay = 0.0
        self.assertEqual(ctx.exception.code, TIMEOUT)


class SseParserTests(unittest.TestCase):
    def test_yields_payloads(self):
        text = (
            "event: message\ndata: {\"a\": 1}\n\n"
            "data: {\"b\": 2}\ndata: extra\n\n"
        )
        self.assertEqual(list(_iter_sse_payloads(text)), ['{"a": 1}', '{"b": 2}\nextra'])

    def test_trailing_payload_without_blank_line(self):
        self.assertEqual(list(_iter_sse_payloads("data: x")), ["x"])


if __name__ == "__main__":
    unittest.main()
