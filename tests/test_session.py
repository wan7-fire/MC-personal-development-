import unittest

from mewcode.session import SYSTEM_PROMPT, ChatSession


class SessionTests(unittest.TestCase):
    def test_pending_request_does_not_change_history(self):
        session = ChatSession("model-a")

        request = session.request_messages("hello")

        self.assertEqual(request[0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertEqual(request[-1], {"role": "user", "content": "hello"})
        self.assertEqual(session.messages, [])
        self.assertEqual(session.turns, 0)

    def test_commit_adds_complete_turn_and_clear_removes_it(self):
        session = ChatSession("model-a")

        session.commit("hello", "hi")

        self.assertEqual(session.turns, 1)
        self.assertEqual(
            session.messages,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )
        session.clear()
        self.assertEqual(session.messages, [])
        self.assertEqual(session.request_messages("again")[0]["content"], SYSTEM_PROMPT)

    def test_model_switch_keeps_history(self):
        session = ChatSession("model-a")
        session.commit("hello", "hi")

        session.set_model("model-b")

        self.assertEqual(session.model, "model-b")
        self.assertEqual(session.turns, 1)

    def test_commit_messages_preserves_tool_history_as_one_user_turn(self):
        session = ChatSession("model-a")
        assistant_messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function"}],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]

        session.commit_messages("use a tool", assistant_messages)

        self.assertEqual(session.turns, 1)
        self.assertEqual(session.messages[0]["role"], "user")
        self.assertEqual(session.messages[1:], assistant_messages)
        request = session.request_messages("again")
        self.assertEqual(request[2:5], assistant_messages)


if __name__ == "__main__":
    unittest.main()
