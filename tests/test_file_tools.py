import hashlib
import tempfile
import unittest
from pathlib import Path

from zxcode.tools import EditFile, ReadFile, ToolContext, WriteFile


class FileToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_read_file_returns_numbered_range_and_hash(self):
        path = self.root / "sample.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = await ReadFile().execute(
            {"path": "sample.txt", "start_line": 2, "end_line": 3},
            ToolContext(self.root),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "2: two\n3: three")
        self.assertEqual(
            result.metadata["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
        )

    async def test_read_file_clamps_end_beyond_eof(self):
        path = self.root / "sample.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = await ReadFile().execute(
            {"path": "sample.txt", "start_line": 2, "end_line": 999},
            ToolContext(self.root),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "2: two\n3: three")
        self.assertEqual(result.metadata["total_lines"], 3)
        self.assertTrue(result.metadata["clamped"])

    async def test_read_file_start_beyond_eof_is_success_with_metadata(self):
        path = self.root / "sample.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = await ReadFile().execute(
            {"path": "sample.txt", "start_line": 10, "end_line": 10},
            ToolContext(self.root),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "")
        self.assertEqual(result.metadata["total_lines"], 3)
        self.assertTrue(result.metadata["clamped"])

    async def test_read_file_whole_file_when_bounds_omitted(self):
        path = self.root / "sample.txt"
        path.write_text("one\ntwo\nthree\n", encoding="utf-8")

        result = await ReadFile().execute(
            {"path": "sample.txt", "start_line": None, "end_line": None},
            ToolContext(self.root),
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "1: one\n2: two\n3: three")
        self.assertEqual(result.metadata["total_lines"], 3)
        self.assertNotIn("clamped", result.metadata)

    async def test_read_file_rejects_path_escape_and_invalid_utf8(self):
        outside = self.root.parent / "outside-zxcode-test.txt"
        outside.write_text("secret", encoding="utf-8")
        invalid = self.root / "invalid.txt"
        invalid.write_bytes(b"\xff")
        try:
            escaped = await ReadFile().execute(
                {"path": "../outside-zxcode-test.txt", "start_line": None, "end_line": None},
                ToolContext(self.root),
            )
            undecodable = await ReadFile().execute(
                {"path": "invalid.txt", "start_line": None, "end_line": None},
                ToolContext(self.root),
            )
        finally:
            outside.unlink(missing_ok=True)

        self.assertEqual(escaped.error["code"], "path_outside_root")
        self.assertEqual(undecodable.error["code"], "invalid_utf8")

    async def test_read_file_rejects_more_than_one_mibibyte(self):
        (self.root / "large.txt").write_bytes(b"x" * 1_048_577)

        result = await ReadFile().execute(
            {"path": "large.txt", "start_line": None, "end_line": None},
            ToolContext(self.root),
        )

        self.assertEqual(result.error["code"], "file_too_large")

    async def test_write_create_is_automatic_but_overwrite_uses_hash_and_confirmation(self):
        confirmations = []

        async def confirm(title, detail):
            confirmations.append((title, detail))
            return True

        context = ToolContext(self.root, confirm)
        tool = WriteFile()
        created = await tool.execute(
            {"path": "new.txt", "content": "first", "expected_sha256": None}, context
        )
        path = self.root / "new.txt"
        wrong = await tool.execute(
            {"path": "new.txt", "content": "second", "expected_sha256": "bad"}, context
        )
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        overwritten = await tool.execute(
            {"path": "new.txt", "content": "second", "expected_sha256": current_hash},
            context,
        )

        self.assertTrue(created.success)
        self.assertEqual(wrong.error["code"], "conflict")
        self.assertTrue(overwritten.success)
        self.assertEqual(path.read_text(encoding="utf-8"), "second")
        self.assertEqual(len(confirmations), 1)

    async def test_edit_file_applies_all_original_non_overlapping_matches_atomically(self):
        path = self.root / "edit.txt"
        path.write_text("A B", encoding="utf-8")
        before = hashlib.sha256(path.read_bytes()).hexdigest()

        async def approve(_title, _detail):
            return True

        result = await EditFile().execute(
            {
                "path": "edit.txt",
                "expected_sha256": before,
                "edits": [
                    {"old_text": "A", "new_text": "B"},
                    {"old_text": "B", "new_text": "C"},
                ],
            },
            ToolContext(self.root, approve),
        )

        self.assertTrue(result.success)
        self.assertEqual(path.read_text(encoding="utf-8"), "B C")

    async def test_edit_file_failure_leaves_original_unchanged(self):
        path = self.root / "edit.txt"
        path.write_text("same same", encoding="utf-8")
        before = path.read_bytes()

        result = await EditFile().execute(
            {
                "path": "edit.txt",
                "expected_sha256": hashlib.sha256(before).hexdigest(),
                "edits": [{"old_text": "same", "new_text": "changed"}],
            },
            ToolContext(self.root),
        )

        self.assertEqual(result.error["code"], "edit_match_error")
        self.assertEqual(path.read_bytes(), before)

    async def test_edit_file_detects_overlapping_duplicate_matches(self):
        path = self.root / "overlap.txt"
        path.write_text("aaa", encoding="utf-8")
        before = path.read_bytes()

        result = await EditFile().execute(
            {
                "path": "overlap.txt",
                "expected_sha256": hashlib.sha256(before).hexdigest(),
                "edits": [{"old_text": "aa", "new_text": "x"}],
            },
            ToolContext(self.root),
        )

        self.assertEqual(result.error["code"], "edit_match_error")
        self.assertEqual(path.read_bytes(), before)

    async def test_write_rechecks_hash_after_confirmation(self):
        path = self.root / "race.txt"
        path.write_text("before", encoding="utf-8")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()

        async def modify_then_approve(_title, _detail):
            path.write_text("external", encoding="utf-8")
            return True

        result = await WriteFile().execute(
            {
                "path": "race.txt",
                "content": "zxcode",
                "expected_sha256": expected,
            },
            ToolContext(self.root, modify_then_approve),
        )

        self.assertEqual(result.error["code"], "conflict")
        self.assertEqual(path.read_text(encoding="utf-8"), "external")

    async def test_edit_rechecks_hash_after_confirmation(self):
        path = self.root / "race-edit.txt"
        path.write_text("before", encoding="utf-8")
        expected = hashlib.sha256(path.read_bytes()).hexdigest()

        async def modify_then_approve(_title, _detail):
            path.write_text("external", encoding="utf-8")
            return True

        result = await EditFile().execute(
            {
                "path": "race-edit.txt",
                "expected_sha256": expected,
                "edits": [{"old_text": "before", "new_text": "zxcode"}],
            },
            ToolContext(self.root, modify_then_approve),
        )

        self.assertEqual(result.error["code"], "conflict")
        self.assertEqual(path.read_text(encoding="utf-8"), "external")


if __name__ == "__main__":
    unittest.main()
