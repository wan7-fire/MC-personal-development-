import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from zxcode.client import AssistantMessage, TextDelta
from zxcode.compress import (
    BEGIN_SUMMARY,
    BOUNDARY_MESSAGE,
    CompressionConfig,
    CompressionManager,
    END_SUMMARY,
)
from zxcode.recovery import recover_session, truncate_dangling
from zxcode.storage import SessionStore


def make_store(root: Path) -> SessionStore:
    return SessionStore(root / "sessions")


def message(role, content):
    return {"role": role, "content": content}


class SessionStoreTests(unittest.TestCase):
    def test_append_writes_one_line_per_message(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "hi")], "model-a")
            store.append_messages(
                "s1", [message("assistant", "yo")], "model-a"
            )
            lines = store.jsonl_path("s1").read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), message("user", "hi"))
        self.assertEqual(json.loads(lines[1]), message("assistant", "yo"))

    def test_meta_title_and_count(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages(
                "s1",
                [message("user", "x" * 100), message("assistant", "ok")],
                "model-a",
            )
            meta = store.read_meta("s1")

        self.assertIsNotNone(meta)
        self.assertEqual(meta.message_count, 2)
        self.assertEqual(len(meta.title), 60)
        self.assertEqual(meta.title, "x" * 60)
        self.assertEqual(meta.model, "model-a")
        self.assertEqual(meta.id, "s1")

    def test_list_meta_uses_meta_not_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "hello")], "model-a")
            store.jsonl_path("s1").unlink()
            metas = store.list_meta()

        self.assertEqual(len(metas), 1)
        self.assertEqual(metas[0].id, "s1")
        self.assertEqual(metas[0].title, "hello")

    def test_broken_meta_falls_back_to_jsonl(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "hello")], "model-a")
            store.meta_path("s1").write_text("{broken", encoding="utf-8")
            metas = store.list_meta()

        self.assertEqual(len(metas), 1)
        self.assertTrue(metas[0].meta_broken)
        self.assertEqual(metas[0].title, "hello")
        self.assertEqual(metas[0].message_count, 1)

    def test_update_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "hello")], "model-a")
            store.update_summary("s1", "一句话摘要")
            meta = store.read_meta("s1")

        self.assertEqual(meta.summary, "一句话摘要")
        self.assertEqual(meta.message_count, 1)

    def test_delete_session_and_clear_all(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "a")], "model-a")
            store.append_messages("s2", [message("user", "b")], "model-a")
            store.delete_session("s1")
            self.assertFalse(store.jsonl_path("s1").exists())
            self.assertFalse(store.meta_path("s1").exists())
            removed = store.clear_all()

        self.assertEqual(removed, 2)
        self.assertEqual(store.list_meta(), [])


class DanglingTests(unittest.TestCase):
    def test_trailing_unmatched_tool_calls_are_truncated(self):
        messages = [
            message("user", "u"),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            message("assistant", "final"),
        ]

        kept, dropped = truncate_dangling(messages)

        self.assertEqual(dropped, 2)
        self.assertEqual(kept, [message("user", "u")])

    def test_middle_dangling_block_is_kept(self):
        messages = [
            message("user", "u1"),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            message("user", "u2"),
            message("assistant", "ok"),
        ]

        kept, dropped = truncate_dangling(messages)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, messages)

    def test_complete_turn_is_kept(self):
        messages = [
            message("user", "u"),
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
            message("assistant", "ok"),
        ]

        kept, dropped = truncate_dangling(messages)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, messages)


class SummaryClient:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, tools))
        yield TextDelta(self.output)
        yield AssistantMessage({"role": "assistant", "content": "x"})


class FailingClient:
    async def stream_events(self, messages, model=None, tools=None):
        if False:
            yield None
        raise RuntimeError("provider down")


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages("s1", [message("user", "ok")], "model-a")
            path = store.jsonl_path("s1")
            with path.open("a", encoding="utf-8") as handle:
                handle.write("{broken}\n")
                handle.write('{"role": "assistant", "content": "fine"}\n')
            messages, report = await recover_session(store, "s1")

        self.assertEqual(report.skipped_lines, 1)
        self.assertEqual(len(messages), 2)

    async def test_idle_reminder_inserted_only_after_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            old = (datetime.now().astimezone() - timedelta(hours=5)).isoformat(
                timespec="seconds"
            )
            store.append_messages(
                "s1", [message("user", "hello")], "model-a", now=old
            )
            messages, report = await recover_session(store, "s1")

        self.assertTrue(report.idle_reminder)
        self.assertTrue(
            any("[时间跨度提醒]" in str(m.get("content")) for m in messages)
        )

    async def test_idle_reminder_not_inserted_within_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            recent = (datetime.now().astimezone() - timedelta(minutes=10)).isoformat(
                timespec="seconds"
            )
            store.append_messages(
                "s1", [message("user", "hello")], "model-a", now=recent
            )
            messages, report = await recover_session(store, "s1")

        self.assertFalse(report.idle_reminder)
        self.assertTrue(
            all("[时间跨度提醒]" not in str(m.get("content")) for m in messages)
        )

    async def test_over_limit_triggers_one_compression(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages(
                "s1",
                [
                    message("user", "u1"),
                    message("assistant", "a" * 8000),
                    message("user", "u2"),
                    message("assistant", "ok"),
                ],
                "model-a",
            )
            client = SummaryClient(
                f"{BEGIN_SUMMARY}\n## 主要请求\n汇总\n{END_SUMMARY}"
            )
            compressor = CompressionManager(
                Path(temp),
                CompressionConfig(context_window=2000),
                client=client,
            )
            messages, report = await recover_session(
                store, "s1", compressor=compressor, model="model-a"
            )

        self.assertTrue(report.compressed)
        self.assertTrue(any("汇总" in str(m.get("content")) for m in messages))
        self.assertEqual(client.requests[0][2], ())

    async def test_over_limit_falls_back_to_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = make_store(Path(temp))
            store.append_messages(
                "s1",
                [
                    message("user", "u1"),
                    message("assistant", "a" * 3000),
                    message("user", "u2"),
                    message("assistant", "b" * 3000),
                    message("user", "u3"),
                    message("assistant", "c" * 3000),
                ],
                "model-a",
            )
            compressor = CompressionManager(
                Path(temp),
                CompressionConfig(context_window=2000),
                client=FailingClient(),
            )
            messages, report = await recover_session(
                store, "s1", compressor=compressor, model="model-a"
            )

        self.assertFalse(report.compressed)
        self.assertGreater(report.over_limit_dropped, 0)
        contents = [m.get("content") for m in messages]
        self.assertIn(BOUNDARY_MESSAGE, contents)
        self.assertIn("u3", contents)


if __name__ == "__main__":
    unittest.main()
