import asyncio
import ctypes
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from zxcode.skills.frontmatter import SkillParseError, parse_skill_file
from zxcode.skills.loader import SkillValidationError, is_within, scan_skills
from zxcode.skills.manager import SkillActivationError, SkillManager
from zxcode.skills.tool import load_skill_tools
from zxcode.tools import Grep, ReadFile, ToolContext, ToolExecutor, ToolRegistry


def write_skill(root: Path, name: str, body: str = "SOP body", **fields) -> Path:
    lines = ["---", f"name: {name}"]
    for key, value in fields.items():
        if key == "tools":
            lines.append("tools:")
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body])
    target = root / f"{name}.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _make_junction(link: Path, target: Path) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and link.exists()
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def _make_file_symlink(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target)
        return True
    except (OSError, NotImplementedError):
        return False


def write_directory_skill(root: Path, name: str, with_tools: bool = True) -> Path:
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
    if with_tools:
        tools_dir = skill_dir / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.md").write_text(
            "---\n"
            "name: echo\n"
            "description: echo\n"
            'input_schema: {"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":false}\n'
            "read_only: true\n"
            "---\n",
            encoding="utf-8",
        )
        (tools_dir / "echo.py").write_text(
            "import json, sys\n"
            "data = json.load(sys.stdin)\n"
            "if 'marker' in data:\n"
            "    open(data['marker'], 'w').write('ran')\n"
            "json.dump({'success': True, 'output': data['text']}, sys.stdout)\n",
            encoding="utf-8",
        )
    return skill_dir


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    else:
        os.kill(pid, 9)


class FrontmatterTests(unittest.TestCase):
    def test_parse_minimal_skill_applies_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_skill(
                Path(directory), "demo", description="演示", mode="shared"
            )

            meta, body = parse_skill_file(path)

        self.assertEqual(meta.name, "demo")
        self.assertEqual(meta.mode, "shared")
        self.assertEqual(meta.history, "recent")
        self.assertEqual(meta.history_size, 10)
        self.assertIsNone(meta.tools)
        self.assertEqual(body, "SOP body")

    def test_parse_full_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_skill(
                Path(directory),
                "demo",
                description="说明",
                mode="isolated",
                model="gpt-x",
                history="recent",
                history_size=3,
                tools=["ReadFile", "Grep"],
            )

            meta, _ = parse_skill_file(path)

        self.assertEqual(meta.description, "说明")
        self.assertEqual(meta.model, "gpt-x")
        self.assertEqual(meta.history_size, 3)
        self.assertEqual(meta.tools, ("ReadFile", "Grep"))

    def test_parse_missing_name_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_skill(Path(directory), "demo", mode="shared")
            text = path.read_text(encoding="utf-8").replace("name: demo\n", "")
            path.write_text(text, encoding="utf-8")

            with self.assertRaises(SkillParseError) as caught:
                parse_skill_file(path)

        self.assertIn("demo.md", str(caught.exception))

    def test_parse_bad_mode_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_skill(Path(directory), "demo", mode="nope")

            with self.assertRaises(SkillParseError):
                parse_skill_file(path)


class LoaderTests(unittest.TestCase):
    def test_is_within_rejects_escape_via_parent_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            self.assertTrue(is_within(root, root / "demo" / "skill.md"))
            self.assertFalse(is_within(root, root / ".." / "outside.md"))

    def test_scan_skips_skill_directory_junction_escaping_root(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            project = temp / "project"
            skills_root = project / ".zxcode" / "skills"
            outside = temp / "outside"
            skills_root.mkdir(parents=True)
            outside.mkdir()
            (outside / "skill.md").write_text(
                "---\nname: evil\ndescription: evil\nmode: shared\n---\nbody",
                encoding="utf-8",
            )
            link = skills_root / "escape"
            if not _make_junction(link, outside):
                self.skipTest("cannot create directory junction")

            index = scan_skills(
                project,
                temp / "user",
                temp / "builtin",
                ToolRegistry([ReadFile()]),
            )

        self.assertNotIn("evil", index.by_name)
        self.assertTrue(
            any("escape" in issue.path for issue in index.issues)
        )

    def test_scan_skips_symlinked_skill_file_outside_root(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            project = temp / "project"
            skills_root = project / ".zxcode" / "skills"
            outside = temp / "outside"
            skills_root.mkdir(parents=True)
            outside.mkdir()
            outside_file = outside / "evil.md"
            outside_file.write_text(
                "---\nname: evil\ndescription: evil\nmode: shared\n---\nbody",
                encoding="utf-8",
            )
            link = skills_root / "evil.md"
            if not _make_file_symlink(link, outside_file):
                self.skipTest("cannot create file symlink")

            index = scan_skills(
                project,
                temp / "user",
                temp / "builtin",
                ToolRegistry([ReadFile()]),
            )

        self.assertNotIn("evil", index.by_name)

    def test_scan_overrides_by_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            project = temp / "project"
            user = temp / "user"
            builtin = temp / "builtin"
            for root in (project, user, builtin):
                (root / ".zxcode" / "skills").mkdir(parents=True)
            (project / ".zxcode" / "skills" / "demo.md").write_text(
                "---\nname: demo\ndescription: project\nmode: shared\n---\nP",
                encoding="utf-8",
            )
            (user / ".zxcode" / "skills" / "demo.md").write_text(
                "---\nname: demo\ndescription: user\nmode: shared\n---\nU",
                encoding="utf-8",
            )
            (builtin / "demo.md").write_text(
                "---\nname: demo\ndescription: builtin\nmode: shared\n---\nB",
                encoding="utf-8",
            )

            index = scan_skills(
                project,
                user / ".zxcode",
                builtin,
                ToolRegistry([ReadFile()]),
            )

        self.assertEqual(index.by_name["demo"].description, "project")
        self.assertEqual(
            index.by_name["demo"].source,
            project / ".zxcode" / "skills" / "demo.md",
        )
        self.assertEqual(index.by_name["demo"].level, "project")

    def test_scan_skips_bad_frontmatter_with_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / ".zxcode" / "skills").mkdir(parents=True)
            (project / ".zxcode" / "skills" / "broken.md").write_text(
                "---\nname: broken\nmode: nope\n---\nx",
                encoding="utf-8",
            )

            index = scan_skills(
                project,
                Path(directory) / "user",
                Path(directory) / "builtin",
                ToolRegistry([ReadFile()]),
            )

        self.assertNotIn("broken", index.by_name)
        self.assertGreaterEqual(len(index.issues), 1)
        self.assertIn("broken.md", index.issues[0].path)

    def test_scan_fails_fast_on_unknown_whitelist_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / ".zxcode" / "skills").mkdir(parents=True)
            write_skill(
                project / ".zxcode" / "skills",
                "bad",
                description="bad",
                mode="shared",
                tools=["NoSuchTool"],
            )

            with self.assertRaises(SkillValidationError) as caught:
                scan_skills(
                    project,
                    Path(directory) / "user",
                    Path(directory) / "builtin",
                    ToolRegistry([ReadFile()]),
                )

        self.assertIn("bad", str(caught.exception))
        self.assertIn("bad.md", str(caught.exception))

    def test_scan_accepts_directory_skill_own_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            skill_dir = project / ".zxcode" / "skills" / "pkg"
            (skill_dir / "tools").mkdir(parents=True)
            (skill_dir / "skill.md").write_text(
                "---\nname: pkg\ndescription: pkg\nmode: shared\n"
                "tools:\n- echo\n---\nbody",
                encoding="utf-8",
            )
            (skill_dir / "tools" / "echo.md").write_text(
                "---\nname: echo\ndescription: echo\n"
                'input_schema: {"type":"object","properties":{},"required":[]}\n'
                "read_only: true\n---\n",
                encoding="utf-8",
            )

            index = scan_skills(
                project,
                Path(directory) / "user",
                Path(directory) / "builtin",
                ToolRegistry([ReadFile()]),
            )

        self.assertIn("pkg", index.by_name)


class ManagerTests(unittest.TestCase):
    def _manager(self, directory: str, *skill_args):
        root = Path(directory)
        skills_root = root / ".zxcode" / "skills"
        skills_root.mkdir(parents=True)
        for args in skill_args:
            write_skill(skills_root, **args)
        registry = ToolRegistry([ReadFile(), Grep()])
        index = scan_skills(root, root / "user", root / "builtin", registry)
        return SkillManager(index, registry, root=root, user_dir=root / "user", builtin_root=root / "builtin")

    def test_activate_adds_skill_message_and_body(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(
                directory,
                dict(name="demo", description="演示", mode="shared"),
            )

            manager.activate("demo")
            messages = manager.active_skill_messages()

        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]["content"].startswith("[Skill 指令：demo]"))
        self.assertIn("SOP body", messages[0]["content"])
        self.assertEqual([meta.name for meta in manager.list_skills()], ["demo"])

    def test_duplicate_activate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(
                directory,
                dict(name="demo", description="演示", mode="shared"),
            )

            manager.activate("demo")
            manager.activate("demo")

        self.assertEqual(len(manager.active_skill_messages()), 1)

    def test_tool_names_union_and_system_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(
                directory,
                dict(name="a", description="a", mode="shared", tools=["ReadFile"]),
                dict(name="b", description="b", mode="shared", tools=["Grep"]),
            )

            manager.activate("a")
            manager.activate("b")

        self.assertEqual(
            manager.active_tool_names(), {"ReadFile", "Grep", "LoadSkill"}
        )

    def test_no_whitelist_means_all_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(
                directory,
                dict(name="a", description="a", mode="shared"),
            )

            manager.activate("a")

        self.assertIsNone(manager.active_tool_names())

    def test_clear_removes_active_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(
                directory,
                dict(name="a", description="a", mode="shared"),
            )
            manager.activate("a")

            manager.clear()

        self.assertEqual(manager.active_skill_messages(), [])
        self.assertIsNone(manager.active_tool_names())

    def test_activate_unknown_skill_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)

            with self.assertRaises(SkillActivationError):
                manager.activate("missing")

    def test_activate_requires_confirmation_for_project_script_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills_root = root / ".zxcode" / "skills"
            write_directory_skill(skills_root, "pkg")
            registry = ToolRegistry([ReadFile(), Grep()])
            index = scan_skills(
                root, root / "user", root / "builtin", registry
            )
            manager = SkillManager(
                index,
                registry,
                root=root,
                user_dir=root / "user",
                builtin_root=root / "builtin",
            )

            with self.assertRaises(SkillActivationError) as caught:
                manager.activate("pkg")

        self.assertIn("confirmation", str(caught.exception))
        self.assertEqual(manager.active_skill_messages(), [])

    def test_builtin_script_skill_activates_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builtin = root / "builtin"
            write_directory_skill(builtin, "pkg")
            registry = ToolRegistry([ReadFile(), Grep()])
            index = scan_skills(
                root, root / "user", builtin, registry
            )
            manager = SkillManager(
                index,
                registry,
                root=root,
                user_dir=root / "user",
                builtin_root=builtin,
            )

            active = manager.activate("pkg")

        self.assertEqual(len(active.tools), 1)
        self.assertEqual(active.tools[0].name, "echo")


class ManagerConfirmTests(unittest.IsolatedAsyncioTestCase):
    def _manager(self, directory: str, confirm):
        root = Path(directory)
        skills_root = root / ".zxcode" / "skills"
        write_directory_skill(skills_root, "pkg")
        registry = ToolRegistry([ReadFile(), Grep()])
        index = scan_skills(root, root / "user", root / "builtin", registry)
        return SkillManager(
            index,
            registry,
            root=root,
            user_dir=root / "user",
            builtin_root=root / "builtin",
            context=ToolContext(root, confirm, None),
        )

    def _user_script_manager(
        self, project_dir: str, user_home: str, confirm, security=None
    ):
        root = Path(project_dir)
        user_skills = Path(user_home) / "skills"
        write_directory_skill(user_skills, "pkg")
        registry = ToolRegistry([ReadFile(), Grep()])
        index = scan_skills(root, Path(user_home), root / "builtin", registry)
        return SkillManager(
            index,
            registry,
            root=root,
            user_dir=Path(user_home),
            builtin_root=root / "builtin",
            context=ToolContext(root, confirm, security),
        )

    async def test_confirm_activate_approval_loads_script_tools(self):
        async def approve(title, detail):
            self.assertIn("pkg", title)
            return True

        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, approve)

            active = await manager.confirm_activate("pkg")

        self.assertEqual(len(active.tools), 1)
        self.assertEqual(active.tools[0].name, "echo")
        self.assertEqual(len(manager.active_skill_messages()), 1)

    async def test_confirm_activate_denial_raises_and_keeps_skill_inactive(self):
        async def deny(title, detail):
            return "deny"

        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, deny)

            with self.assertRaises(SkillActivationError):
                await manager.confirm_activate("pkg")

        self.assertEqual(manager.active_skill_messages(), [])

    async def test_once_approval_prompts_again_for_same_skill(self):
        calls = []

        async def confirm(title, detail):
            calls.append(title)
            return "once"

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as home:
            manager = self._user_script_manager(directory, home, confirm)

            await manager.confirm_activate("pkg")
            await manager.confirm_activate("pkg")

        self.assertEqual(len(calls), 2)

    async def test_session_approval_skips_prompt_for_same_skill(self):
        calls = []

        async def confirm(title, detail):
            calls.append(title)
            if len(calls) > 1:
                raise AssertionError("unexpected second confirmation")
            return "session"

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as home:
            manager = self._user_script_manager(directory, home, confirm)

            await manager.confirm_activate("pkg")
            await manager.confirm_activate("pkg")

        self.assertEqual(len(calls), 1)

    async def test_clear_resets_session_trust(self):
        calls = []

        async def confirm(title, detail):
            calls.append(title)
            return "session"

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as home:
            manager = self._user_script_manager(directory, home, confirm)

            await manager.confirm_activate("pkg")
            manager.clear()
            await manager.confirm_activate("pkg")

        self.assertEqual(len(calls), 2)

    async def test_confirm_activate_registers_skill_script_root_with_security(self):
        from zxcode.security import load_policy

        async def confirm(title, detail):
            return "once"

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as home:
            root = Path(directory)
            skill_dir = Path(home) / "skills" / "pkg"
            policy = load_policy(root)
            manager = self._user_script_manager(
                directory, home, confirm, security=policy
            )

            await manager.confirm_activate("pkg")

            decision = policy.evaluate_script(
                "echo", skill_dir / "tools" / "echo.py"
            )

        self.assertEqual(decision.action, "ask")


class DirectoryToolTests(unittest.IsolatedAsyncioTestCase):
    def _skill_with_tool(self, directory: str, read_only: bool = True):
        root = Path(directory)
        tools_dir = root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "echo.md").write_text(
            "---\n"
            "name: echo\n"
            "description: echo text\n"
            'input_schema: {"type":"object","properties":{"text":{"type":"string"},"marker":{"type":"string"}},"required":["text"],"additionalProperties":false}\n'
            f"read_only: {str(read_only).lower()}\n"
            "timeout_seconds: 5\n"
            "---\n",
            encoding="utf-8",
        )
        (tools_dir / "echo.py").write_text(
            "import json, sys\n"
            "data = json.load(sys.stdin)\n"
            "if 'marker' in data:\n"
            "    open(data['marker'], 'w').write('ran')\n"
            "json.dump({'success': True, 'output': data['text']}, sys.stdout)\n",
            encoding="utf-8",
        )
        return root

    async def test_script_tool_executes_and_returns_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)

            result = await executor.execute(
                "1", "echo", {"text": "hi"}, ToolContext()
            )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "hi")

    async def test_write_tool_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory, read_only=False)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)
            marker = Path(directory) / "marker.txt"

            async def deny(title, detail):
                return "deny"

            context = ToolContext(confirm=deny)

            result = await executor.execute(
                "1", "echo", {"text": "x", "marker": str(marker)}, context
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "permission_denied")
        self.assertFalse(marker.exists())

    async def test_script_tool_consults_security_policy_before_execution(self):
        from zxcode.security import load_policy

        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)
            policy = load_policy(root)
            context = ToolContext(root, None, policy)

            result = await executor.execute(
                "1", "echo", {"text": "hi"}, context
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "permission_denied")

    async def test_script_tool_with_security_approval_runs(self):
        from zxcode.security import load_policy

        async def approve(title, detail):
            return True

        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)
            policy = load_policy(root)
            marker = Path(directory) / "marker.txt"
            context = ToolContext(root, approve, policy)

            result = await executor.execute(
                "1",
                "echo",
                {"text": "hi", "marker": str(marker)},
                context,
            )
            marker_written = marker.exists()

        self.assertTrue(result.success)
        self.assertEqual(result.output, "hi")
        self.assertTrue(marker_written)

    async def test_script_tool_allowed_in_allow_mode_without_prompt(self):
        from zxcode.security import load_policy

        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)
            policy = load_policy(root)
            policy.mode = "allow"
            context = ToolContext(root, None, policy)

            result = await executor.execute(
                "1", "echo", {"text": "hi"}, context
            )

        self.assertTrue(result.success)
        self.assertEqual(result.output, "hi")

    async def test_script_tool_blocked_in_strict_mode(self):
        from zxcode.security import load_policy

        with tempfile.TemporaryDirectory() as directory:
            root = self._skill_with_tool(directory)
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)
            policy = load_policy(root)
            policy.mode = "strict"
            context = ToolContext(root, None, policy)

            result = await executor.execute(
                "1", "echo", {"text": "hi"}, context
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "security_blocked")

    async def test_load_skill_tools_skips_junction_tools_dir_outside_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "skill"
            skill_dir.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "evil.md").write_text(
                "---\nname: evil\ndescription: evil\n---\n",
                encoding="utf-8",
            )
            (outside / "evil.py").write_text(
                "import json, sys\njson.dump({'success': True}, sys.stdout)\n",
                encoding="utf-8",
            )
            link = skill_dir / "tools"
            if not _make_junction(link, outside):
                self.skipTest("cannot create directory junction")

            tools = load_skill_tools(skill_dir)

        self.assertEqual(tools, [])

    async def test_timeout_terminates_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools_dir = root / "tools"
            tools_dir.mkdir(parents=True)
            marker = root / "pid.txt"
            (tools_dir / "sleep.md").write_text(
                "---\n"
                "name: sleep\n"
                "description: sleep\n"
                'input_schema: {"type":"object","properties":{"marker":{"type":"string"}},"required":["marker"],"additionalProperties":false}\n'
                "read_only: true\n"
                "timeout_seconds: 1\n"
                "---\n",
                encoding="utf-8",
            )
            (tools_dir / "sleep.py").write_text(
                "import json, os, sys, time\n"
                "data = json.load(sys.stdin)\n"
                "with open(data['marker'], 'w') as f:\n"
                "    f.write(str(os.getpid()))\n"
                "time.sleep(60)\n"
                "json.dump({'success': True, 'output': 'late'}, sys.stdout)\n",
                encoding="utf-8",
            )
            tools = load_skill_tools(root)
            registry = ToolRegistry(tools)
            executor = ToolExecutor(registry)

            result = await executor.execute(
                "1", "sleep", {"marker": str(marker)}, ToolContext()
            )
            pid = int(marker.read_text(encoding="utf-8").strip())
            alive = True
            for _ in range(50):
                alive = _pid_alive(pid)
                if not alive:
                    break
                await asyncio.sleep(0.1)
            if alive:
                _kill_pid(pid)

        self.assertFalse(result.success)
        self.assertEqual(result.error["code"], "timeout")
        self.assertFalse(alive, "child process still running after timeout")


if __name__ == "__main__":
    unittest.main()
