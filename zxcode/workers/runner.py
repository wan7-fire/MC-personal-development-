"""Sub-worker execution: definition mode and fork mode."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..agent import AgentLoop
from ..compress import estimate_messages
from ..config import AgentConfig
from ..events import EventChannel
from ..security import load_policy
from ..tools import ToolContext, ToolExecutor, ToolRegistry
from .filters import GLOBAL_DENY, filter_tool_names
from .model import WorkerRole
from .prompts import FORK_INSTRUCTION


@dataclass
class WorkerResult:
    text: str
    messages: list[dict]
    token_usage: int


def _definition_messages(role: WorkerRole, task: str) -> list[dict]:
    return [
        {"role": "system", "content": role.body},
        {"role": "user", "content": task},
    ]


def _fork_messages(parent_history, task: str) -> list[dict]:
    messages = [dict(message) for message in parent_history]
    injected = False
    for message in messages:
        if message.get("role") == "user":
            message["content"] = (
                FORK_INSTRUCTION
                + "\n\n"
                + str(message.get("content") or "")
            )
            injected = True
            break
    if not injected:
        messages.append({"role": "user", "content": FORK_INSTRUCTION})
    if task:
        messages.append({"role": "user", "content": task})
    return messages


async def run_worker(
    *,
    role: WorkerRole | None,
    task: str,
    fork: bool,
    parent_history: Sequence[Mapping],
    client,
    registry,
    root: Path,
    config: AgentConfig,
    rule_engine,
    model: str,
    parent_tool_names=None,
) -> WorkerResult:
    if fork:
        messages = _fork_messages(parent_history, task)
        tool_names = (
            set(parent_tool_names)
            if parent_tool_names is not None
            else set(registry.names())
        )
        tool_names -= GLOBAL_DENY
        worker_model = model
        max_turns = config.max_turns
        permission_mode = config.security_mode
    else:
        if role is None:
            raise ValueError("definition mode requires a role")
        messages = _definition_messages(role, task)
        tool_names = filter_tool_names(
            registry.names(), role=role, background=False
        )
        worker_model = role.model or model
        max_turns = role.max_turns
        permission_mode = role.permission_mode

    tools = [
        registry.get(name)
        for name in tool_names
        if registry.get(name) is not None
    ]
    sub_registry = ToolRegistry(tools)
    policy = load_policy(root, permission_mode)
    worker_config = AgentConfig(
        max_turns=max_turns,
        security_mode=permission_mode,
    )
    worker_context = ToolContext(root, confirm=None, security=policy)
    agent = AgentLoop(
        client,
        sub_registry,
        ToolExecutor(sub_registry),
        config=worker_config,
        context=worker_context,
        rule_engine=rule_engine,
    )
    channel = EventChannel()
    runner = asyncio.create_task(agent.run(messages, worker_model, channel))
    async for _ in channel:
        pass
    completed = await runner
    return WorkerResult(
        completed.text,
        list(completed.messages),
        estimate_messages(completed.messages),
    )
