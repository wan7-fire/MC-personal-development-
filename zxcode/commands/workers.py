"""Sub-worker task management command."""

from __future__ import annotations

from .model import CommandMeta, CommandType
from .registry import CommandRegistry


def _workers(ctx, invocation) -> None:
    args = invocation.args.strip()
    if not args:
        ctx.ui.list_workers()
        return
    sub, _, rest = args.partition(" ")
    if sub == "kill":
        ctx.ui.kill_worker(rest.strip())
    else:
        ctx.ui.worker_detail(args)


def register_workers_command(registry: CommandRegistry) -> None:
    registry.register(
        CommandMeta(
            "workers",
            "子工作者任务管理",
            "/workers",
            CommandType.LOCAL,
            _workers,
            param_hint="[<id>|kill <id>]",
        )
    )
