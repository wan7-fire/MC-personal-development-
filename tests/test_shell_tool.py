import tempfile
import unittest
from pathlib import Path

from mewcode.tools import Bash, ToolContext


class ShellToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_read_only_command_runs_without_confirmation(self):
        confirmations = []

        async def confirm(title, detail):
            confirmations.append((title, detail))
            return False

        result = await Bash().execute(
            {"command": "Get-Location"}, ToolContext(self.root, confirm)
        )

        self.assertTrue(result.success, result)
        self.assertIn(str(self.root), result.output)
        self.assertEqual(confirmations, [])

    async def test_write_command_requires_single_confirmation(self):
        confirmations = []

        async def confirm(title, detail):
            confirmations.append((title, detail))
            return True

        result = await Bash().execute(
            {"command": "Set-Content created.txt hello"},
            ToolContext(self.root, confirm),
        )

        self.assertTrue(result.success, result)
        self.assertEqual((self.root / "created.txt").read_text().strip(), "hello")
        self.assertEqual(len(confirmations), 1)

    async def test_denied_and_background_commands_do_not_run(self):
        async def deny(_title, _detail):
            return False

        denied = await Bash().execute(
            {"command": "Set-Content denied.txt no"}, ToolContext(self.root, deny)
        )
        background = await Bash().execute(
            {"command": "Start-Job { Get-Location }"}, ToolContext(self.root, deny)
        )

        self.assertEqual(denied.error["code"], "permission_denied")
        self.assertFalse((self.root / "denied.txt").exists())
        self.assertEqual(background.error["code"], "background_process_not_allowed")

    async def test_multiline_variables_and_destructive_git_branch_require_confirmation(self):
        confirmations = []

        async def deny(title, detail):
            confirmations.append((title, detail))
            return False

        commands = (
            "Get-Location\nSet-Content escaped.txt bad",
            "Get-Content $env:USERPROFILE",
            "git branch -D main",
        )
        results = [
            await Bash().execute({"command": command}, ToolContext(self.root, deny))
            for command in commands
        ]

        self.assertEqual([result.error["code"] for result in results], [
            "permission_denied", "permission_denied", "permission_denied"
        ])
        self.assertEqual(len(confirmations), 3)
        self.assertFalse((self.root / "escaped.txt").exists())

    async def test_known_outside_absolute_path_is_rejected_without_confirmation(self):
        confirmations = []

        async def confirm(title, detail):
            confirmations.append((title, detail))
            return True

        results = [
            await Bash().execute(
                {"command": command}, ToolContext(self.root, confirm)
            )
            for command in (
                r"Get-Content C:\Windows\win.ini",
                r"Get-Content \Windows\win.ini",
                "Get-Content /Windows/win.ini",
            )
        ]

        self.assertEqual(
            [result.error["code"] for result in results],
            ["path_outside_root", "path_outside_root", "path_outside_root"],
        )
        self.assertEqual(confirmations, [])

    async def test_home_and_non_filesystem_providers_are_rejected(self):
        async def approve(_title, _detail):
            return True

        commands = (
            r"Get-Content ~\secret.txt",
            "Get-Content Env:LLM_API_KEY",
            "Get-ChildItem Registry::HKEY_LOCAL_MACHINE",
            "Get-ChildItem HKLM:\\Software",
        )

        results = [
            await Bash().execute({"command": command}, ToolContext(self.root, approve))
            for command in commands
        ]

        self.assertEqual(
            [result.error["code"] for result in results],
            ["path_outside_root"] * len(commands),
        )

    async def test_nested_expression_is_not_auto_approved(self):
        confirmations = []

        async def deny(title, detail):
            confirmations.append((title, detail))
            return False

        result = await Bash().execute(
            {"command": "Get-Content (Set-Content pwn.txt bad)"},
            ToolContext(self.root, deny),
        )

        self.assertEqual(result.error["code"], "permission_denied")
        self.assertEqual(len(confirmations), 1)
        self.assertFalse((self.root / "pwn.txt").exists())

    async def test_relative_symlink_escape_is_rejected(self):
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        try:
            try:
                (self.root / "link").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            result = await Bash().execute(
                {"command": r"Get-Content link\secret.txt"}, ToolContext(self.root)
            )
        finally:
            (outside / "secret.txt").unlink(missing_ok=True)
            outside.rmdir()

        self.assertEqual(result.error["code"], "path_outside_root")


if __name__ == "__main__":
    unittest.main()
