import unittest

from zxcode.commands.parser import parse


class ParserTests(unittest.TestCase):
    def test_non_command_input_returns_none(self):
        self.assertIsNone(parse("hello"))
        self.assertIsNone(parse("  hello world "))
        self.assertIsNone(parse(""))

    def test_name_is_lowercased_and_args_kept(self):
        invocation = parse("/HELP x y")
        self.assertEqual(invocation.name, "help")
        self.assertEqual(invocation.args, "x y")

    def test_command_without_args(self):
        invocation = parse("/plan")
        self.assertEqual(invocation.name, "plan")
        self.assertEqual(invocation.args, "")

    def test_args_preserve_case(self):
        invocation = parse("/model GPT-5")
        self.assertEqual(invocation.name, "model")
        self.assertEqual(invocation.args, "GPT-5")

    def test_bare_slash_is_unknown(self):
        self.assertEqual(parse("/").name, "")
        self.assertEqual(parse("/  ").name, "")
        self.assertEqual(parse("/  ").args, "")


if __name__ == "__main__":
    unittest.main()
