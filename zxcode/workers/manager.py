"""Background task manager for sub-workers."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .model import WorkerRole
from .runner import run_worker


STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


@dataclass
class WorkerTask:
    id: str
    role: str
    mode: str
    status: str = STATUS_RUNNING
    result: str = ""
    error: str = ""
    token_usage: int = 0
    started_at: float = 0.0
    finished_at: float | None = None
    background: bool = False


class TaskManager:
    def __init__(
        self,
        *,
        roles,
        root,
        client,
        registry,
        config,
        rule_engine,
        history_provider=None,
        model_provider=None,
        parent_tool_names_provider=None,
        on_complete=None,
        foreground_timeout: float = 30.0,
    ) -> None:
        self.roles = dict(roles)
        self.root = Path(root)
        self.client = client
        self.registry = registry
        self.config = config
        self.rule_engine = rule_engine
        self.history_provider = history_provider
        self.model_provider = model_provider
        self.parent_tool_names_provider = parent_tool_names_provider
        self.on_complete = on_complete
        self.foreground_timeout = foreground_timeout
        self._tasks: dict[str, WorkerTask] = {}
        self._futures: dict[str, asyncio.Task] = {}

    def resolve_role(self, name: str) -> WorkerRole | None:
        return self.roles.get(name)

    def list_tasks(self) -> list[WorkerTask]:
        return [self._tasks[key] for key in sorted(self._tasks)]

    def get(self, task_id: str) -> WorkerTask | None:
        return self._tasks.get(task_id)

    async def start(
        self,
        *,
        role_name: str | None = None,
        task: str,
        model: str | None = None,
        background: bool = False,
    ) -> WorkerTask:
        fork = role_name is None
        role = None if fork else self.resolve_role(role_name)
        if not fork and role is None:
            raise ValueError(f"unknown worker role: {role_name}")
        task_obj = WorkerTask(
            id=uuid.uuid4().hex[:8],
            role=role_name or "fork",
            mode="fork" if fork else "definition",
        )
        self._tasks[task_obj.id] = task_obj
        future = asyncio.create_task(
            self._run(task_obj, role, task, fork, model)
        )
        self._futures[task_obj.id] = future
        future.add_done_callback(
            lambda _done: self._futures.pop(task_obj.id, None)
        )
        if fork or background:
            task_obj.background = True
            return task_obj
        try:
            await asyncio.wait_for(
                asyncio.shield(future), self.foreground_timeout
            )
        except TimeoutError:
            task_obj.background = True
            return task_obj
        except asyncio.CancelledError:
            task_obj.background = True
            raise
        return task_obj

    async def wait(self, task_id: str, timeout: float | None = None) -> WorkerTask | None:
        future = self._futures.get(task_id)
        if future is None:
            return self._tasks.get(task_id)
        if timeout is None:
            await asyncio.shield(future)
        else:
            await asyncio.wait_for(asyncio.shield(future), timeout)
        return self._tasks.get(task_id)

    def kill(self, task_id: str) -> bool:
        task_obj = self._tasks.get(task_id)
        if task_obj is None:
            return False
        future = self._futures.get(task_id)
        if future is not None and not future.done():
            future.cancel()
        if task_obj.status == STATUS_RUNNING:
            task_obj.status = STATUS_CANCELLED
            task_obj.finished_at = time.time()
        return True

    async def _run(
        self,
        task_obj: WorkerTask,
        role: WorkerRole | None,
        task: str,
        fork: bool,
        model: str | None,
    ) -> None:
        task_obj.started_at = time.time()
        try:
            parent_history = (
                self.history_provider() if self.history_provider else []
            )
            worker_model = model or (
                self.model_provider() if self.model_provider else "model"
            )
            parent_tool_names = (
                self.parent_tool_names_provider()
                if self.parent_tool_names_provider
                else None
            )
            result = await run_worker(
                role=role,
                task=task,
                fork=fork,
                parent_history=parent_history,
                client=self.client,
                registry=self.registry,
                root=self.root,
                config=self.config,
                rule_engine=self.rule_engine,
                model=worker_model,
                parent_tool_names=parent_tool_names,
            )
            task_obj.status = STATUS_SUCCEEDED
            task_obj.result = result.text
            task_obj.token_usage = result.token_usage
        except asyncio.CancelledError:
            task_obj.status = STATUS_CANCELLED
            raise
        except Exception as error:
            task_obj.status = STATUS_FAILED
            task_obj.error = str(error)
        finally:
            if task_obj.finished_at is None:
                task_obj.finished_at = time.time()
            if self.on_complete is not None and task_obj.status in (
                STATUS_SUCCEEDED,
                STATUS_FAILED,
            ):
                self.on_complete(task_obj)
