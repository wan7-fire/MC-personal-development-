import unittest

from zxcode.commands.model import CommandMeta, CommandType
from zxcode.commands.registry import CommandRegistrationError, CommandRegistry


def _handler(ctx, invocation):
    return "ok"


def _meta(name, aliases=(), hidden=False, command_type=CommandType.LOCAL):
    return CommandMeta(
        name,
        "测试命令",
        "/" + name,
        command_type,
        _handler,
        aliases=aliases,
        hidden=hidden,
    )


class RegistryTests(unittest.TestCase):
    def test_register_and_get_case_insensitive(self):
        registry = CommandRegistry()
        registry.register(_meta("help", aliases=("h",)))
        self.assertIs(registry.get("help"), registry.get("HELP"))
        self.assertIs(registry.get("H"), registry.get("help"))
        self.assertIsNone(registry.get("nope"))

    def test_alias_conflict_raises(self):
        registry = CommandRegistry()
        registry.register(_meta("help"))
        with self.assertRaises(CommandRegistrationError):
            registry.register(_meta("help"))
        with self.assertRaises(CommandRegistrationError):
            registry.register(_meta("other", aliases=("help",)))
        with self.assertRaises(CommandRegistrationError):
            registry.register(_meta("other", aliases=("HELP",)))

    def test_visible_commands_exclude_hidden(self):
        registry = CommandRegistry()
        registry.register(_meta("status"))
        registry.register(_meta("debug", hidden=True))
        self.assertEqual(
            [meta.name for meta in registry.visible_commands()], ["status"]
        )
        self.assertIsNotNone(registry.get("debug"))

    def test_complete_matches_name_and_alias(self):
        registry = CommandRegistry()
        registry.register(_meta("help", aliases=("h",)))
        registry.register(_meta("sessions", aliases=("s",)))
        registry.register(_meta("status"))
        self.assertEqual(
            [meta.name for meta in registry.complete("s")],
            ["sessions", "status"],
        )
        self.assertEqual(
            [meta.name for meta in registry.complete("se")], ["sessions"]
        )
        self.assertEqual(
            [meta.name for meta in registry.complete("H")], ["help"]
        )
        self.assertEqual(registry.complete(""), [])

    def test_hidden_excluded_from_completion(self):
        registry = CommandRegistry()
        registry.register(_meta("debug", hidden=True))
        self.assertEqual(registry.complete("d"), [])


if __name__ == "__main__":
    unittest.main()
