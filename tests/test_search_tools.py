import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from mewcode.tools import Glob, Grep, ToolContext


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_glob_ignores_directories_and_sorts_newest_first(self):
        old = self.root / "old.py"
        new = self.root / "new.py"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        for ignored in (".git", "node_modules", "vendor", ".idea", ".venv", "__pycache__"):
            directory = self.root / ignored
            directory.mkdir()
            (directory / "hidden.py").write_text("hidden", encoding="utf-8")

        result = await Glob().execute(
            {"root": ".", "pattern": "**/*.py"}, ToolContext(self.root)
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output.splitlines(), ["new.py", "old.py"])
        self.assertEqual(result.metadata["category"], "search")

    async def test_glob_limits_results_to_two_hundred(self):
        await asyncio.to_thread(self._create_files, 201)

        result = await Glob().execute(
            {"root": ".", "pattern": "*.txt"}, ToolContext(self.root)
        )

        self.assertEqual(len(result.output.splitlines()), 200)
        self.assertTrue(result.metadata["truncated"])

    def _create_files(self, count):
        for number in range(count):
            (self.root / f"{number:03}.txt").write_text("x", encoding="utf-8")

    async def test_grep_returns_path_line_text_and_skips_ignored_directories(self):
        (self.root / "code.py").write_text(
            "class First:\n    pass\nclass Second:\n", encoding="utf-8"
        )
        ignored = self.root / ".git"
        ignored.mkdir()
        (ignored / "hidden.py").write_text("class Hidden:\n", encoding="utf-8")

        result = await Grep().execute(
            {"root": ".", "pattern": r"class\s+\w+"}, ToolContext(self.root)
        )

        self.assertTrue(result.success)
        self.assertEqual(
            result.output.splitlines(),
            ["code.py:1:class First:", "code.py:3:class Second:"],
        )

    async def test_grep_rejects_invalid_regex(self):
        result = await Grep().execute(
            {"root": ".", "pattern": "["}, ToolContext(self.root)
        )

        self.assertEqual(result.error["code"], "invalid_arguments")

    async def test_grep_skips_large_and_invalid_utf8_files(self):
        (self.root / "large.txt").write_bytes(b"match" + b"x" * 1_048_572)
        (self.root / "binary.txt").write_bytes(b"match\xff")

        result = await Grep().execute(
            {"root": ".", "pattern": "match"}, ToolContext(self.root)
        )

        self.assertEqual(result.output, "")
        self.assertEqual(result.metadata["skipped_large"], 1)
        self.assertEqual(result.metadata["skipped_invalid_utf8"], 1)


if __name__ == "__main__":
    unittest.main()
