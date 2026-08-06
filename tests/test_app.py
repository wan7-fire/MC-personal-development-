import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from time import monotonic
from unittest.mock import patch

from textual.widgets import Static, TextArea

from zxcode.agent import AgentComplete
from zxcode.app import ConfirmScreen, ZXCodeApp
from zxcode.client import AssistantMessage, Settings, TextDelta
from zxcode.compress import (
    BEGIN_SUMMARY,
    BOUNDARY_MESSAGE,
    CompressionConfig,
    CompressionManager,
    END_SUMMARY,
)
from zxcode.events import Event, EventType


class FakeClient:
    def __init__(self, parts=("你", "好", "！")):
        self.parts = parts
        self.calls = []

    async def stream(self, messages, model=None):
        self.calls.append((messages, model))
        for part in self.parts:
            yield part


class FailingClient:
    async def stream(self, messages, model=None):
        if False:
            yield ""
        raise RuntimeError("provider detail")


class BlockingClient:
    async def stream(self, messages, model=None):
        import asyncio

        yield "partial"
        await asyncio.Event().wait()


class FakeAgent:
    async def run(self, messages, model, channel):
        await channel.emit(Event(type=EventType.TEXT, data={"content": "完成"}))
        await channel.emit(
            Event(
                type=EventType.FINAL_REPLY,
                data={"content": "完成", "blocked_calls": []},
            )
        )
        channel.close()
        return AgentComplete(
            "完成",
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call-1", "type": "function"}],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "result"},
                {"role": "assistant", "content": "完成"},
            ],
        )


class ToolCallingClient:
    def __init__(self, relative_path, expected_sha256):
        self.relative_path = relative_path
        self.expected_sha256 = expected_sha256
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, tools))
        if len(self.requests) == 1:
            yield AssistantMessage(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "write-1",
                            "type": "function",
                            "function": {
                                "name": "WriteFile",
                                "arguments": json.dumps(
                                    {
                                        "path": self.relative_path,
                                        "content": "new",
                                        "expected_sha256": self.expected_sha256,
                                    }
                                ),
                            },
                        }
                    ],
                }
            )
        else:
            yield TextDelta("完成")
            yield AssistantMessage({"role": "assistant", "content": "完成"})


def assertNoDanglingCalls(case, messages):
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    for message in messages:
        for call in message.get("tool_calls") or []:
            case.assertIn(call["id"], answered)


class TrackingApp(ZXCodeApp):
    def __init__(self, *args, **kwargs):
        self.states = []
        super().__init__(*args, **kwargs)

    def set_status(self, state):
        self.states.append(state)
        super().set_status(state)


class AppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._sessions_tmp = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(
            os.environ,
            {
                "ZXCODE_SESSIONS_DIR": str(
                    Path(self._sessions_tmp.name) / "sessions"
                )
            },
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.addCleanup(self._sessions_tmp.cleanup)

    def test_default_registry_exposes_six_builtin_tools(self):
        app = ZXCodeApp(Settings("secret", "https://example.test/v1", "model-a"), FakeClient())

        self.assertEqual(
            {definition["function"]["name"] for definition in app.registry.definitions()},
            {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"},
        )

    async def test_generate_rebuilds_session_from_final_history(self):
        class HistoryAgent:
            async def run(self, messages, model, channel):
                await channel.emit(
                    Event(type=EventType.TEXT, data={"content": "ok"})
                )
                await channel.emit(
                    Event(
                        type=EventType.FINAL_REPLY,
                        data={"content": "ok", "blocked_calls": []},
                    )
                )
                channel.close()
                return AgentComplete(
                    "ok",
                    [{"role": "assistant", "content": "ok"}],
                    final_history=[
                        {"role": "system", "content": "stable"},
                        {"role": "system", "content": "env"},
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "ok"},
                    ],
                )

        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            FakeClient(),
            agent=HistoryAgent(),
        )

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("hello")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            self.assertEqual(
                app.session.messages,
                [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "ok"},
                ],
            )
            self.assertNotIn(
                "system", [message["role"] for message in app.session.messages]
            )

    async def test_help_includes_compact(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            app.handle_command("/help")
            await pilot.pause()
            help_text = app.query(".notice").last(Static).render().plain
            self.assertIn("/compact", help_text)

    async def test_compact_empty_session_notices_no_content(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            worker = app.compact()
            await worker.wait()
            await pilot.pause()
            notice = app.query(".notice").last(Static).render().plain
            self.assertIn("没有可压缩的内容", notice)

    async def test_compact_replaces_history_and_notices(self):
        class SummaryClient:
            def __init__(self):
                self.requests = []

            async def stream_events(self, messages, model=None, tools=None):
                self.requests.append((list(messages), model, tools))
                yield TextDelta(
                    f"{BEGIN_SUMMARY}\n## 主要请求\n汇总\n{END_SUMMARY}"
                )
                yield AssistantMessage(
                    {"role": "assistant", "content": "x"}
                )

        with tempfile.TemporaryDirectory() as directory:
            client = SummaryClient()
            compressor = CompressionManager(
                Path(directory),
                CompressionConfig(context_window=2000),
                client=client,
            )
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"),
                FakeClient(),
                compressor=compressor,
            )
            app.session.messages = [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a" * 5000},
                {"role": "user", "content": "u2"},
            ]

            async with app.run_test() as pilot:
                worker = app.compact()
                await worker.wait()
                await pilot.pause()

                contents = [
                    message.get("content") for message in app.session.messages
                ]
                self.assertIn(BOUNDARY_MESSAGE, contents)
                notice = app.query(".notice").last(Static).render().plain
                self.assertIn("压缩完成", notice)
                self.assertEqual(client.requests[0][2], ())

    async def test_write_tool_confirmation_executes_and_returns_result_to_model(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "target.txt"
            path.write_text("old", encoding="utf-8")
            relative = path.relative_to(Path.cwd()).as_posix()
            client = ToolCallingClient(
                relative, hashlib.sha256(path.read_bytes()).hexdigest()
            )
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"), client
            )

            async with app.run_test() as pilot:
                app.query_one("#input", TextArea).load_text("overwrite")
                app.action_submit()
                await pilot.pause()
                await pilot.click("#approve")
                await app.active_worker.wait()
                await pilot.pause()

                self.assertEqual(path.read_text(encoding="utf-8"), "new")
                tool_message = client.requests[1][0][-1]
                self.assertEqual(tool_message["role"], "tool")
                self.assertEqual(tool_message["tool_call_id"], "write-1")
                self.assertTrue(json.loads(tool_message["content"])["success"])
                self.assertEqual(app.session.turns, 1)

    async def test_cancelling_write_confirmation_closes_modal(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "target.txt"
            path.write_text("old", encoding="utf-8")
            client = ToolCallingClient(
                path.relative_to(Path.cwd()).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"), client
            )

            async with app.run_test() as pilot:
                app.query_one("#input", TextArea).load_text("overwrite")
                app.action_submit()
                await pilot.pause()
                self.assertIsInstance(app.screen, ConfirmScreen)
                await pilot.press("ctrl+c")
                await pilot.pause()

                await app.active_worker.wait()
                await pilot.pause()

                self.assertNotIsInstance(app.screen, ConfirmScreen)
                self.assertEqual(path.read_text(encoding="utf-8"), "old")
                self.assertIn("已取消", app.query_one("#status", Static).render().plain)
                assertNoDanglingCalls(self, app.session.messages)

    async def test_agent_tool_messages_are_committed_as_one_turn(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"),
            FakeClient(),
            agent=FakeAgent(),
        )

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("use tool")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            self.assertEqual(app.session.turns, 1)
            self.assertEqual(
                [message["role"] for message in app.session.messages],
                ["user", "assistant", "tool", "assistant"],
            )
            self.assertEqual(
                app.query(".assistant").last(Static).render().plain,
                "ZXCode:\n完成",
            )

    async def test_layout_shows_model_and_zero_turns(self):
        app = ZXCodeApp(Settings("secret", "https://example.test/v1", "model-a"), FakeClient())

        async with app.run_test(size=(80, 24)):
            status = app.query_one("#status", Static).render().plain
            self.assertIn("model-a", status)
            self.assertIn("0 轮", status)
            self.assertIsNotNone(app.query_one("#input", TextArea))

    async def test_submit_streams_and_commits_complete_turn(self):
        client = FakeClient()
        app = TrackingApp(Settings("secret", "https://example.test/v1", "model-a"), client)

        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#input", TextArea).load_text("hello")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            assistant = app.query(".assistant").last(Static).render().plain
            self.assertEqual(assistant, "ZXCode:\n你好！")
            self.assertEqual(app.session.turns, 1)
            self.assertEqual(app.query_one("#input", TextArea).text, "")
            self.assertEqual(client.calls[0][1], "model-a")
            self.assertEqual(
                app.states[-5:], ["连接中", "生成中", "生成中", "生成中", "就绪"]
            )

    async def test_submit_preserves_code_indentation_and_trailing_newline(self):
        client = FakeClient(("ok",))
        app = ZXCodeApp(Settings("secret", "https://example.test/v1", "model-a"), client)

        async with app.run_test() as pilot:
            original = "    print('hello')\n"
            app.query_one("#input", TextArea).load_text(original)
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            self.assertEqual(client.calls[0][0][-1]["content"], original)

    async def test_pilot_enter_adds_newline_and_ctrl_enter_submits(self):
        client = FakeClient(("ok",))
        app = ZXCodeApp(Settings("secret", "https://example.test/v1", "model-a"), client)

        async with app.run_test() as pilot:
            input_box = app.query_one("#input", TextArea)
            input_box.load_text("hello")
            input_box.cursor_location = (0, 5)
            input_box.focus()
            await pilot.press("enter")
            self.assertEqual(input_box.text, "hello\n")
            await pilot.press("ctrl+enter")
            await app.active_worker.wait()

            self.assertEqual(app.session.messages[0]["content"], "hello\n")

    async def test_pilot_ctrl_s_submits_on_windows(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient(("ok",))
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input", TextArea)
            input_box.load_text("hello")
            input_box.focus()
            await pilot.press("ctrl+s")
            await app.active_worker.wait()

            self.assertEqual(app.session.messages[0]["content"], "hello")

    async def test_help_clear_and_model_commands_do_not_call_llm(self):
        client = FakeClient()
        app = ZXCodeApp(Settings("secret", "https://example.test/v1", "model-a"), client)

        async with app.run_test() as pilot:
            input_box = app.query_one("#input", TextArea)
            input_box.load_text("/help")
            app.action_submit()
            await pilot.pause()
            help_text = "\n".join(
                widget.render().plain for widget in app.query(".notice")
            )
            input_box.load_text("/model model-b")
            app.action_submit()
            app.session.commit("old", "answer")
            input_box.load_text("/clear")
            app.action_submit()
            await pilot.pause()

            self.assertIn("/help  /clear  /exit  /model <名称>  /plan", help_text)
            self.assertEqual(app.session.model, "model-b")
            self.assertEqual(app.session.turns, 0)
            self.assertEqual(client.calls, [])

    async def test_plan_command_toggles_mode_and_status(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input", TextArea)
            self.assertNotIn(
                "plan-only", app.query_one("#status", Static).render().plain
            )

            input_box.load_text("/plan")
            app.action_submit()
            await pilot.pause()
            self.assertTrue(app.config.plan_only)
            self.assertIs(app.agent.config.plan_only, True)
            self.assertIn("plan-only", app.query_one("#status", Static).render().plain)

            input_box.load_text("/plan")
            app.action_submit()
            await pilot.pause()
            self.assertFalse(app.config.plan_only)
            self.assertNotIn(
                "plan-only", app.query_one("#status", Static).render().plain
            )

    async def test_plan_only_blocks_the_write_tool_end_to_end(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "created.txt"
            client = ToolCallingClient(
                path.relative_to(Path.cwd()).as_posix(), None
            )
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"), client
            )

            async with app.run_test() as pilot:
                input_box = app.query_one("#input", TextArea)
                input_box.load_text("/plan")
                app.action_submit()
                await pilot.pause()

                input_box.load_text("write it")
                app.action_submit()
                await app.active_worker.wait()
                await pilot.pause()

                self.assertFalse(path.exists())
                tool_message = client.requests[1][0][-1]
                self.assertEqual(
                    json.loads(tool_message["content"])["error"]["code"],
                    "plan_only_blocked",
                )
                notices = "\n".join(
                    widget.render().plain for widget in app.query(".notice")
                )
                self.assertIn("plan-only 已拦截", notices)

    async def test_tool_lifecycle_is_rendered_and_turn_is_paired(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "target.txt"
            path.write_text("old", encoding="utf-8")
            client = ToolCallingClient(
                path.relative_to(Path.cwd()).as_posix(),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            app = ZXCodeApp(
                Settings("secret", "https://example.test/v1", "model-a"), client
            )

            async with app.run_test() as pilot:
                app.query_one("#input", TextArea).load_text("overwrite")
                app.action_submit()
                await pilot.pause()
                await pilot.click("#approve")
                await app.active_worker.wait()
                await pilot.pause()

                notices = "\n".join(
                    widget.render().plain for widget in app.query(".notice")
                )
                self.assertIn("⚙ WriteFile", notices)
                self.assertIn("✓ WriteFile", notices)
                self.assertEqual(
                    [message["role"] for message in app.session.messages],
                    ["user", "assistant", "tool", "assistant"],
                )
                assertNoDanglingCalls(self, app.session.messages)

    async def test_conversation_continues_after_a_cancelled_turn(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), BlockingClient()
        )

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("cancel me")
            app.action_submit()
            await pilot.pause()
            await pilot.press("ctrl+c")
            await app.active_worker.wait()
            await pilot.pause()

            app.client = FakeClient(("好",))
            app.agent.client = app.client
            app.query_one("#input", TextArea).load_text("再来一次")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            self.assertEqual(app.session.turns, 1)
            assertNoDanglingCalls(self, app.session.messages)

    async def test_failure_preserves_input_and_does_not_commit(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FailingClient()
        )

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("keep me")
            app.action_submit()
            await app.active_worker.wait()
            await pilot.pause()

            self.assertEqual(app.query_one("#input", TextArea).text, "keep me")
            self.assertEqual(app.session.turns, 0)
            self.assertIn("失败", app.query_one("#status", Static).render().plain)

    async def test_cancel_preserves_input_and_does_not_commit_partial_answer(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), BlockingClient()
        )

        async with app.run_test() as pilot:
            app.query_one("#input", TextArea).load_text("cancel me")
            app.action_submit()
            await pilot.pause()
            started = monotonic()
            await pilot.press("ctrl+c")
            await app.active_worker.wait()
            await pilot.pause()

            self.assertLess(monotonic() - started, 1.0)
            self.assertEqual(app.query_one("#input", TextArea).text, "cancel me")
            self.assertEqual(app.session.turns, 0)
            self.assertIn("已取消", app.query_one("#status", Static).render().plain)

    async def test_exit_command_calls_app_exit(self):
        app = ZXCodeApp(
            Settings("secret", "https://example.test/v1", "model-a"), FakeClient()
        )

        async with app.run_test():
            app.query_one("#input", TextArea).load_text("/exit")
            with patch.object(app, "exit") as exit_app:
                app.action_submit()
            exit_app.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
