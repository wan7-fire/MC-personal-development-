import tempfile
import unittest
from pathlib import Path

from zxcode.rules.loader import RuleLoadError, load_rules
from zxcode.rules.matcher import match_group
from zxcode.rules.model import (
    ACTION_TYPES,
    EVENTS,
    OPERATORS,
    REJECT_EVENTS,
    Action,
    Condition,
    ConditionGroup,
    Rule,
)


def write_rules(root: Path, text: str, name: str = "rules.yaml") -> Path:
    target = root / ".zxcode" / "rules"
    target.mkdir(parents=True)
    path = target / name
    path.write_text(text, encoding="utf-8")
    return path


VALID = """
rules:
  - id: hello
    event: session_start
    once: true
    actions:
      - type: prompt
        prompt: "你好 {{event}}"
  - id: block-tmp
    event: pre_tool_use
    when:
      all:
        - field: tool
          op: exact
          value: WriteFile
        - field: args.path
          op: glob
          value: "*.tmp"
    reject: 禁止写临时文件
"""


class ModelTests(unittest.TestCase):
    def test_event_catalog_covers_all_layers(self):
        for expected in (
            "session_start",
            "session_end",
            "turn_start",
            "turn_end",
            "pre_message",
            "post_message",
            "pre_tool_use",
            "post_tool_use",
            "system_startup",
            "system_exit",
            "system_error",
            "system_compact",
        ):
            self.assertIn(expected, EVENTS)

    def test_only_pre_tool_use_allows_reject(self):
        self.assertEqual(REJECT_EVENTS, {"pre_tool_use"})

    def test_action_types_and_operators(self):
        self.assertEqual(ACTION_TYPES, ("command", "prompt", "http", "agent"))
        self.assertEqual(OPERATORS, ("exact", "ne", "regex", "glob"))


class LoaderTests(unittest.TestCase):
    def test_loads_valid_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            write_rules(Path(directory), VALID)

            rules = load_rules(Path(directory))

        self.assertEqual([rule.id for rule in rules], ["hello", "block-tmp"])
        hello, block = rules
        self.assertEqual(hello.event, "session_start")
        self.assertTrue(hello.once)
        self.assertEqual(hello.actions[0].type, "prompt")
        self.assertEqual(hello.actions[0].payload["prompt"], "你好 {{event}}")
        self.assertEqual(block.reject, "禁止写临时文件")
        self.assertEqual(block.conditions.combinator, "all")
        self.assertEqual(
            [condition.field for condition in block.conditions.conditions],
            ["tool", "args.path"],
        )

    def test_unconditional_rule_without_when(self):
        text = """
rules:
  - id: always
    event: turn_start
    actions:
      - type: prompt
        prompt: hi
"""
        with tempfile.TemporaryDirectory() as directory:
            rules = load_rules(Path(directory)) if write_rules(
                Path(directory), text
            ) else []

        self.assertEqual(rules[0].id, "always")
        self.assertIsNone(rules[0].conditions)

    def _assert_invalid(self, text: str, fragment: str):
        with tempfile.TemporaryDirectory() as directory:
            write_rules(Path(directory), text)
            with self.assertRaises(RuleLoadError) as caught:
                load_rules(Path(directory))
        self.assertIn(fragment, str(caught.exception))
        self.assertIn("rules.yaml", str(caught.exception))

    def test_unknown_event_fails_with_rule_id(self):
        self._assert_invalid(
            """
rules:
  - id: bad-event
    event: nope
    actions:
      - type: prompt
        prompt: hi
""",
            "bad-event",
        )

    def test_unknown_action_type_fails(self):
        self._assert_invalid(
            """
rules:
  - id: bad-action
    event: turn_start
    actions:
      - type: explode
        command: hi
""",
            "bad-action",
        )

    def test_reject_on_non_pre_tool_use_fails(self):
        self._assert_invalid(
            """
rules:
  - id: bad-reject
    event: session_start
    reject: no
    actions:
      - type: prompt
        prompt: hi
""",
            "bad-reject",
        )

    def test_async_on_pre_tool_use_fails(self):
        self._assert_invalid(
            """
rules:
  - id: bad-async
    event: pre_tool_use
    async: true
    actions:
      - type: prompt
        prompt: hi
""",
            "bad-async",
        )

    def test_command_action_requires_command_field(self):
        self._assert_invalid(
            """
rules:
  - id: no-command
    event: turn_start
    actions:
      - type: command
        prompt: hi
""",
            "no-command",
        )

    def test_http_action_requires_url_field(self):
        self._assert_invalid(
            """
rules:
  - id: no-url
    event: turn_start
    actions:
      - type: http
        method: GET
""",
            "no-url",
        )

    def test_prompt_action_requires_prompt_field(self):
        self._assert_invalid(
            """
rules:
  - id: no-prompt
    event: turn_start
    actions:
      - type: prompt
        command: hi
""",
            "no-prompt",
        )

    def test_agent_action_requires_name_field(self):
        self._assert_invalid(
            """
rules:
  - id: no-name
    event: turn_start
    actions:
      - type: agent
        prompt: hi
""",
            "no-name",
        )

    def test_missing_event_and_actions_fail(self):
        self._assert_invalid(
            """
rules:
  - id: empty
    once: true
""",
            "empty",
        )

    def test_duplicate_rule_ids_fail(self):
        self._assert_invalid(
            """
rules:
  - id: dup
    event: turn_start
    actions:
      - type: prompt
        prompt: hi
  - id: dup
    event: turn_end
    actions:
      - type: prompt
        prompt: bye
""",
            "dup",
        )

    def test_when_with_both_all_and_any_fails(self):
        self._assert_invalid(
            """
rules:
  - id: mixed
    event: turn_start
    when:
      all:
        - field: tool
          op: exact
          value: ReadFile
      any:
        - field: tool
          op: exact
          value: Grep
    actions:
      - type: prompt
        prompt: hi
""",
            "mixed",
        )

    def test_condition_with_unknown_operator_fails(self):
        self._assert_invalid(
            """
rules:
  - id: bad-op
    event: turn_start
    when:
      all:
        - field: tool
          op: fuzzy
          value: ReadFile
    actions:
      - type: prompt
        prompt: hi
""",
            "bad-op",
        )

    def test_unparsable_yaml_fails(self):
        self._assert_invalid("rules: [unclosed", "rules.yaml")


class MatcherTests(unittest.TestCase):
    def _group(self, combinator, conditions):
        return ConditionGroup(
            combinator, tuple(Condition(f, o, v) for f, o, v in conditions)
        )

    def test_exact_and_ne(self):
        group = self._group(
            "all", [("tool", "exact", "WriteFile"), ("tool", "ne", "Grep")]
        )
        self.assertTrue(match_group(group, {"tool": "WriteFile"}))
        self.assertFalse(match_group(group, {"tool": "Grep"}))

    def test_regex_and_glob(self):
        group = self._group(
            "all",
            [
                ("message", "regex", "TODO"),
                ("args.path", "glob", "*.tmp"),
            ],
        )
        self.assertTrue(
            match_group(group, {"message": "fix TODO now", "args": {"path": "a.tmp"}})
        )
        self.assertFalse(
            match_group(group, {"message": "fix todo now", "args": {"path": "a.tmp"}})
        )

    def test_any_combinator(self):
        group = self._group(
            "any", [("tool", "exact", "ReadFile"), ("tool", "exact", "Grep")]
        )
        self.assertTrue(match_group(group, {"tool": "Grep"}))
        self.assertFalse(match_group(group, {"tool": "Bash"}))

    def test_missing_field_and_invalid_regex_do_not_match(self):
        group = self._group("all", [("args.path", "regex", "[")])
        self.assertFalse(match_group(group, {"args": {}}))
        self.assertFalse(match_group(group, {"args": {"path": "x"}}))

    def test_none_group_matches_everything(self):
        self.assertTrue(match_group(None, {}))


if __name__ == "__main__":
    unittest.main()
