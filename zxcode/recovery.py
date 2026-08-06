"""Session recovery: bad-line skipping, dangling-call truncation, size control."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .compress import BOUNDARY_MESSAGE, CompressionFailure, estimate_messages
from .storage import SessionStore

IDLE_REMINDER_THRESHOLD_SECONDS = 4 * 3600
IDLE_REMINDER_PREFIX = "[时间跨度提醒]"


@dataclass
class RecoveryReport:
    session_id: str
    restored_messages: int
    skipped_lines: int
    dangling_truncated: bool = False
    dangling_dropped: int = 0
    compressed: bool = False
    over_limit_dropped: int = 0
    idle_reminder: bool = False
    issues: list[str] = field(default_factory=list)


def truncate_dangling(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop a trailing incomplete tool turn.

    If the newest assistant message declares tool calls without matching
    ``tool`` results, the conversation is cut back to the last complete
    position.  A dangling block in the middle (followed by a newer user
    message) is left alone so later turns are not lost.
    """
    answered = {
        str(message.get("tool_call_id") or "")
        for message in messages
        if message.get("role") == "tool"
    }
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls") or []
        unmatched = any(
            str(call.get("id") or "") not in answered for call in calls
        )
        if not unmatched:
            continue
        if any(
            other.get("role") == "user" for other in messages[index + 1 :]
        ):
            break
        return messages[:index], len(messages) - index
    return messages, 0


def _drop_oldest_turns(
    messages: list[dict[str, Any]], target: int
) -> tuple[list[dict[str, Any]], int]:
    first = 0
    while first < len(messages) and messages[first].get("role") == "system":
        first += 1
    user_indices = [
        index
        for index in range(first, len(messages))
        if messages[index].get("role") == "user"
    ]
    if len(user_indices) < 2:
        return messages, 0
    cut = first
    for next_user in user_indices[1:]:
        remaining = messages[:first] + messages[next_user:]
        if estimate_messages(remaining) <= target:
            cut = next_user
    if cut > first:
        return messages[:first] + messages[cut:], cut - first
    return messages, 0


def _insert_boundary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first = 0
    while first < len(messages) and messages[first].get("role") == "system":
        first += 1
    messages.insert(first, {"role": "user", "content": BOUNDARY_MESSAGE})
    return messages


def _format_gap(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int(seconds % 3600 // 60)
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _maybe_idle_reminder(
    messages: list[dict[str, Any]],
    last_active: str | None,
    *,
    now: datetime | None,
    threshold_seconds: float,
) -> tuple[list[dict[str, Any]], bool]:
    if not last_active:
        return messages, False
    try:
        last = datetime.fromisoformat(last_active)
        current = now or datetime.now().astimezone()
        if last.tzinfo is None:
            last = last.astimezone()
    except ValueError:
        return messages, False
    gap = (current - last).total_seconds()
    if gap < threshold_seconds:
        return messages, False
    reminder = {
        "role": "system",
        "content": (
            f"{IDLE_REMINDER_PREFIX} 距上次活跃已超过 {_format_gap(gap)}，"
            "请基于当前上下文继续。"
        ),
    }
    first = 0
    while first < len(messages) and messages[first].get("role") == "system":
        first += 1
    messages.insert(first, reminder)
    return messages, True


async def recover_session(
    store: SessionStore,
    session_id: str,
    *,
    compressor=None,
    model: str = "",
    now: datetime | None = None,
    idle_threshold_seconds: float = IDLE_REMINDER_THRESHOLD_SECONDS,
) -> tuple[list[dict[str, Any]], RecoveryReport]:
    """Restore a session archive into a usable message list."""
    messages, skipped = store.read_raw_lines(session_id)
    report = RecoveryReport(
        session_id=session_id,
        restored_messages=len(messages),
        skipped_lines=skipped,
    )
    messages, dropped = truncate_dangling(messages)
    if dropped:
        report.dangling_truncated = True
        report.dangling_dropped = dropped
        report.restored_messages = len(messages)
    if compressor is not None and model:
        messages, changed, dropped = await _size_control(
            messages, compressor, model, report
        )
    meta = store.read_meta(session_id)
    last_active = meta.updated_at if meta else None
    if not last_active:
        try:
            last_active = (
                datetime.fromtimestamp(
                    store.jsonl_path(session_id).stat().st_mtime
                )
                .astimezone()
                .isoformat(timespec="seconds")
            )
        except OSError:
            pass
    messages, reminder = _maybe_idle_reminder(
        messages,
        last_active,
        now=now,
        threshold_seconds=idle_threshold_seconds,
    )
    if reminder:
        report.idle_reminder = True
    report.restored_messages = len(messages)
    return messages, report


async def _size_control(messages, compressor, model, report):
    window = compressor.config.context_window
    trigger = int(window * compressor.config.trigger_ratio)
    if estimate_messages(messages) < trigger:
        return messages, False, 0
    try:
        new_messages, outcome = await compressor.compress_history(
            messages, model
        )
        if outcome.changed:
            report.compressed = True
            return new_messages, True, 0
    except Exception as error:
        if isinstance(error, CompressionFailure):
            report.issues.append(str(error.message))
    target = int(window * compressor.config.target_ratio)
    new_messages, dropped = _drop_oldest_turns(messages, target)
    if dropped:
        report.over_limit_dropped = dropped
        new_messages = _insert_boundary(new_messages)
    return new_messages, False, dropped
