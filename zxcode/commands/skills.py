"""Skill management commands and dynamic skill shortcuts."""

from __future__ import annotations

import asyncio

from ..skills.frontmatter import SkillParseError, parse_skill_file
from ..skills.installer import SkillInstallError, install_skill
from .model import CommandMeta, CommandType
from .registry import CommandRegistrationError, CommandRegistry


async def _install(manager, ctx, url: str) -> None:
    if not url:
        ctx.ui.notice("用法：/skills install <skills.sh 链接>")
        return
    ctx.ui.notice("正在安装 Skill…")
    try:
        target = await asyncio.to_thread(install_skill, manager.root, url)
    except SkillInstallError as error:
        ctx.ui.notice(f"安装失败：{error}")
        return
    try:
        meta, _ = parse_skill_file(target / "skill.md")
    except SkillParseError as error:
        ctx.ui.notice(f"安装失败：{error.message}")
        return
    issues = ctx.ui.rescan_skills()
    suffix = f"；跳过 {len(issues)} 个文件" if issues else ""
    ctx.ui.notice(f"已安装 Skill：{meta.name}{suffix}")


def _make_skills_handler(manager):
    def handler(ctx, invocation):
        args = invocation.args.strip()
        if not args:
            metas = manager.list_skills()
            if not metas:
                ctx.ui.notice("没有已加载的 Skill")
                return
            lines = [
                f"{meta.name} | {meta.description} | {meta.level} | {meta.mode}"
                for meta in metas
            ]
            ctx.ui.notice("已加载 Skills：\n" + "\n".join(lines))
            return
        sub, _, rest = args.partition(" ")
        if sub == "install":
            return _install(manager, ctx, rest.strip())
        if args == "rescan":
            issues = ctx.ui.rescan_skills()
            suffix = f"，跳过 {len(issues)} 个文件" if issues else ""
            ctx.ui.notice(f"已重新扫描 Skill{suffix}")
            return
        meta = manager.get(args)
        if meta is None:
            ctx.ui.notice(f"未知 Skill：{args}")
            return
        ctx.ui.notice(
            f"{meta.name} | {meta.description}\n"
            f"模式：{meta.mode}\n"
            f"来源：{meta.level}（{meta.source}）\n"
            f"历史：{meta.history}"
            + (f" / {meta.history_size}" if meta.history == "recent" else "")
            + (f"\n工具白名单：{', '.join(meta.tools)}" if meta.tools else "")
        )

    return handler


def _make_shortcut_handler(name: str):
    def handler(ctx, invocation):
        return ctx.ui.run_skill(name, invocation.args)

    return handler


def register_skill_shortcut(registry: CommandRegistry, meta) -> None:
    try:
        registry.register(
            CommandMeta(
                meta.name,
                meta.description,
                f"/{meta.name}",
                CommandType.LOCAL,
                _make_shortcut_handler(meta.name),
                param_hint="[参数]",
            )
        )
    except CommandRegistrationError:
        pass


def register_skill_commands(registry: CommandRegistry, manager) -> None:
    registry.register(
        CommandMeta(
            "skills",
            "Skill 管理",
            "/skills",
            CommandType.LOCAL,
            _make_skills_handler(manager),
            param_hint="[<name>|rescan|install <url>]",
        )
    )
    for meta in manager.list_skills():
        register_skill_shortcut(registry, meta)
