import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Static

from zxcode.app import ZXCodeApp
from zxcode.client import Settings
from zxcode.rules.engine import RuleEngine
from zxcode.rules.loader import RuleLoadError
from zxcode.rules.model import Action, Rule


class FakeClient:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        yield "ok"


def prompt_rule(rule_id, event, prompt):
    return Rule(
        id=rule_id,
        event=event,
        actions=(Action("prompt", {"prompt": prompt}),),
    )


class RuleAppTests(unittest.IsolatedAsyncioTestCase):
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

    def test_app_loads_rules_at_startup(self):
        rules = [prompt_rule("r1", "session_start", "hi")]
        with patch("zxcode.app.load_rules", return_value=rules):
            app = self.make_app()

        self.assertIsNotNone(app.rule_engine.get("r1"))

    def test_app_reports_load_error_and_keeps_empty_engine(self):
        with patch(
            "zxcode.app.load_rules",
            side_effect=RuleLoadError(Path("rules.yaml"), "bad", "bad event"),
        ):
            app = self.make_app()

        self.assertEqual(app.rule_engine.list_rules(), [])
        self.assertIn("bad", app.rule_load_error)

    async def test_on_mount_fires_session_and_system_events(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            app = self.make_app()
            app.rule_engine = RuleEngine(
                [
                    Rule(
                        id="start",
                        event="session_start",
                        actions=(
                            Action(
                                "command",
                                {"command": "Set-Content session.txt start"},
                            ),
                        ),
                    ),
                    Rule(
                        id="sys",
                        event="system_startup",
                        actions=(
                            Action(
                                "command",
                                {"command": "Set-Content system.txt up"},
                            ),
                        ),
                    ),
                ],
                root=root,
            )

            async with app.run_test() as pilot:
                await pilot.pause()

            self.assertEqual(
                (root / "session.txt").read_text(encoding="utf-8").strip(),
                "start",
            )
            self.assertEqual(
                (root / "system.txt").read_text(encoding="utf-8").strip(), "up"
            )

    async def test_rules_command_lists_and_shows_detail(self):
        app = self.make_app()
        app.rule_engine = RuleEngine(
            [prompt_rule("r1", "session_start", "hi")]
        )

        async with app.run_test() as pilot:
            app.handle_command("/rules")
            await pilot.pause()
            listed = self.notices(app)
            app.handle_command("/rules r1")
            await pilot.pause()
            detail = self.notices(app)

        self.assertIn("r1", listed)
        self.assertIn("session_start", detail)
        self.assertIn("prompt", detail)

    async def test_rules_reload_replaces_rules(self):
        app = self.make_app()
        app.rule_engine = RuleEngine([prompt_rule("old", "turn_start", "x")])
        new_rules = [prompt_rule("new", "turn_end", "y")]
        with patch("zxcode.app.load_rules", return_value=new_rules):
            async with app.run_test() as pilot:
                app.handle_command("/rules reload")
                await pilot.pause()

        self.assertIsNotNone(app.rule_engine.get("new"))
        self.assertIsNone(app.rule_engine.get("old"))

    async def test_rules_reload_keeps_old_rules_on_error(self):
        app = self.make_app()
        app.rule_engine = RuleEngine([prompt_rule("old", "turn_start", "x")])
        with patch(
            "zxcode.app.load_rules",
            side_effect=RuleLoadError(Path("rules.yaml"), "bad", "bad"),
        ):
            async with app.run_test() as pilot:
                app.handle_command("/rules reload")
                await pilot.pause()
                text = self.notices(app)

        self.assertIsNotNone(app.rule_engine.get("old"))
        self.assertIn("保留旧规则", text)

    async def test_help_includes_rules_command(self):
        app = self.make_app()

        async with app.run_test() as pilot:
            app.handle_command("/help")
            await pilot.pause()
            text = self.notices(app)

        self.assertIn("/rules", text)


if __name__ == "__main__":
    unittest.main()
