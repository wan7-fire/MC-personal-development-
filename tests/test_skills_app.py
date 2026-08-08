import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Static

from zxcode.app import ZXCodeApp
from zxcode.client import Settings


class FakeClient:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        yield "ok"


class SkillAppTests(unittest.IsolatedAsyncioTestCase):
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

    def test_app_registers_load_skill_and_builtin_commands(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        self.assertIsNotNone(app.registry.get("LoadSkill"))
        self.assertIsNotNone(app.command_registry.get("skills"))
        self.assertIsNotNone(app.command_registry.get("commit"))
        self.assertIsNotNone(app.command_registry.get("review"))
        self.assertIsNotNone(app.command_registry.get("test"))
        names = [meta.name for meta in app.skill_manager.list_skills()]
        for expected in ("commit", "review", "test"):
            self.assertIn(expected, names)

    def test_app_registers_install_skill_tool(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        self.assertIsNotNone(app.registry.get("InstallSkill"))

    async def test_clear_resets_active_skills(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )
        app.skill_manager.activate("commit")

        async with app.run_test() as pilot:
            app.handle_command("/clear")
            await pilot.pause()

        self.assertEqual(app.skill_manager.active_skill_messages(), [])

    async def test_help_includes_skill_commands(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            app.handle_command("/help")
            await pilot.pause()
            text = "\n".join(
                widget.render().plain for widget in app.query(".notice")
            )

        self.assertIn("/skills", text)
        self.assertIn("/commit", text)
        self.assertIn("/review", text)
        self.assertIn("/test", text)

    async def test_skills_command_lists_builtins(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            app.handle_command("/skills")
            await pilot.pause()
            text = "\n".join(
                widget.render().plain for widget in app.query(".notice")
            )

        self.assertIn("commit", text)
        self.assertIn("review", text)
        self.assertIn("test", text)

    async def test_skills_install_command_registers_shortcut(self):
        from unittest.mock import patch

        from zxcode.commands import skills as skills_commands

        def fake_install(project_root, url):
            dest = Path(project_root) / ".zxcode" / "skills" / "demo"
            dest.mkdir(parents=True)
            (dest / "skill.md").write_text(
                "---\nname: demo\ndescription: installed\nmode: shared\n---\nbody",
                encoding="utf-8",
            )
            return dest

        with tempfile.TemporaryDirectory() as directory:
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"),
                FakeClient(),
            )
            app.skill_manager.root = Path(directory)
            with patch.object(skills_commands, "install_skill", fake_install):
                async with app.run_test() as pilot:
                    app.handle_command(
                        "/skills install https://www.skills.sh/acme/skills/demo"
                    )
                    await pilot.pause(0.2)
                    text = "\n".join(
                        widget.render().plain for widget in app.query(".notice")
                    )

        self.assertIsNotNone(app.skill_manager.get("demo"))
        self.assertIsNotNone(app.command_registry.get("demo"))
        self.assertIn("demo", text)

    async def test_skills_install_without_url_shows_usage(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            app.handle_command("/skills install")
            await pilot.pause()
            text = "\n".join(
                widget.render().plain for widget in app.query(".notice")
            )

        self.assertIn("install <", text)

    async def test_commit_shortcut_activates_and_runs_shared_skill(self):
        client = FakeClient()
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), client
        )

        async with app.run_test() as pilot:
            app.handle_command("/commit")
            await pilot.pause()
            await app.active_worker.wait()
            await pilot.pause()

        self.assertEqual(len(app.skill_manager.active_skill_messages()), 1)
        self.assertIn(
            "[Skill 指令：commit]", client.calls[0][0][1]["content"]
        )
        self.assertEqual(app.session.turns, 1)


if __name__ == "__main__":
    unittest.main()
