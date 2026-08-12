import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zxcode.app import ZXCodeApp
from zxcode.client import Settings


class FakeClient:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        yield "ok"


class SlowClient(FakeClient):
    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        await asyncio.sleep(5)
        yield "slow"


class WorkerAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._sessions_tmp = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(
            os.environ,
            {
                "ZXCODE_SESSIONS_DIR": str(
                    Path(self._sessions_tmp.name) / "sessions"
                )
            },
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.addCleanup(self._sessions_tmp.cleanup)

    def make_app(self, client=None):
        return ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            client or FakeClient(),
        )

    def notices(self, app):
        return "\n".join(
            widget.render().plain for widget in app.query(".notice")
        )

    def test_app_registers_worker_tool_and_builtin_roles(self):
        app = self.make_app()

        self.assertIsNotNone(app.registry.get("SpawnWorker"))
        roles = app.worker_roles
        for expected in ("explorer", "planner", "general"):
            self.assertIn(expected, roles)
        self.assertNotIn("verifier", roles)

    def test_verifier_role_enabled_by_env(self):
        with patch.dict(os.environ, {"ZXCODE_ENABLE_VERIFIER": "1"}):
            app = self.make_app()

        self.assertIn("verifier", app.worker_roles)

    async def test_workers_command_list_detail_kill(self):
        app = self.make_app(client=SlowClient())

        async with app.run_test() as pilot:
            task = await app.worker_manager.start(
                role_name="explorer", task="scan", background=True
            )
            await pilot.pause()
            app.handle_command("/workers")
            await pilot.pause()
            listed = self.notices(app)
            app.handle_command(f"/workers {task.id}")
            await pilot.pause()
            detail = self.notices(app)
            app.handle_command(f"/workers kill {task.id}")
            await pilot.pause()
            killed = self.notices(app)

        self.assertIn(task.id, listed)
        self.assertIn("explorer", detail)
        self.assertIn("已终止", killed)
        self.assertEqual(app.worker_manager.get(task.id).status, "cancelled")

    async def test_help_includes_workers_command(self):
        app = self.make_app()

        async with app.run_test() as pilot:
            app.handle_command("/help")
            await pilot.pause()
            text = self.notices(app)

        self.assertIn("/workers", text)


if __name__ == "__main__":
    unittest.main()
