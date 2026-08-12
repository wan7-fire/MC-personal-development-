"""Unified sub-worker tool entry."""

from __future__ import annotations

from ..tools import Tool, ToolContext, ToolResult
from .manager import STATUS_SUCCEEDED, TaskManager


class SpawnWorker(Tool):
    name = "SpawnWorker"
    description = (
        "Create a sub-worker that runs a task to completion. "
        "Pass a role name for a definition-mode worker, or omit role "
        "to fork the current conversation in the background."
    )
    read_only = False
    input_schema = {
        "type": "object",
        "properties": {
            "role": {"type": "string"},
            "task": {"type": "string", "minLength": 1},
            "background": {"type": "boolean"},
            "model": {"type": "string"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    def __init__(self, manager: TaskManager) -> None:
        self.manager = manager

    async def execute(self, arguments, context: ToolContext) -> ToolResult:
        role_name = arguments.get("role")
        if role_name is not None and self.manager.resolve_role(role_name) is None:
            return ToolResult(
                False,
                error={
                    "code": "unknown_role",
                    "message": f"unknown worker role: {role_name}",
                },
            )
        try:
            task_obj = await self.manager.start(
                role_name=role_name,
                task=arguments["task"],
                model=arguments.get("model"),
                background=bool(arguments.get("background", False)),
            )
        except ValueError as error:
            return ToolResult(
                False, error={"code": "worker_error", "message": str(error)}
            )
        if task_obj.background:
            return ToolResult(
                True,
                output=(
                    f"后台任务 {task_obj.id} 已启动"
                    f"（{task_obj.mode} / {task_obj.role}）"
                ),
                metadata={"task_id": task_obj.id},
            )
        if task_obj.status == STATUS_SUCCEEDED:
            return ToolResult(
                True,
                output=task_obj.result or "（无文本输出）",
                metadata={
                    "task_id": task_obj.id,
                    "token_usage": task_obj.token_usage,
                },
            )
        return ToolResult(
            False,
            output=task_obj.result,
            error={
                "code": "worker_failed",
                "message": task_obj.error or "worker failed",
            },
            metadata={"task_id": task_obj.id},
        )
