"""Built-in command registration for ZXCode."""

from __future__ import annotations

from .dispatcher import CommandContext
from .model import CommandMeta, CommandType
from .registry import CommandRegistry


def _help(ctx: CommandContext, invocation) -> None:
    visible = ctx.registry.visible_commands()
    summary = "  ".join(
        f"/{meta.name}"
        + (f" {meta.param_hint}" if meta.param_hint else "")
        for meta in visible
    )
    lines = [f"可用命令：{summary}", ""]
    for meta in visible:
        usage = meta.usage
        if meta.param_hint:
            usage += f" {meta.param_hint}"
        alias_text = (
            f"（别名：{'/'.join('/' + alias for alias in meta.aliases)}）"
            if meta.aliases
            else ""
        )
        lines.append(f"  {usage} - {meta.description}{alias_text}")
    ctx.ui.notice("\n".join(lines))


def _clear(ctx: CommandContext, invocation) -> None:
    ctx.ui.clear_chat()


def _exit(ctx: CommandContext, invocation) -> None:
    ctx.ui.exit_app()


def _model(ctx: CommandContext, invocation) -> None:
    if invocation.args:
        ctx.ui.set_model(invocation.args)
    else:
        ctx.ui.notice("用法：/model <名称>")


def _plan(ctx: CommandContext, invocation) -> None:
    ctx.ui.toggle_plan_mode()


def _compact(ctx: CommandContext, invocation) -> None:
    ctx.ui.run_compact()


def _resume(ctx: CommandContext, invocation) -> None:
    if invocation.args:
        ctx.ui.resume_session(invocation.args)
    else:
        ctx.ui.choose_session()


def _sessions(ctx: CommandContext, invocation) -> None:
    if not invocation.args:
        ctx.ui.list_sessions()
        return
    sub, _, rest = invocation.args.partition(" ")
    if sub == "delete":
        if rest.strip():
            ctx.ui.delete_session(rest.strip())
        else:
            ctx.ui.notice("用法：/sessions delete <会话ID>")
    elif sub == "clear":
        ctx.ui.clear_sessions()
    elif sub == "path":
        ctx.ui.sessions_path()
    else:
        ctx.ui.notice("用法：/sessions [delete <ID>|clear|path]")


def _notes(ctx: CommandContext, invocation) -> None:
    if not invocation.args:
        ctx.ui.list_notes("all")
        return
    sub, _, scope = invocation.args.partition(" ")
    if sub == "view":
        ctx.ui.list_notes(scope or "all")
    elif sub == "clear":
        target = scope or "project"
        if target in ("project", "user", "all"):
            ctx.ui.clear_notes(target)
        else:
            ctx.ui.notice("用法：/notes clear [project|user|all]")
    elif sub == "edit":
        ctx.ui.notes_path(scope or "project")
    elif sub == "path":
        ctx.ui.notes_path(scope or "all")
    else:
        ctx.ui.notice("用法：/notes [view|clear|edit|path] [project|user|all]")


def _status(ctx: CommandContext, invocation) -> None:
    info = ctx.ui.status_summary()
    mode = "计划" if info["plan_only"] else "执行"
    ctx.ui.notice(
        f"模型：{info['model']}  |  轮次：{info['turns']}  |  模式：{mode}\n"
        f"token 估算：{info['token_estimate']}\n"
        f"会话：{info['session_id'] or '无会话'}\n"
        f"会话目录：{info['sessions_dir']}\n"
        f"用户笔记：{info['user_notes']}\n"
        f"项目笔记：{info['project_notes']}"
    )


def _permissions(ctx: CommandContext, invocation) -> None:
    ctx.ui.notice(ctx.ui.security_summary())


def register_builtins(registry: CommandRegistry, ctx: CommandContext) -> None:
    """Register every built-in command onto ``registry``."""
    registry.register(
        CommandMeta(
            "help",
            "显示帮助",
            "/help",
            CommandType.LOCAL,
            _help,
            aliases=("h",),
        )
    )
    registry.register(
        CommandMeta("clear", "清空当前对话", "/clear", CommandType.UI_STATE, _clear)
    )
    registry.register(
        CommandMeta("exit", "退出应用", "/exit", CommandType.UI_STATE, _exit)
    )
    registry.register(
        CommandMeta(
            "model",
            "切换模型",
            "/model",
            CommandType.UI_STATE,
            _model,
            param_hint="<名称>",
        )
    )
    registry.register(
        CommandMeta(
            "plan",
            "切换计划/执行模式",
            "/plan",
            CommandType.UI_STATE,
            _plan,
            aliases=("mode",),
        )
    )
    registry.register(
        CommandMeta(
            "compact", "手动触发上下文压缩", "/compact", CommandType.LOCAL, _compact
        )
    )
    registry.register(
        CommandMeta(
            "resume",
            "恢复会话",
            "/resume",
            CommandType.UI_STATE,
            _resume,
            param_hint="[ID]",
        )
    )
    registry.register(
        CommandMeta(
            "sessions",
            "会话管理",
            "/sessions",
            CommandType.UI_STATE,
            _sessions,
            aliases=("s",),
            param_hint="[delete <ID>|clear|path]",
        )
    )
    registry.register(
        CommandMeta(
            "notes",
            "记忆管理",
            "/notes",
            CommandType.UI_STATE,
            _notes,
            aliases=("n",),
            param_hint="[view|clear|edit|path] [project|user|all]",
        )
    )
    registry.register(
        CommandMeta(
            "status",
            "综合状态",
            "/status",
            CommandType.LOCAL,
            _status,
            aliases=("st",),
        )
    )
    registry.register(
        CommandMeta(
            "permissions", "权限管理（查看）", "/permissions", CommandType.LOCAL, _permissions
        )
    )
