"""Isolated skill execution with summary backflow."""

from __future__ import annotations

from ..agent import AgentLoop
from ..events import EventChannel
from ..tools import ToolExecutor


class _NullEventChannel(EventChannel):
    async def emit(self, event) -> None:
        pass


async def run_isolated(manager, name: str, user_text: str | None = None) -> str:
    active = manager.activate(name)
    try:
        if active.meta.mode != "isolated":
            raise ValueError(f"skill {name} is not isolated")
        if manager.client is None:
            raise RuntimeError("isolated execution requires a client")
        history = (
            list(manager.messages_provider()) if manager.messages_provider else []
        )
        if active.meta.history == "none":
            history = []
        elif active.meta.history == "recent":
            history = history[-active.meta.history_size :]
        model = active.meta.model or (
            manager.model_provider() if manager.model_provider else None
        )
        if not model:
            raise RuntimeError("isolated execution requires a model")
        messages = [
            {
                "role": "system",
                "content": f"[Skill 指令：{name}]\n{active.body}",
            },
            *history,
            {
                "role": "user",
                "content": user_text
                or f"请执行 Skill {name}：{active.meta.description}",
            },
        ]
        agent = AgentLoop(
            manager.client,
            manager.registry,
            ToolExecutor(manager.registry),
            config=manager.config,
            context=manager.context,
            skill_manager=manager,
        )
        completed = await agent.run(messages, model, _NullEventChannel())
        return (
            f"结论：{completed.text or '（无文本输出）'}\n"
            "变更：（见执行过程）\n"
            "未决问题：（见执行过程）\n"
            f"状态：{completed.termination_reason}"
        )
    finally:
        manager.deactivate(name)
