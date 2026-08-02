import hashlib
import tempfile
import unittest
from pathlib import Path

from zxcode.client import AssistantMessage, TextDelta
from zxcode.compress import (
    BEGIN_SUMMARY,
    BOUNDARY_MESSAGE,
    CompressionConfig,
    CompressionFailure,
    CompressionManager,
    CircuitBreaker,
    END_SUMMARY,
    estimate_tokens,
)
from zxcode.session import ChatSession


def tool_message(content, call_id="call-1"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class SummaryClient:
    def __init__(self, summary=None, fail=False, tool_call=False):
        self.requests = []
        self.summary = summary or (
            f"{BEGIN_SUMMARY}\n## 主要请求\n测试\n## 用户原话\n原句\n{END_SUMMARY}"
        )
        self.fail = fail
        self.tool_call = tool_call

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, tools))
        if self.fail:
            raise RuntimeError("provider down")
        if self.tool_call:
            yield AssistantMessage(
                {"role": "assistant", "tool_calls": [{"id": "x", "type": "function"}]}
            )
            return
        yield TextDelta("分析草稿内容")
        yield TextDelta(self.summary)
        yield AssistantMessage({"role": "assistant", "content": "草稿"})


def turns(count, chars=2400):
    messages = [{"role": "system", "content": "stable"}]
    for index in range(count):
        messages.append({"role": "user", "content": f"请求 {index}"})
        messages.append({"role": "assistant", "content": "x" * chars})
    return messages


class CompressionConfigTests(unittest.TestCase):
    def test_defaults_match_checklist(self):
        config = CompressionConfig()
        self.assertEqual(config.context_window, 131072)
        self.assertEqual(config.trigger_ratio, 0.8)
        self.assertEqual(config.target_ratio, 0.4)
        self.assertEqual(config.single_result_limit, 8192)
        self.assertEqual(config.batch_total_limit, 32768)
        self.assertIsNone(config.summary_model)
        self.assertEqual(config.breaker_limit, 2)
        self.assertEqual(config.spool_dir, ".zxcode/spool")

    def test_estimate_tokens_is_chars_divided_by_four(self):
        self.assertEqual(estimate_tokens("a" * 4000), 1000)


class SpoolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = CompressionManager(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_single_result_is_spooled_with_preview(self):
        content = "z" * 9000
        result, spooled = self.manager.spool_batch([tool_message(content)])

        self.assertEqual(len(spooled), 1)
        preview = result[0]["content"]
        self.assertIn("[工具结果已溢出: 9000 字符", preview)
        self.assertIn(".zxcode/spool/", preview)
        self.assertIn("可用 ReadFile 读取", preview)
        self.assertIn("z" * 200, preview)
        self.assertNotIn("z" * 201, preview)
        self.assertEqual(spooled[0].read_text(encoding="utf-8"), content)
        self.assertEqual(
            spooled[0].name, f"{hashlib.sha256(content.encode()).hexdigest()}.txt"
        )

    def test_small_message_stays_unchanged(self):
        message = tool_message("small")
        result, spooled = self.manager.spool_batch([message])

        self.assertEqual(result[0], message)
        self.assertEqual(spooled, [])
        self.assertFalse((self.root / ".zxcode").exists())

    def test_same_content_spools_to_one_file(self):
        first, spooled_first = self.manager.spool_batch([tool_message("a" * 9000)])
        second, spooled_second = self.manager.spool_batch([tool_message("a" * 9000)])

        self.assertEqual(spooled_first[0], spooled_second[0])
        self.assertEqual(first[0], second[0])

    def test_batch_total_spools_largest_first(self):
        messages = [tool_message("y" * 7000, call_id=str(index)) for index in range(5)]
        result, spooled = self.manager.spool_batch(messages)

        self.assertEqual(len(spooled), 1)
        remaining = sum(
            len(message.get("content") or "")
            for message in result
            if message.get("role") == "tool"
        )
        self.assertLessEqual(remaining, self.manager.config.batch_total_limit)

    def test_write_failure_keeps_message_untouched(self):
        blocker = self.root / "block"
        blocker.write_text("x", encoding="utf-8")
        manager = CompressionManager(
            self.root, CompressionConfig(spool_dir="block/sub")
        )
        message = tool_message("q" * 9000)

        new_message, path = manager.spool_tool_message(message)

        self.assertIs(new_message, message)
        self.assertIsNone(path)


class RecheckTests(unittest.TestCase):
    def test_prepare_request_spools_legacy_tool_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = CompressionManager(root)
            session = ChatSession("model-a")
            session.messages = [
                {"role": "user", "content": "read it"},
                tool_message("d" * 20000),
            ]

            request = session.prepare_request("again", compressor=manager)

            self.assertEqual(request[-1], {"role": "user", "content": "again"})
            self.assertIn("已溢出", session.messages[1]["content"])
            self.assertEqual(
                len(list((root / ".zxcode/spool").glob("*.txt"))), 1
            )
            request_again = session.prepare_request("again", compressor=manager)
            self.assertEqual(request[2:], request_again[2:])

    def test_rebuild_from_history_strips_system_prefix(self):
        session = ChatSession("model-a")
        session.rebuild_from_history(
            [
                {"role": "system", "content": "stable"},
                {"role": "system", "content": "env"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        )
        self.assertEqual(
            session.messages,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )

    def test_rebuild_also_strips_plan_only_system_message(self):
        session = ChatSession("model-a")
        session.rebuild_from_history(
            [
                {"role": "system", "content": "stable"},
                {"role": "system", "content": "env"},
                {"role": "system", "content": "plan-only"},
                {"role": "user", "content": "hello"},
            ]
        )
        self.assertEqual(session.messages, [{"role": "user", "content": "hello"}])


class SummaryPromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = CompressionManager(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_prompt_has_nine_sections_and_forbids_tools_twice(self):
        prompt = self.manager.build_summary_prompt(
            [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "ok"}]
        )
        for section in (
            "主要请求",
            "关键概念",
            "文件代码",
            "错误修复",
            "解决过程",
            "用户原话",
            "待办",
            "当前工作",
            "下一步",
        ):
            self.assertIn(section, prompt)
        third = len(prompt) // 3
        self.assertIn("禁止使用任何工具", prompt[:third])
        self.assertIn("禁止使用任何工具", prompt[2 * third :])
        self.assertIn(BEGIN_SUMMARY, prompt)
        self.assertIn(END_SUMMARY, prompt)

    def test_parse_summary_extracts_between_markers(self):
        text = f"草稿\n{BEGIN_SUMMARY}\n## 主要请求\nX\n{END_SUMMARY}"
        self.assertEqual(self.manager.parse_summary(text), "## 主要请求\nX")

    def test_parse_summary_missing_markers_fails(self):
        with self.assertRaises(CompressionFailure) as raised:
            self.manager.parse_summary("没有标记")
        self.assertEqual(raised.exception.kind, "parse_error")

    def test_parse_summary_empty_fails(self):
        with self.assertRaises(CompressionFailure):
            self.manager.parse_summary(f"{BEGIN_SUMMARY}\n{END_SUMMARY}")

    def test_parse_summary_with_tool_call_fails(self):
        with self.assertRaises(CompressionFailure) as raised:
            self.manager.parse_summary(
                f"{BEGIN_SUMMARY}\n\"type\": \"function\"\n{END_SUMMARY}"
            )
        self.assertEqual(raised.exception.kind, "tool_call")


class SummarizeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_summarize_block_uses_no_tools(self):
        client = SummaryClient()
        manager = CompressionManager(self.root, client=client)

        summary = await manager.summarize_block(
            [{"role": "user", "content": "你好"}], "model-a"
        )

        self.assertIn("## 主要请求", summary)
        self.assertEqual(client.requests[0][1], "model-a")
        self.assertEqual(client.requests[0][2], ())

    async def test_summarize_block_model_error(self):
        manager = CompressionManager(self.root, client=SummaryClient(fail=True))
        with self.assertRaises(CompressionFailure) as raised:
            await manager.summarize_block([{"role": "user", "content": "x"}], "m")
        self.assertEqual(raised.exception.kind, "model_error")

    async def test_summarize_block_tool_call_is_a_failure(self):
        manager = CompressionManager(self.root, client=SummaryClient(tool_call=True))
        with self.assertRaises(CompressionFailure) as raised:
            await manager.summarize_block([{"role": "user", "content": "x"}], "m")
        self.assertEqual(raised.exception.kind, "tool_call")


class CompressHistoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = SummaryClient()
        self.manager = CompressionManager(
            self.root,
            CompressionConfig(context_window=2000),
            client=self.client,
        )

    def tearDown(self):
        self.temp.cleanup()

    async def test_replaces_oldest_turns_with_summary_and_boundary(self):
        history = turns(3, 2400)

        new_history, outcome = await self.manager.compress_history(history, "model-a")

        self.assertTrue(outcome.changed)
        self.assertEqual(outcome.removed_messages, 4)
        self.assertEqual(new_history[0], history[0])
        self.assertEqual(new_history[1]["role"], "user")
        self.assertIn("## 主要请求", new_history[1]["content"])
        self.assertEqual(new_history[2], {"role": "user", "content": BOUNDARY_MESSAGE})
        self.assertEqual(new_history[-1], history[-1])
        self.assertLessEqual(
            estimate_tokens("".join(str(m.get("content") or "") for m in new_history)),
            2000 * 0.4,
        )

    async def test_noop_when_below_target(self):
        history = turns(2, 10)
        new_history, outcome = await self.manager.compress_history(history, "model-a")
        self.assertFalse(outcome.changed)
        self.assertEqual(new_history, history)

    async def test_noop_with_single_user_turn(self):
        history = [{"role": "user", "content": "only"}, {"role": "assistant", "content": "x" * 9000}]
        new_history, outcome = await self.manager.compress_history(history, "model-a")
        self.assertFalse(outcome.changed)

    async def test_still_over_limit_raises(self):
        manager = CompressionManager(
            self.root,
            CompressionConfig(context_window=200),
            client=self.client,
        )
        history = turns(2, 1000)
        with self.assertRaises(CompressionFailure) as raised:
            await manager.compress_history(history, "model-a")
        self.assertEqual(raised.exception.kind, "still_over_limit")

    async def test_removed_spool_files_are_cleaned_up(self):
        digest = "a" * 64
        spool_file = self.root / ".zxcode/spool" / f"{digest}.txt"
        spool_file.parent.mkdir(parents=True)
        spool_file.write_text("content", encoding="utf-8")
        history = [
            {"role": "user", "content": "u1"},
            {
                "role": "assistant",
                "content": (
                    "[工具结果已溢出: 9000 字符，完整内容见 "
                    f".zxcode/spool/{digest}.txt，可用 ReadFile 读取]"
                    + "x" * 5000
                ),
            },
            {"role": "user", "content": "u2"},
        ]

        await self.manager.compress_history(history, "model-a")

        self.assertFalse(spool_file.exists())


class BreakerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_trips_after_limit_and_resets_on_success(self):
        breaker = CircuitBreaker(2)
        self.assertTrue(breaker.allowed())
        breaker.record_failure()
        breaker.record_failure()
        self.assertFalse(breaker.allowed())
        breaker.record_success()
        self.assertTrue(breaker.allowed())

    async def test_prepare_compresses_and_records_success(self):
        client = SummaryClient()
        manager = CompressionManager(
            self.root,
            CompressionConfig(context_window=2000),
            client=client,
        )
        history = turns(3, 2400)

        new_history = await manager.prepare(history, "model-a")

        self.assertNotEqual(new_history, history)
        self.assertIn(BOUNDARY_MESSAGE, [m.get("content") for m in new_history])
        self.assertTrue(manager.breaker.allowed())

    async def test_prepare_trips_breaker_and_stops_auto_calls(self):
        client = SummaryClient(fail=True)
        manager = CompressionManager(
            self.root,
            CompressionConfig(context_window=2000),
            client=client,
        )
        history = turns(3, 2400)

        first = await manager.prepare(history, "model-a")
        second = await manager.prepare(history, "model-a")
        third = await manager.prepare(history, "model-a")

        self.assertEqual(first, history)
        self.assertEqual(second, history)
        self.assertEqual(third, history)
        self.assertTrue(manager.breaker.tripped)
        self.assertEqual(len(client.requests), 2)

    async def test_manual_compress_bypasses_breaker(self):
        client = SummaryClient(fail=True)
        manager = CompressionManager(
            self.root,
            CompressionConfig(context_window=2000),
            client=client,
        )
        history = turns(3, 2400)
        await manager.prepare(history, "model-a")
        await manager.prepare(history, "model-a")
        self.assertTrue(manager.breaker.tripped)

        with self.assertRaises(CompressionFailure):
            await manager.manual_compress(history, "model-a")
        self.assertEqual(len(client.requests), 3)


if __name__ == "__main__":
    unittest.main()
