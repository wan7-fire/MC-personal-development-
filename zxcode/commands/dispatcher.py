"""Dispatch parsed commands to handlers by type."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

from .model import AIPrompt, CommandInvocation, CommandType
from .registry import CommandRegistry
from .ui import UIControl


@dataclass
class CommandContext:
    registry: CommandRegistry
    ui: UIControl


def dispatch_command(ctx: CommandContext, invocation: CommandInvocation) -> None:
    """Run one parsed command; unknown names get a help hint."""
    meta = ctx.registry.get(invocation.name)
    if meta is None:
        if invocation.name:
            ctx.ui.notice(f"未知命令：{invocation.name}，输入 /help 查看帮助")
        else:
            ctx.ui.notice("未知命令，输入 /help 查看帮助")
        return
    try:
        result = meta.handler(ctx, invocation)
        if inspect.isawaitable(result):
            asyncio.create_task(_guarded(ctx, result))
    except Exception as error:
        ctx.ui.notice(f"命令执行失败：{error}")
        return
    if meta.command_type is CommandType.AI_FLOW and isinstance(result, AIPrompt):
        ctx.ui.send_user_message(result.user_text, result.system_parts)


async def _guarded(ctx: CommandContext, awaitable) -> None:
    try:
        await awaitable
    except Exception as error:
        ctx.ui.notice(f"命令执行失败：{error}")
