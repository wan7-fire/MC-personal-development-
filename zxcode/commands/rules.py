"""Rule engine management command."""

from __future__ import annotations

from .model import CommandMeta, CommandType
from .registry import CommandRegistry


def _rules(ctx, invocation) -> None:
    args = invocation.args.strip()
    if not args:
        ctx.ui.list_rules()
        return
    sub, _, rest = args.partition(" ")
    if sub == "reload":
        ctx.ui.reload_rules()
    elif sub == "detail":
        ctx.ui.rule_detail(rest.strip())
    else:
        ctx.ui.rule_detail(args)


def register_rules_command(registry: CommandRegistry) -> None:
    registry.register(
        CommandMeta(
            "rules",
            "规则引擎管理",
            "/rules",
            CommandType.LOCAL,
            _rules,
            param_hint="[<id>|reload|detail <id>]",
        )
    )
