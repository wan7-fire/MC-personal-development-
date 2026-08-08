"""Agent-facing skill installation tool."""

from __future__ import annotations

import asyncio

from ..tools import Tool, ToolContext, ToolResult
from .installer import SkillInstallError, install_skill


class InstallSkill(Tool):
    name = "InstallSkill"
    description = (
        "Install a skill into the project from a skills.sh URL "
        "(https://www.skills.sh/<owner>/<repo>/<skill>). "
        "Use when the user asks to install a skill from a link."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string", "minLength": 1}},
        "required": ["url"],
        "additionalProperties": False,
    }

    def __init__(self, manager, *, opener=None, on_installed=None) -> None:
        self.manager = manager
        self.opener = opener
        self.on_installed = on_installed

    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        url = arguments["url"]
        if context.confirm is None:
            return ToolResult(
                False,
                error={
                    "code": "permission_denied",
                    "message": "install requires user confirmation",
                },
            )
        choice = await context.confirm(
            "安装 Skill",
            f"确认从 {url} 下载并安装 Skill 到 "
            f"{self.manager.root / '.zxcode' / 'skills'}？",
        )
        if choice not in ("once", "session", "permanent", True):
            return ToolResult(
                False,
                error={
                    "code": "permission_denied",
                    "message": "permission denied by user",
                },
            )
        try:
            target = await asyncio.to_thread(
                install_skill, self.manager.root, url, opener=self.opener
            )
        except SkillInstallError as error:
            return ToolResult(
                False,
                error={"code": "skill_install_error", "message": str(error)},
            )
        issues = self.manager.rescan()
        if self.on_installed is not None:
            self.on_installed()
        meta = self.manager.get(target.name)
        name = meta.name if meta is not None else target.name
        suffix = f"；跳过 {len(issues)} 个文件" if issues else ""
        return ToolResult(True, output=f"已安装 Skill：{name}{suffix}")
