import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Static

from zxcode.app import ZXCodeApp
from zxcode.client import Settings
from zxcode.commands.model import AIPrompt, CommandMeta, CommandType


class FakeClient:
    def __init__(self, parts=("你", "好", "！")):
        self.parts = parts
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        for part in self.parts:
            yield part


class CommandE2ETests(unittest.IsolatedAsyncioTestCase):
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

    def notices(self, app):
        return "\n".join(widget.render().plain for widget in app.query(".notice"))

    async def test_help_is_case_insensitive(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            app.handle_command("/HELP")
            await pilot.pause()
            upper = self.notices(app)
            app.handle_command("/help")
            await pilot.pause()
            lower = self.notices(app)
        self.assertIn("可用命令", upper)
        self.assertIn("/help  /clear  /exit  /model <名称>  /plan", upper)
        self.assertIn("/status", lower)
        self.assertIn("/permissions", lower)
        self.assertIn("/review", lower)

    async def test_alias_executes(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            app.handle_command("/h")
            await pilot.pause()
            self.assertIn("可用命令", self.notices(app))

    async def test_unknown_command_guides_help_without_llm(self):
        client = FakeClient()
        app = self.make_app(client)
        async with app.run_test() as pilot:
            app.handle_command("/nope")
            await pilot.pause()
            self.assertIn("/help", self.notices(app))
            self.assertEqual(client.calls, [])

    async def test_status_command_output(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            app.handle_command("/status")
            await pilot.pause()
            text = self.notices(app)
        self.assertIn("模型：model-a", text)
        self.assertIn("模式：执行", text)
        self.assertIn("token 估算", text)
        self.assertIn("无会话", text)

    async def test_status_bar_shows_mode_and_command_hints(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            status = app.query_one("#status", Static).render().plain
            self.assertIn("模式: 执行", status)
            self.assertIn("命令: /help /status /compact", status)
            app.handle_command("/plan")
            await pilot.pause()
            status = app.query_one("#status", Static).render().plain
            self.assertIn("模式: 计划", status)
            app.handle_command("/plan")
            await pilot.pause()
            status = app.query_one("#status", Static).render().plain
            self.assertIn("模式: 执行", status)

    async def test_review_skill_runs_in_isolated_subsession(self):
        client = FakeClient()
        app = self.make_app(client)
        async with app.run_test() as pilot:
            await app.run_skill("review")
            await pilot.pause()
            self.assertIn("隔离执行 Skill review", self.notices(app))
            self.assertEqual(len(client.calls), 1)
            request_messages = client.calls[0][0]
            self.assertTrue(
                any(
                    str(message.get("content", "")).startswith(
                        "[Skill 指令：review]"
                    )
                    for message in request_messages
                )
            )
            self.assertIn(
                "状态：", app.session.messages[-1]["content"]
            )

    async def test_permissions_command_output(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            app.handle_command("/permissions")
            await pilot.pause()
            self.assertIn("安全策略模式", self.notices(app))

    async def test_ai_command_a_plus_b_persists_trigger_only(self):
        client = FakeClient()
        app = self.make_app(client)

        def handler(ctx, invocation):
            return AIPrompt("触发消息", ["预设提示词全文"])

        app.command_registry.register(
            CommandMeta(
                "ai",
                "测试 AI 命令",
                "/ai",
                CommandType.AI_FLOW,
                handler,
            )
        )
        async with app.run_test() as pilot:
            app.handle_command("/ai")
            await app.active_worker.wait()
            await pilot.pause()
            self.assertEqual(app.session.turns, 1)
            self.assertIsNone(app.notes_task)
            archive = app.store.jsonl_path(app.session_id).read_text(
                encoding="utf-8"
            )
            self.assertIn("触发消息", archive)
            self.assertNotIn("预设提示词全文", archive)


if __name__ == "__main__":
    unittest.main()
