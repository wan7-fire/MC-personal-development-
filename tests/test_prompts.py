import os
import tempfile
import unittest
from pathlib import Path

from zxcode.prompts import (
    DEFAULT_STABLE_MODULES,
    PromptModule,
    build_environment_message,
    build_stable_prompt,
    load_project_modules,
    plan_only_message,
)


class PromptTests(unittest.TestCase):
    def test_default_modules_cover_the_stable_sections(self):
        self.assertEqual(
            {module.name for module in DEFAULT_STABLE_MODULES},
            {"identity", "behavior", "coding", "safety", "tools", "output"},
        )

    def test_stable_prompt_order_is_deterministic(self):
        left = [
            PromptModule("b", 20, "B"),
            PromptModule("a", 20, "A"),
            PromptModule("first", 10, "FIRST"),
        ]
        right = list(reversed(left))

        self.assertEqual(build_stable_prompt(left), build_stable_prompt(right))
        self.assertLess(
            build_stable_prompt(left).index("FIRST"),
            build_stable_prompt(left).index("A"),
        )
        self.assertLess(
            build_stable_prompt(left).index("A"),
            build_stable_prompt(left).index("B"),
        )

    def test_empty_modules_are_skipped(self):
        prompt = build_stable_prompt(
            [PromptModule("empty", 1, "  "), PromptModule("kept", 2, "Kept")]
        )

        self.assertIn("Kept", prompt)
        self.assertNotIn("empty", prompt)

    def test_project_markdown_modules_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prompt_dir = root / "prompts"
            prompt_dir.mkdir()
            (prompt_dir / "025-extra.md").write_text("Extra rules", encoding="utf-8")
            (prompt_dir / "030-empty.md").write_text(" ", encoding="utf-8")

            modules = load_project_modules(root)

        self.assertEqual([module.name for module in modules], ["extra"])
        self.assertEqual(modules[0].priority, 25)
        self.assertEqual(modules[0].content, "Extra rules")

    def test_environment_message_is_dynamic_and_does_not_leak_secrets(self):
        environ = {
            "LLM_API_KEY": "secret-value",
            "OTHER_TOKEN": "token-value",
            "NORMAL": "visible",
        }
        message = build_environment_message(
            Path("C:/work"),
            now="2026-07-31T12:00:00+08:00",
            environ=environ,
            git_summary="branch main, clean",
        )

        self.assertEqual(message["role"], "system")
        self.assertIn("C:/work", message["content"])
        self.assertIn("2026-07-31T12:00:00+08:00", message["content"])
        self.assertIn("branch main, clean", message["content"])
        self.assertNotIn("secret-value", message["content"])
        self.assertNotIn("token-value", message["content"])

    def test_stable_prompt_ignores_environment_changes(self):
        first = build_stable_prompt()
        build_environment_message(Path.cwd(), now="2026-07-31T12:00:00+08:00")
        second = build_stable_prompt()

        self.assertEqual(first, second)

    def test_plan_only_message_is_separate(self):
        message = plan_only_message()

        self.assertEqual(message["role"], "system")
        self.assertIn("plan-only", message["content"])

    def test_tool_prompt_names_the_builtin_tools_and_preferences(self):
        prompt = build_stable_prompt()

        for name in ("ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"):
            self.assertIn(name, prompt)
        self.assertIn("Prefer dedicated tools", prompt)
        self.assertIn("ReadFile for files", prompt)
        self.assertIn("Glob or Grep for search", prompt)
        self.assertIn("Read the relevant code before editing", prompt)

    def test_readme_documents_prompt_layering(self):
        readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

        self.assertIn("提示词分层", readme)


if __name__ == "__main__":
    unittest.main()
