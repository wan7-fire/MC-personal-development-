"""Runtime state for activated skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AgentConfig
from ..tools import Tool, ToolContext, ToolExecutor, ToolRegistry
from .frontmatter import SkillParseError, parse_skill_file
from .loader import SkillIndex, scan_skills
from .model import SkillMeta
from .tool import load_skill_tools


class SkillActivationError(ValueError):
    pass


@dataclass
class ActiveSkill:
    meta: SkillMeta
    body: str
    tools: tuple[Tool, ...] = ()


class SkillManager:
    def __init__(
        self,
        index: SkillIndex,
        registry: ToolRegistry,
        *,
        root: Path | str | None = None,
        user_dir: Path | str | None = None,
        builtin_root: Path | str | None = None,
        client=None,
        config: AgentConfig | None = None,
        context: ToolContext | None = None,
        messages_provider=None,
        model_provider=None,
    ) -> None:
        self.index = index
        self.registry = registry
        self.root = Path(root) if root is not None else Path.cwd()
        self.user_dir = Path(user_dir) if user_dir is not None else Path.home() / ".zxcode"
        self.builtin_root = (
            Path(builtin_root) if builtin_root is not None else Path(__file__).parent / "builtin"
        )
        self.client = client
        self.config = config or AgentConfig()
        self.context = context or ToolContext()
        self.messages_provider = messages_provider
        self.model_provider = model_provider
        self.executor = ToolExecutor(registry)
        self.active: dict[str, ActiveSkill] = {}
        self._order: list[str] = []
        self._trusted_skill_dirs: set[Path] = set()

    def list_skills(self) -> list[SkillMeta]:
        return [self.index.by_name[name] for name in sorted(self.index.by_name)]

    def get(self, name: str) -> SkillMeta | None:
        return self.index.by_name.get(name)

    def activate(self, name: str) -> ActiveSkill:
        meta = self.index.by_name.get(name)
        if meta is None:
            raise SkillActivationError(f"unknown skill: {name}")
        try:
            loaded_meta, body = parse_skill_file(meta.source, level=meta.level)
        except SkillParseError as error:
            raise SkillActivationError(str(error)) from error
        if (
            meta.level != "builtin"
            and self._has_script_tools(meta)
            and meta.source.parent.resolve() not in self._trusted_skill_dirs
        ):
            raise SkillActivationError(
                f"{name}: skill tool scripts require user confirmation"
            )
        tools = tuple(load_skill_tools(meta.source.parent))
        if loaded_meta.tools is not None:
            known = set(self.registry.names()) | {"LoadSkill"} | {tool.name for tool in tools}
            unknown = [tool for tool in loaded_meta.tools if tool not in known]
            if unknown:
                raise SkillActivationError(
                    f"{name}: unknown tool(s) {', '.join(unknown)}"
                )
        if name not in self.active:
            self._order.append(name)
        self.active[name] = ActiveSkill(loaded_meta, body, tools)
        return self.active[name]

    def _has_script_tools(self, meta: SkillMeta) -> bool:
        tools_dir = meta.source.parent / "tools"
        return tools_dir.is_dir() and any(
            spec.with_suffix(".py").exists()
            for spec in tools_dir.glob("*.md")
        )

    async def confirm_activate(self, name: str) -> ActiveSkill:
        meta = self.index.by_name.get(name)
        if meta is None:
            raise SkillActivationError(f"unknown skill: {name}")
        if (
            meta.level != "builtin"
            and self._has_script_tools(meta)
            and meta.source.parent.resolve() not in self._trusted_skill_dirs
        ):
            if self.context.confirm is None:
                raise SkillActivationError(
                    f"{name}: skill tool scripts require user confirmation"
                )
            choice = await self.context.confirm(
                f"Skill 工具授权：{name}",
                f"Skill 目录包含可执行脚本（{meta.source.parent / 'tools'}），"
                "是否允许加载？",
            )
            if choice not in ("once", "session", "permanent", True):
                raise SkillActivationError(
                    f"{name}: user denied skill tool scripts"
                )
            skill_dir = meta.source.parent.resolve()
            was_trusted = skill_dir in self._trusted_skill_dirs
            if not was_trusted:
                self._trusted_skill_dirs.add(skill_dir)
            security = getattr(self.context, "security", None)
            if security is not None:
                security.allow_script_root(skill_dir)
            try:
                return self.activate(name)
            finally:
                if choice == "once" and not was_trusted:
                    self._trusted_skill_dirs.discard(skill_dir)
        return self.activate(name)

    def active_skill_messages(self) -> list[dict]:
        return [
            {
                "role": "system",
                "content": f"[Skill 指令：{name}]\n{self.active[name].body}",
            }
            for name in self._order
        ]

    def active_tool_names(self) -> set[str] | None:
        if not self.active:
            return None
        if any(skill.meta.tools is None for skill in self.active.values()):
            return None
        names = set()
        for skill in self.active.values():
            names.update(skill.meta.tools)
        names.add("LoadSkill")
        return names

    def clear(self) -> None:
        self.active.clear()
        self._order.clear()
        self._trusted_skill_dirs.clear()
        security = getattr(self.context, "security", None)
        if security is not None:
            security.clear_script_roots()

    def deactivate(self, name: str) -> None:
        if name in self.active:
            del self.active[name]
            self._order.remove(name)

    async def run_isolated(self, name: str, user_text: str | None = None) -> str:
        from .runner import run_isolated

        return await run_isolated(self, name, user_text)

    def rescan(self) -> list:
        self.index = scan_skills(
            self.root, self.user_dir, self.builtin_root, self.registry
        )
        for name in list(self._order):
            if name not in self.index.by_name:
                del self.active[name]
                self._order.remove(name)
                continue
            try:
                self.activate(name)
            except SkillActivationError:
                del self.active[name]
                self._order.remove(name)
        return self.index.issues
