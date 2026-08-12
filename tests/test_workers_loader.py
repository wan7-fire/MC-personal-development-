import tempfile
import unittest
from pathlib import Path

from zxcode.workers.filters import (
    BACKGROUND_ALLOW,
    GLOBAL_DENY,
    filter_tool_names,
)
from zxcode.workers.loader import load_roles
from zxcode.workers.model import WorkerRole


def write_role(
    root: Path,
    name: str,
    body: str = "SOP body",
    *,
    level: str = "project",
    **fields,
) -> Path:
    if level == "project":
        target = root / ".zxcode" / "workers"
    elif level == "user":
        target = root / "workers"
    else:
        target = root
    target.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}"]
    for key, value in fields.items():
        if key in ("tools", "deny_tools"):
            lines.append(f"{key}:")
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(f"{key}: {value}")
    lines.extend(["---", body])
    path = target / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ModelTests(unittest.TestCase):
    def test_role_defaults(self):
        role = WorkerRole(name="r", description="d", body="b")

        self.assertIsNone(role.tools_allow)
        self.assertEqual(role.tools_deny, ())
        self.assertIsNone(role.model)
        self.assertEqual(role.max_turns, 20)
        self.assertEqual(role.permission_mode, "default")


class LoaderTests(unittest.TestCase):
    def _load(self, project, **kwargs):
        return load_roles(
            project,
            user_dir=kwargs.pop("user_dir", None),
            builtin_root=kwargs.pop("builtin_root", None),
            plugin_dirs=kwargs.pop("plugin_dirs", ()),
            include_verifier=kwargs.pop("include_verifier", False),
        )

    def test_loads_role_with_fields_and_body(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            write_role(
                project,
                "explorer",
                "explore the code",
                description="代码探索",
                tools=["ReadFile", "Grep"],
                deny_tools=["Bash"],
                model="gpt-x",
                max_turns=5,
                permission_mode="strict",
            )

            roles = self._load(project)

        role = roles["explorer"]
        self.assertEqual(role.description, "代码探索")
        self.assertEqual(role.body, "explore the code")
        self.assertEqual(role.tools_allow, ("ReadFile", "Grep"))
        self.assertEqual(role.tools_deny, ("Bash",))
        self.assertEqual(role.model, "gpt-x")
        self.assertEqual(role.max_turns, 5)
        self.assertEqual(role.permission_mode, "strict")

    def test_priority_project_over_user_over_builtin(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            project = temp / "project"
            user = temp / "user"
            builtin = temp / "builtin"
            for root, text in (
                (project, "project"),
                (user, "user"),
                (builtin, "builtin"),
            ):
                write_role(
                    root,
                    "demo",
                    description=text,
                    level=(
                        "project"
                        if root == project
                        else "user"
                        if root == user
                        else "builtin"
                    ),
                )

            roles = self._load(
                project,
                user_dir=user,
                builtin_root=builtin,
            )

        self.assertEqual(roles["demo"].description, "project")

    def test_user_overrides_builtin_when_no_project(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            user = temp / "user"
            builtin = temp / "builtin"
            write_role(user, "demo", description="user", level="user")
            write_role(builtin, "demo", description="builtin", level="builtin")

            roles = self._load(
                temp / "project",
                user_dir=user,
                builtin_root=builtin,
            )

        self.assertEqual(roles["demo"].description, "user")

    def test_bad_frontmatter_is_skipped_and_others_load(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            write_role(project, "good", description="ok")
            (project / ".zxcode" / "workers" / "broken.md").write_text(
                "---\nname: broken\nmode: nope\n---\nbody",
                encoding="utf-8",
            )

            roles = self._load(project)

        self.assertIn("good", roles)
        self.assertNotIn("broken", roles)

    def test_missing_name_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".zxcode" / "workers").mkdir(parents=True)
            (project / ".zxcode" / "workers" / "anon.md").write_text(
                "---\ndescription: no name\n---\nbody",
                encoding="utf-8",
            )

            roles = self._load(project)

        self.assertEqual(roles, {})

    def test_verifier_included_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            builtin = Path(directory)
            write_role(builtin, "verifier", description="验证", level="builtin")

            disabled = self._load(
                Path(directory) / "project", builtin_root=builtin
            )
            enabled = self._load(
                Path(directory) / "project",
                builtin_root=builtin,
                include_verifier=True,
            )

        self.assertNotIn("verifier", disabled)
        self.assertIn("verifier", enabled)

    def test_plugin_dir_is_lowest_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            builtin = temp / "builtin"
            plugin = temp / "plugin"
            write_role(builtin, "demo", description="builtin", level="builtin")
            write_role(plugin, "demo", description="plugin", level="plugin")

            roles = self._load(
                temp / "project",
                builtin_root=builtin,
                plugin_dirs=(plugin,),
            )

        self.assertEqual(roles["demo"].description, "builtin")


class FilterTests(unittest.TestCase):
    def test_global_deny_always_excludes_worker_tool(self):
        names = {"ReadFile", "SpawnWorker", "Bash"}
        self.assertNotIn("SpawnWorker", filter_tool_names(names))
        self.assertNotIn("SpawnWorker", filter_tool_names(names, background=True))

    def test_role_allow_intersects_and_deny_removes(self):
        names = {"ReadFile", "Grep", "Bash", "WriteFile"}
        role = WorkerRole(
            name="r",
            description="d",
            body="b",
            tools_allow=("ReadFile", "Grep", "Bash"),
            tools_deny=("Bash",),
        )
        self.assertEqual(
            filter_tool_names(names, role=role), ("Grep", "ReadFile")
        )

    def test_no_allow_keeps_all_except_deny_and_global(self):
        names = {"ReadFile", "Grep", "Bash"}
        role = WorkerRole(name="r", description="d", body="b", tools_deny=("Bash",))
        self.assertEqual(filter_tool_names(names, role=role), ("Grep", "ReadFile"))

    def test_background_whitelist_is_stricter(self):
        names = {"ReadFile", "Grep", "Glob", "Bash", "WriteFile", "EditFile"}
        self.assertEqual(
            filter_tool_names(names, background=True),
            ("Glob", "Grep", "ReadFile"),
        )

    def test_background_plus_role_allow(self):
        names = {"ReadFile", "Grep", "Glob", "Bash"}
        role = WorkerRole(
            name="r",
            description="d",
            body="b",
            tools_allow=("ReadFile", "Bash"),
        )
        self.assertEqual(
            filter_tool_names(names, role=role, background=True), ("ReadFile",)
        )


if __name__ == "__main__":
    unittest.main()
