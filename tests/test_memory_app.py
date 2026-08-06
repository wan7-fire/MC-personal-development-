import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Label, ListItem, Static, TextArea

from zxcode.app import ConfirmScreen, SessionPickerScreen, ZXCodeApp
from zxcode.client import Settings
from zxcode.notes import NotesConfig, NotesManager
from zxcode.storage import SessionStore


class FakeClient:
    def __init__(self, parts=("你", "好", "！")):
        self.parts = parts
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        for part in self.parts:
            yield part


class MemoryAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"ZXCODE_SESSIONS_DIR": str(Path(self._temp.name) / "sessions")},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._temp.cleanup)

    def make_app(self, store=None, notes=None):
        store = store or SessionStore(Path(self._temp.name) / "sessions")
        return ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            FakeClient(),
            store=store,
            notes=notes,
        )

    def notices(self, app):
        return "\n".join(widget.render().plain for widget in app.query(".notice"))

    async def test_startup_injects_project_instructions(self):
        expected = "\n".join(
            Path.cwd().joinpath("ZXCODE.md").read_text(encoding="utf-8").splitlines()
        )
        app = self.make_app()

        async with app.run_test():
            request = app.session.request_messages("hello")

        self.assertTrue(
            any(
                message.get("role") == "system"
                and expected in str(message.get("content"))
                for message in request[2:]
            )
        )
        self.assertEqual(app.session.messages, [])
        self.assertEqual(app.session.turns, 0)

    async def test_clear_keeps_instructions_in_request(self):
        app = self.make_app()
        expected = app.instruction_messages

        async with app.run_test() as pilot:
            app.session.commit("hi", "yo")
            app.handle_command("/clear")
            await pilot.pause()
            request = app.session.request_messages("again")

        self.assertEqual(app.session.messages, [])
        self.assertTrue(
            all(instruction in request for instruction in expected)
        )

    async def test_generate_persists_turn_and_meta(self):
        app = self.make_app()

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("hello")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()
            session_id = app.session_id
            store = app.store

        self.assertIsNotNone(session_id)
        lines = store.jsonl_path(session_id).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["content"], "hello")
        meta = store.read_meta(session_id)
        self.assertEqual(meta.message_count, 2)
        self.assertEqual(meta.title, "hello")

    async def test_resume_restores_session(self):
        app = self.make_app()
        app.store.append_messages(
            "abc123", [{"role": "user", "content": "saved"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/resume abc123")
            await app.resume_worker.wait()
            await pilot.pause()
            self.assertEqual(
                app.session.messages, [{"role": "user", "content": "saved"}]
            )
            self.assertEqual(app.session_id, "abc123")
            self.assertIn("已恢复会话 abc123", self.notices(app))

    async def test_resume_unknown_session_notices(self):
        app = self.make_app()

        async with app.run_test() as pilot:
            app.handle_command("/resume nope")
            await app.resume_worker.wait()
            await pilot.pause()
            self.assertIn("未找到会话", self.notices(app))

    async def test_resume_without_id_shows_session_picker(self):
        app = self.make_app()
        app.store.append_messages(
            "abc123", [{"role": "user", "content": "saved"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/resume")
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionPickerScreen)
            self.assertEqual(app.screen.metas[0].id, "abc123")
            labels = [
                item.query_one(Label).render().plain
                for item in app.screen.query(ListItem)
            ]
            self.assertTrue(any("saved" in text for text in labels))

    async def test_resume_picker_loads_history(self):
        app = self.make_app()
        app.store.append_messages(
            "abc123",
            [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "我是助手"},
            ],
            "model-a",
        )

        async with app.run_test() as pilot:
            app.handle_command("/resume")
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionPickerScreen)
            await pilot.press("enter")
            await pilot.pause()
            await app.resume_worker.wait()
            await pilot.pause()
            self.assertEqual(app.session_id, "abc123")
            self.assertEqual(len(app.session.messages), 2)
            history = "\n".join(
                widget.render().plain for widget in app.query(".message")
            )
            self.assertIn("你好", history)
            self.assertIn("我是助手", history)

    async def test_resume_picker_escape_cancels(self):
        app = self.make_app()
        app.store.append_messages(
            "abc123", [{"role": "user", "content": "saved"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/resume")
            await pilot.pause()
            self.assertIsInstance(app.screen, SessionPickerScreen)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsNone(app.session_id)

    async def test_resume_without_id_and_no_sessions_notices(self):
        app = self.make_app()

        async with app.run_test() as pilot:
            app.handle_command("/resume")
            await pilot.pause()
            self.assertIn("没有会话", self.notices(app))

    async def test_sessions_list_and_path(self):
        app = self.make_app()
        app.store.append_messages(
            "s1", [{"role": "user", "content": "title-here"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/sessions")
            await pilot.pause()
            self.assertIn("s1", self.notices(app))
            self.assertIn("title-here", self.notices(app))
            app.handle_command("/sessions path")
            await pilot.pause()
            self.assertIn(str(app.store.root), self.notices(app))

    async def test_sessions_delete_with_confirm(self):
        app = self.make_app()
        app.store.append_messages(
            "s1", [{"role": "user", "content": "x"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/sessions delete s1")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#approve")
            await app.sessions_worker.wait()
            await pilot.pause()

        self.assertFalse(app.store.jsonl_path("s1").exists())
        self.assertFalse(app.store.meta_path("s1").exists())

    async def test_sessions_clear_with_confirm(self):
        app = self.make_app()
        app.store.append_messages(
            "s1", [{"role": "user", "content": "x"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/sessions clear")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#approve")
            await app.sessions_worker.wait()
            await pilot.pause()

        self.assertEqual(app.store.list_meta(), [])

    async def test_sessions_delete_cancelled_on_dialog_dismiss(self):
        app = self.make_app()
        app.store.append_messages(
            "s1", [{"role": "user", "content": "x"}], "model-a"
        )

        async with app.run_test() as pilot:
            app.handle_command("/sessions delete s1")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            # Dismissing without a button (e.g. Escape) returns None, which
            # must cancel the destructive action instead of approving it.
            app.screen.dismiss(None)
            await app.sessions_worker.wait()
            await pilot.pause()

        self.assertTrue(app.store.jsonl_path("s1").exists())
        self.assertTrue(app.store.meta_path("s1").exists())

    async def test_notes_view_clear_and_path(self):
        user_dir = Path(self._temp.name) / "user"
        project_notes = Path(self._temp.name) / "proj" / ".zxcode" / "notes.md"
        notes = NotesManager(
            Path(self._temp.name) / "proj",
            FakeClient(),
            user_dir=user_dir,
            config=NotesConfig(project_notes_path=project_notes),
        )
        notes._write_atomic(notes.user_notes_path(), "## 用户偏好\n偏好A")
        notes._write_atomic(project_notes, "## 项目知识\n知识A")
        app = self.make_app(notes=notes)

        async with app.run_test() as pilot:
            app.handle_command("/notes")
            await pilot.pause()
            text = self.notices(app)
            self.assertIn("偏好A", text)
            self.assertIn("知识A", text)

            app.handle_command("/notes path")
            await pilot.pause()
            self.assertIn(str(project_notes), self.notices(app))

            app.handle_command("/notes clear project")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#approve")
            await app.notes_worker.wait()
            await pilot.pause()

        self.assertEqual(project_notes.read_text(encoding="utf-8"), "")
        self.assertIn("偏好A", notes.user_notes_path().read_text(encoding="utf-8"))

    async def test_resume_keeps_instructions_in_request(self):
        app1 = self.make_app()
        async with app1.run_test() as pilot:
            app1.query_one("#input", TextArea).load_text("hello")
            app1.action_submit()
            await app1.active_worker.wait()
            await pilot.pause()
            session_id = app1.session_id
        store = app1.store

        app2 = self.make_app(store=store)
        async with app2.run_test() as pilot:
            app2.handle_command(f"/resume {session_id}")
            await app2.resume_worker.wait()
            await pilot.pause()
            request = app2.session.request_messages("continue")
            self.assertEqual(app2.session.turns, 1)

        self.assertTrue(
            all(instruction in request for instruction in app2.instruction_messages)
        )


if __name__ == "__main__":
    unittest.main()
