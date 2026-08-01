"""Shared fake MCP servers and clients for integration tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zxcode.client import AssistantMessage, TextDelta


STDIO_SERVER_SCRIPT = r"""
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
            "serverInfo": {"name": "fake-stdio", "version": "1.0.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "echo", "description": "Echo text back",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]},
             "annotations": {"readOnlyHint": True}},
            {"name": "write", "description": "Pretend to write something",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") == "echo":
            text = params.get("arguments", {}).get("text", "")
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "hello from stdio:" + text}],
                "isError": False}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "wrote"}], "isError": False}})
"""


class McpHttpHandler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)
        msg = json.loads(body)
        if "id" not in msg:
            # Notifications must not receive a response.
            self.send_response(202)
            self.end_headers()
            return
        McpHttpHandler.received.append(
            {"headers": dict(self.headers), "message": msg}
        )
        method = msg.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-http", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo over http",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        elif method == "tools/call":
            params = msg.get("params", {})
            text = params.get("arguments", {}).get("text", "")
            result = {
                "content": [{"type": "text", "text": "hello from http:" + text}],
                "isError": False,
            }
        else:
            result = None
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if method == "initialize":
            self.send_header("Mcp-Session-Id", "e2e-session")
        self.end_headers()
        self.wfile.write(payload)


def start_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), McpHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


class RemoteToolCallingClient:
    """Calls one remote tool on the first turn, then answers from its result."""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, list(tools or [])))
        if len(self.requests) == 1:
            yield AssistantMessage(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "mcp-call-1",
                            "type": "function",
                            "function": {
                                "name": self.tool_name,
                                "arguments": json.dumps({"text": "ping"}),
                            },
                        }
                    ],
                }
            )
            return
        output = ""
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                output = str(json.loads(message.get("content") or "{}").get("output", ""))
            except json.JSONDecodeError:
                output = str(message.get("content") or "")
        text = f"result:{output}"
        yield TextDelta(text)
        yield AssistantMessage({"role": "assistant", "content": text})
