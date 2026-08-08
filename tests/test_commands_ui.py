import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zxcode.app import ZXCodeApp
from zxcode.client import Settings


class FakeClient:
    def __init__(self, parts=("你", "好", "！")):
        self.parts = parts
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        for part in self.parts:
            yield part


class TextualUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"ZXCODE_SESSIONS_DIR": str(Path(self._temp.name) / "sessions")},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._temp.cleanup)

    def make_app(self, client=None):
        return ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            client or FakeClient(),
        )

    async def test_clear_chat(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            app.session.commit("hi", "yo")
            app.command_context.ui.clear_chat()
            await pilot.pause()
            self.assertEqual(app.session.messages, [])
            self.assertEqual(app.session.turns, 0)
            self.assertIsNone(app.session_id)

    async def test_set_model_and_toggle_plan_mode(self):
        app = self.make_app()
        ui = app.command_context.ui
        async with app.run_test():
            ui.set_model("model-b")
            self.assertEqual(app.session.model, "model-b")
            self.assertTrue(ui.toggle_plan_mode())
            self.assertTrue(app.config.plan_only)
            self.assertFalse(ui.toggle_plan_mode())
            self.assertFalse(app.config.plan_only)

    async def test_status_summary_fields(self):
        app = self.make_app()
        async with app.run_test():
            app.session.commit("hi", "yo")
            info = app.command_context.ui.status_summary()
            self.assertEqual(info["model"], "model-a")
            self.assertEqual(info["turns"], 1)
            self.assertFalse(info["plan_only"])
            self.assertIsNone(info["session_id"])
            self.assertIn("token_estimate", info)
            self.assertIn("sessions_dir", info)

    async def test_send_user_message_a_plus_b(self):
        client = FakeClient()
        app = self.make_app(client)
        async with app.run_test() as pilot:
            app.command_context.ui.send_user_message(
                "触发消息", ["预设提示词全文"]
            )
            await app.active_worker.wait()
            await pilot.pause()
            self.assertEqual(app.session.turns, 1)
            self.assertTrue(
                any(
                    message.get("role") == "user"
                    and message.get("content") == "触发消息"
                    for message in app.session.messages
                )
            )
            request = client.calls[0][0]
            self.assertTrue(
                any(
                    message.get("role") == "system"
                    and "预设提示词全文" in message.get("content", "")
                    for message in request
                )
            )
            archive = app.store.jsonl_path(app.session_id).read_text(
                encoding="utf-8"
            )
            self.assertIn("触发消息", archive)
            self.assertNotIn("预设提示词全文", archive)

    async def test_worker_scheduling_helpers(self):
        app = self.make_app()
        ui = app.command_context.ui
        app.store.append_messages(
            "abc123", [{"role": "user", "content": "saved"}], "model-a"
        )
        async with app.run_test() as pilot:
            ui.run_compact()
            self.assertIsNotNone(app.compact_worker)
            ui.resume_session("abc123")
            self.assertIsNotNone(app.resume_worker)
            await app.resume_worker.wait()
            await pilot.pause()
            self.assertEqual(app.session_id, "abc123")

    async def test_security_summary_mentions_mode(self):
        app = self.make_app()
        async with app.run_test():
            summary = app.command_context.ui.security_summary()
            self.assertIn("default", summary)


if __name__ == "__main__":
    unittest.main()
