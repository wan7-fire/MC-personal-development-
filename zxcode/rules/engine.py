"""Rule engine: event dispatch, interception and action orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .actions import render_template
from .executor import run_action
from .matcher import match_group
from .model import Rule


logger = logging.getLogger("zxcode.rules")


@dataclass
class EmitResult:
    rejected: bool = False
    reason: str = ""
    injected: list[str] = field(default_factory=list)


class RuleEngine:
    def __init__(
        self,
        rules=(),
        *,
        root: Path | str = Path.cwd(),
        confirm=None,
        security=None,
        log=None,
    ) -> None:
        self.root = Path(root)
        self.confirm = confirm
        self.security = security
        self.log = log or logger
        self._rules: list[Rule] = []
        self._by_event: dict[str, list[Rule]] = {}
        self._done: set[str] = set()
        self._background: set[asyncio.Task] = set()
        self.reload(rules)

    def _rebuild(self) -> None:
        by_event: dict[str, list[Rule]] = {}
        for rule in self._rules:
            by_event.setdefault(rule.event, []).append(rule)
        self._by_event = by_event

    def list_rules(self) -> list[Rule]:
        return list(self._rules)

    def get(self, rule_id: str) -> Rule | None:
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        return None

    def reload(self, rules) -> None:
        self._rules = list(rules)
        self._done.clear()
        self._rebuild()

    def reset_once(self) -> None:
        self._done.clear()

    async def drain(self) -> None:
        tasks = [task for task in self._background if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit(self, event: str, context=None) -> EmitResult:
        full = dict(context or {})
        full["event"] = event
        result = EmitResult()
        for rule in self._by_event.get(event, ()):
            if rule.once and rule.id in self._done:
                continue
            if not match_group(rule.conditions, full):
                continue
            if rule.once:
                self._done.add(rule.id)
            if rule.reject is not None:
                result.rejected = True
                result.reason = render_template(rule.reject, full)
                return result
            if rule.async_:
                task = asyncio.create_task(self._run_rule(rule, full))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
            else:
                await self._run_rule(rule, full, result)
        return result

    async def _run_rule(self, rule: Rule, context, result: EmitResult | None = None) -> None:
        for action in rule.actions:
            try:
                outcome = await run_action(
                    action,
                    context,
                    root=self.root,
                    confirm=self.confirm,
                    security=self.security,
                    timeout_seconds=rule.timeout_seconds,
                )
            except Exception as error:
                self.log.error("rule %s action %s failed: %s", rule.id, action.type, error)
                continue
            if not outcome.success:
                self.log.error(
                    "rule %s action %s failed: %s (%s)",
                    rule.id,
                    action.type,
                    outcome.error_message or "",
                    outcome.error_code or "error",
                )
                continue
            if action.type == "prompt" and result is not None:
                result.injected.append(outcome.output)
