"""Multi-layer tool filtering for sub-workers."""

from __future__ import annotations

from .model import WorkerRole


GLOBAL_DENY = frozenset({"SpawnWorker"})
BACKGROUND_ALLOW = frozenset({"ReadFile", "Grep", "Glob"})


def filter_tool_names(
    names,
    role: WorkerRole | None = None,
    *,
    background: bool = False,
) -> tuple[str, ...]:
    allowed = set(names) - GLOBAL_DENY
    if role is not None:
        if role.tools_allow is not None:
            allowed &= set(role.tools_allow)
        allowed -= set(role.tools_deny)
    if background:
        allowed &= BACKGROUND_ALLOW
    return tuple(sorted(allowed))
