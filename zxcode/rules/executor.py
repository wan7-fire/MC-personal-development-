"""Execution controls (timeout) for rule actions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .actions import ActionResult, execute_action
from .model import Action


async def run_action(
    action: Action,
    context,
    *,
    root: Path,
    confirm,
    security,
    timeout_seconds: float,
) -> ActionResult:
    try:
        return await asyncio.wait_for(
            execute_action(
                action,
                context,
                root=root,
                confirm=confirm,
                security=security,
                timeout_seconds=timeout_seconds,
            ),
            timeout_seconds,
        )
    except TimeoutError:
        return ActionResult(
            False, error_code="timeout", error_message="action timed out"
        )
