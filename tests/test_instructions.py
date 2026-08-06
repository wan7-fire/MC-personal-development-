import tempfile
import unittest
from pathlib import Path

from zxcode.instructions import load_instructions


class InstructionTests(unittest.TestCase):
    def test_missing_files_yield_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = root / "user"
            user.mkdir()
            loaded = load_instructions(root, user_dir=user)

        self.assertEqual(loaded, [])

    def test_project_and_user_loaded_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user = root / "user"
            user.mkdir()
            (root / "ZXCODE.md").write_text("project rules", encoding="utf-8")
            (user / "AGENTS.md").write_text("user rules", encoding="utf-8")
            loaded = load_instructions(root, user_dir=user)

        self.assertEqual([item.scope for item in loaded], ["project", "user"])
        self.assertEqual(loaded[0].content, "project rules")
        self.assertEqual(loaded[1].content, "user rules")
        self.assertEqual(
            loaded[0].to_message()["role"], "system"
        )
        self.assertIn("项目指令", loaded[0].to_message()["content"])
        self.assertIn("project rules", loaded[0].to_message()["content"])

    def test_unreadable_utf8_is_skipped_without_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ZXCODE.md").write_bytes(b"\xff\xfe\x00broken")
            loaded = load_instructions(root, user_dir=root / "nouser")

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(any("无法读取" in i.message for i in loaded[0].issues))

    def test_include_expands_relative_to_including_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sub = root / "sub"
            sub.mkdir()
            (root / "ZXCODE.md").write_text(
                "top\n@include sub/guide.md\ntail", encoding="utf-8"
            )
            (sub / "guide.md").write_text("GUIDE", encoding="utf-8")
            loaded = load_instructions(root, user_dir=root / "nouser")

        content = loaded[0].content
        self.assertIn("top", content)
        self.assertIn("GUIDE", content)
        self.assertIn("tail", content)
        self.assertNotIn("@include", content)
        self.assertEqual(loaded[0].issues, [])

    def test_nested_include_depth_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(1, 6):
                (root / f"f{index}.md").write_text(
                    f"L{index}\n@include f{index + 1}.md", encoding="utf-8"
                )
            (root / "ZXCODE.md").write_text("@include f1.md", encoding="utf-8")
            loaded = load_instructions(
                root, user_dir=root / "nouser", max_depth=3
            )
            content = loaded[0].content

        self.assertIn("L1", content)
        self.assertIn("L2", content)
        self.assertNotIn("L3", content)
        self.assertTrue(
            any("深度上限" in issue.message for issue in loaded[0].issues)
        )

    def test_cycle_is_detected_and_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.md").write_text("A\n@include b.md", encoding="utf-8")
            (root / "b.md").write_text("B\n@include a.md", encoding="utf-8")
            (root / "ZXCODE.md").write_text("@include a.md", encoding="utf-8")
            loaded = load_instructions(root, user_dir=root / "nouser")

        self.assertIn("A", loaded[0].content)
        self.assertIn("B", loaded[0].content)
        self.assertTrue(
            any("循环" in issue.message for issue in loaded[0].issues)
        )

    def test_parent_traversal_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            outside = Path(temp) / "outside.md"
            outside.write_text("OUTSIDE", encoding="utf-8")
            (root / "ZXCODE.md").write_text(
                "@include ../outside.md", encoding="utf-8"
            )
            loaded = load_instructions(root, user_dir=Path(temp) / "nouser")

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(
            any("超出" in issue.message for issue in loaded[0].issues)
        )

    def test_unresolvable_include_is_reported_not_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # Embedded NUL makes path resolution raise ValueError; it must be
            # recorded as an issue instead of crashing the whole load.
            (root / "ZXCODE.md").write_text(
                "@include a\x00b.md", encoding="utf-8"
            )
            loaded = load_instructions(root, user_dir=root / "nouser")

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(
            any("无法解析" in issue.message for issue in loaded[0].issues)
        )

    def test_absolute_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "secret.md"
            outside.write_text("SECRET", encoding="utf-8")
            (root / "ZXCODE.md").write_text(
                f"@include {outside}", encoding="utf-8"
            )
            loaded = load_instructions(root, user_dir=root / "nouser")

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(
            any("绝对路径" in issue.message for issue in loaded[0].issues)
        )

    def test_symlink_escape_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            outside = Path(temp) / "secret.md"
            outside.write_text("SECRET", encoding="utf-8")
            link = root / "link.md"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported on this platform")
            (root / "ZXCODE.md").write_text("@include link.md", encoding="utf-8")
            loaded = load_instructions(root, user_dir=Path(temp) / "nouser")

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(
            any("超出" in issue.message for issue in loaded[0].issues)
        )

    def test_project_cannot_include_user_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            (user / "notes.md").write_text("USER-SECRET", encoding="utf-8")
            (root / "ZXCODE.md").write_text(
                "@include ../user/notes.md", encoding="utf-8"
            )
            loaded = load_instructions(root, user_dir=user)

        self.assertEqual(loaded[0].content, "")
        self.assertTrue(
            any("超出" in issue.message for issue in loaded[0].issues)
        )

    def test_content_over_limit_is_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ZXCODE.md").write_text("x" * 100, encoding="utf-8")
            loaded = load_instructions(
                root, user_dir=root / "nouser", max_content_chars=10
            )

        self.assertEqual(len(loaded[0].content), 10)
        self.assertTrue(loaded[0].truncated)
        self.assertTrue(any("截断" in i.message for i in loaded[0].issues))


if __name__ == "__main__":
    unittest.main()
