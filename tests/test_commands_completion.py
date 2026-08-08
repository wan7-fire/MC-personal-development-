import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import TextArea

from zxcode.app import CommandPickerScreen, ZXCodeApp
from zxcode.client import Settings


class FakeClient:
    def __init__(self, parts=("你", "好", "！")):
        self.parts = parts
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        for part in self.parts:
            yield part


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"ZXCODE_SESSIONS_DIR": str(Path(self._temp.name) / "sessions")},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._temp.cleanup)

    def make_app(self):
        return ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            FakeClient(),
        )

    async def test_single_match_completes(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            box = app.query_one("#input", TextArea)
            box.load_text("/res")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(box.text, "/resume")

    async def test_completion_keeps_existing_args(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            box = app.query_one("#input", TextArea)
            box.load_text("/res abc")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(box.text, "/resume abc")

    async def test_multi_match_opens_picker_and_selects(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            box = app.query_one("#input", TextArea)
            box.load_text("/s")
            await pilot.press("tab")
            await pilot.pause()
            self.assertIsInstance(app.screen, CommandPickerScreen)
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(box.text, "/sessions")

    async def test_no_match_keeps_text(self):
        app = self.make_app()
        async with app.run_test() as pilot:
            box = app.query_one("#input", TextArea)
            box.load_text("/zzz")
            await pilot.press("tab")
            await pilot.pause()
            self.assertEqual(box.text, "/zzz")


if __name__ == "__main__":
    unittest.main()
