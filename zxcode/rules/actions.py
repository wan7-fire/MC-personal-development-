"""Action executors and template rendering for the rule engine."""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..tools import ToolContext
from ..tools.shell import _run
from .matcher import resolve_field
from .model import Action


_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


def render_template(text: str, context) -> str:
    def replace(match) -> str:
        value = resolve_field(match.group(1), context)
        return "" if value is None else str(value)

    return _TEMPLATE.sub(replace, text)


@dataclass
class ActionResult:
    success: bool
    output: str = ""
    error_code: str | None = None
    error_message: str | None = None


async def _ask_confirm(confirm, title: str, detail: str) -> bool:
    if confirm is None:
        return True
    choice = await confirm(title, detail)
    return choice in ("once", "session", "permanent", True)


async def execute_action(
    action: Action,
    context,
    *,
    root: Path,
    confirm,
    security,
    timeout_seconds: float,
) -> ActionResult:
    if action.type == "prompt":
        prompt = render_template(str(action.payload.get("prompt", "")), context)
        return ActionResult(True, prompt)
    if action.type == "command":
        command = render_template(str(action.payload.get("command", "")), context)
        if security is not None:
            blocked = await security.guard_shell(
                command, ToolContext(root, confirm, security)
            )
            if blocked is not None:
                error = blocked.error or {}
                return ActionResult(
                    False,
                    error_code=error.get("code", "security_blocked"),
                    error_message=error.get("message", ""),
                )
        outcome = await _run(command, root)
        if not outcome.success:
            error = outcome.error or {}
            return ActionResult(
                False,
                output=outcome.output,
                error_code=error.get("code", "execution_error"),
                error_message=error.get("message", ""),
            )
        return ActionResult(True, outcome.output)
    if action.type == "http":
        url = render_template(str(action.payload.get("url", "")), context)
        if security is not None and security.mode == "strict":
            return ActionResult(
                False,
                error_code="security_blocked",
                error_message="strict mode requires an allow rule",
            )
        if security is not None and security.mode != "allow":
            if not await _ask_confirm(
                confirm, "Rule HTTP action", f"Send request to {url}?"
            ):
                return ActionResult(
                    False,
                    error_code="permission_denied",
                    error_message="permission denied by user",
                )
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                return ActionResult(True, f"HTTP {response.status}")
        except Exception as error:
            return ActionResult(
                False, error_code="http_error", error_message=str(error)
            )
    if action.type == "agent":
        return ActionResult(
            False,
            error_code="not_implemented",
            error_message="agent action is not implemented",
        )
    return ActionResult(
        False,
        error_code="unknown_action",
        error_message=f"unknown action type: {action.type}",
    )
