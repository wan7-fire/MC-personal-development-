import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from zxcode.rules.engine import EmitResult, RuleEngine
from zxcode.rules.model import Action, Condition, ConditionGroup, Rule
from zxcode.security import load_policy
from zxcode.tools import ToolContext


def prompt_rule(
    rule_id,
    event,
    prompt,
    *,
    once=False,
    async_=False,
    conditions=None,
    reject=None,
    timeout=5,
):
    return Rule(
        id=rule_id,
        event=event,
        actions=(Action("prompt", {"prompt": prompt}),),
        conditions=conditions,
        reject=reject,
        once=once,
        async_=async_,
        timeout_seconds=timeout,
    )


def command_rule(rule_id, event, command, **kwargs):
    timeout = kwargs.pop("timeout", 5)
    return Rule(
        id=rule_id,
        event=event,
        actions=(Action("command", {"command": command}),),
        timeout_seconds=timeout,
        **kwargs,
    )


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def _engine(self, rules, *, root=None, security=None):
        return RuleEngine(
            rules,
            root=root or Path.cwd(),
            confirm=None,
            security=security,
        )

    async def test_prompt_injection_is_collected(self):
        engine = self._engine(
            [prompt_rule("p1", "turn_start", "hello {{event}}")]
        )

        result = await engine.emit("turn_start", {})

        self.assertIsInstance(result, EmitResult)
        self.assertFalse(result.rejected)
        self.assertEqual(result.injected, ["hello turn_start"])

    async def test_once_rule_fires_only_once(self):
        engine = self._engine([prompt_rule("p1", "turn_start", "hi", once=True)])

        first = await engine.emit("turn_start", {})
        second = await engine.emit("turn_start", {})

        self.assertEqual(first.injected, ["hi"])
        self.assertEqual(second.injected, [])

    async def test_reset_once_allows_rule_to_fire_again(self):
        engine = self._engine([prompt_rule("p1", "turn_start", "hi", once=True)])
        await engine.emit("turn_start", {})
        engine.reset_once()

        result = await engine.emit("turn_start", {})

        self.assertEqual(result.injected, ["hi"])

    async def test_reject_returns_reason_and_skips_actions(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            rule = Rule(
                id="block",
                event="pre_tool_use",
                actions=(Action("command", {"command": "Set-Content marker.txt x"}),),
                reject="禁止 {{tool}}",
            )
            engine = self._engine([rule], root=root)

            result = await engine.emit(
                "pre_tool_use", {"tool": "WriteFile", "args": {}}
            )

            self.assertTrue(result.rejected)
            self.assertEqual(result.reason, "禁止 WriteFile")
            self.assertFalse((root / "marker.txt").exists())

    async def test_first_reject_wins(self):
        rules = [
            prompt_rule("r1", "pre_tool_use", "x", reject="first"),
            prompt_rule("r2", "pre_tool_use", "x", reject="second"),
        ]
        engine = self._engine(rules)

        result = await engine.emit("pre_tool_use", {"tool": "ReadFile", "args": {}})

        self.assertEqual(result.reason, "first")

    async def test_conditions_filter_rules(self):
        group = ConditionGroup(
            "all", (Condition("tool", "exact", "ReadFile"),)
        )
        engine = self._engine(
            [prompt_rule("p1", "pre_tool_use", "hi", conditions=group)]
        )

        match = await engine.emit("pre_tool_use", {"tool": "ReadFile", "args": {}})
        no_match = await engine.emit("pre_tool_use", {"tool": "Grep", "args": {}})

        self.assertEqual(match.injected, ["hi"])
        self.assertEqual(no_match.injected, [])

    async def test_async_action_completes_in_background(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            engine = self._engine(
                [
                    command_rule(
                        "bg",
                        "turn_start",
                        "Start-Sleep -Milliseconds 100; Set-Content marker.txt done",
                        async_=True,
                        timeout=5,
                    )
                ],
                root=root,
            )

            result = await engine.emit("turn_start", {})
            await engine.drain()

            self.assertFalse(result.rejected)
            self.assertEqual(
                (root / "marker.txt").read_text(encoding="utf-8").strip(), "done"
            )

    async def test_timeout_action_does_not_block_emit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            engine = self._engine(
                [
                    command_rule(
                        "slow", "turn_start", "Start-Sleep -Seconds 10", timeout=0.2
                    )
                ],
                root=root,
            )

            result = await engine.emit("turn_start", {})
            await asyncio.sleep(0.5)

        self.assertFalse(result.rejected)
        self.assertEqual(result.injected, [])

    async def test_action_error_is_isolated_and_logged(self):
        engine = self._engine(
            [
                command_rule(
                    "boom", "turn_start", "This-Is-Not-A-Real-Cmdlet 1", timeout=3
                )
            ]
        )

        with self.assertLogs("zxcode.rules", level=logging.ERROR) as captured:
            result = await engine.emit("turn_start", {})

        self.assertFalse(result.rejected)
        self.assertIn("boom", "\n".join(captured.output))

    async def test_event_with_no_rules_is_noop(self):
        engine = self._engine([])

        result = await engine.emit("turn_start", {})

        self.assertFalse(result.rejected)
        self.assertEqual(result.injected, [])

    async def test_engine_keeps_security_context_for_actions(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            policy = load_policy(root)
            policy.mode = "strict"
            engine = RuleEngine(
                [
                    command_rule(
                        "strict", "turn_start", "Set-Content marker.txt x", timeout=3
                    )
                ],
                root=root,
                confirm=None,
                security=policy,
            )

            await engine.emit("turn_start", {})

            self.assertFalse((root / "marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
