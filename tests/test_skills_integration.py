import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

from zxcode.agent import AgentLoop
from zxcode.client import AssistantMessage, TextDelta
from zxcode.commands.registry import CommandRegistry
from zxcode.config import AgentConfig
from zxcode.events import EventChannel
from zxcode.skills.loader import scan_skills
from zxcode.skills.manager import SkillManager
from zxcode.tools import Tool, ToolContext, ToolExecutor, ToolRegistry, ToolResult


class EchoTool(Tool):
    name = "Echo"
    description = "echo"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(True, arguments["text"])


class WriteTool(Tool):
    name = "Write"
    description = "write"
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(True, "written")


class SystemLoadTool(Tool):
    name = "LoadSkill"
    description = "system load tool"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context):
        return ToolResult(True, "ok")


class FakeSkillClient:
    def __init__(self, text="done"):
        self.text = text
        self.requests = []

    async def stream_events(self, messages, model=None, tools=None):
        self.requests.append((list(messages), model, list(tools or [])))
        yield TextDelta(self.text)
        yield AssistantMessage({"role": "assistant", "content": self.text})


class FakeOpener:
    def __init__(self, body: bytes):
        self.body = body
        self.urls = []

    def open(self, url, timeout=None):
        self.urls.append(url)
        return io.BytesIO(self.body)


def write_skill(root: Path, name: str, **fields):
    lines = ["---", f"name: {name}"]
    for key, value in fields.items():
        if key == "tools":
            lines.append("tools:")
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", f"{name} SOP body"])
    path = root / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_directory_skill(root: Path, name: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: dir skill\n"
        "mode: shared\n"
        "---\nbody",
        encoding="utf-8",
    )
    tools_dir = skill_dir / "tools"
    tools_dir.mkdir()
    (tools_dir / "echo.md").write_text(
        "---\n"
        "name: echo\n"
        "description: echo\n"
        "---\n",
        encoding="utf-8",
    )
    (tools_dir / "echo.py").write_text(
        "import json, sys\n"
        "json.dump({'success': True, 'output': 'ok'}, sys.stdout)\n",
        encoding="utf-8",
    )
    return skill_dir


def make_manager(directory: str, registry: ToolRegistry, *skills, **runtime):
    root = Path(directory)
    skills_root = root / ".zxcode" / "skills"
    skills_root.mkdir(parents=True)
    for fields in skills:
        write_skill(skills_root, **fields)
    index = scan_skills(root, root / "user", root / "builtin", registry)
    return SkillManager(
        index,
        registry,
        root=root,
        user_dir=root / "user",
        builtin_root=root / "builtin",
        **runtime,
    )


async def drive(agent, messages):
    channel = EventChannel()
    events = []
    runner = asyncio.create_task(agent.run(messages, "demo", channel))
    async for event in channel:
        events.append(event)
    return await runner


class AgentSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_loop_injects_skill_and_filters_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry([EchoTool(), WriteTool(), SystemLoadTool()])
            manager = make_manager(
                directory,
                registry,
                dict(name="demo", description="演示", mode="shared", tools=["Echo"]),
            )
            manager.activate("demo")
            client = FakeSkillClient()
            agent = AgentLoop(
                client,
                registry,
                ToolExecutor(registry),
                skill_manager=manager,
            )

            await drive(
                agent,
                [
                    {"role": "system", "content": "stable"},
                    {"role": "user", "content": "hi"},
                ],
            )

        request = client.requests[0][0]
        self.assertTrue(request[1]["content"].startswith("[Skill 指令：demo]"))
        tool_names = {
            definition["function"]["name"]
            for definition in client.requests[0][2]
        }
        self.assertEqual(tool_names, {"Echo", "LoadSkill"})


class LoadSkillToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_skill_activates_shared_skill(self):
        from zxcode.skills.load_skill import LoadSkill

        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = make_manager(
                directory,
                registry,
                dict(name="demo", description="演示", mode="shared"),
            )
            tool = LoadSkill(manager)

            result = await tool.execute({"name": "demo"}, ToolContext())

        self.assertTrue(result.success)
        self.assertIn("demo", result.output)
        self.assertEqual(len(manager.active_skill_messages()), 1)

    async def test_load_skill_requires_confirmation_for_script_tools(self):
        from zxcode.skills.load_skill import LoadSkill
        from zxcode.skills.manager import SkillManager

        async def deny(title, detail):
            return "deny"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills_root = root / ".zxcode" / "skills"
            write_directory_skill(skills_root, "pkg")
            registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = SkillManager(
                scan_skills(
                    root, root / "user", root / "builtin", registry
                ),
                registry,
                root=root,
                user_dir=root / "user",
                builtin_root=root / "builtin",
                context=ToolContext(root, deny, None),
            )
            tool = LoadSkill(manager)

            result = await tool.execute({"name": "pkg"}, ToolContext())

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "skill_activation_error")
        self.assertEqual(manager.active_skill_messages(), [])


class IsolatedSkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_isolated_returns_summary_without_history_when_none(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            client = FakeSkillClient("完成")
            manager = make_manager(
                directory,
                registry,
                dict(
                    name="iso",
                    description="隔离",
                    mode="isolated",
                    history="none",
                    tools=["Echo"],
                ),
                client=client,
                config=AgentConfig(max_turns=3),
                context=ToolContext(),
                messages_provider=lambda: [{"role": "user", "content": "旧历史"}],
                model_provider=lambda: "model-x",
            )

            summary = await manager.run_isolated("iso")

        self.assertIn("结论：完成", summary)
        self.assertIn("状态：", summary)
        self.assertEqual(client.requests[0][1], "model-x")
        contents = [message.get("content", "") for message in client.requests[0][0]]
        self.assertNotIn("旧历史", contents)

    async def test_run_isolated_does_not_leave_skill_active(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = make_manager(
                directory,
                registry,
                dict(
                    name="iso",
                    description="隔离",
                    mode="isolated",
                    history="none",
                    tools=["Echo"],
                ),
                client=FakeSkillClient(),
                config=AgentConfig(max_turns=3),
                context=ToolContext(),
                messages_provider=lambda: [],
                model_provider=lambda: "model-x",
            )

            await manager.run_isolated("iso")

        self.assertEqual(manager.active_skill_messages(), [])


class SkillCommandTests(unittest.TestCase):
    def test_register_skill_commands_registers_management_and_shortcuts(self):
        from zxcode.commands.skills import register_skill_commands

        with tempfile.TemporaryDirectory() as directory:
            registry = CommandRegistry()
            tool_registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = make_manager(
                directory,
                tool_registry,
                dict(name="demo", description="演示", mode="shared"),
            )

            register_skill_commands(registry, manager)

        self.assertIsNotNone(registry.get("skills"))
        self.assertIsNotNone(registry.get("demo"))

    def test_rescan_can_register_new_shortcut(self):
        from zxcode.commands.skills import (
            register_skill_commands,
            register_skill_shortcut,
        )

        with tempfile.TemporaryDirectory() as directory:
            command_registry = CommandRegistry()
            tool_registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = make_manager(directory, tool_registry)
            register_skill_commands(command_registry, manager)
            root = Path(directory)
            write_skill(
                root / ".zxcode" / "skills",
                "new",
                description="新增",
                mode="shared",
            )

            manager.rescan()
            register_skill_shortcut(command_registry, manager.get("new"))

        self.assertIsNotNone(command_registry.get("new"))


class InstallSkillToolTests(unittest.IsolatedAsyncioTestCase):
    def _payload(self):
        return json.dumps(
            {
                "files": [
                    {
                        "path": "SKILL.md",
                        "contents": (
                            "---\nname: demo\ndescription: demo\nmode: shared\n---\nbody"
                        ),
                    },
                    {"path": "LICENSE.txt", "contents": "MIT"},
                ]
            }
        ).encode("utf-8")

    async def test_install_skill_tool_installs_and_rescans(self):
        from zxcode.skills.install_tool import InstallSkill

        rescan_calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry([EchoTool(), SystemLoadTool()])
            manager = make_manager(directory, registry)
            tool = InstallSkill(
                manager,
                opener=FakeOpener(self._payload()),
                on_installed=lambda: rescan_calls.append(1),
            )

            async def approve(title, detail):
                return "once"

            result = await tool.execute(
                {"url": "https://www.skills.sh/acme/skills/demo"},
                ToolContext(root, approve, None),
            )

            self.assertTrue(result.success)
            self.assertIn("demo", result.output)
            self.assertIsNotNone(manager.get("demo"))
            self.assertTrue(
                (root / ".zxcode" / "skills" / "demo" / "skill.md").exists()
            )
            self.assertEqual(rescan_calls, [1])
            self.assertEqual(len(tool.opener.urls), 1)

    async def test_install_skill_tool_requires_confirmation(self):
        from zxcode.skills.install_tool import InstallSkill

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = make_manager(
                directory, ToolRegistry([EchoTool(), SystemLoadTool()])
            )
            tool = InstallSkill(manager, opener=FakeOpener(self._payload()))

            result = await tool.execute(
                {"url": "https://www.skills.sh/acme/skills/demo"},
                ToolContext(root, None, None),
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "permission_denied")

    async def test_install_skill_tool_reports_bad_url(self):
        from zxcode.skills.install_tool import InstallSkill

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = make_manager(
                directory, ToolRegistry([EchoTool(), SystemLoadTool()])
            )
            tool = InstallSkill(manager, opener=FakeOpener(self._payload()))

            async def approve(title, detail):
                return "once"

            result = await tool.execute(
                {"url": "https://example.com/acme/skills/demo"},
                ToolContext(root, approve, None),
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "skill_install_error")


if __name__ == "__main__":
    unittest.main()
