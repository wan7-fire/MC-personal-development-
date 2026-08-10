import asyncio
import http.server
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from zxcode.rules.actions import ActionResult, execute_action, render_template
from zxcode.rules.executor import run_action
from zxcode.rules.model import Action
from zxcode.security import load_policy
from zxcode.tools import ToolContext


CONTEXT = {
    "event": "session_start",
    "tool": "WriteFile",
    "path": "a.tmp",
    "message": "hi",
    "error": "boom",
    "args": {"path": "a.tmp"},
}


class RenderTests(unittest.TestCase):
    def test_known_variables_are_replaced(self):
        text = "{{event}}/{{tool}}/{{path}}/{{message}}/{{error}}/{{args.path}}"
        self.assertEqual(
            render_template(text, CONTEXT),
            "session_start/WriteFile/a.tmp/hi/boom/a.tmp",
        )

    def test_undefined_variable_becomes_empty_string(self):
        self.assertEqual(render_template("x{{missing}}y", CONTEXT), "xy")


class DirTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._roots = []

    def tearDown(self):
        for root in self._roots:
            shutil.rmtree(root, ignore_errors=True)

    def _root(self):
        root = Path(tempfile.mkdtemp(dir=Path.cwd()))
        self._roots.append(root)
        return root


class CommandActionTests(DirTests):

    async def test_command_runs_without_security(self):
        root = self._root()
        action = Action("command", {"command": "Set-Content marker.txt done"})

        result = await execute_action(
            action,
            CONTEXT,
            root=root,
            confirm=None,
            security=None,
            timeout_seconds=10,
        )

        self.assertTrue(result.success)
        self.assertEqual((root / "marker.txt").read_text(encoding="utf-8").strip(), "done")

    async def test_command_respects_security_policy(self):
        root = self._root()
        policy = load_policy(root)
        approvals = []

        async def confirm(title, detail):
            approvals.append(title)
            return True

        action = Action("command", {"command": "Set-Content marker.txt done"})
        result = await execute_action(
            action,
            CONTEXT,
            root=root,
            confirm=confirm,
            security=policy,
            timeout_seconds=10,
        )

        self.assertTrue(result.success)
        self.assertEqual(len(approvals), 1)
        self.assertTrue((root / "marker.txt").exists())

    async def test_command_blocked_in_strict_mode(self):
        root = self._root()
        policy = load_policy(root)
        policy.mode = "strict"
        action = Action("command", {"command": "Set-Content marker.txt done"})

        result = await execute_action(
            action,
            CONTEXT,
            root=root,
            confirm=None,
            security=policy,
            timeout_seconds=10,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "security_blocked")
        self.assertFalse((root / "marker.txt").exists())


class HttpActionTests(DirTests):
    def _serve(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, requests

    async def test_http_action_reaches_server_in_allow_mode(self):
        server, requests = self._serve()
        root = self._root()
        policy = load_policy(root)
        policy.mode = "allow"
        try:
            action = Action(
                "http",
                {"url": f"http://127.0.0.1:{server.server_port}/ping"},
            )
            result = await execute_action(
                action,
                CONTEXT,
                root=root,
                confirm=None,
                security=policy,
                timeout_seconds=10,
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertTrue(result.success)
        self.assertEqual(requests, ["/ping"])


class AgentActionTests(DirTests):
    async def test_agent_action_returns_not_implemented(self):
        root = self._root()
        action = Action("agent", {"name": "reviewer"})

        result = await execute_action(
            action, CONTEXT, root=root, confirm=None, security=None, timeout_seconds=5
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "not_implemented")


class ExecutorTests(DirTests):
    async def test_action_timeout_returns_timeout_result(self):
        root = self._root()
        action = Action("command", {"command": "Start-Sleep -Seconds 10"})

        started = asyncio.get_event_loop().time()
        result = await run_action(
            action,
            CONTEXT,
            root=root,
            confirm=None,
            security=None,
            timeout_seconds=0.2,
        )
        elapsed = asyncio.get_event_loop().time() - started

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "timeout")
        self.assertLess(elapsed, 5)


if __name__ == "__main__":
    unittest.main()
