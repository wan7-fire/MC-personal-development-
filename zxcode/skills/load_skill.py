"""System-level skill loading tool."""

from __future__ import annotations

from ..tools import Tool, ToolContext, ToolResult
from .manager import SkillActivationError


class LoadSkill(Tool):
    name = "LoadSkill"
    description = "Load and activate a skill by name into the current session."
    read_only = True
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1}},
        "required": ["name"],
        "additionalProperties": False,
    }

    def __init__(self, manager) -> None:
        self.manager = manager

    async def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        name = arguments["name"]
        try:
            active = await self.manager.confirm_activate(name)
        except SkillActivationError as error:
            return ToolResult(
                False,
                error={"code": "skill_activation_error", "message": str(error)},
            )
        if active.meta.mode == "isolated":
            try:
                summary = await self.manager.run_isolated(name)
            except Exception as error:
                return ToolResult(
                    False,
                    error={"code": "isolated_run_error", "message": str(error)},
                )
            return ToolResult(True, output=summary)
        return ToolResult(True, output=f"Skill {name} 已激活（shared）")
