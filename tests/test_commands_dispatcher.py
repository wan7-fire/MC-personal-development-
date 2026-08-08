import unittest

from zxcode.commands.dispatcher import CommandContext, dispatch_command
from zxcode.commands.model import AIPrompt, CommandInvocation, CommandMeta, CommandType
from zxcode.commands.registry import CommandRegistry


class FakeUI:
    def __init__(self):
        self.notices = []
        self.sent = []

    def notice(self, text):
        self.notices.append(text)

    def send_user_message(self, text, system_parts=()):
        self.sent.append((text, tuple(system_parts)))


def _register(registry, name, handler, command_type=CommandType.LOCAL):
    registry.register(
        CommandMeta(name, "测试", "/" + name, command_type, handler)
    )


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.ui = FakeUI()
        self.registry = CommandRegistry()
        self.ctx = CommandContext(self.registry, self.ui)

    def test_unknown_command_guides_help(self):
        dispatch_command(self.ctx, CommandInvocation("nope"))
        self.assertIn("/help", self.ui.notices[0])

    def test_empty_name_guides_help(self):
        dispatch_command(self.ctx, CommandInvocation(""))
        self.assertIn("/help", self.ui.notices[0])

    def test_handler_receives_args_and_runs(self):
        calls = []

        def handler(ctx, invocation):
            calls.append(invocation.args)

        _register(self.registry, "ping", handler)
        dispatch_command(self.ctx, CommandInvocation("PING", "x"))
        self.assertEqual(calls, ["x"])

    def test_handler_exception_is_noticed_not_raised(self):
        def handler(ctx, invocation):
            raise RuntimeError("boom")

        _register(self.registry, "boom", handler)
        dispatch_command(self.ctx, CommandInvocation("boom"))
        self.assertTrue(any("执行失败" in text for text in self.ui.notices))

    def test_ai_command_sends_a_plus_b(self):
        def handler(ctx, invocation):
            return AIPrompt("触发消息", ["预设一", "预设二"])

        _register(self.registry, "ai", handler, CommandType.AI_FLOW)
        dispatch_command(self.ctx, CommandInvocation("ai"))
        self.assertEqual(self.ui.sent, [("触发消息", ("预设一", "预设二"))])

    def test_ai_placeholder_without_prompt_sends_nothing(self):
        def handler(ctx, invocation):
            return None

        _register(self.registry, "review", handler, CommandType.AI_FLOW)
        dispatch_command(self.ctx, CommandInvocation("review"))
        self.assertEqual(self.ui.sent, [])


if __name__ == "__main__":
    unittest.main()
