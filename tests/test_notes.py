import tempfile
import unittest
from pathlib import Path

from zxcode.client import AssistantMessage, TextDelta
from zxcode.notes import NotesConfig, NotesManager


class NotesClient:
    def __init__(self, output=None, fail=False):
        self.output = output
        self.fail = fail
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, tools))
        if self.fail:
            raise RuntimeError("provider down")
        if self.output is not None:
            yield TextDelta(self.output)
        yield AssistantMessage({"role": "assistant", "content": "x"})


OUTPUT = (
    "## 用户身份\n用户是后端工程师，习惯用中文\n\n"
    "## 用户偏好\n用户喜欢中文回答\n\n"
    "## 纠正反馈\n不要猜测文件名\n\n"
    "## 项目知识\n技术栈是 Python\n\n"
    "## 参考资料\nREADME.md\n\n"
    "【一句话摘要】会话总结一句话"
)


def make_manager(root: Path, user_dir: Path, client, interval=5) -> NotesManager:
    return NotesManager(
        root,
        client,
        user_dir=user_dir,
        config=NotesConfig(interval_turns=interval),
    )


class NotesTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_notes_writes_categorized_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            client = NotesClient(OUTPUT)
            manager = make_manager(root, user, client)

            summary = await manager.update_notes(
                "model-a", conversation=[{"role": "user", "content": "hi"}]
            )
            user_text = manager.user_notes_path().read_text(encoding="utf-8")
            project_text = manager.project_notes_path().read_text(encoding="utf-8")

        self.assertEqual(summary, "会话总结一句话")
        self.assertIn("## 用户身份", user_text)
        self.assertIn("用户是后端工程师", user_text)
        self.assertIn("## 用户偏好", user_text)
        self.assertIn("用户喜欢中文回答", user_text)
        self.assertIn("## 纠正反馈", user_text)
        self.assertNotIn("## 用户身份", project_text)
        self.assertNotIn("## 项目知识", user_text)
        self.assertIn("## 项目知识", project_text)
        self.assertIn("技术栈是 Python", project_text)
        self.assertIn("## 参考资料", project_text)
        self.assertNotIn("## 用户偏好", project_text)
        self.assertEqual(client.requests[0][2], ())

    async def test_missing_section_keeps_existing_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            manager = make_manager(root, user, NotesClient())
            manager.user_notes_path().parent.mkdir(parents=True, exist_ok=True)
            manager.user_notes_path().write_text(
                "## 用户身份\n身份OLD\n\n"
                "## 用户偏好\nOLD\n\n## 纠正反馈\nOLD-CORR",
                encoding="utf-8",
            )
            client = NotesClient(
                "## 项目知识\nNEW\n\n## 参考资料\nREF\n\n【一句话摘要】s"
            )
            manager.client = client

            await manager.update_notes(
                "model-a", conversation=[{"role": "user", "content": "hi"}]
            )
            user_text = manager.user_notes_path().read_text(encoding="utf-8")
            project_text = manager.project_notes_path().read_text(encoding="utf-8")

        self.assertIn("身份OLD", user_text)
        self.assertIn("OLD", user_text)
        self.assertIn("OLD-CORR", user_text)
        self.assertIn("NEW", project_text)
        self.assertIn("REF", project_text)

    async def test_empty_section_keeps_existing_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            manager = make_manager(root, user, NotesClient())
            manager.user_notes_path().parent.mkdir(parents=True, exist_ok=True)
            manager.user_notes_path().write_text(
                "## 用户身份\n身份OLD\n\n"
                "## 用户偏好\nOLD\n\n## 纠正反馈\nOLD-CORR",
                encoding="utf-8",
            )
            client = NotesClient(
                "## 用户身份\n\n## 用户偏好\n\n## 纠正反馈\n\n"
                "## 项目知识\nNEW\n\n【一句话摘要】s"
            )
            manager.client = client

            await manager.update_notes(
                "model-a", conversation=[{"role": "user", "content": "hi"}]
            )
            user_text = manager.user_notes_path().read_text(encoding="utf-8")

        self.assertIn("身份OLD", user_text)
        self.assertIn("OLD", user_text)
        self.assertIn("OLD-CORR", user_text)

    async def test_failure_is_silent_and_leaves_no_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            manager = make_manager(root, user, NotesClient(fail=True))

            summary = await manager.update_notes("model-a")

        self.assertIsNone(summary)
        self.assertFalse(manager.user_notes_path().exists())
        self.assertFalse(manager.project_notes_path().exists())

    async def test_on_turn_triggers_every_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            client = NotesClient(OUTPUT)
            manager = make_manager(root, user, client, interval=2)
            conversation = [{"role": "user", "content": "hi"}]

            task1 = manager.on_turn_completed("model-a", conversation)
            self.assertIsNone(task1)
            task2 = manager.on_turn_completed("model-a", conversation)
            self.assertIsNotNone(task2)
            await task2
            task3 = manager.on_turn_completed("model-a", conversation)
            self.assertIsNone(task3)
            task4 = manager.on_turn_completed("model-a", conversation)
            await task4

        self.assertEqual(len(client.requests), 2)

    async def test_memorable_signal_triggers_early(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            client = NotesClient(OUTPUT)
            manager = make_manager(root, user, client, interval=5)

            task = manager.on_turn_completed(
                "model-a",
                [{"role": "user", "content": "我是后端工程师，喜欢用 Python"}],
            )
            self.assertIsNotNone(task)
            await task

        self.assertEqual(len(client.requests), 1)

    async def test_no_signal_waits_for_interval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            client = NotesClient(OUTPUT)
            manager = make_manager(root, user, client, interval=5)

            first = manager.on_turn_completed(
                "model-a", [{"role": "user", "content": "请重构这个函数"}]
            )
            self.assertIsNone(first)
            pending = None
            for _ in range(4):
                pending = manager.on_turn_completed(
                    "model-a", [{"role": "user", "content": "继续"}]
                )
            self.assertIsNotNone(pending)
            await pending

        self.assertEqual(len(client.requests), 1)

    async def test_atomic_write_leaves_no_tmp_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            manager = make_manager(root, user, NotesClient(OUTPUT))

            await manager.update_notes("model-a")
            user_tmp = list(user.glob("*.tmp"))
            project_tmp = list((root / ".zxcode").glob("*.tmp"))

        self.assertEqual(user_tmp, [])
        self.assertEqual(project_tmp, [])

    def test_clear_notes_scopes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "proj"
            root.mkdir()
            user = Path(temp) / "user"
            user.mkdir()
            manager = make_manager(root, user, NotesClient())
            manager.clear_notes("all")
            manager.user_notes_path().write_text("U", encoding="utf-8")
            manager.project_notes_path().write_text("P", encoding="utf-8")

            manager.clear_notes("user")
            self.assertEqual(manager.user_notes_path().read_text(encoding="utf-8"), "")
            self.assertEqual(manager.project_notes_path().read_text(encoding="utf-8"), "P")

            manager.clear_notes("project")
            self.assertEqual(manager.project_notes_path().read_text(encoding="utf-8"), "")

            manager.user_notes_path().write_text("U2", encoding="utf-8")
            manager.clear_notes("all")
            self.assertEqual(manager.user_notes_path().read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
